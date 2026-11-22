#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
Creates the differencing VHD / VHDX test images of adiskreader. Needs Windows 10 / Server 2016 or newer
and an elevated PowerShell, the Hyper-V role is NOT needed (diskpart and Mount-DiskImage are used).

    powershell -ExecutionPolicy Bypass -File make_diff_vhd.ps1 [-OutDir C:\adiskreader_fixtures]

For each of the four base disks (dynamic vhd, fixed vhd, dynamic vhdx, fixed vhdx) the chain is:

    diff_<name>_base  <-  diff_<name>_child1  <-  diff_<name>_child2  <-  sub\diff_<name>_sub

The base has an NTFS partition in its first 100MB, each level changes files on it and writes byte
patterns to the unpartitioned area after it. After a level is finished, the disk is attached read-only
and its full contents are saved (gzipped) as <image>.raw.gz, this is what a reader must return.

Everything is collected in <OutDir>\adiskreader_diff_fixtures.zip, manifest.json in it describes the files.
Nothing outside of OutDir is touched, the virtual disks are detached when the script ends.
#>
param(
    [string]$OutDir = 'C:\adiskreader_fixtures'
)

$ErrorActionPreference = 'Stop'

$DISK_SIZE_MB   = 256
$PARTITION_SIZE = 100MB
# the raw writes must be after the partition
$RAW_AREA_START = 112MB

$CHAINS = @(
    @{ name = 'vhd';       ext = 'vhd';  type = 'expandable' },
    @{ name = 'vhdfixed';  ext = 'vhd';  type = 'fixed' },
    @{ name = 'vhdx';      ext = 'vhdx'; type = 'expandable' },
    @{ name = 'vhdxfixed'; ext = 'vhdx'; type = 'fixed' }
)

# (offset, length, byte) written directly to the disk at each level, sector aligned
$RAW_WRITES = @{
    base   = @( ,@(120MB, 1MB, 0x42) )
    child1 = @( @(112MB, 1MB, 0xA5), @((150MB + 512), 1536, 0x5A), @(200MB, 4MB, 0x77), @((120MB + 4096), 4096, 0x00) )
    child2 = @( @((112MB + 64KB), 64KB, 0x11), @(201MB, 1MB, 0x00), @(250MB, 2MB, 0xC3) )
    sub    = @( ,@(130MB, 512KB, 0xEE) )
}

Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Microsoft.Win32.SafeHandles;

public static class ADRTools
{
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);

    // FileStream refuses to open \\.\ paths by name
    static FileStream OpenDisk(int number, bool write)
    {
        uint access = write ? 0xC0000000 : 0x80000000;
        SafeFileHandle handle = CreateFile("\\\\.\\PhysicalDrive" + number, access, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
        if (handle.IsInvalid)
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        return new FileStream(handle, write ? FileAccess.ReadWrite : FileAccess.Read, 1024 * 1024);
    }

    static string Hex(byte[] data)
    {
        return BitConverter.ToString(data).Replace("-", "").ToLowerInvariant();
    }

    // saves the whole disk gzipped, returns the sha256 of the uncompressed data
    public static string DumpDisk(int number, long size, string outPath)
    {
        byte[] buffer = new byte[1024 * 1024];
        using (SHA256 sha = SHA256.Create())
        using (FileStream disk = OpenDisk(number, false))
        using (FileStream outFile = new FileStream(outPath, FileMode.CreateNew, FileAccess.Write))
        using (GZipStream gz = new GZipStream(outFile, CompressionLevel.Optimal))
        {
            long total = 0;
            while (total < size)
            {
                int toRead = (int)Math.Min(buffer.Length, size - total);
                int done = 0;
                while (done < toRead)
                {
                    int res = disk.Read(buffer, done, toRead - done);
                    if (res <= 0)
                        throw new IOException("Disk " + number + " ended at " + (total + done) + ", expected " + size);
                    done += res;
                }
                sha.TransformBlock(buffer, 0, toRead, null, 0);
                gz.Write(buffer, 0, toRead);
                total += toRead;
            }
            sha.TransformFinalBlock(buffer, 0, 0);
            return Hex(sha.Hash);
        }
    }

    public static void FillDisk(int number, long offset, long length, byte value)
    {
        if (offset % 512 != 0 || length % 512 != 0)
            throw new ArgumentException("Raw writes must be sector aligned");
        byte[] buffer = new byte[1024 * 1024];
        for (int i = 0; i < buffer.Length; i++)
            buffer[i] = value;
        using (FileStream disk = OpenDisk(number, true))
        {
            disk.Seek(offset, SeekOrigin.Begin);
            while (length > 0)
            {
                int toWrite = (int)Math.Min(buffer.Length, length);
                disk.Write(buffer, 0, toWrite);
                length -= toWrite;
            }
            disk.Flush();
        }
    }

    // every 4096 byte block of the file is filled with (seed + index of the block) & 0xFF
    public static void PatternFile(string path, long size, int seed)
    {
        byte[] block = new byte[4096];
        using (FileStream f = new FileStream(path, FileMode.Create, FileAccess.Write))
        {
            long index = 0;
            while (size > 0)
            {
                byte value = (byte)((seed + index) & 0xFF);
                for (int i = 0; i < block.Length; i++)
                    block[i] = value;
                int toWrite = (int)Math.Min(block.Length, size);
                f.Write(block, 0, toWrite);
                size -= toWrite;
                index++;
            }
        }
    }

    public static void PatchFile(string path, long offset, int length, byte value)
    {
        byte[] data = new byte[length];
        for (int i = 0; i < length; i++)
            data[i] = value;
        using (FileStream f = new FileStream(path, FileMode.Open, FileAccess.Write))
        {
            f.Seek(offset, SeekOrigin.Begin);
            f.Write(data, 0, length);
        }
    }

    public static string GzipFile(string source, string destination)
    {
        byte[] buffer = new byte[1024 * 1024];
        using (SHA256 sha = SHA256.Create())
        using (FileStream inFile = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (FileStream outFile = new FileStream(destination, FileMode.CreateNew, FileAccess.Write))
        using (GZipStream gz = new GZipStream(outFile, CompressionLevel.Optimal))
        {
            int res;
            while ((res = inFile.Read(buffer, 0, buffer.Length)) > 0)
            {
                sha.TransformBlock(buffer, 0, res, null, 0);
                gz.Write(buffer, 0, res);
            }
            sha.TransformFinalBlock(buffer, 0, 0);
            return Hex(sha.Hash);
        }
    }
}
'@

$script:Mounted = New-Object System.Collections.ArrayList

function Invoke-DiskPart([string[]]$Commands) {
    $scriptfile = Join-Path $env:TEMP ('adr_diskpart_{0}.txt' -f [guid]::NewGuid().ToString('N'))
    Set-Content -Path $scriptfile -Value $Commands -Encoding ASCII
    try {
        $output = & diskpart.exe /s $scriptfile 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw ("diskpart failed ({0}):`n{1}`n{2}" -f $LASTEXITCODE, ($Commands -join "`n"), ($output -join "`n"))
        }
    }
    finally {
        Remove-Item -Path $scriptfile -Force -ErrorAction SilentlyContinue
    }
}

function Mount-Image([string]$Path, [switch]$ReadOnly) {
    # returns the number of the disk
    if ($ReadOnly) {
        Mount-DiskImage -ImagePath $Path -Access ReadOnly -NoDriveLetter | Out-Null
    }
    else {
        Mount-DiskImage -ImagePath $Path -NoDriveLetter | Out-Null
    }
    [void]$script:Mounted.Add($Path)
    $number = $null
    for ($i = 0; $i -lt 20 -and $null -eq $number; $i++) {
        $image = Get-DiskImage -ImagePath $Path
        $number = $image.Number
        if ($null -eq $number) {
            $disk = $image | Get-Disk -ErrorAction SilentlyContinue
            if ($null -ne $disk) { $number = $disk.Number }
        }
        if ($null -eq $number) { Start-Sleep -Milliseconds 500 }
    }
    if ($null -eq $number) { throw "Could not find the disk of $Path" }
    if (-not $ReadOnly) {
        $disk = Get-Disk -Number $number
        if ($disk.IsOffline)  { Set-Disk -Number $number -IsOffline $false }
        if ($disk.IsReadOnly) { Set-Disk -Number $number -IsReadOnly $false }
    }
    return [int]$number
}

function Dismount-Image([string]$Path) {
    Dismount-DiskImage -ImagePath $Path | Out-Null
    $script:Mounted.Remove($Path)
}

function Get-VolumeRoot([int]$Number) {
    # returns the root (X:\) of the NTFS partition of the disk, assigns a drive letter if needed
    $partition = Get-Partition -DiskNumber $Number | Where-Object { $_.Size -gt 50MB } | Select-Object -First 1
    if ($null -eq $partition) { throw "No partition on disk $Number" }
    $letter = ("{0}" -f $partition.DriveLetter).Trim([char]0, ' ')
    if ($letter.Length -eq 0) {
        $partition | Add-PartitionAccessPath -AssignDriveLetter | Out-Null
        for ($i = 0; $i -lt 20 -and $letter.Length -eq 0; $i++) {
            Start-Sleep -Milliseconds 500
            $partition = Get-Partition -DiskNumber $Number -PartitionNumber $partition.PartitionNumber
            $letter = ("{0}" -f $partition.DriveLetter).Trim([char]0, ' ')
        }
    }
    if ($letter.Length -eq 0) { throw "No drive letter for disk $Number" }
    $root = $letter + ':\'
    for ($i = 0; $i -lt 20 -and -not (Test-Path $root); $i++) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Path $root)) { throw "$root is not available" }
    return $root
}

function Write-RawPatterns([int]$Number, [string]$Level) {
    foreach ($write in $RAW_WRITES[$Level]) {
        [ADRTools]::FillDisk($Number, [long]$write[0], [long]$write[1], [byte]$write[2])
    }
}

function Update-Base([int]$Number) {
    Initialize-Disk -Number $Number -PartitionStyle MBR
    $partition = New-Partition -DiskNumber $Number -Size $PARTITION_SIZE -AssignDriveLetter
    if (($partition.Offset + $partition.Size) -gt $RAW_AREA_START) {
        throw "The partition ends at $($partition.Offset + $partition.Size), after the start of the raw writes"
    }
    Format-Volume -DriveLetter $partition.DriveLetter -FileSystem NTFS -NewFileSystemLabel 'ADRDIFF' -Confirm:$false | Out-Null
    $root = Get-VolumeRoot $Number
    Set-Content -Path (Join-Path $root 'hello.txt') -Value 'base' -Encoding ASCII
    New-Item -ItemType Directory -Path (Join-Path $root 'dir1') | Out-Null
    [ADRTools]::PatternFile((Join-Path $root 'dir1\pattern_a.bin'), 4MB, 1)
    [ADRTools]::PatternFile((Join-Path $root 'dir1\delete_me.bin'), 2MB, 2)
    [ADRTools]::PatternFile((Join-Path $root 'overwrite.bin'), 8MB, 3)
}

function Update-Child1([int]$Number) {
    $root = Get-VolumeRoot $Number
    Set-Content -Path (Join-Path $root 'hello.txt') -Value 'child1' -Encoding ASCII
    Remove-Item -Path (Join-Path $root 'dir1\delete_me.bin') -Force
    New-Item -ItemType Directory -Path (Join-Path $root 'dir2') | Out-Null
    [ADRTools]::PatternFile((Join-Path $root 'dir2\pattern_c.bin'), 6MB, 4)
}

function Update-Child2([int]$Number) {
    $root = Get-VolumeRoot $Number
    Set-Content -Path (Join-Path $root 'child2.txt') -Value 'child2' -Encoding ASCII
    [ADRTools]::PatchFile((Join-Path $root 'overwrite.bin'), 3MB, 1MB, 0xBB)
}

function Update-Sub([int]$Number) {
    $root = Get-VolumeRoot $Number
    Set-Content -Path (Join-Path $root 'sub.txt') -Value 'sub' -Encoding ASCII
}

function Build-Level([hashtable]$Chain, [string]$Level, [string]$RelPath, [string]$ParentRelPath) {
    $path = Join-Path $OutDir $RelPath
    Write-Host ("  {0}" -f $RelPath)
    if ($ParentRelPath) {
        Invoke-DiskPart @(('create vdisk file="{0}" parent="{1}"' -f $path, (Join-Path $OutDir $ParentRelPath)))
    }
    else {
        Invoke-DiskPart @(('create vdisk file="{0}" maximum={1} type={2}' -f $path, $DISK_SIZE_MB, $Chain.type))
    }

    $number = Mount-Image $path
    switch ($Level) {
        'base'   { Update-Base $number | Out-Null }
        'child1' { Update-Child1 $number | Out-Null }
        'child2' { Update-Child2 $number | Out-Null }
        'sub'    { Update-Sub $number | Out-Null }
    }
    Write-RawPatterns $number $Level | Out-Null
    Dismount-Image $path | Out-Null

    # what Windows reads from the finished disk
    $number = Mount-Image $path -ReadOnly
    $size = (Get-Disk -Number $number).Size
    $rawname = ($RelPath -replace '\\', '_') + '.raw.gz'
    $sha256 = [ADRTools]::DumpDisk($number, $size, (Join-Path $PackDir $rawname))
    Dismount-Image $path | Out-Null

    return [ordered]@{
        file       = ($RelPath -replace '\\', '/')
        parent     = $(if ($ParentRelPath) { $ParentRelPath -replace '\\', '/' } else { $null })
        chain      = $Chain.name
        level      = $Level
        type       = $(if ($ParentRelPath) { 'differencing' } else { $Chain.type })
        disk_size  = $size
        raw        = $rawname
        raw_sha256 = $sha256
        raw_writes = @($RAW_WRITES[$Level] | ForEach-Object { ,@([long]$_[0], [long]$_[1], [int]$_[2]) })
    }
}

########################################## MAIN ##########################################

if (Test-Path $OutDir) {
    if (@(Get-ChildItem -Path $OutDir -Force).Count -ne 0) {
        throw "$OutDir is not empty, remove it or give another -OutDir"
    }
}
else {
    New-Item -ItemType Directory -Path $OutDir | Out-Null
}
$OutDir = (Resolve-Path $OutDir).Path
$PackDir = Join-Path $OutDir 'pack'
New-Item -ItemType Directory -Path $PackDir | Out-Null
New-Item -ItemType Directory -Path (Join-Path $PackDir 'sub') | Out-Null
New-Item -ItemType Directory -Path (Join-Path $OutDir 'sub') | Out-Null

$images = @()
try {
    foreach ($chain in $CHAINS) {
        Write-Host ("Creating the {0} chain" -f $chain.name)
        $base   = 'diff_{0}_base.{1}' -f $chain.name, $chain.ext
        $child1 = 'diff_{0}_child1.{1}' -f $chain.name, $chain.ext
        $child2 = 'diff_{0}_child2.{1}' -f $chain.name, $chain.ext
        $sub    = 'sub\diff_{0}_sub.{1}' -f $chain.name, $chain.ext
        $images += Build-Level $chain 'base' $base $null
        $images += Build-Level $chain 'child1' $child1 $base
        $images += Build-Level $chain 'child2' $child2 $child1
        $images += Build-Level $chain 'sub' $sub $child2
    }
}
finally {
    foreach ($path in @($script:Mounted)) {
        Dismount-DiskImage -ImagePath $path -ErrorAction SilentlyContinue | Out-Null
    }
}

Write-Host 'Compressing the images'
foreach ($image in $images) {
    $relpath = $image.file -replace '/', '\'
    $image['file_sha256'] = [ADRTools]::GzipFile((Join-Path $OutDir $relpath), (Join-Path $PackDir ($relpath + '.gz')))
}

$os = Get-CimInstance Win32_OperatingSystem
$manifest = [ordered]@{
    created_on = ('{0} {1}' -f $os.Caption, $os.Version)
    images     = $images
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $PackDir 'manifest.json') -Encoding ASCII

$zip = Join-Path $OutDir 'adiskreader_diff_fixtures.zip'
Compress-Archive -Path (Join-Path $PackDir '*') -DestinationPath $zip
Write-Host ("Done: {0} ({1:N1} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
