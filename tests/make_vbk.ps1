#Requires -Version 7
#Requires -RunAsAdministrator
<#
Creates Veeam backup test fixtures (VBK/VIB/VRB) from a tiny powered-off Hyper-V VM on this machine.

Needs: Windows 11 Pro with nested virtualization, Veeam Backup & Replication 13 (Community Edition).
Everything is created under -Root; objects in Hyper-V and Veeam are all named vbkfix*.
Rerunning removes what a previous run created and starts over.

  .\make_vbk.ps1                                   all scenarios
  .\make_vbk.ps1 -Scenarios chain_default,comp_none   a subset
  .\make_vbk.ps1 -ZipOnly                          only rebuild the zip (e.g. after adding report.md)
#>
param(
    [string]$Root = 'C:\vbkfix',
    [string[]]$Scenarios,
    [int]$RandomKB = 1536,   # incompressible data ends up in every full and every ground truth copy
    [switch]$DisableCbt,
    [switch]$ZipOnly
)

$ErrorActionPreference = 'Stop'
$VmName   = 'vbkfix-vm'
$VmDir    = Join-Path $Root 'vm'
$RepoDir  = Join-Path $Root 'repo'
$Repo1Dir = Join-Path $Root 'repo_single'
$ZipDir   = Join-Path $Root 'zip'
$RestDir  = Join-Path $Root 'restore'
$OutDir   = Join-Path $Root 'vbk_fixtures'
$ZipFile  = Join-Path $Root 'vbk_fixtures.zip'
$Disks    = @(
    @{ Key = 'disk1'; Path = (Join-Path $VmDir 'disk1.vhdx'); Size = 512MB; Style = 'GPT'; Fs = 'NTFS';  Label = 'VBKFIX1' }
    @{ Key = 'disk2'; Path = (Join-Path $VmDir 'disk2.vhdx'); Size = 256MB; Style = 'MBR'; Fs = 'FAT32'; Label = 'VBKFIX2' }
)

$CompressionNames = @{ 0 = 'None'; 4 = 'Dedupe-friendly'; 5 = 'Optimal'; 6 = 'High'; 9 = 'Extreme' }
$BlockNames = @{ 256 = 'WAN target (256 KB)'; 512 = 'LAN target (512 KB)'; 1024 = 'Local target (1 MB)'; 4096 = 'Local target (large blocks) (4 MB)' }

# Runs: State = which content state of the disks is backed up, Type = full | incremental | reverse | active_full
function New-Scenario($Name, [hashtable]$Set = @{}, $Runs = @(@{ State = 1; Type = 'full' })) {
    $s = @{ Name = $Name; Kind = 'Hyper-V job'; Compression = 5; BlockKB = 1024; Algorithm = 'Incremental'
            Encrypted = $false; Dedup = $true; PerVm = $true; Runs = $Runs }
    foreach ($k in $Set.Keys) { $s[$k] = $Set[$k] }
    $s
}
$AllScenarios = @(
    New-Scenario 'chain_default' @{} @(@{ State = 1; Type = 'full' }, @{ State = 2; Type = 'incremental' }, @{ State = 3; Type = 'incremental' })
    New-Scenario 'chain_reverse' @{ Algorithm = 'ReverseIncremental' } @(@{ State = 1; Type = 'full' }, @{ State = 2; Type = 'reverse' }, @{ State = 3; Type = 'reverse' })
    New-Scenario 'comp_none'    @{ Compression = 0 }
    New-Scenario 'comp_dedupe'  @{ Compression = 4 }
    New-Scenario 'comp_high'    @{ Compression = 6 }
    New-Scenario 'comp_extreme' @{ Compression = 9 }
    New-Scenario 'block_wan'    @{ BlockKB = 256 }
    New-Scenario 'block_lan'    @{ BlockKB = 512 }
    New-Scenario 'block_large'  @{ BlockKB = 4096 }
    New-Scenario 'encrypted'    @{ Encrypted = $true }
    New-Scenario 'zip'          @{ Kind = 'VeeamZIP' }
    # nice to have
    New-Scenario 'chain_active_full' @{} @(@{ State = 1; Type = 'full' }, @{ State = 2; Type = 'incremental' }, @{ State = 3; Type = 'active_full' })
    New-Scenario 'single_file'  @{ PerVm = $false }
    New-Scenario 'dedup_off'    @{ Dedup = $false }
)
if ($Scenarios) {
    $unknown = $Scenarios | Where-Object { $_ -notin $AllScenarios.Name }
    if ($unknown) { throw "Unknown scenario(s): $($unknown -join ', ')" }
    $AllScenarios = @($AllScenarios | Where-Object { $_.Name -in $Scenarios })
}

function Write-Step($Text) { Write-Host "[$(Get-Date -Format HH:mm:ss)] $Text" }

# ---------------------------------------------------------------- zip

function Write-FixtureZip {
    foreach ($f in 'make_vbk.ps1', 'report.md') {
        $src = Join-Path $PSScriptRoot $f
        if (Test-Path $src) { Copy-Item $src (Join-Path $OutDir $f) -Force }
    }
    if (Test-Path $ZipFile) { Remove-Item $ZipFile -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($OutDir, $ZipFile, [IO.Compression.CompressionLevel]::SmallestSize, $true)
    Write-Step ("Zip: {0} ({1:N1} MB)" -f $ZipFile, ((Get-Item $ZipFile).Length / 1MB))
}
if ($ZipOnly) { Write-FixtureZip; return }

# ---------------------------------------------------------------- step 0: preflight

Write-Step 'Preflight'
$feature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All
if ($feature.State -ne 'Enabled') {
    Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All -NoRestart | Out-Null
    Write-Warning 'Hyper-V has been enabled. Reboot the machine, then run this script again.'
    exit 3010
}
if ((Get-Service vmms).Status -ne 'Running') { throw 'Hyper-V is enabled but vmms is not running (reboot pending, or nested virtualization is off).' }
$free = (Get-PSDrive -Name ([IO.Path]::GetPathRoot($Root).Substring(0, 1))).Free
if ($free -lt 10GB) { throw "Less than 10 GB free on the drive of $Root." }

Import-Module Veeam.Backup.PowerShell -WarningAction SilentlyContinue
if (-not (Get-VBRServerSession)) { Connect-VBRServer }

# ---------------------------------------------------------------- step 1: remove what an earlier run left

Write-Step 'Cleanup of earlier runs'
Get-VBRJob -WarningAction SilentlyContinue | Where-Object Name -like 'vbkfix*' | ForEach-Object { Remove-VBRJob -Job $_ -Confirm:$false | Out-Null }
@(Get-VBRBackup) + @(Get-VBRBackup -VeeamZIP) | Where-Object { $_ -and ($_.Name -like 'vbkfix*' -or "$($_.DirPath)" -like "$Root\*") } |
    ForEach-Object { Remove-VBRBackup -Backup $_ -FromDisk -Confirm:$false | Out-Null }
Get-VBRBackupRepository | Where-Object Name -like 'vbkfix*' | ForEach-Object { Remove-VBRBackupRepository -Repository $_ -Confirm:$false | Out-Null }
Get-VBREncryptionKey | Where-Object Description -like 'vbkfix*' | ForEach-Object { Remove-VBREncryptionKey -EncryptionKey $_ -Confirm:$false -ErrorAction SilentlyContinue | Out-Null }
Get-VM -Name 'vbkfix*' -ErrorAction SilentlyContinue | ForEach-Object { Remove-VM -VM $_ -Force }
Get-ChildItem $Root -Filter *.vhdx -Recurse -ErrorAction SilentlyContinue | ForEach-Object { Dismount-VHD -Path $_.FullName -ErrorAction SilentlyContinue }
foreach ($d in $VmDir, $RepoDir, $Repo1Dir, $ZipDir, $RestDir, $OutDir, (Join-Path $Root 'probe'), (Join-Path $Root 'repo_probe')) {
    if (Test-Path $d) { Remove-Item -LiteralPath $d -Recurse -Force }
}
New-Item -ItemType Directory -Force $VmDir, $RepoDir, $Repo1Dir, $ZipDir, $RestDir, $OutDir, (Join-Path $OutDir 'ground_truth') | Out-Null

# ---------------------------------------------------------------- step 2: Veeam infrastructure

Write-Step 'Veeam: Hyper-V host and repositories'
$HostWarnings = @()
$hv = Get-VBRServer -Type HvServer | Where-Object Name -eq $env:COMPUTERNAME
if (-not $hv) {
    # Certificate authentication: the Veeam deployment service is already on the backup server, no password needed.
    $hv = Add-VBRHvHost -Name $env:COMPUTERNAME -UseCertificate -Description 'vbkfix: local Hyper-V host' -WarningVariable +HostWarnings -WarningAction SilentlyContinue
}
$repo  = Add-VBRBackupRepository -Name 'vbkfix-repo' -Folder $RepoDir -Type WinLocal
$repo1 = Add-VBRBackupRepository -Name 'vbkfix-repo-single' -Folder $Repo1Dir -Type WinLocal
$SingleFileError = $null
try {
    # New repositories store per-machine backup files by default; this one is switched the other way.
    Set-VBRBackupRepository -Repository $repo1 -UsePerVMFile:$false -WarningVariable +RepoWarnings -WarningAction SilentlyContinue | Out-Null
    $repo1 = Get-VBRBackupRepository -Name 'vbkfix-repo-single'
    if ($repo1.Options.OneBackupFilePerVm) { throw "OneBackupFilePerVm is still true after Set-VBRBackupRepository -UsePerVMFile:`$false. $RepoWarnings" }
} catch { $SingleFileError = $_.Exception.Message }

# ---------------------------------------------------------------- step 3: deterministic disk content

$Sha = [Security.Cryptography.SHA256]::Create()

# Pseudo-random bytes: SHA-256("vbkfix:<label>:<counter>") for counter 0,1,2,... concatenated.
function Get-StreamBytes([string]$Label, [int]$Length) {
    $buf = [byte[]]::new($Length); $pos = 0; $i = 0
    while ($pos -lt $Length) {
        $h = $Sha.ComputeHash([Text.Encoding]::UTF8.GetBytes("vbkfix:${Label}:$i"))
        $n = [Math]::Min(32, $Length - $pos)
        [Array]::Copy($h, 0, $buf, $pos, $n); $pos += $n; $i++
    }
    , $buf
}

# Repeating, but not constant, text.
function Get-TextBytes([string]$Tag, [int]$MinLength) {
    $sb = [Text.StringBuilder]::new($MinLength + 128); $i = 0
    while ($sb.Length -lt $MinLength) {
        [void]$sb.Append(('{0:D7} {1}: the quick brown fox jumps over the lazy dog, line {0} squared is {2}' -f $i, $Tag, ([long]$i * $i))).Append("`n"); $i++
    }
    , [Text.Encoding]::ASCII.GetBytes($sb.ToString())
}

# Every 4 KiB chunk starts with "CHUNK <tag> <number>\n" and is filled with the byte (number mod 251).
function Get-ChunkBytes([string]$Tag, [int]$Chunks) {
    $buf = [byte[]]::new($Chunks * 4096)
    for ($i = 0; $i -lt $Chunks; $i++) {
        [Array]::Fill($buf, [byte]($i % 251), $i * 4096, 4096)
        $head = [Text.Encoding]::ASCII.GetBytes(('CHUNK {0} {1:D8}' -f $Tag, $i) + "`n")
        [Array]::Copy($head, 0, $buf, $i * 4096, $head.Length)
    }
    , $buf
}

function Write-Bytes([string]$Path, [byte[]]$Bytes) {
    New-Item -ItemType Directory -Force (Split-Path $Path) | Out-Null
    [IO.File]::WriteAllBytes($Path, $Bytes)
}
function Write-Text([string]$Path, [string]$Text) { Write-Bytes $Path ([Text.Encoding]::UTF8.GetBytes($Text)) }

function Write-At([string]$Path, [long]$Offset, [byte[]]$Bytes) {
    $fs = [IO.File]::Open($Path, 'Open', 'Write')
    try { $fs.Position = $Offset; $fs.Write($Bytes, 0, $Bytes.Length) } finally { $fs.Dispose() }
}

function Set-DiskContent([int]$State, [string]$D1, [string]$D2) {
    switch ($State) {
        1 {
            Write-Text "$D1\readme.txt" "vbkfix disk1, NTFS on GPT`n"
            Write-Text "$D1\empty.txt" ''
            Write-Text "$D1\notes\a.txt" "first line of a.txt`n"
            Write-Text "$D1\notes\deep\nested\b.txt" "b.txt lives three folders down`n"
            Write-Text "$D1\notes\árvíztűrő tükörfúrógép.txt" "non-ASCII file name, content: árvíztűrő tükörfúrógép`n"
            Write-Bytes "$D1\compressible.txt" (Get-TextBytes 'compressible' (6MB))
            Write-Bytes "$D1\random.bin" (Get-StreamBytes 'random.bin' ($RandomKB * 1KB))
            Write-Bytes "$D1\zeros.bin" ([byte[]]::new(4MB))
            Write-Bytes "$D1\chunks.bin" (Get-ChunkBytes 'chunks.bin' 640)
            # sparse.bin: 8 MiB long, 4 KiB of data at offset 0 and at 6 MiB, the rest is not allocated
            [IO.File]::WriteAllBytes("$D1\sparse.bin", [byte[]]::new(0))
            fsutil sparse setflag "$D1\sparse.bin" | Out-Null
            $fs = [IO.File]::Open("$D1\sparse.bin", 'Open', 'Write'); $fs.SetLength(8MB); $fs.Dispose()
            Write-At "$D1\sparse.bin" 0 (Get-ChunkBytes 'sparse-head' 1)
            Write-At "$D1\sparse.bin" (6MB) (Get-ChunkBytes 'sparse-6M' 1)

            Write-Text "$D2\readme.txt" "vbkfix disk2, FAT32 on MBR`n"
            Write-Text "$D2\dir\sub\c.txt" "c.txt on the FAT32 volume`n"
            Write-Bytes "$D2\chunks2.bin" (Get-ChunkBytes 'chunks2.bin' 128)
            Write-Bytes "$D2\random2.bin" (Get-StreamBytes 'random2.bin' (64KB))
        }
        2 {
            $len = (Get-Item "$D1\random.bin").Length
            Write-At "$D1\random.bin" ([long]($len / 2) - 32KB) (Get-StreamBytes 'random.bin:run2' (64KB))
            [IO.File]::AppendAllText("$D1\notes\a.txt", "line appended in run 2`n")
            Write-Bytes "$D1\new_run2.bin" (Get-ChunkBytes 'new_run2.bin' 256)
            [IO.File]::AppendAllText("$D2\readme.txt", "line appended in run 2`n")
        }
        3 {
            Remove-Item -LiteralPath "$D1\new_run2.bin" -Force
            Write-Text "$D1\run3\one.txt" "added in run 3`n"
            Write-Text "$D1\run3\two.txt" "also added in run 3`n"
            Write-Bytes "$D1\run3\chunks3.bin" (Get-ChunkBytes 'chunks3.bin' 64)
            $patch = [byte[]]::new(64KB)
            [Array]::Copy((Get-TextBytes 'OVERWRITTEN-IN-RUN-3' (64KB)), $patch, 64KB)
            Write-At "$D1\compressible.txt" 0 $patch
            # disk2 is left unchanged in run 3
        }
    }
    # fixed timestamps, so that reruns differ as little as possible
    $stamp = [datetime]::new(2024, 1, $State, 12, 0, 0, [DateTimeKind]::Utc)
    foreach ($d in $D1, $D2) {
        Get-ChildItem -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch 'System Volume Information|\$RECYCLE\.BIN' -and ($State -eq 1 -or $_.LastWriteTimeUtc.Year -ne 2024) } |
            ForEach-Object { $_.CreationTimeUtc = $stamp; $_.LastWriteTimeUtc = $stamp; $_.LastAccessTimeUtc = $stamp }
    }
}

function Mount-FixtureDisk([string]$Path, [switch]$ReadOnly) {
    $disk = Mount-VHD -Path $Path -ReadOnly:$ReadOnly -Passthru | Get-Disk
    foreach ($try in 1..20) {
        $part = Get-Partition -DiskNumber $disk.Number -ErrorAction SilentlyContinue | Where-Object Type -ne 'Reserved' | Sort-Object Size -Descending | Select-Object -First 1
        if ($part -and -not $part.DriveLetter) { $part | Add-PartitionAccessPath -AssignDriveLetter -ErrorAction SilentlyContinue }
        if ($part.DriveLetter -and (Test-Path "$($part.DriveLetter):\")) { return "$($part.DriveLetter):" }
        Start-Sleep -Milliseconds 500
    }
    throw "No volume appeared for $Path"
}

function Get-VolumeListing([string]$Drive) {
    @(Get-ChildItem -LiteralPath "$Drive\" -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch 'System Volume Information|\$RECYCLE\.BIN' } | Sort-Object FullName | ForEach-Object {
            $rel = $_.FullName.Substring(3).Replace('\', '/')
            if ($_.PSIsContainer) { [ordered]@{ path = $rel; type = 'dir' } }
            else { [ordered]@{ path = $rel; type = 'file'; size = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() } }
        })
}

function Get-Sha256([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower() }

# Brings the disks to content state 1, 2 or 3 and writes ground_truth\stateN.
function Set-FixtureState([int]$State) {
    Write-Step "Disk content: state $State"
    $vm = Get-VM -Name $VmName -ErrorAction SilentlyContinue
    if ($vm) {
        if (Get-VMSnapshot -VM $vm -ErrorAction SilentlyContinue) { throw 'The VM still has a checkpoint; a backup did not clean up.' }
        if ($vm | Get-VMHardDiskDrive | Where-Object Path -notlike '*.vhdx') { throw 'A VM disk does not point to its .vhdx; a backup did not clean up.' }
    }
    if ($State -eq 1) {
        foreach ($d in $Disks) {
            # 1 MB blocks keep the dynamic VHDX close to the size of the data in it
            New-VHD -Path $d.Path -SizeBytes $d.Size -Dynamic -BlockSizeBytes 1MB | Out-Null
            $disk = Mount-VHD -Path $d.Path -Passthru | Get-Disk
            Initialize-Disk -Number $disk.Number -PartitionStyle $d.Style
            $part = New-Partition -DiskNumber $disk.Number -UseMaximumSize -AssignDriveLetter
            Format-Volume -Partition $part -FileSystem $d.Fs -NewFileSystemLabel $d.Label -Confirm:$false | Out-Null
            Dismount-VHD -Path $d.Path
        }
    }
    $drives = @{}
    try {
        foreach ($d in $Disks) { $drives[$d.Key] = Mount-FixtureDisk $d.Path }
        Set-DiskContent $State $drives.disk1 $drives.disk2
        $gt = [ordered]@{ state = $State; disks = @() }
        foreach ($d in $Disks) {
            Write-VolumeCache -DriveLetter $drives[$d.Key][0]
            $gt.disks += [ordered]@{
                key = $d.Key; source_vhdx = "$($d.Key).vhdx"; virtual_size = (Get-VHD -Path $d.Path).Size
                partition_style = $d.Style; filesystem = $d.Fs; label = $d.Label
                entries = Get-VolumeListing $drives[$d.Key]
            }
        }
    } finally {
        foreach ($d in $Disks) { Dismount-VHD -Path $d.Path -ErrorAction SilentlyContinue }
    }
    $gtDir = Join-Path $OutDir "ground_truth\state$State"
    New-Item -ItemType Directory -Force $gtDir | Out-Null
    foreach ($e in $gt.disks) {
        $copy = Join-Path $gtDir $e.source_vhdx
        Copy-Item ($Disks | Where-Object Key -eq $e.key).Path $copy
        $e.vhdx_file_size = (Get-Item $copy).Length
        $e.vhdx_sha256 = Get-Sha256 $copy
    }
    $gt | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $gtDir 'files.json') -Encoding utf8
    if ($State -eq 1) {
        New-VM -Name $VmName -Generation 2 -MemoryStartupBytes 32MB -NoVHD -Path $VmDir | Out-Null
        foreach ($d in $Disks) { Add-VMHardDiskDrive -VMName $VmName -Path $d.Path }
    }
}

# ---------------------------------------------------------------- step 4: backup runs

function Get-DirState([string]$Dir) {
    $state = [ordered]@{}
    if (Test-Path $Dir) {
        foreach ($f in Get-ChildItem -LiteralPath $Dir -Recurse -File | Sort-Object FullName) {
            $state[$f.FullName.Substring($Dir.Length + 1).Replace('\', '/')] = [ordered]@{ size = $f.Length; sha256 = Get-Sha256 $f.FullName }
        }
    }
    $state
}

function Get-SessionLog($Session) {
    $lines = @()
    try {
        $lines += $Session.Logger.GetLog().UpdatedRecords | Sort-Object StartTime | ForEach-Object { "[$($_.Status)] $($_.Title)" }
        foreach ($t in Get-VBRTaskSession -Session $Session) {
            $lines += $t.Logger.GetLog().UpdatedRecords | Sort-Object StartTime | ForEach-Object { "[$($_.Status)] $($t.Name): $($_.Title)" }
        }
    } catch { $lines += "(log not available: $($_.Exception.Message))" }
    , @($lines)
}

$Password = $null
$Results = [ordered]@{}   # scenario name -> manifest entry

function Get-ScenarioDir($Scn) {
    if ($Scn.Kind -eq 'VeeamZIP') { return $ZipDir }
    Join-Path ($Scn.PerVm ? $RepoDir : $Repo1Dir) "vbkfix_$($Scn.Name)"
}

function New-ScenarioJob($Scn) {
    $entity = Find-VBRHvEntity -Server $hv -Name $VmName
    $job = Add-VBRHvBackupJob -Name "vbkfix_$($Scn.Name)" -Entity $entity -BackupRepository ($Scn.PerVm ? $repo : $repo1)
    $storage = @{ Job = $job; CompressionLevel = $Scn.Compression; StorageBlockSize = "KbBlockSize$($Scn.BlockKB)"; EnableDeduplication = $Scn.Dedup }
    if ($Scn.Encrypted) {
        $script:Password = 'vbkfix-' + [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(8)).ToLower()
        $key = Add-VBREncryptionKey -Password (ConvertTo-SecureString $script:Password -AsPlainText -Force) -Description 'vbkfix throwaway test key'
        $storage.EnableEncryption = $true; $storage.EncryptionKey = $key
    }
    Set-VBRJobAdvancedStorageOptions @storage | Out-Null
    # No scheduled synthetic or active fulls: the type of each run is decided here, not by the weekday.
    Set-VBRJobAdvancedBackupOptions -Job $job -Algorithm $Scn.Algorithm -TransformFullToSynthetic $false -EnableFullBackup $false | Out-Null
    if ($DisableCbt) { Set-VBRJobAdvancedHvOptions -Job $job -UseChangeTracking $false | Out-Null }
    Get-VBRJob -Name "vbkfix_$($Scn.Name)" -WarningAction SilentlyContinue
}

function Invoke-ScenarioRun($Scn, $Run) {
    $entry = $Results[$Scn.Name]
    if ($entry.skipped) { return }
    $dir = Get-ScenarioDir $Scn
    Write-Step "  $($Scn.Name): $($Run.Type) (state $($Run.State))"
    try {
        if (-not $Scn.PerVm -and $SingleFileError) { throw "Could not switch off per-machine backup files: $SingleFileError" }
        $before = Get-DirState $dir
        $started = Get-Date
        if ($Scn.Kind -eq 'VeeamZIP') {
            $session = Start-VBRZip -Entity (Find-VBRHvEntity -Server $hv -Name $VmName) -Folder $ZipDir -Compression $Scn.Compression
        } else {
            $job = Get-VBRJob -Name "vbkfix_$($Scn.Name)" -WarningAction SilentlyContinue
            if (-not $job) { $job = New-ScenarioJob $Scn }
            $session = Start-VBRJob -Job $job -FullBackup:($Run.Type -eq 'active_full')
        }
        $log = Get-SessionLog $session
        if ("$($session.Result)" -ne 'Success') { throw "Session result is '$($session.Result)'.`n$($log -join "`n")" }
        Start-Sleep -Seconds 2   # let Veeam release the files
        $after = Get-DirState $dir
        $files = @(foreach ($name in $after.Keys) {
            $change = if (-not $before.Contains($name)) { 'created' } elseif ($before[$name].sha256 -ne $after[$name].sha256) { 'modified' } else { $null }
            if ($change) { [ordered]@{ name = $name; change = $change; size = $after[$name].size; sha256 = $after[$name].sha256 } }
        })
        $files += @($before.Keys | Where-Object { -not $after.Contains($_) } | ForEach-Object { [ordered]@{ name = $_; change = 'deleted' } })
        $entry.runs += [ordered]@{
            run = $entry.runs.Count + 1; type = $Run.Type; time = $started.ToString('o'); ground_truth = "ground_truth/state$($Run.State)"
            session_result = "$($session.Result)"; files = $files; session_log = $log
        }
    } catch {
        Write-Warning "$($Scn.Name) skipped: $($_.Exception.Message)"
        $entry.skipped = $true; $entry.skip_reason = $_.Exception.Message
    }
}

foreach ($scn in $AllScenarios) {
    $Results[$scn.Name] = [ordered]@{
        name = $scn.Name; kind = $scn.Kind; skipped = $false
        settings = [ordered]@{
            compression = $CompressionNames[$scn.Compression]; compression_level = $scn.Compression
            storage_optimization = $BlockNames[$scn.BlockKB]; block_size_kb = $scn.BlockKB
            backup_mode = $scn.Algorithm; inline_deduplication = $scn.Dedup
            encryption = $scn.Encrypted; password = $null
            change_tracking = -not $DisableCbt; per_machine_backup_files = $scn.PerVm
            other = 'scheduled synthetic/active fulls switched off; VM powered off, crash-consistent'
        }
        runs = @()
    }
}

foreach ($state in 1..3) {
    $todo = @($AllScenarios | Where-Object { $_.Runs.State -contains $state })
    if (-not $todo) { continue }
    Set-FixtureState $state
    foreach ($scn in $todo) { Invoke-ScenarioRun $scn ($scn.Runs | Where-Object State -eq $state) }
}

# ---------------------------------------------------------------- step 5: collect backup files, restore points

Write-Step 'Collecting backup files'
foreach ($scn in $AllScenarios) {
    $entry = $Results[$scn.Name]
    if ($scn.Encrypted) { $entry.settings.password = $Password }
    if ($entry.skipped) { continue }
    $dst = Join-Path $OutDir $scn.Name
    Copy-Item -LiteralPath (Get-ScenarioDir $scn) -Destination $dst -Recurse
    $entry.final_files = @(Get-ChildItem $dst -Recurse -File | Sort-Object Name | ForEach-Object { [ordered]@{ name = $_.FullName.Substring($dst.Length + 1).Replace('\', '/'); size = $_.Length; sha256 = Get-Sha256 $_.FullName } })
    try {
        $backup = if ($scn.Kind -eq 'VeeamZIP') { Get-VBRBackup -VeeamZIP | Where-Object { "$($_.DirPath)" -like "$ZipDir*" } } else { Get-VBRBackup -Name "vbkfix_$($scn.Name)" }
        # After the last run: which file holds which restore point (for reverse incremental the .vbk is the newest point).
        $entry.restore_points = @(Get-VBRRestorePoint -Backup $backup | Sort-Object CreationTime | ForEach-Object {
            [ordered]@{ time = $_.CreationTime.ToString('o'); type = "$($_.Type)"; algorithm = "$($_.Algorithm)"; file = [IO.Path]::GetFileName("$($_.GetStorage().FilePath)") }
        })
    } catch { $entry.restore_points = "not available: $($_.Exception.Message)" }
}

# ---------------------------------------------------------------- step 6: Veeam as the reference reader

# Restores a restore point with Veeam, compares the disks in it with the ground truth of that state.
function Test-RestorePoint($Scn, [int]$RunIndex) {
    $state = $Scn.Runs[$RunIndex].State
    $dst = Join-Path $RestDir "$($Scn.Name)_run$($RunIndex + 1)"
    New-Item -ItemType Directory -Force $dst | Out-Null
    $rp = @(Get-VBRRestorePoint -Backup (Get-VBRBackup -Name "vbkfix_$($Scn.Name)") | Sort-Object CreationTime)[$RunIndex]
    $res = [ordered]@{ scenario = $Scn.Name; run = $RunIndex + 1; ground_truth = "ground_truth/state$state"; method = 'Start-VBRRestoreVMFiles' }
    $res.files_in_restore_point = @(Get-VBRFilesInRestorePoint -RestorePoint $rp | ForEach-Object { [ordered]@{ name = "$($_.FileName)"; size = $_.Size } })
    $session = Start-VBRRestoreVMFiles -RestorePoint $rp -Server (Get-VBRLocalhost) -Path $dst -Reason 'vbkfix reference restore' -Force
    $res.session_result = "$($session.Result)"
    $res.restored = Get-RestoredInfo $dst $state
    $res
}

function Get-RestoredInfo([string]$Dir, [int]$State) {
    $gt = Get-Content (Join-Path $OutDir "ground_truth\state$State\files.json") -Raw | ConvertFrom-Json
    @(Get-ChildItem $Dir -Recurse -File | Sort-Object Name | ForEach-Object {
        $info = [ordered]@{ name = $_.FullName.Substring($Dir.Length + 1).Replace('\', '/'); size = $_.Length; sha256 = Get-Sha256 $_.FullName }
        $src = $gt.disks | Where-Object source_vhdx -eq $_.Name
        if ($src) {
            $info.identical_to_source_vhdx_file = ($info.sha256 -eq $src.vhdx_sha256)
            $vhd = Get-VHD -Path $_.FullName
            $info.vhd_type = "$($vhd.VhdType)"; $info.virtual_size = $vhd.Size; $info.vhdx_block_size = $vhd.BlockSize
            try {
                $drive = Mount-FixtureDisk $_.FullName -ReadOnly
                $got = Get-VolumeListing $drive | ConvertTo-Json -Depth 5 -Compress
                $info.volume_content_matches_ground_truth = ($got -eq ($src.entries | ConvertTo-Json -Depth 5 -Compress))
            } catch { $info.volume_content_matches_ground_truth = "check failed: $($_.Exception.Message)" }
            finally { Dismount-VHD -Path $_.FullName -ErrorAction SilentlyContinue }
        }
        $info
    })
}

Write-Step 'Reference restores with Veeam'
$Reference = @()
foreach ($scn in $AllScenarios | Where-Object { -not $Results[$_.Name].skipped -and $_.Kind -ne 'VeeamZIP' }) {
    # every point of the chains (this also proves the incrementals are right), the full of everything else
    foreach ($i in 0..($scn.Runs.Count - 1)) {
        Write-Step "  restore $($scn.Name) run $($i + 1)"
        try { $Reference += Test-RestorePoint $scn $i }
        catch { $Reference += [ordered]@{ scenario = $scn.Name; run = $i + 1; error = $_.Exception.Message } }
    }
}

# Extract.exe, the standalone utility that ships with Veeam, on the plain files.
foreach ($name in 'comp_high', 'zip') {
    $scn = $AllScenarios | Where-Object Name -eq $name
    if (-not $scn -or $Results[$name].skipped) { continue }
    Write-Step "  Extract.exe on $name"
    $vbk = Get-ChildItem (Join-Path $OutDir $name) -Recurse -Filter *.vbk | Select-Object -First 1
    $dst = Join-Path $RestDir "${name}_extract"
    New-Item -ItemType Directory -Force $dst | Out-Null
    $exe = Join-Path (Split-Path (Get-Module Veeam.Backup.PowerShell).ModuleBase) 'Extract.exe'
    $out = Join-Path $RestDir "${name}_extract.log"
    $p = Start-Process -FilePath $exe -ArgumentList '-restore', "`"$($vbk.FullName)`"", "`"$dst`"" -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError "$out.err"
    $res = [ordered]@{ scenario = $name; run = 1; ground_truth = 'ground_truth/state1'; method = 'Extract.exe -restore' }
    if (-not $p.WaitForExit(600000)) { $p.Kill(); $res.error = 'timeout' }
    else { $res.exit_code = $p.ExitCode; $res.restored = Get-RestoredInfo $dst 1 }
    $Reference += $res
}
$Reference | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $OutDir 'ground_truth\veeam_restore.json') -Encoding utf8

# ---------------------------------------------------------------- step 7: manifest and zip

Write-Step 'Manifest'
$os = Get-CimInstance Win32_OperatingSystem
$veeamExe = Get-Item (Join-Path (Split-Path (Split-Path (Get-Module Veeam.Backup.PowerShell).ModuleBase)) 'Backup\Veeam.Backup.Service.exe') -ErrorAction SilentlyContinue
$manifest = [ordered]@{
    date = (Get-Date).ToString('o')
    veeam = [ordered]@{
        product = 'Veeam Backup & Replication'; powershell_module_version = "$((Get-Module Veeam.Backup.PowerShell).Version)"
        build = $veeamExe ? $veeamExe.VersionInfo.ProductVersion : $null
        license = "$((Get-VBRInstalledLicense).Edition) / $((Get-VBRInstalledLicense).Type)"
    }
    windows = [ordered]@{ caption = $os.Caption; version = $os.Version; build = $os.BuildNumber }
    powershell = "$($PSVersionTable.PSVersion)"
    source = [ordered]@{
        kind = 'Hyper-V VM, generation 2, powered off, no OS'; vm_name = $VmName
        hyperv_host_add_warnings = @($HostWarnings | ForEach-Object { "$_" })
        random_stream = 'SHA-256("vbkfix:<label>:<counter>") for counter 0,1,2,... concatenated; labels: file name, "random.bin:run2" for the run 2 patch'
        states = [ordered]@{
            '1' = 'initial content'
            '2' = 'disk1: 64 KiB in the middle of random.bin replaced, notes/a.txt appended, new_run2.bin (1 MiB) added; disk2: readme.txt appended'
            '3' = 'disk1: new_run2.bin deleted, folder run3 with 3 files added, first 64 KiB of compressible.txt overwritten; disk2 unchanged'
        }
    }
    scenarios = @($Results.Values)
}
$manifest | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $OutDir 'manifest.json') -Encoding utf8
Write-FixtureZip

Write-Step 'Done. Created on this machine:'
"  Hyper-V VM      : $VmName"
"  Veeam jobs      : $((Get-VBRJob -WarningAction SilentlyContinue | Where-Object Name -like 'vbkfix*').Name -join ', ')"
"  Veeam repos     : vbkfix-repo, vbkfix-repo-single; Hyper-V host entry $env:COMPUTERNAME; encryption key 'vbkfix throwaway test key'"
"  Folders         : $Root"
"  Skipped         : $(($Results.Values | Where-Object skipped | ForEach-Object name) -join ', ')"
