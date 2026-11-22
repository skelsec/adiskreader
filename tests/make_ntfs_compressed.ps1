#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
Creates the NTFS test image of adiskreader with files compressed by Windows. Needs Windows 10 / Server 2016 or newer
and an elevated PowerShell, the Hyper-V role is NOT needed (diskpart and Mount-DiskImage are used).

    powershell -ExecutionPolicy Bypass -File make_ntfs_compressed.ps1 [-OutDir C:\adiskreader_ntfs_fixtures] [-WithEfs]

Two dynamic disks are created with an NTFS partition on each: a VHDX with 4096 byte clusters and a VHD with 512 byte
clusters, the same files are written to both:

    lznt1\      files compressed by NTFS (LZNT1): written to a compressed directory, compressed after they were written,
                partly overwritten, sparse and compressed, with named data streams, long enough to need an attribute list
    plain\      files without compression: sparse files, named data streams, files which are longer than their valid data
                (the clusters after the valid data hold the 0xCC bytes of a deleted file, a reader must return zeroes)
    wof\        files compressed with "compact /exe" (XPRESS4K, XPRESS8K, XPRESS16K, LZX), skipped if compact refuses it
    efs\        an encrypted file, only with -WithEfs: this makes an EFS certificate for the user running the script,
                the only thing touched outside of OutDir

The data of the files is generated (no files of the system are copied). After a disk is detached it is attached again
read-only and every file and stream is read back through Windows and compared with the data written.

Everything is collected in <OutDir>\adiskreader_ntfs_fixtures.zip: the disks and manifest.json, which describes the
volumes and the files (size, sha256, attributes, size on the disk, valid data length, named data streams).
#>
param(
    [string]$OutDir = 'C:\adiskreader_ntfs_fixtures',
    [switch]$WithEfs
)

$ErrorActionPreference = 'Stop'

$DISK_SIZE_MB   = 128
$PARTITION_SIZE = 100MB
$JUNK_SIZE      = 24MB
$VOLUMES = @(
    @{ label = 'ADRNTFS4K';  cluster_size = 4096; image = 'ntfs_windows_4k.vhdx' },
    # a VHDX has 4096 byte physical sectors, Windows does not format it with smaller clusters
    @{ label = 'ADRNTFS512'; cluster_size = 512;  image = 'ntfs_windows_512.vhd' }
)
$WOF_FORMATS = @('XPRESS4K', 'XPRESS8K', 'XPRESS16K', 'LZX')

Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class ADRNtfsTools
{
    const uint GENERIC_READ = 0x80000000;
    const uint GENERIC_WRITE = 0x40000000;
    const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
    const uint FSCTL_SET_COMPRESSION = 0x0009C040;
    const uint FSCTL_SET_SPARSE = 0x000900C4;
    const uint FSCTL_SET_ZERO_DATA = 0x000980C8;

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool DeviceIoControl(SafeFileHandle handle, uint code, byte[] input, int inputSize, IntPtr output, int outputSize, out int returned, IntPtr overlapped);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern uint GetCompressedFileSize(string name, out uint high);

    // FileStream does not take the names of the named data streams (file:stream), the handles of CreateFile do
    static SafeFileHandle OpenHandle(string path, uint access, uint disposition)
    {
        SafeFileHandle handle = CreateFile(path, access, 3, IntPtr.Zero, disposition, FILE_FLAG_BACKUP_SEMANTICS, IntPtr.Zero);
        if (handle.IsInvalid)
            throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "Could not open " + path);
        return handle;
    }

    static void Control(string path, uint code, byte[] input)
    {
        int returned;
        using (SafeFileHandle handle = OpenHandle(path, GENERIC_READ | GENERIC_WRITE, 3))
        {
            if (!DeviceIoControl(handle, code, input, input.Length, IntPtr.Zero, 0, out returned, IntPtr.Zero))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "DeviceIoControl " + code.ToString("X") + " failed on " + path);
        }
    }

    // works on files, named data streams and directories (the files made in a compressed directory are compressed)
    public static void SetCompression(string path, bool compressed)
    {
        // COMPRESSION_FORMAT_DEFAULT or COMPRESSION_FORMAT_NONE
        Control(path, FSCTL_SET_COMPRESSION, BitConverter.GetBytes((ushort)(compressed ? 1 : 0)));
    }

    public static void SetSparse(string path)
    {
        Control(path, FSCTL_SET_SPARSE, new byte[] { 1 });
    }

    // makes a hole of the range in a sparse file
    public static void ZeroRange(string path, long offset, long length)
    {
        byte[] input = new byte[16];
        BitConverter.GetBytes(offset).CopyTo(input, 0);
        BitConverter.GetBytes(offset + length).CopyTo(input, 8);
        Control(path, FSCTL_SET_ZERO_DATA, input);
    }

    // creates the file (or stream) if it does not exist
    public static void WriteAt(string path, long offset, byte[] data)
    {
        using (SafeFileHandle handle = OpenHandle(path, GENERIC_WRITE, 4))
        using (FileStream f = new FileStream(handle, FileAccess.Write, 64 * 1024))
        {
            f.Seek(offset, SeekOrigin.Begin);
            f.Write(data, 0, data.Length);
            f.Flush(true);
        }
    }

    // the data after the end of the valid data is not written, the valid data length of the file stays where it is
    public static void SetLength(string path, long length)
    {
        using (SafeFileHandle handle = OpenHandle(path, GENERIC_WRITE, 4))
        using (FileStream f = new FileStream(handle, FileAccess.Write, 64 * 1024))
        {
            f.SetLength(length);
            f.Flush(true);
        }
    }

    public static long GetLength(string path)
    {
        using (SafeFileHandle handle = OpenHandle(path, GENERIC_READ, 3))
        using (FileStream f = new FileStream(handle, FileAccess.Read, 64 * 1024))
            return f.Length;
    }

    public static string Sha256File(string path)
    {
        using (SHA256 sha = SHA256.Create())
        using (SafeFileHandle handle = OpenHandle(path, GENERIC_READ, 3))
        using (FileStream f = new FileStream(handle, FileAccess.Read, 1024 * 1024))
            return BitConverter.ToString(sha.ComputeHash(f)).Replace("-", "").ToLowerInvariant();
    }

    public static string Sha256(byte[] data)
    {
        using (SHA256 sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(data)).Replace("-", "").ToLowerInvariant();
    }

    // bytes the file (or stream) takes on the disk
    public static long StoredSize(string path)
    {
        uint high;
        uint low = GetCompressedFileSize(path, out high);
        if (low == 0xFFFFFFFF)
        {
            int error = Marshal.GetLastWin32Error();
            if (error != 0)
                throw new System.ComponentModel.Win32Exception(error, "GetCompressedFileSize failed on " + path);
        }
        return ((long)high << 32) | low;
    }

    public static byte[] RandomData(int size, int seed)
    {
        byte[] data = new byte[size];
        new Random(seed).NextBytes(data);
        return data;
    }

    public static byte[] Fill(int size, byte value)
    {
        byte[] data = new byte[size];
        for (int i = 0; i < size; i++)
            data[i] = value;
        return data;
    }

    // lines of text, compresses well
    public static byte[] TextData(int size, int seed)
    {
        string[] words = { "cluster", "runlist", "attribute", "record", "index", "bitmap", "volume", "stream", "sparse", "journal", "sector", "offset" };
        Random rng = new Random(seed);
        StringBuilder sb = new StringBuilder(size + 256);
        int line = 0;
        while (sb.Length < size)
        {
            sb.Append(line++).Append(':');
            int count = rng.Next(3, 12);
            for (int i = 0; i < count; i++)
                sb.Append(' ').Append(words[rng.Next(words.Length)]);
            sb.Append(' ').Append(rng.Next(1000000)).Append("\r\n");
        }
        byte[] data = new byte[size];
        Encoding.ASCII.GetBytes(sb.ToString(), 0, size, data, 0);
        return data;
    }

    // random bytes of 4 values, compresses to less than its half with matches all over the chunk
    public static byte[] LowEntropyData(int size, int seed)
    {
        byte[] letters = { 0x41, 0x43, 0x47, 0x54 };
        Random rng = new Random(seed);
        byte[] data = new byte[size];
        for (int i = 0; i < size; i++)
            data[i] = letters[rng.Next(4)];
        return data;
    }

    // parts of text and of random bytes, the parts do not end at the end of the compression units
    public static byte[] MixedData(int size, int seed)
    {
        Random rng = new Random(seed);
        byte[] data = new byte[size];
        int pos = 0;
        bool text = true;
        while (pos < size)
        {
            int part = Math.Min(rng.Next(3000, 90000), size - pos);
            byte[] src = text ? TextData(part, rng.Next()) : RandomData(part, rng.Next());
            Buffer.BlockCopy(src, 0, data, pos, part);
            pos += part;
            text = !text;
        }
        return data;
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
        # the partitions get their drive letters here, they may not be assigned later on a read-only disk
        Mount-DiskImage -ImagePath $Path -Access ReadOnly | Out-Null
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

function Get-VolumeRoot([int]$Number, [int]$PartitionNumber) {
    # returns the root (X:\) of the partition, assigns a drive letter if needed
    $partition = Get-Partition -DiskNumber $Number -PartitionNumber $PartitionNumber
    $letter = ("{0}" -f $partition.DriveLetter).Trim([char]0, ' ')
    if ($letter.Length -eq 0) {
        $partition | Add-PartitionAccessPath -AssignDriveLetter | Out-Null
        for ($i = 0; $i -lt 20 -and $letter.Length -eq 0; $i++) {
            Start-Sleep -Milliseconds 500
            $partition = Get-Partition -DiskNumber $Number -PartitionNumber $PartitionNumber
            $letter = ("{0}" -f $partition.DriveLetter).Trim([char]0, ' ')
        }
    }
    if ($letter.Length -eq 0) { throw "No drive letter for partition $PartitionNumber of disk $Number" }
    $root = $letter + ':\'
    for ($i = 0; $i -lt 20 -and -not (Test-Path $root); $i++) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Path $root)) { throw "$root is not available" }
    return $root
}

# path -> description of the file, the same for all of the volumes (filled while the first one is written)
$script:Files = [ordered]@{}
$script:Dirs = New-Object System.Collections.ArrayList
$script:Skipped = New-Object System.Collections.ArrayList
$script:Root = $null
$script:First = $true

function Add-Dir([string]$RelPath, [switch]$Compressed) {
    $path = Join-Path $script:Root $RelPath
    New-Item -ItemType Directory -Path $path | Out-Null
    if ($Compressed) { [ADRNtfsTools]::SetCompression($path, $true) }
    if ($script:First) { [void]$script:Dirs.Add($RelPath) }
}

function Get-Expected([long]$Size, [object[]]$Writes) {
    # the data of a file of Size bytes after the (offset, data) writes, the rest of it is zeroes
    $data = New-Object byte[] $Size
    foreach ($write in $Writes) {
        $part = [byte[]]$write[1]
        $count = [Math]::Min([long]$part.Length, $Size - [long]$write[0])
        if ($count -gt 0) { [Buffer]::BlockCopy($part, 0, $data, [long]$write[0], $count) }
    }
    return ,$data
}

function Add-File {
    # Writes: (offset, data) pairs. Size: the length of the file when it is longer than the writes.
    # Kind: what the file is a test of, the tests select the files by it. Stream: name of the named data stream.
    param(
        [string]$RelPath,
        [object[]]$Writes,
        [string]$Kind,
        [long]$Size = -1,
        [string]$Stream = '',
        [switch]$Sparse,
        [switch]$CompressAfter,
        [object[]]$Holes = @(),
        [object[]]$Overwrites = @(),
        [switch]$UncompressAfter
    )
    $path = Join-Path $script:Root $RelPath
    if ($Stream.Length -gt 0) { $path = $path + ':' + $Stream }
    $end = 0
    foreach ($write in $Writes) { $end = [Math]::Max($end, [long]$write[0] + ([byte[]]$write[1]).Length) }
    $valid = $end
    if ($Size -lt 0) { $Size = $end }

    if ($Sparse) {
        [ADRNtfsTools]::WriteAt($path, 0, (New-Object byte[] 0))
        [ADRNtfsTools]::SetSparse($path)
    }
    if ($Writes.Count -eq 0) { [ADRNtfsTools]::WriteAt($path, 0, (New-Object byte[] 0)) }
    foreach ($write in $Writes) { [ADRNtfsTools]::WriteAt($path, [long]$write[0], [byte[]]$write[1]) }
    if ($Size -ne $end) { [ADRNtfsTools]::SetLength($path, $Size) }
    $all = @($Writes)
    foreach ($hole in $Holes) {
        [ADRNtfsTools]::ZeroRange($path, [long]$hole[0], [long]$hole[1])
        $all += ,@([long]$hole[0], (New-Object byte[] ([long]$hole[1])))
    }
    if ($CompressAfter) { [ADRNtfsTools]::SetCompression($path, $true) }
    foreach ($write in $Overwrites) {
        [ADRNtfsTools]::WriteAt($path, [long]$write[0], [byte[]]$write[1])
        $all += ,$write
    }
    if ($UncompressAfter) { [ADRNtfsTools]::SetCompression($path, $false) }

    if (-not $script:First) { return }
    $expected = Get-Expected $Size $all
    $entry = [ordered]@{
        size   = $Size
        sha256 = [ADRNtfsTools]::Sha256($expected)
        kind   = $Kind
    }
    if ($Stream.Length -gt 0) {
        $file = $script:Files[$RelPath]
        if ($null -eq $file) { throw "The file of the stream ${RelPath}:$Stream must be added first" }
        $file.streams[$Stream] = $entry
    }
    else {
        # the length the file was written up to, Windows gives zeroes after it
        if ($Size -gt $valid) { $entry['valid_data_length'] = $valid }
        $entry['streams'] = [ordered]@{}
        $script:Files[$RelPath] = $entry
    }
}

function Write-Volume {
    $T = [ADRNtfsTools]
    $unit = 65536

    # the clusters of this file are free again when the files after it are written: what is read from the disk
    # after the valid data of a file is likely not zeroes
    $junk = Join-Path $script:Root 'junk.bin'
    for ($offset = 0; $offset -lt $JUNK_SIZE; $offset += 4MB) { $T::WriteAt($junk, $offset, $T::Fill(4MB, 0xCC)) }
    Remove-Item -Path $junk -Force

    Add-Dir 'lznt1' -Compressed
    Add-Dir 'lznt1\sub'
    Add-Dir 'plain'

    # written to a compressed directory
    Add-File 'lznt1\text.txt'          @(,@(0, $T::TextData(300000, 1)))        'lznt1'
    Add-File 'lznt1\random.bin'        @(,@(0, $T::RandomData(100000, 2)))      'lznt1_stored'
    Add-File 'lznt1\mixed.bin'         @(,@(0, $T::MixedData(700000, 3)))       'lznt1'
    Add-File 'lznt1\low_entropy.bin'   @(,@(0, $T::LowEntropyData(200000, 4)))  'lznt1'
    Add-File 'lznt1\zeros.bin'         @(,@(0, (New-Object byte[] 200000)))     'lznt1'
    Add-File 'lznt1\empty.bin'         @()                                      'resident'
    Add-File 'lznt1\small.txt'         @(,@(0, $T::TextData(100, 5)))           'resident'
    Add-File 'lznt1\short.txt'         @(,@(0, $T::TextData(3000, 6)))          'lznt1'
    Add-File 'lznt1\one_chunk.txt'     @(,@(0, $T::TextData(4096, 7)))          'lznt1'
    Add-File 'lznt1\unit_512.txt'      @(,@(0, $T::TextData(8192, 8)))          'lznt1'
    Add-File 'lznt1\unit_4k.txt'       @(,@(0, $T::TextData($unit, 9)))         'lznt1'
    Add-File 'lznt1\unit_and_byte.txt' @(,@(0, $T::TextData(2*$unit + 1, 10)))  'lznt1'
    Add-File 'lznt1\unit_less_byte.txt' @(,@(0, $T::TextData(2*$unit - 1, 11))) 'lznt1'
    Add-File 'lznt1\long_matches.bin'  @(,@(0, [byte[]]($T::Fill(70000, 0xAA) + $T::TextData(10, 12) * 3000 + $T::Fill(5000, 0)))) 'lznt1'
    Add-File 'lznt1\sub\nested.txt'    @(,@(0, $T::TextData(90000, 13)))        'lznt1'
    # every unit of it has a run of clusters and a sparse run: the runs do not fit in the file record
    Add-File 'lznt1\attribute_list.txt' @(,@(0, $T::TextData(8MB, 14)))         'lznt1_attribute_list'
    # written in pieces, with holes left between them
    Add-File 'lznt1\holes.bin'         @(@(0, $T::TextData(20000, 15)), @(300000, $T::RandomData(10000, 16)), @(1000000, $T::TextData(70000, 17))) 'lznt1'
    # longer than what was written to it
    Add-File 'lznt1\extended.txt'      @(,@(0, $T::TextData(100000, 18)))       'lznt1_valid_data' -Size 500000
    Add-File 'lznt1\extended_only.bin' @()                                      'lznt1_valid_data' -Size 300000
    # compressed data overwritten with data which can not be compressed, and the other way
    Add-File 'lznt1\overwritten.bin'   @(,@(0, $T::TextData(400000, 19)))       'lznt1' -Overwrites @(@(70000, $T::RandomData(100000, 20)), @(300001, $T::Fill(50000, 0x55)))
    Add-File 'lznt1\sparse.bin'        @(@(0, $T::TextData(150000, 21)), @(2000000, $T::TextData(150000, 22))) 'lznt1_sparse' -Sparse -Holes @(,@(65536, 65536))
    # named data streams are compressed with the file
    Add-File 'lznt1\streams.txt'       @(,@(0, $T::TextData(120000, 23)))       'lznt1_streams'
    Add-File 'lznt1\streams.txt'       @(,@(0, $T::TextData(200000, 24)))       'lznt1_streams' -Stream 'compressed'
    Add-File 'lznt1\streams.txt'       @(,@(0, $T::RandomData(70000, 25)))      'lznt1_streams' -Stream 'random'
    Add-File 'lznt1\streams.txt'       @(,@(0, $T::TextData(50, 26)))           'lznt1_streams' -Stream 'small'
    # compressed when it had its data already
    Add-File 'plain\compressed_later.txt' @(,@(0, $T::TextData(250000, 27)))    'lznt1' -CompressAfter
    # it was compressed, it is not compressed any more
    Add-File 'lznt1\uncompressed_later.txt' @(,@(0, $T::TextData(250000, 28)))  'plain' -UncompressAfter

    Add-File 'plain\text.txt'          @(,@(0, $T::TextData(300000, 1)))        'plain'
    Add-File 'plain\extended.bin'      @(,@(0, $T::RandomData(100000, 29)))     'valid_data' -Size 1500000
    Add-File 'plain\extended_only.bin' @()                                      'valid_data' -Size 1000000
    Add-File 'plain\sparse.bin'        @(@(0, $T::RandomData(150000, 30)), @(3000000, $T::RandomData(150000, 31))) 'sparse' -Sparse -Holes @(,@(65536, 65536))
    Add-File 'plain\streams.txt'       @(,@(0, $T::TextData(20000, 32)))        'streams'
    Add-File 'plain\streams.txt'       @(,@(0, $T::RandomData(150000, 33)))     'streams' -Stream 'large'
    Add-File 'plain\streams.txt'       @(,@(0, $T::TextData(60, 34)))           'streams' -Stream 'small'
    # only one of the streams is compressed
    Add-File 'plain\streams.txt'       @(,@(0, $T::TextData(150000, 35)))       'streams' -Stream 'compressed' -CompressAfter

    # compressed by the WOF driver: the data is in the WofCompressedData stream, the file has a reparse point
    Add-Dir 'wof'
    $seed = 40
    foreach ($format in $WOF_FORMATS) {
        foreach ($item in @(@('text', 500000), @('mixed', 300000), @('small', 2000))) {
            $name = 'wof\{0}_{1}.bin' -f $format.ToLower(), $item[0]
            if ($script:Skipped -contains $name) { continue }
            $data = $(if ($item[0] -eq 'mixed') { $T::MixedData($item[1], $seed) } else { $T::TextData($item[1], $seed) })
            $seed++
            Add-File $name @(,@(0, $data)) ('wof_' + $format.ToLower())
            $output = & compact.exe /c /q ('/exe:' + $format) (Join-Path $script:Root $name) 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Warning ("compact /exe:{0} failed on {1}, the file is left out: {2}" -f $format, $name, ($output -join ' '))
                Remove-Item -Path (Join-Path $script:Root $name) -Force
                $script:Files.Remove($name)
                [void]$script:Skipped.Add($name)
            }
        }
    }

    if ($WithEfs) {
        Add-Dir 'efs'
        Add-File 'efs\encrypted.txt' @(,@(0, $T::TextData(100000, 60))) 'efs'
        $output = & cipher.exe /e (Join-Path $script:Root 'efs\encrypted.txt') 2>&1
        if ($LASTEXITCODE -ne 0) { throw ("cipher /e failed: {0}" -f ($output -join ' ')) }
    }
}

function Test-Volume([string]$Root) {
    # what Windows reads from the finished volume, returns attributes and sizes on the disk by path
    $info = [ordered]@{}
    foreach ($relpath in $script:Files.Keys) {
        $file = $script:Files[$relpath]
        $path = Join-Path $Root $relpath
        $targets = @(,@($path, $file))
        foreach ($stream in $file.streams.Keys) { $targets += ,@(($path + ':' + $stream), $file.streams[$stream]) }
        $stored = [ordered]@{}
        foreach ($target in $targets) {
            $size = [ADRNtfsTools]::GetLength($target[0])
            if ($size -ne $target[1].size) { throw ("{0} is {1} bytes, expected {2}" -f $target[0], $size, $target[1].size) }
            # the encrypted file is read (decrypted) by the user who made it
            $sha256 = [ADRNtfsTools]::Sha256File($target[0])
            if ($sha256 -ne $target[1].sha256) { throw ("Windows reads other data from {0} than what was written" -f $target[0]) }
            $name = $target[0].Substring($path.Length).TrimStart(':')
            if ($name.Length -eq 0) { $name = '$DATA' }
            $stored[$name] = [ADRNtfsTools]::StoredSize($target[0])
        }
        $attributes = (Get-Item -LiteralPath $path -Force).Attributes.ToString()
        if ($file.kind.StartsWith('lznt1') -and $attributes -notmatch 'Compressed') { throw "$path is not compressed" }
        if ($file.kind -eq 'plain' -and $attributes -match 'Compressed') { throw "$path is compressed" }
        $info[$relpath] = [ordered]@{
            attributes  = $attributes
            # bytes taken on the disk by the unnamed stream ($DATA) and by the named ones
            stored_size = $stored
        }
    }
    return $info
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
$made = @()
try {
    foreach ($volume in $VOLUMES) {
        Write-Host ("Writing the files of {0}" -f $volume.label)
        $image = Join-Path $PackDir $volume.image
        Invoke-DiskPart @(('create vdisk file="{0}" maximum={1} type=expandable' -f $image, $DISK_SIZE_MB))
        $number = Mount-Image $image
        Initialize-Disk -Number $number -PartitionStyle MBR
        $partition = New-Partition -DiskNumber $number -Size $PARTITION_SIZE -AssignDriveLetter
        Format-Volume -DriveLetter $partition.DriveLetter -FileSystem NTFS -AllocationUnitSize $volume.cluster_size -NewFileSystemLabel $volume.label -Confirm:$false -ErrorAction Stop | Out-Null
        $script:Root = Get-VolumeRoot $number $partition.PartitionNumber
        Write-Volume
        $script:First = $false
        Dismount-Image $image | Out-Null

        Write-Host ("Reading the files of {0} back" -f $volume.label)
        $number = Mount-Image $image -ReadOnly
        $files = Test-Volume (Get-VolumeRoot $number $partition.PartitionNumber)
        Dismount-Image $image | Out-Null
        $made += [ordered]@{
            image        = $volume.image
            label        = $volume.label
            cluster_size = $volume.cluster_size
            partition    = $partition.PartitionNumber
            offset       = $partition.Offset
            size         = $partition.Size
            files        = $files
        }
    }
}
finally {
    foreach ($path in @($script:Mounted)) {
        Dismount-DiskImage -ImagePath $path -ErrorAction SilentlyContinue | Out-Null
    }
}

$os = Get-CimInstance Win32_OperatingSystem
$manifest = [ordered]@{
    created_on = ('{0} {1}' -f $os.Caption, $os.Version)
    # files which could not be made on this system
    skipped    = @($script:Skipped)
    volumes    = $made
    dirs       = @($script:Dirs)
    files      = $script:Files
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $PackDir 'manifest.json') -Encoding ASCII

$zip = Join-Path $OutDir 'adiskreader_ntfs_fixtures.zip'
Compress-Archive -Path (Join-Path $PackDir '*') -DestinationPath $zip
Write-Host ("Done: {0} ({1:N1} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
