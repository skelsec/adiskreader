import pytest
import hashlib
import os
import ntpath
import random
from config import *
from checks import *
from vmdk_builder import build_test_disks

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource.file import FileSource
from adiskreader.datasource.gzipfile import GzipFileSource
from adiskreader.disks import Disk
from adiskreader.disks.raw import RAWDisk
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.disks.vmdk import VMDKDisk
from adiskreader.filesystems.ntfs import NTFS
from adiskreader.filesystems.ntfs.structures import file as ntfs_file
from adiskreader.filesystems.ntfs.structures.attributes import AttributeHeaderFlag
from adiskreader.compression import native, DecompressionError
from adiskreader.compression.lznt1 import lznt1_decompress, lznt1_decompress_py, LZNT1_SHIFTS


async def open_raw():
    ds = await FileSource.from_file(get_image(NTFS_TEST_FILE_GZ))
    return await RAWDisk.from_datasource(ds)

async def open_raw_gz():
    ds = await GzipFileSource.from_file(get_image(NTFS_TEST_FILE_GZ, compressed=True))
    return await RAWDisk.from_datasource(ds)

async def open_vhdx_dynamic():
    ds = await FileSource.from_file(get_image(NTFS_TEST_FILE_VHDX_DYNAMIC_GZ))
    return await VHDXDisk.from_datasource(ds)

async def open_vhdx_fixed():
    ds = await FileSource.from_file(get_image(NTFS_TEST_FILE_VHDX_FIXED_GZ))
    return await VHDXDisk.from_datasource(ds)

async def open_vhdx_log():
    ds = await FileSource.from_file(get_image(NTFS_TEST_FILE_VHDX_GZ))
    return await VHDXDisk.from_datasource(ds)

async def open_vmdk(name):
    filename, _, _ = build_test_disks()[name]
    ds = await FileSource.from_file(str(TESTFILES_FOLDER_PATH.joinpath(filename)))
    return await VMDKDisk.from_datasource(ds)

async def open_vmdk_sparse():
    return await open_vmdk('monolithicSparse')

async def open_vmdk_stream():
    return await open_vmdk('streamOptimized')

async def open_vmdk_vmware_ova():
    return await open_vmdk('vmware_ova')

async def open_vmdk_flat():
    return await open_vmdk('flat')

# the vhdx with the log is a different image, but holds all files of the manifest
DISKS = [open_raw, open_raw_gz, open_vhdx_dynamic, open_vhdx_fixed, open_vhdx_log, open_vmdk_sparse, open_vmdk_stream, open_vmdk_vmware_ova, open_vmdk_flat]

async def open_fs(open_disk):
    disk = await open_disk()
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None
    return fs


@pytest.mark.asyncio
@pytest.mark.parametrize('open_disk', DISKS)
async def test_ntfs_init(open_disk):
    await open_fs(open_disk)

@pytest.mark.asyncio
@pytest.mark.parametrize('open_disk', DISKS)
async def test_ntfs_file_list(open_disk):
    fs = await open_fs(open_disk)
    await check_file_list(fs, load_manifest('ntfs'))

@pytest.mark.asyncio
@pytest.mark.parametrize('open_disk', DISKS)
async def test_ntfs_file_open(open_disk):
    fs = await open_fs(open_disk)
    await check_file_open(fs, load_manifest('ntfs'))

@pytest.mark.asyncio
@pytest.mark.parametrize('open_disk', DISKS)
async def test_ntfs_file_stat(open_disk):
    fs = await open_fs(open_disk)
    await check_file_stat(fs, load_manifest('ntfs'))

@pytest.mark.asyncio
@pytest.mark.parametrize('open_disk', DISKS)
async def test_ntfs_file_fullread(open_disk):
    fs = await open_fs(open_disk)
    await check_file_fullread(fs, load_manifest('ntfs'))

@pytest.mark.asyncio
@pytest.mark.parametrize('open_disk', DISKS)
async def test_ntfs_file_ops(open_disk):
    fs = await open_fs(open_disk)
    await check_file_ops(fs, load_manifest('ntfs'))

@pytest.mark.asyncio
@pytest.mark.parametrize('open_disk', DISKS)
async def test_ntfs_walk_once(open_disk):
    fs = await open_fs(open_disk)
    roots = []
    async for root, dirs, files in fs.walk():
        roots.append(root)
    assert len(roots) == len(set(roots))

@pytest.mark.asyncio
@pytest.mark.parametrize('open_disk', DISKS)
async def test_ntfs_ls(open_disk):
    fs = await open_fs(open_disk)
    manifest = load_manifest('ntfs')
    listed = {}
    async for root, dirs, files in fs.walk():
        entries = []
        async for etype, name, entry in fs.ls(root):
            entries.append((etype, name))
            listed[ntpath.join(root, name)] = etype
        expected = [('dir', name) for name in dirs] + [('file', name) for name in files]
        assert sorted(entries) == sorted(expected)

    for dir_path in manifest['dirs']:
        assert listed['\\' + dir_path] == 'dir'
    for file_path in manifest['files']:
        assert listed['\\' + file_path] == 'file'

@pytest.mark.asyncio
async def test_ntfs_ls_errors():
    fs = await open_fs(open_raw)
    with pytest.raises(FileNotFoundError):
        async for _ in fs.ls('\\nonexistent'):
            pass
    with pytest.raises(NotADirectoryError):
        async for _ in fs.ls('\\SAM'):
            pass


# compressed files (LZNT1)

needs_native = pytest.mark.skipif(native is None, reason='adiskreader_native is not installed')
LZNT1_DECOMPRESSORS = [
    pytest.param(lznt1_decompress_py, id='python'),
    pytest.param(lznt1_decompress, id='native', marks=needs_native),
]
COMPRESSED_CLUSTER_SIZES = list(NTFS_COMPRESSED_FILES_GZ)

async def open_compressed_fs(cluster_size):
    ds = await FileSource.from_file(get_image(NTFS_COMPRESSED_FILES_GZ[cluster_size]))
    disk = await RAWDisk.from_datasource(ds)
    fs = await NTFS.from_disk(disk, 0)
    assert fs.cluster_size == cluster_size
    return fs

def lznt1_compress(data, compress = True):
    """Simple LZNT1 compressor for the tests, with compress being False the chunks are stored as they are"""
    out = b''
    for chunk_start in range(0, len(data), 4096):
        chunk = data[chunk_start:chunk_start+4096]
        items = []
        pos = 0
        while pos < len(chunk) and compress is True:
            shift = LZNT1_SHIFTS[pos]
            # the closest earlier place of the next 3 bytes, the match may run into itself
            start = chunk.rfind(chunk[pos:pos+3], max(0, pos - (1 << (16 - shift))), pos + 2) if pos + 3 <= len(chunk) else -1
            if start == -1:
                items.append((0, chunk[pos:pos+1]))
                pos += 1
                continue
            length = 3
            while pos + length < len(chunk) and length < (1 << shift) + 2 and chunk[start+length] == chunk[pos+length]:
                length += 1
            items.append((1, (((pos - start - 1) << shift) | (length - 3)).to_bytes(2, 'little')))
            pos += length
        body = b''
        for i in range(0, len(items), 8):
            body += bytes([sum(flag << j for j, (flag, _) in enumerate(items[i:i+8]))]) + b''.join(item for _, item in items[i:i+8])
        if compress is True and len(body) < len(chunk):
            out += (0xB000 | (len(body) - 1)).to_bytes(2, 'little') + body
        else:
            out += (0x3000 | (len(chunk) - 1)).to_bytes(2, 'little') + chunk
    return out

def lznt1_test_data():
    rng = random.Random(3)
    return [
        b'',
        b'a',
        b'abc' * 5,
        b'\x00' * 4096,
        b'ab' * 5000,
        rng.randbytes(5000),
        bytes(rng.choice(b'ACGT') for _ in range(20000)),
        b''.join(rng.choice([b'cluster ', b'runlist ', b'attribute ', b'record\r\n']) for _ in range(4000)),
        # compressed and stored chunks
        b'x' * 4096 + rng.randbytes(4096) + b'yz' * 3000,
    ]

@pytest.mark.parametrize('decompress', LZNT1_DECOMPRESSORS)
def test_lznt1_decompress(decompress):
    for data in lznt1_test_data():
        size = (len(data) + 4095) // 4096 * 4096
        for compressed in [lznt1_compress(data), lznt1_compress(data, compress=False)]:
            assert decompress(compressed, len(data)) == data
            # the rest of the last chunk and of the unit is zeroes, the clusters after the chunks are not read
            assert decompress(compressed, size + 8192) == data + b'\x00' * (size + 8192 - len(data))
            assert decompress(compressed + b'\x00' * 100, size) == data + b'\x00' * (size - len(data))
    # the data really gets compressed
    assert len(lznt1_compress(b'ab' * 5000)) < 100

@pytest.mark.parametrize('decompress', LZNT1_DECOMPRESSORS)
def test_lznt1_decompress_format(decompress):
    # literals, then a match which runs into itself
    assert decompress(b'\x03\xB0' + b'\x02' + b'a' + b'\x02\x00', 6) == b'aaaaaa'
    # the offset takes more bits as the chunk grows: 4 bits up to 16 bytes, 5 bits after it
    chunk = b'\x00' + bytes(range(8)) + b'\x00' + bytes(range(8, 16)) + b'\x01' + (0xF000).to_bytes(2, 'little')
    assert decompress((0xB000 | (len(chunk) - 1)).to_bytes(2, 'little') + chunk, 19) == bytes(range(16)) + b'\x00\x01\x02'
    chunk = b'\x00' + bytes(range(8)) + b'\x00' + bytes(range(8, 16)) + b'\x02' + b'\x10' + (0x8000).to_bytes(2, 'little')
    assert decompress((0xB000 | (len(chunk) - 1)).to_bytes(2, 'little') + chunk, 20) == bytes(range(17)) + b'\x00\x01\x02'
    # a short chunk in the middle is filled with zeroes: every chunk is 4096 bytes of the file
    data = b'\x02\x30abc' + b'\x01\x30de'
    assert decompress(data, 8192) == b'abc' + b'\x00' * 4093 + b'de' + b'\x00' * 4094
    # a zero header ends the data
    assert decompress(b'\x02\x30abc' + b'\x00\x00' + b'\x02\x30def', 4096) == b'abc' + b'\x00' * 4093
    assert decompress(b'', 0) == b''

@pytest.mark.parametrize('decompress', LZNT1_DECOMPRESSORS)
def test_lznt1_decompress_errors(decompress):
    bad = [
        (b'\x02', 16), # no chunk header
        (b'\x05\x30abc', 16), # the chunk is longer than the data
        (b'\x02\xB0\x01\x00\x00', 16), # match at the start of the chunk
        (b'\x03\xB0\x02a\x01\x10', 16), # match starts before the chunk
        (b'\x02\xB0\x02a\x00', 16), # match without its second byte
        (b'\x02\x30abc', 2), # more data than the output
        (b'\x03\xB0\x02a\x02\x00', 5),
        (b'\x02\x30abc' + b'\x02\xB0\x01\x00\x00', 8192), # the matches do not reach the previous chunk
        (b'\x05\xB0\x06a\xFF\x0F\xFF\x0F', 8192), # the chunk is longer than 4096 bytes
    ]
    for data, size in bad:
        with pytest.raises(DecompressionError):
            decompress(data, size)

@needs_native
def test_lznt1_decompress_native_same():
    # the native decompressor accepts and rejects the same input as the python one
    def run(func, data, size):
        try:
            return func(data, size)
        except (DecompressionError, IndexError):
            return None

    assert lznt1_decompress is not lznt1_decompress_py
    rng = random.Random(7)
    valid = lznt1_compress(lznt1_test_data()[-2][:6000])
    for _ in range(3000):
        data = bytearray(valid)
        for _ in range(rng.randrange(1, 4)):
            data[rng.randrange(len(data))] = rng.randrange(256)
        data = bytes(data[:rng.choice([len(data), rng.randrange(len(data))])])
        assert run(lznt1_decompress, data, 8192) == run(lznt1_decompress_py, data, 8192)
        chunk = bytes(rng.choice([0, 0, 1, 2, 0x10, 0x0f, 0xf0, 0xff, rng.randrange(256)]) for _ in range(rng.randrange(40)))
        data = ((0xB000 if rng.randrange(4) else 0x3000) | max(len(chunk) - 1 + rng.randrange(-1, 2), 0)).to_bytes(2, 'little') + chunk
        for size in [rng.randrange(64), 8192]:
            assert run(lznt1_decompress, data, size) == run(lznt1_decompress_py, data, size)

@pytest.fixture(params=LZNT1_DECOMPRESSORS)
def ntfs_decompressor(request, monkeypatch):
    monkeypatch.setattr(ntfs_file, 'lznt1_decompress', request.param)

@pytest.mark.asyncio
@pytest.mark.parametrize('cluster_size', COMPRESSED_CLUSTER_SIZES)
async def test_ntfs_compressed_file_list(cluster_size):
    fs = await open_compressed_fs(cluster_size)
    manifest = load_manifest('ntfs_compressed')
    await check_file_list(fs, manifest)
    await check_file_stat(fs, manifest)

@pytest.mark.asyncio
@pytest.mark.parametrize('cluster_size', COMPRESSED_CLUSTER_SIZES)
async def test_ntfs_compressed_file_fullread(cluster_size, ntfs_decompressor):
    fs = await open_compressed_fs(cluster_size)
    await check_file_fullread(fs, load_manifest('ntfs_compressed'))

@pytest.mark.asyncio
@pytest.mark.parametrize('cluster_size', COMPRESSED_CLUSTER_SIZES)
async def test_ntfs_compressed_file_ops(cluster_size, ntfs_decompressor):
    fs = await open_compressed_fs(cluster_size)
    await check_file_ops(fs, load_manifest('ntfs_compressed'))

@pytest.mark.asyncio
@pytest.mark.parametrize('cluster_size', COMPRESSED_CLUSTER_SIZES)
async def test_ntfs_compressed_files_are_compressed(cluster_size):
    # the files of the image are stored the way their names say
    fs = await open_compressed_fs(cluster_size)
    stored = {}
    for file_path, expected in load_manifest('ntfs_compressed')['files'].items():
        file = await fs.open(file_path)
        header = file.get_dataattr().header
        if header.non_resident is False:
            stored[file_path] = None
            continue
        assert bool(header.flags & AttributeHeaderFlag.COMPRESSED) is file_path.startswith('compressed\\')
        clusters = sum(run_length for run_offset, run_length in header.data_runs if run_offset != 0)
        stored[file_path] = clusters * cluster_size
        if file_path.startswith('compressed\\'):
            assert header.compression_unit == 4
        else:
            assert stored[file_path] >= expected['size']
    assert stored['compressed\\small.txt'] is None
    assert stored['compressed\\zeros.bin'] == 0
    assert stored['compressed\\text.txt'] < 300000 // 2
    assert stored['compressed\\holes.bin'] < 100000
    # nothing to spare in these units
    assert stored['compressed\\random.bin'] >= 100000
    assert stored['compressed\\random.bin'] < stored['compressed\\mixed.bin'] < 320000

@pytest.mark.asyncio
@pytest.mark.parametrize('cluster_size', COMPRESSED_CLUSTER_SIZES)
async def test_ntfs_compressed_same_as_plain(cluster_size):
    fs = await open_compressed_fs(cluster_size)
    compressed = await fs.open('compressed\\text.txt')
    plain = await fs.open('plain\\text.txt')
    assert await compressed.read() == await plain.read()
    # reads at the borders of the compression units
    unit = 16 * cluster_size
    for offset, size in [(unit - 1, 2), (unit, unit), (unit - 5, unit + 10), (0, 1), (300000 - 1, 10), (2*unit, 0), (300000, 5)]:
        assert await fs_read(compressed, offset, size) == await fs_read(plain, offset, size)
        assert await compressed.tell() == await plain.tell()


# volumes written by Windows (make_ntfs_compressed.ps1): the files compressed by NTFS (LZNT1) and by the WOF driver
# (XPRESS, LZX), sparse files, named data streams, files longer than their valid data, runs listed by an attribute list

WINDOWS_VOLUMES = [0, 1]

async def open_windows_fs(index):
    folder, manifest = get_ntfs_fixtures()
    volume = manifest['volumes'][index]
    ds = await FileSource.from_file(str(folder.joinpath(volume['image'])))
    disk = await Disk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[volume['partition'] - 1])
    assert fs.cluster_size == volume['cluster_size']
    return fs, manifest, volume

def windows_streams(manifest):
    """The streams of the files of the manifest: path to open, description"""
    for file_path, expected in manifest['files'].items():
        yield file_path, expected
        for name, stream in expected['streams'].items():
            yield file_path + ':' + name, stream

@pytest.mark.asyncio
@pytest.mark.parametrize('index', WINDOWS_VOLUMES)
async def test_ntfs_windows_file_list(index):
    fs, manifest, _ = await open_windows_fs(index)
    await check_file_list(fs, manifest)
    await check_file_stat(fs, manifest)

@pytest.mark.asyncio
@pytest.mark.parametrize('index', WINDOWS_VOLUMES)
async def test_ntfs_windows_file_read(index, ntfs_decompressor):
    fs, manifest, _ = await open_windows_fs(index)
    rng = random.Random(index)
    kinds = set()
    for path, expected in windows_streams(manifest):
        file = await fs.open(path)
        odata = await file.read()
        assert len(odata) == expected['size'], path
        assert hashlib.sha256(odata).hexdigest() == expected['sha256'], path
        assert await file.tell() == expected['size']
        assert await file.read(10) == b''
        kinds.add(expected['kind'])
        # the partial reads are verified against the full read
        fsize = expected['size']
        for _ in range(10):
            pos = rng.randint(0, fsize)
            size = rng.randint(0, min(fsize - pos, 200000))
            assert await fs_read(file, pos, size) == odata[pos:pos+size], path
        await file.seek(-min(fsize, 5000), 2)
        assert await file.read() == odata[-5000:]
    for kind in ['lznt1', 'lznt1_stored', 'lznt1_attribute_list', 'lznt1_valid_data', 'lznt1_sparse', 'lznt1_streams', 'resident',
                 'plain', 'valid_data', 'sparse', 'streams', 'wof_xpress4k', 'wof_xpress8k', 'wof_xpress16k', 'wof_lzx']:
        assert kind in kinds

@pytest.mark.asyncio
@pytest.mark.parametrize('index', WINDOWS_VOLUMES)
async def test_ntfs_windows_files_are_stored(index):
    # the files are stored the way their kind says, the tests above read what they are named after
    fs, manifest, volume = await open_windows_fs(index)
    for path, expected in windows_streams(manifest):
        kind = expected['kind']
        file = await fs.open(path)
        header = file.get_dataattr().header
        if kind == 'resident':
            assert header.non_resident is False
        elif kind.startswith('lznt1') and header.non_resident is True:
            assert header.flags & AttributeHeaderFlag.COMPRESSED and header.compression_unit == 4, path
        elif kind in ('plain', 'valid_data'):
            assert not header.flags & AttributeHeaderFlag.COMPRESSED and header.compression_unit == 0, path
        elif kind in ('sparse', 'lznt1_sparse'):
            assert header.flags & AttributeHeaderFlag.SPARSE, path
        elif kind.startswith('wof'):
            if volume['files'][path]['stored_size']['$DATA'] >= expected['size']:
                # Windows left it as it was: it would not take fewer clusters this way
                assert len(await file.get_record().get_attribute(0xC0)) == 0
                continue
            # the unnamed stream has no clusters, the data is in the stream of the WOF driver
            assert header.flags & AttributeHeaderFlag.SPARSE, path
            stream = await fs.open(path + ':WofCompressedData')
            await stream.seek(0, 2)
            assert 0 < await stream.tell() < expected['size']
        if kind in ('valid_data', 'lznt1_valid_data') and header.non_resident is True:
            assert header.init_size == expected['valid_data_length'] < header.real_size, path
    if volume['cluster_size'] == 512:
        # with 4096 byte clusters the runs of the file fit in its file record
        record = (await fs.open('lznt1\\attribute_list.txt')).get_record()
        assert len(await record.get_attribute(0x20)) == 1
        assert len(await record.get_attribute(0x80, name='')) > 1
