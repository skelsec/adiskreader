#Requires -Version 5.1
<#
Creates the ISO9660 / Joliet / UDF test images of adiskreader with IMAPI2, the disc image writer of Windows.
Needs Windows 7 / Server 2008 R2 or newer (with the Desktop Experience on the servers), no elevation is needed.

    powershell -ExecutionPolicy Bypass -File make_iso.ps1 [-OutDir C:\adiskreader_iso_fixtures]

Two directory trees are created (generated data only, no files of the system are copied):

    common   names and depth which fit in every filesystem
    extra    long names and deep directories, only for the images having nothing else than UDF

and written to:

    iso_iso9660.iso   ISO9660 only
    iso_joliet.iso    ISO9660 + Joliet
    iso_bridge.iso    ISO9660 + Joliet + UDF 1.02
    iso_udf102.iso    UDF 1.02
    iso_udf150.iso    UDF 1.50
    iso_udf200.iso    UDF 2.00
    iso_udf201.iso    UDF 2.01
    iso_udf250.iso    UDF 2.50
    iso_udf260.iso    UDF 2.60

The images IMAPI2 refuses to create are left out, the reason is written to the manifest.
Everything is collected in <OutDir>\adiskreader_iso_fixtures.zip, manifest.json in it describes the images
and the contents of the trees (size, sha256, attributes, last write time).
Nothing outside of OutDir is touched.
#>
param(
    [string]$OutDir = 'C:\adiskreader_iso_fixtures'
)

$ErrorActionPreference = 'Stop'

$FS_ISO9660 = 1
$FS_JOLIET = 2
$FS_UDF = 4
$VOLUME_NAME = 'ADR_TEST'

Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Security.Cryptography;
using System.Text;

public static class ADRIsoTools
{
    public static byte[] RandomData(int size, int seed)
    {
        byte[] data = new byte[size];
        new Random(seed).NextBytes(data);
        return data;
    }

    // lines of text, compresses well
    public static byte[] TextData(int size, int seed)
    {
        string[] words = { "disc", "image", "volume", "extent", "sector", "descriptor", "anchor", "partition", "directory", "record", "block", "tag" };
        Random rng = new Random(seed);
        StringBuilder sb = new StringBuilder(size + 256);
        int line = 0;
        while (sb.Length < size)
        {
            sb.Append(line++).Append(':');
            int count = 3 + rng.Next(10);
            for (int i = 0; i < count; i++)
                sb.Append(' ').Append(words[rng.Next(words.Length)]);
            sb.Append("\r\n");
        }
        return Encoding.ASCII.GetBytes(sb.ToString(0, size));
    }

    // the image is given as a COM stream by IMAPI2
    public static long SaveStream(object comStream, string path)
    {
        IStream stream = (IStream)comStream;
        byte[] buffer = new byte[1024 * 1024];
        IntPtr read = Marshal.AllocHGlobal(sizeof(int));
        long total = 0;
        try
        {
            using (FileStream output = new FileStream(path, FileMode.Create, FileAccess.Write))
            {
                while (true)
                {
                    Marshal.WriteInt32(read, 0);
                    stream.Read(buffer, buffer.Length, read);
                    int count = Marshal.ReadInt32(read);
                    if (count <= 0)
                        break;
                    output.Write(buffer, 0, count);
                    total += count;
                }
            }
        }
        finally
        {
            Marshal.FreeHGlobal(read);
        }
        return total;
    }
}
'@

function Write-Bytes([string]$Root, [string]$RelPath, [byte[]]$Data) {
    $path = Join-Path $Root $RelPath
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -Path $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
    [System.IO.File]::WriteAllBytes($path, $Data)
}

function Set-Time([string]$Root, [string]$RelPath, [string]$Time) {
    $item = Get-Item -Path (Join-Path $Root $RelPath)
    $item.LastWriteTimeUtc = [datetime]::Parse($Time, [cultureinfo]::InvariantCulture, [System.Globalization.DateTimeStyles]'AssumeUniversal, AdjustToUniversal')
}

function Build-Common([string]$Root) {
    # the script is ASCII, the other characters are given by their codes
    $accents = -join ([char[]](0xE1, 0x72, 0x76, 0xED, 0x7A, 0x74, 0x171, 0x72, 0x151))
    $kanji = -join ([char[]](0x65E5, 0x672C, 0x8A9E))

    Write-Bytes $Root 'README.TXT' ([System.Text.Encoding]::ASCII.GetBytes("adiskreader optical test image`r`n"))
    Write-Bytes $Root 'EMPTY.TXT' (New-Object byte[] 0)
    Write-Bytes $Root 'TEXT.TXT' ([ADRIsoTools]::TextData(300000, 1))
    Write-Bytes $Root 'RANDOM.BIN' ([ADRIsoTools]::RandomData(100000, 2))
    Write-Bytes $Root 'ONEBLOCK.BIN' ([ADRIsoTools]::RandomData(2048, 3))
    Write-Bytes $Root 'lower case name.txt' ([ADRIsoTools]::TextData(5000, 4))
    Write-Bytes $Root 'a name with spaces and (some) other + characters.txt' ([ADRIsoTools]::TextData(3000, 5))
    Write-Bytes $Root ($accents + '.txt') ([ADRIsoTools]::TextData(1000, 6))
    Write-Bytes $Root ($kanji + '.txt') ([ADRIsoTools]::TextData(1000, 7))
    Write-Bytes $Root 'DOCS\NOTES.TXT' ([ADRIsoTools]::TextData(20000, 8))
    Write-Bytes $Root ('DOCS\' + $accents + '\' + $kanji + '.bin') ([ADRIsoTools]::RandomData(5000, 9))
    Write-Bytes $Root 'L1\L2\L3\L4\L5\L6\DEEP.TXT' ([ADRIsoTools]::TextData(2000, 10))
    Write-Bytes $Root 'HIDDEN.DAT' ([ADRIsoTools]::RandomData(3000, 11))
    Write-Bytes $Root 'OLD.TXT' ([ADRIsoTools]::TextData(100, 12))
    New-Item -ItemType Directory -Path (Join-Path $Root 'EMPTYDIR') | Out-Null
    # a directory which does not fit in one sector
    for ($i = 0; $i -lt 300; $i++) {
        Write-Bytes $Root ('MANY\file number {0:D3}.txt' -f $i) ([ADRIsoTools]::TextData(10 + $i * 7, 100 + $i))
    }
    (Get-Item -Path (Join-Path $Root 'HIDDEN.DAT')).Attributes = 'Hidden'
    Set-Time $Root 'OLD.TXT' '2001-02-03 04:05:06'
    Set-Time $Root 'README.TXT' '2020-07-08 09:10:11'
    Set-Time $Root 'DOCS\NOTES.TXT' '2022-12-31 23:59:58'
}

function Build-Extra([string]$Root) {
    $kanji = -join ([char[]](0x65E5, 0x672C, 0x8A9E))
    Write-Bytes $Root (('n' * 180) + '.txt') ([ADRIsoTools]::TextData(1500, 20))
    Write-Bytes $Root (($kanji * 40) + '.txt') ([ADRIsoTools]::TextData(1500, 21))
    Write-Bytes $Root 'D1\D2\D3\D4\D5\D6\D7\D8\D9\D10\D11\D12\very deep.txt' ([ADRIsoTools]::TextData(2500, 22))
    Write-Bytes $Root 'EXTRA.BIN' ([ADRIsoTools]::RandomData(70000, 23))
}

function Get-Tree([string]$Root) {
    # what a reader must find in the images made from the directory
    $dirs = @()
    $files = [ordered]@{}
    foreach ($item in (Get-ChildItem -Path $Root -Recurse -Force | Sort-Object FullName)) {
        $relpath = $item.FullName.Substring($Root.Length + 1)
        if ($item.PSIsContainer) {
            $dirs += $relpath
            continue
        }
        $files[$relpath] = [ordered]@{
            size       = [long]$item.Length
            sha256     = (Get-FileHash -Path $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            attributes = [int]$item.Attributes
            mtime      = [long]$item.LastWriteTimeUtc.ToFileTimeUtc()
        }
    }
    return [ordered]@{ dirs = @($dirs); files = $files }
}

function New-Image([string]$Path, [int]$FileSystems, [int]$UdfRevision, [string[]]$Roots) {
    $image = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
    try {
        $image.FileSystemsToCreate = $FileSystems
        if ($FileSystems -band $FS_UDF) { $image.UDFRevision = $UdfRevision }
        $image.VolumeName = $VOLUME_NAME
        foreach ($root in $Roots) {
            # the contents of the directory goes to the root of the image
            $image.Root.AddTree($root, $false)
        }
        $result = $image.CreateResultImage()
        $size = [ADRIsoTools]::SaveStream($result.ImageStream, $Path)
        return [ordered]@{ block_size = [int]$result.BlockSize; blocks = [long]$result.TotalBlocks; size = [long]$size }
    }
    finally {
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($image)
    }
}

if (Test-Path -Path $OutDir) {
    throw ("{0} already exists, remove it or give another -OutDir" -f $OutDir)
}
$PackDir = Join-Path $OutDir 'pack'
$TreeDir = Join-Path $OutDir 'trees'
New-Item -ItemType Directory -Path $PackDir | Out-Null

$sources = @(
    @{ name = 'common'; root = (Join-Path $TreeDir 'common') },
    @{ name = 'extra'; root = (Join-Path $TreeDir 'extra') }
)
Write-Host 'Creating the directories'
foreach ($source in $sources) {
    New-Item -ItemType Directory -Path $source.root | Out-Null
}
Build-Common $sources[0].root
Build-Extra $sources[1].root

$treeinfo = [ordered]@{}
foreach ($source in $sources) {
    $treeinfo[$source.name] = Get-Tree $source.root
}

$plans = @(
    @{ name = 'iso9660'; filesystems = $FS_ISO9660; revision = 0; trees = @('common') },
    @{ name = 'joliet'; filesystems = ($FS_ISO9660 -bor $FS_JOLIET); revision = 0; trees = @('common') },
    @{ name = 'bridge'; filesystems = ($FS_ISO9660 -bor $FS_JOLIET -bor $FS_UDF); revision = 0x102; trees = @('common') },
    @{ name = 'udf102'; filesystems = $FS_UDF; revision = 0x102; trees = @('common', 'extra') },
    @{ name = 'udf150'; filesystems = $FS_UDF; revision = 0x150; trees = @('common', 'extra') },
    @{ name = 'udf200'; filesystems = $FS_UDF; revision = 0x200; trees = @('common', 'extra') },
    @{ name = 'udf201'; filesystems = $FS_UDF; revision = 0x201; trees = @('common', 'extra') },
    @{ name = 'udf250'; filesystems = $FS_UDF; revision = 0x250; trees = @('common', 'extra') },
    @{ name = 'udf260'; filesystems = $FS_UDF; revision = 0x260; trees = @('common', 'extra') }
)

$images = @()
$skipped = @()
foreach ($plan in $plans) {
    $file = 'iso_{0}.iso' -f $plan.name
    $path = Join-Path $PackDir $file
    Write-Host ("Creating {0}" -f $file)
    $roots = @($sources | Where-Object { $plan.trees -contains $_.name } | ForEach-Object { $_.root })
    try {
        $info = New-Image $path $plan.filesystems $plan.revision $roots
    }
    catch {
        Write-Warning ("{0} was not created: {1}" -f $file, $_)
        Remove-Item -Path $path -Force -ErrorAction SilentlyContinue
        $skipped += [ordered]@{ name = $plan.name; error = [string]$_ }
        continue
    }
    $images += [ordered]@{
        name         = $plan.name
        file         = $file
        iso9660      = [bool]($plan.filesystems -band $FS_ISO9660)
        joliet       = [bool]($plan.filesystems -band $FS_JOLIET)
        udf          = [bool]($plan.filesystems -band $FS_UDF)
        udf_revision = [int]$plan.revision
        volume_name  = $VOLUME_NAME
        trees        = @($plan.trees)
        block_size   = $info.block_size
        size         = $info.size
        sha256       = (Get-FileHash -Path $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
if ($images.Count -eq 0) {
    throw 'No image was created, is IMAPI2 available on this machine?'
}

$os = Get-CimInstance Win32_OperatingSystem
$manifest = [ordered]@{
    created_on = ('{0} {1}' -f $os.Caption, $os.Version)
    images     = @($images)
    skipped    = @($skipped)
    trees      = $treeinfo
}
# the names of the files are not ASCII
$json = $manifest | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText((Join-Path $PackDir 'manifest.json'), $json, (New-Object System.Text.UTF8Encoding $false))

$zip = Join-Path $OutDir 'adiskreader_iso_fixtures.zip'
Compress-Archive -Path (Join-Path $PackDir '*') -DestinationPath $zip
Write-Host ("Done: {0} ({1:N1} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
