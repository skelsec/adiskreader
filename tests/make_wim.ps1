#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
Creates the WIM test files of adiskreader with DISM. Needs Windows 10 / Server 2016 or newer and an elevated PowerShell.

    powershell -ExecutionPolicy Bypass -File make_wim.ps1 [-OutDir C:\adiskreader_wim_fixtures]

Two directory trees are created (generated data only, no files of the system are copied) and captured to:

    wim_none.wim     /Compress:none, images: first, second
    wim_fast.wim     /Compress:fast (XPRESS), images: first, second
    wim_max.wim      /Compress:max (LZX), images: first, second
    wim_split.swm    wim_max.wim split to parts (wim_split2.swm ...)
    wim_recovery.esd first image of wim_max.wim exported with /Compress:recovery (LZMS, solid), skipped if DISM refuses it

Everything is collected in <OutDir>\adiskreader_wim_fixtures.zip, manifest.json in it describes the files
and the contents of the trees (size, sha256, attributes, last write time, named data streams).
Nothing outside of OutDir is touched.
#>
param(
    [string]$OutDir = 'C:\adiskreader_wim_fixtures'
)

$ErrorActionPreference = 'Stop'

$SPLIT_SIZE_MB = 1

Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;

public static class ADRWimTools
{
    public static string Sha256(byte[] data)
    {
        using (SHA256 sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(data)).Replace("-", "").ToLowerInvariant();
    }

    public static byte[] RandomData(int size, int seed)
    {
        byte[] data = new byte[size];
        new Random(seed).NextBytes(data);
        return data;
    }

    // lines of text, compresses well
    public static byte[] TextData(int size, int seed)
    {
        string[] words = { "disk", "image", "partition", "cluster", "sector", "resource", "stream", "chunk", "header", "table", "offset", "lookup" };
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

    // looks like x86 code for the compressors: a few byte values with many call (E8) instructions with all kinds of targets
    public static byte[] CodeData(int size, int seed)
    {
        Random rng = new Random(seed);
        byte[] data = new byte[size];
        byte[] alphabet = { 0x8B, 0x45, 0x89, 0x55, 0x48, 0x83, 0xEC, 0xC3, 0x00, 0xFF, 0x0F, 0x85, 0x74, 0x75, 0x90, 0xCC };
        int pos = 0;
        while (pos < size)
        {
            if (rng.Next(6) == 0 && pos + 5 <= size)
            {
                int target;
                switch (rng.Next(5))
                {
                    case 0: target = rng.Next(-pos - 16, 16); break;
                    case 1: target = rng.Next(0, 12000000 + 16); break;
                    case 2: target = 12000000 - pos + rng.Next(-4, 4); break;
                    case 3: target = rng.Next(); break;
                    default: target = rng.Next(-70000, 70000); break;
                }
                data[pos++] = 0xE8;
                BitConverter.GetBytes(target).CopyTo(data, pos);
                pos += 4;
            }
            else
            {
                data[pos++] = alphabet[rng.Next(alphabet.Length)];
            }
        }
        return data;
    }

    // every 4096 byte block is filled with (seed + index of the block) & 0xFF
    public static byte[] PatternData(int size, int seed)
    {
        byte[] data = new byte[size];
        for (int i = 0; i < size; i++)
            data[i] = (byte)((seed + i / 4096) & 0xFF);
        return data;
    }
}
'@

function Write-Bytes([string]$Root, [string]$RelPath, [byte[]]$Data) {
    $path = Join-Path $Root $RelPath
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -Path $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
    [System.IO.File]::WriteAllBytes($path, $Data)
}

function Write-Stream([string]$Root, [string]$RelPath, [string]$Stream, [byte[]]$Data) {
    Set-Content -Path (Join-Path $Root $RelPath) -Stream $Stream -Value $Data -Encoding Byte
    # the tree of the manifest is made from the directory, the streams are added from here
    $script:Streams[$Root + '|' + $RelPath] += @(,@($Stream, $Data.Length, [ADRWimTools]::Sha256($Data)))
}

function Build-First([string]$Root) {
    $unicodename = 'unicode_' + [char]0x00E1 + [char]0x0151 + [char]0x4E2D + [char]0x6587 + '.txt'

    Write-Bytes $Root 'hello.txt' ([System.Text.Encoding]::ASCII.GetBytes("hello from the first image`r`n"))
    Write-Bytes $Root 'empty.txt' (New-Object byte[] 0)
    Write-Bytes $Root $unicodename ([System.Text.Encoding]::UTF8.GetBytes('unicode'))
    Write-Bytes $Root 'text.txt' ([ADRWimTools]::TextData(1500000, 1))
    Write-Bytes $Root 'code.bin' ([ADRWimTools]::CodeData(1000000, 2))
    # can not be compressed, its chunks are stored as they are
    Write-Bytes $Root 'random.bin' ([ADRWimTools]::RandomData(300000, 3))
    # around the size of the chunks
    Write-Bytes $Root 'chunks\chunk_32767.bin' ([ADRWimTools]::TextData(32767, 4))
    Write-Bytes $Root 'chunks\chunk_32768.bin' ([ADRWimTools]::TextData(32768, 5))
    Write-Bytes $Root 'chunks\chunk_32769.bin' ([ADRWimTools]::TextData(32769, 6))
    Write-Bytes $Root 'chunks\chunk_65536.bin' ([ADRWimTools]::CodeData(65536, 7))
    Write-Bytes $Root 'chunks\one.bin' ([byte[]]@(0x41))
    Write-Bytes $Root 'chunks\zeros.bin' (New-Object byte[] 200000)
    Write-Bytes $Root 'dir1\pattern.bin' ([ADRWimTools]::PatternData(2000000, 8))
    # the tail can be compressed, the start can not
    Write-Bytes $Root 'dir1\mixed.bin' ([ADRWimTools]::RandomData(100000, 9) + [ADRWimTools]::TextData(100000, 10))
    Write-Bytes $Root 'dir1\sub\deep\deeper\deep.txt' ([System.Text.Encoding]::ASCII.GetBytes('deep'))
    # same contents, stored once
    Write-Bytes $Root 'dir1\sub\text_copy.txt' ([ADRWimTools]::TextData(1500000, 1))
    New-Item -ItemType Directory -Path (Join-Path $Root 'emptydir') | Out-Null
    for ($i = 0; $i -lt 300; $i++) {
        Write-Bytes $Root ('many\file_{0:d3}.txt' -f $i) ([System.Text.Encoding]::ASCII.GetBytes(('file {0}' -f $i)))
    }

    Write-Bytes $Root 'streams.txt' ([System.Text.Encoding]::ASCII.GetBytes('main data'))
    Write-Stream $Root 'streams.txt' 'Zone.Identifier' ([System.Text.Encoding]::ASCII.GetBytes("[ZoneTransfer]`r`nZoneId=3`r`n"))
    Write-Stream $Root 'streams.txt' 'big' ([ADRWimTools]::TextData(150000, 11))
    Write-Bytes $Root 'onlystream.txt' (New-Object byte[] 0)
    Write-Stream $Root 'onlystream.txt' 'data' ([ADRWimTools]::CodeData(50000, 12))

    Write-Bytes $Root 'readonly.txt' ([System.Text.Encoding]::ASCII.GetBytes('readonly'))
    Write-Bytes $Root 'hidden.txt' ([System.Text.Encoding]::ASCII.GetBytes('hidden'))
    (Get-Item -Path (Join-Path $Root 'readonly.txt')).Attributes = 'ReadOnly'
    (Get-Item -Path (Join-Path $Root 'hidden.txt') -Force).Attributes = 'Hidden'
    (Get-Item -Path (Join-Path $Root 'hello.txt')).LastWriteTimeUtc = [datetime]::new(2020, 2, 29, 12, 34, 56, [System.DateTimeKind]::Utc)
    # after the times are set, the other name of the file gets them only later
    New-Item -ItemType HardLink -Path (Join-Path $Root 'dir1\hardlink.txt') -Target (Join-Path $Root 'hello.txt') | Out-Null
}

function Build-Second([string]$Root) {
    Write-Bytes $Root 'hello.txt' ([System.Text.Encoding]::ASCII.GetBytes("hello from the second image`r`n"))
    # in the first image as well
    Write-Bytes $Root 'text.txt' ([ADRWimTools]::TextData(1500000, 1))
    Write-Bytes $Root 'second\code2.bin' ([ADRWimTools]::CodeData(400000, 20))
    Write-Bytes $Root 'second\text2.txt' ([ADRWimTools]::TextData(400000, 21))
}

function Get-Tree([string]$Root) {
    # what a reader must find in the image captured from the directory
    $dirs = @()
    $files = [ordered]@{}
    foreach ($item in (Get-ChildItem -Path $Root -Recurse -Force | Sort-Object FullName)) {
        $relpath = $item.FullName.Substring($Root.Length + 1)
        if ($item.PSIsContainer) {
            $dirs += $relpath
            continue
        }
        $streams = [ordered]@{}
        foreach ($stream in @($script:Streams[$Root + '|' + $relpath])) {
            if ($null -eq $stream) { continue }
            $streams[$stream[0]] = [ordered]@{ size = [long]$stream[1]; sha256 = $stream[2] }
        }
        $files[$relpath] = [ordered]@{
            size       = [long]$item.Length
            sha256     = (Get-FileHash -Path $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            attributes = [int]$item.Attributes
            mtime      = [long]$item.LastWriteTimeUtc.ToFileTimeUtc()
            streams    = $streams
        }
    }
    return [ordered]@{ dirs = @($dirs); files = $files }
}

function Invoke-Dism([string[]]$Arguments) {
    $output = & dism.exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ("dism failed ({0}): {1}`n{2}" -f $LASTEXITCODE, ($Arguments -join ' '), ($output -join "`n"))
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

$script:Streams = @{}
$TREES = @(
    @{ name = 'first';  description = 'first image of adiskreader';  root = (Join-Path $OutDir 'first') },
    @{ name = 'second'; description = 'second image of adiskreader'; root = (Join-Path $OutDir 'second') }
)

Write-Host 'Creating the directories'
foreach ($tree in $TREES) {
    New-Item -ItemType Directory -Path $tree.root | Out-Null
}
Build-First $TREES[0].root
Build-Second $TREES[1].root

$treeinfo = [ordered]@{}
foreach ($tree in $TREES) {
    $treeinfo[$tree.name] = Get-Tree $tree.root
}

$allimages = @()
for ($i = 0; $i -lt $TREES.Count; $i++) {
    $allimages += [ordered]@{ index = $i + 1; name = $TREES[$i].name; description = $TREES[$i].description; tree = $TREES[$i].name }
}

$wims = @()
foreach ($compression in @('none', 'fast', 'max')) {
    $file = 'wim_{0}.wim' -f $compression
    Write-Host ("Capturing {0}" -f $file)
    $action = '/Capture-Image'
    foreach ($tree in $TREES) {
        $arguments = @($action, ('/ImageFile:{0}' -f (Join-Path $PackDir $file)), ('/CaptureDir:{0}' -f $tree.root),
            ('/Name:{0}' -f $tree.name), ('/Description:{0}' -f $tree.description))
        if ($action -eq '/Capture-Image') { $arguments += ('/Compress:{0}' -f $compression) }
        Invoke-Dism $arguments
        $action = '/Append-Image'
    }
    $wims += [ordered]@{ name = $compression; file = $file; parts = @($file); images = @($allimages) }
}

Write-Host 'Splitting wim_max.wim'
Invoke-Dism @('/Split-Image', ('/ImageFile:{0}' -f (Join-Path $PackDir 'wim_max.wim')),
    ('/SWMFile:{0}' -f (Join-Path $PackDir 'wim_split.swm')), ('/FileSize:{0}' -f $SPLIT_SIZE_MB))
$parts = @(Get-ChildItem -Path $PackDir -Filter 'wim_split*.swm' | Sort-Object { $_.Name.Length }, Name | ForEach-Object { $_.Name })
$wims += [ordered]@{ name = 'split'; file = 'wim_split.swm'; parts = $parts; images = @($allimages) }

Write-Host 'Exporting wim_recovery.esd'
try {
    Invoke-Dism @('/Export-Image', ('/SourceImageFile:{0}' -f (Join-Path $PackDir 'wim_max.wim')), '/SourceIndex:1',
        ('/DestinationImageFile:{0}' -f (Join-Path $PackDir 'wim_recovery.esd')), '/Compress:recovery')
    $wims += [ordered]@{ name = 'recovery'; file = 'wim_recovery.esd'; parts = @('wim_recovery.esd'); images = @($allimages[0]) }
}
catch {
    Write-Warning ("No ESD file was created: {0}" -f $_)
    Remove-Item -Path (Join-Path $PackDir 'wim_recovery.esd') -Force -ErrorAction SilentlyContinue
}

foreach ($wim in $wims) {
    $hashes = [ordered]@{}
    foreach ($part in $wim.parts) {
        $hashes[$part] = (Get-FileHash -Path (Join-Path $PackDir $part) -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $wim['sha256'] = $hashes
}

$os = Get-CimInstance Win32_OperatingSystem
$manifest = [ordered]@{
    created_on = ('{0} {1}' -f $os.Caption, $os.Version)
    wims       = $wims
    trees      = $treeinfo
}
# the names of the files are not ASCII
$json = $manifest | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText((Join-Path $PackDir 'manifest.json'), $json, (New-Object System.Text.UTF8Encoding $false))

$zip = Join-Path $OutDir 'adiskreader_wim_fixtures.zip'
Compress-Archive -Path (Join-Path $PackDir '*') -DestinationPath $zip
Write-Host ("Done: {0} ({1:N1} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
