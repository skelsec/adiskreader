"""
Tests of the WIM files. The test file is made by 7z without compression, the XPRESS, LZX and LZMS compressed ones and
the files with more images, named data streams, more parts and solid resources (ESD) are built by wim_builder.py.
7z reads all of them, the files are compared against it when it is installed.
"""

import pytest
import hashlib
import os
import random
import shutil
import struct
import subprocess
from config import *
from checks import *
from wim_builder import build_wim, xpress_compress, lzx_compress, lzms_compress, lzms_do_x86

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.compression import DecompressionError, native
from adiskreader.compression.xpress import xpress_decompress, xpress_decompress_py
from adiskreader.compression.lzx import lzx_decompress, lzx_decompress_py
from adiskreader.compression.lzms import lzms_decompress, lzms_decompress_py, lzms_code_lengths, lzms_undo_x86
from adiskreader.datasource.file import FileSource
from adiskreader.datasource.fsfile import FSFileSource
from adiskreader.filesystems.wim import WIM, WIMImage

COMPRESSIONS = [None, 'xpress', 'lzx', 'lzms']
# compression of the solid resources, chunk size, number of resources, number of runs
SOLIDS = [
    pytest.param(('lzms', 0x10000, 1, 1), id='lzms'),
    pytest.param(('lzms', 0x4000000, 1, 1), id='lzms-one-chunk'),
    pytest.param(('lzms', 0x8000, 3, 1), id='lzms-resources'),
    pytest.param(('lzms', 0x8000, 2, 2), id='lzms-runs'),
    pytest.param(('lzx', 0x8000, 2, 1), id='lzx'),
    pytest.param(('xpress', 0x8000, 1, 2), id='xpress'),
    pytest.param((None, 0x1000, 2, 1), id='none'),
]

SECURITY_DESCRIPTORS = [b'\x01\x00\x04\x80' + b'\x00' * 16, b'\x01\x00\x04\x80' + b'\x11' * 28]
IMAGES = [
    ('first', {
        'a.txt' : b'aaa',
        'dir' : {
            'b.bin' : b'b' * 100000,
            'sub' : {'empty.txt' : b''},
        },
        'streams.txt' : (b'main data', {'Zone.Identifier' : b'[ZoneTransfer]\r\nZoneId=3\r\n', 'big' : b'xyz' * 30000}, 1),
    }, SECURITY_DESCRIPTORS),
    ('second', {
        'a.txt' : b'aaa',
        'other.bin' : bytes(range(256)) * 300,
        'onlystream' : (b'', {'s' : b'stream'}, -1),
    }, []),
]

# the decompressors written in python and the ones of the adiskreader_native module (native/adiskreader_native)
needs_native = pytest.mark.skipif(native is None, reason='adiskreader_native is not installed')
DECOMPRESSORS = [
    pytest.param(xpress_compress, xpress_decompress_py, id='xpress-python'),
    pytest.param(lzx_compress, lzx_decompress_py, id='lzx-python'),
    pytest.param(lzms_compress, lzms_decompress_py, id='lzms-python'),
    pytest.param(xpress_compress, xpress_decompress, id='xpress-native', marks=needs_native),
    pytest.param(lzx_compress, lzx_decompress, id='lzx-native', marks=needs_native),
    pytest.param(lzms_compress, lzms_decompress, id='lzms-native', marks=needs_native),
]
LZMS_DECOMPRESSORS = [
    pytest.param(lzms_decompress_py, id='python'),
    pytest.param(lzms_decompress, id='native', marks=needs_native),
]

async def open_wim(path):
    ds = await FileSource.from_file(str(path))
    try:
        return ds, await WIM.from_datasource(ds)
    except Exception:
        await ds.close()
        raise

async def open_fs(compression):
    _, wim = await open_wim(get_wim_image(compression))
    return await wim.mount(1)

def write_parts(folder, parts):
    """Writes the parts of a WIM file as test.wim or test.swm, test2.swm ..., returns the path of the first part"""
    if len(parts) == 1:
        folder.joinpath('test.wim').write_bytes(parts[0])
        return folder.joinpath('test.wim')
    for index, data in enumerate(parts):
        folder.joinpath('test%s.swm' % ('' if index == 0 else index + 1)).write_bytes(data)
    return folder.joinpath('test.swm')

async def read_file(fs, path):
    f = await fs.open(path)
    return await f.read()


@pytest.mark.asyncio
@pytest.mark.parametrize('compression', COMPRESSIONS)
async def test_wim_info(compression):
    ds, wim = await open_wim(get_wim_image(compression))
    try:
        assert await WIM.is_wim(ds) is True
        assert wim.header.compression == (compression.upper() if compression is not None else None)
        assert wim.header.image_count == 1
        partitions = await wim.list_partitions()
        assert len(partitions) == 1
        assert partitions[0].properties['FILECOUNT'] == '14'
        assert str(partitions[0]).startswith('Image 1')
        assert str(wim).find('WIM') != -1
        fs = await partitions[0].mount()
        assert isinstance(fs, WIMImage)
        with pytest.raises(Exception, match='No such image'):
            await wim.mount(2)
    finally:
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('compression', COMPRESSIONS)
async def test_wim_file_list(compression):
    fs = await open_fs(compression)
    manifest = load_manifest('wim')
    await check_file_list(fs, manifest)
    # nothing else is listed
    count = 0
    async for root, dirs, files in fs.walk():
        count += len(dirs) + len(files)
    assert count == len(manifest['dirs']) + len(manifest['files'])

@pytest.mark.asyncio
@pytest.mark.parametrize('compression', COMPRESSIONS)
async def test_wim_file_open(compression):
    fs = await open_fs(compression)
    await check_file_open(fs, load_manifest('wim'))

@pytest.mark.asyncio
@pytest.mark.parametrize('compression', COMPRESSIONS)
async def test_wim_file_stat(compression):
    fs = await open_fs(compression)
    await check_file_stat(fs, load_manifest('wim'))

@pytest.mark.asyncio
@pytest.mark.parametrize('compression', COMPRESSIONS)
async def test_wim_file_fullread(compression):
    fs = await open_fs(compression)
    await check_file_fullread(fs, load_manifest('wim'))

@pytest.mark.asyncio
@pytest.mark.parametrize('compression', COMPRESSIONS)
async def test_wim_file_ops(compression):
    fs = await open_fs(compression)
    await check_file_ops(fs, load_manifest('wim'))

@pytest.mark.asyncio
@pytest.mark.parametrize('compression', COMPRESSIONS)
async def test_wim_directories(compression):
    fs = await open_fs(compression)
    root = await fs.get_root()
    assert root.is_directory() is True
    assert await root.resolve_full_path() == '\\'
    assert await root.get_parent() is root
    children = {name : etype async for etype, name, _ in root.get_children()}
    assert children['dir1'] == 'dir' and children['hello.txt'] == 'file'
    assert len([line async for line in root.get_console_output()]) == len(children)

    sub = await (await root.get_child('dir1')).get_child('sub')
    assert await sub.resolve_full_path() == '\\dir1\\sub'
    assert (await sub.get_parent()).name == 'dir1'
    assert await sub.get_child('nothere') is None
    emptydir = await fs.get_record_by_path('\\dir2\\emptydir')
    assert emptydir.is_directory() is True
    assert [child async for child in emptydir.get_children()] == []

    # the names are looked up case insensitively when there is no exact match, both separators are accepted
    assert await read_file(fs, 'DIR1/SUB/Deep.TXT') == b'deep file\n' * 100
    assert await read_file(fs, '\\dir1\\..\\.\\hello.txt') == b'hello wim\n'
    assert await read_file(fs, 'empty.txt') == b''
    assert (await fs.stat('dir1')).st_size == 0
    walked = [root async for root, dirs, files in fs.walk('\\dir1')]
    assert walked == ['\\dir1', '\\dir1\\sub']

    with pytest.raises(FileNotFoundError):
        await fs.open('nothere.txt')
    with pytest.raises(FileNotFoundError):
        await fs.open('hello.txt:nostream')
    with pytest.raises(FileNotFoundError):
        await fs.stat('dir1\\nothere')
    with pytest.raises(IsADirectoryError):
        await fs.open('dir1')
    with pytest.raises(NotADirectoryError):
        async for _ in fs.walk('hello.txt'):
            pass

@pytest.mark.parametrize('compression', COMPRESSIONS)
def test_wim_7z(compression, tmp_path):
    # 7z extracts the same files from the WIM files built by wim_builder.py
    if shutil.which('7z') is None:
        pytest.skip('7z is not installed')
    subprocess.run(['7z', 'x', '-y', '-o' + str(tmp_path), str(get_wim_image(compression))], stdout=subprocess.DEVNULL, check=True)
    for path, expected in load_manifest('wim')['files'].items():
        data = tmp_path.joinpath(*path.split('\\')).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected['sha256']

    path = write_parts(tmp_path, build_wim(IMAGES, compression))
    subprocess.run(['7z', 'x', '-y', '-o' + str(tmp_path.joinpath('images')), str(path)], stdout=subprocess.DEVNULL, check=True)
    assert tmp_path.joinpath('images', '1', 'dir', 'b.bin').read_bytes() == b'b' * 100000
    assert tmp_path.joinpath('images', '2', 'other.bin').read_bytes() == bytes(range(256)) * 300

@pytest.mark.asyncio
@pytest.mark.parametrize('compression', COMPRESSIONS)
@pytest.mark.parametrize('parts', [1, 3])
async def test_wim_images(compression, parts, tmp_path):
    # more images, named data streams, security descriptors and files split to more parts
    path = write_parts(tmp_path, build_wim(IMAGES, compression, parts))
    ds, wim = await open_wim(path)
    try:
        assert wim.header.total_parts == parts
        assert [image.name for image in wim.images] == ['first', 'second']
        assert wim.images[1].description == 'second description'

        fs = await wim.images[0].mount()
        assert await read_file(fs, 'a.txt') == b'aaa'
        assert await read_file(fs, 'dir\\b.bin') == b'b' * 100000
        assert await read_file(fs, 'dir\\sub\\empty.txt') == b''
        assert await read_file(fs, 'streams.txt') == b'main data'
        assert await read_file(fs, 'streams.txt:Zone.Identifier') == b'[ZoneTransfer]\r\nZoneId=3\r\n'
        assert await read_file(fs, 'streams.txt:BIG') == b'xyz' * 30000
        assert (await fs.stat('streams.txt')).st_size == 9
        entry = await fs.get_record_by_path('streams.txt')
        assert entry.get_stream_names() == ['Zone.Identifier', 'big']
        assert entry.get_size('big') == 90000
        assert await entry.get_security_descriptor() == SECURITY_DESCRIPTORS[1]
        assert await (await fs.get_record_by_path('a.txt')).get_security_descriptor() is None
        assert await fs.get_record_by_path('other.bin') is None

        fs = await wim.mount(2)
        assert await read_file(fs, 'a.txt') == b'aaa'
        assert await read_file(fs, 'other.bin') == bytes(range(256)) * 300
        assert await read_file(fs, 'onlystream') == b''
        assert await read_file(fs, 'onlystream:s') == b'stream'
        assert await fs.get_record_by_path('dir') is None
    finally:
        await wim.close()
        await ds.close()

@pytest.mark.asyncio
async def test_wim_missing_part(tmp_path, caplog):
    path = write_parts(tmp_path, build_wim(IMAGES, 'xpress', 3))
    tmp_path.joinpath('test2.swm').unlink()
    ds, wim = await open_wim(path)
    try:
        assert 'Part 2 of the WIM file' in caplog.text
        # the directories are in the first part, the contents of some of the files are missing
        fs = await wim.mount(1)
        results = []
        for filepath in ['a.txt', 'dir\\b.bin', 'streams.txt', 'streams.txt:Zone.Identifier', 'streams.txt:big']:
            try:
                await read_file(fs, filepath)
                results.append(True)
            except FileNotFoundError:
                results.append(False)
        assert True in results and False in results
    finally:
        await wim.close()
        await ds.close()

@pytest.mark.asyncio
async def test_wim_in_filesystem(tmp_path):
    # a WIM file read from an image of another WIM file
    inner = build_wim(IMAGES, 'lzx', 2)
    outer = build_wim([('outer', {'sources' : {'install.swm' : inner[0], 'install2.swm' : inner[1]}}, [])], 'xpress')
    ds, wim = await open_wim(write_parts(tmp_path, outer))
    try:
        fs = await wim.mount(1)
        inner_ds = await FSFileSource.from_filesystem(fs, '\\sources\\install.swm')
        assert await WIM.is_wim(inner_ds) is True
        inner_wim = await WIM.from_datasource(inner_ds)
        inner_fs = await inner_wim.mount(1)
        assert await read_file(inner_fs, 'dir\\b.bin') == b'b' * 100000
        assert await read_file(inner_fs, 'streams.txt:big') == b'xyz' * 30000
        await inner_wim.close()
        await inner_ds.close()
    finally:
        await ds.close()

@pytest.mark.asyncio
async def test_not_a_wim(tmp_path):
    path = tmp_path.joinpath('notawim.wim')
    path.write_bytes(b'\x01' * 4096)
    ds = await FileSource.from_file(str(path))
    try:
        assert await WIM.is_wim(ds) is False
        with pytest.raises(Exception, match='Not a WIM file'):
            await WIM.from_datasource(ds)
    finally:
        await ds.close()

async def check_images(wim):
    fs = await wim.images[0].mount()
    assert await read_file(fs, 'a.txt') == b'aaa'
    assert await read_file(fs, 'dir\\b.bin') == b'b' * 100000
    assert await read_file(fs, 'dir\\sub\\empty.txt') == b''
    assert await read_file(fs, 'streams.txt') == b'main data'
    assert await read_file(fs, 'streams.txt:Zone.Identifier') == b'[ZoneTransfer]\r\nZoneId=3\r\n'
    assert await read_file(fs, 'streams.txt:big') == b'xyz' * 30000
    entry = await fs.get_record_by_path('streams.txt')
    assert entry.get_size('big') == 90000
    assert (await fs.stat('dir\\b.bin')).st_size == 100000
    fs = await wim.mount(2)
    assert await read_file(fs, 'a.txt') == b'aaa'
    assert await read_file(fs, 'other.bin') == bytes(range(256)) * 300
    assert await read_file(fs, 'onlystream:s') == b'stream'

@pytest.mark.asyncio
@pytest.mark.parametrize('solid', SOLIDS)
async def test_wim_solid(solid, tmp_path):
    # ESD files: the contents of the files are in solid resources, the metadata is compressed with LZMS
    path = write_parts(tmp_path, build_wim(IMAGES, 'lzms', solid=solid))
    ds, wim = await open_wim(path)
    try:
        assert wim.header.compression == 'LZMS'
        assert wim.header.version == 0xe00
        await check_images(wim)

        # reads from the middle of the data, over the borders of the chunks
        fs = await wim.mount(2)
        expected = bytes(range(256)) * 300
        f = await fs.open('other.bin')
        rng = random.Random(8)
        for _ in range(30):
            offset = rng.randrange(len(expected))
            size = rng.randint(0, 70000)
            await f.seek(offset, 0)
            assert await f.read(size) == expected[offset:offset+size]
    finally:
        await wim.close()
        await ds.close()

@pytest.mark.parametrize('solid', SOLIDS)
def test_wim_solid_7z(solid, tmp_path):
    if shutil.which('7z') is None:
        pytest.skip('7z is not installed')
    path = write_parts(tmp_path, build_wim(IMAGES, 'lzms', solid=solid))
    subprocess.run(['7z', 'x', '-y', '-o' + str(tmp_path.joinpath('images')), str(path)], stdout=subprocess.DEVNULL, check=True)
    assert tmp_path.joinpath('images', '1', 'a.txt').read_bytes() == b'aaa'
    assert tmp_path.joinpath('images', '1', 'dir', 'b.bin').read_bytes() == b'b' * 100000
    assert tmp_path.joinpath('images', '2', 'other.bin').read_bytes() == bytes(range(256)) * 300

def find_solid_resource(data):
    """Returns the offset of the first solid resource of a WIM file and the offset of its entry in the lookup table"""
    _, lookup_offset, _ = struct.unpack_from('<QQQ', data, 48)
    lookup_size = struct.unpack_from('<Q', data, 48)[0] & 0xFFFFFFFFFFFFFF
    for pos in range(lookup_offset, lookup_offset + lookup_size, 50):
        size_flags, offset, original_size = struct.unpack_from('<QQQ', data, pos)
        if (size_flags >> 56) & 0x10 and original_size == 0x100000000:
            return offset, pos
    raise Exception('No solid resource')

@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['format', 'chunk_size', 'size', 'chunk_table', 'truncated', 'no_resource'])
async def test_wim_solid_damaged(damage, tmp_path, caplog):
    data = bytearray(build_wim(IMAGES, 'lzms', solid=('lzms', 0x8000, 1, 1))[0])
    offset, entry = find_solid_resource(data)
    if damage == 'format':
        data[offset+12:offset+16] = struct.pack('<I', 7)
    elif damage == 'chunk_size':
        data[offset+8:offset+12] = struct.pack('<I', 0)
    elif damage == 'size':
        data[offset:offset+8] = struct.pack('<Q', 1 << 50)
    elif damage == 'chunk_table':
        data[offset+16:offset+20] = struct.pack('<I', 0x7FFFFFFF)
    elif damage == 'truncated':
        # the resource is said to be shorter than its header
        data[entry:entry+7] = (8).to_bytes(7, 'little')
    else:
        # the entry of the resource is not solid anymore, the data has nothing to be read from
        data[entry+7] = 0
        data[entry+16:entry+24] = struct.pack('<Q', 100)
    ds, wim = await open_wim(write_parts(tmp_path, [bytes(data)]))
    try:
        if damage == 'no_resource':
            assert 'Data without solid resource' in caplog.text
        fs = await wim.mount(1)
        with pytest.raises(Exception):
            await read_file(fs, 'dir\\b.bin')
        # the files without data are not affected
        assert await read_file(fs, 'dir\\sub\\empty.txt') == b''
    finally:
        await wim.close()
        await ds.close()

def lzms_delta_data(rng, size):
    """Tables of numbers, each one differing from the previous one by the same amount: for the delta matches of LZMS"""
    res = b''
    while len(res) < size:
        width = rng.choice([1, 2, 3, 4, 6, 8, 16])
        start = rng.randrange(1 << (8 * min(width, 8)))
        step = rng.randrange(1, 2000)
        res += b''.join(((start + i * step) % (1 << (8 * width))).to_bytes(width, 'little') for i in range(rng.randint(5, 300)))
        if rng.random() < 0.3:
            res += bytes(rng.randrange(256) for _ in range(rng.randint(1, 20)))
    return res[:size]

def lzms_x86_data(rng, size):
    """Instructions with relative addresses, some of them pointing to the same target"""
    opcodes = [b'\x48\x8b\x05', b'\x48\x8b\x0d', b'\x48\x8d\x05', b'\x48\x8d\x3d', b'\x4c\x8d\x05', b'\x4c\x8d\x2d',
        b'\xe8', b'\xe9', b'\xf0\x83\x05', b'\xff\x15']
    res = b''
    while len(res) < size:
        target = rng.choice([0x1000, 0x2000, -0x300, rng.randrange(-70000, 70000)])
        if rng.random() < 0.7:
            target -= len(res) & 0xfff0
        res += rng.choice(opcodes) + struct.pack('<i', target) + bytes(rng.randrange(256) for _ in range(rng.randint(0, 12)))
    return res[:size]

@pytest.mark.parametrize('decompress', LZMS_DECOMPRESSORS)
def test_lzms_features(decompress):
    # all kinds of items: literals, LZ and delta matches with new offsets and with the last ones
    rng = random.Random(9)
    stats = {}
    datas = [lzms_delta_data(rng, 60000), lzms_x86_data(rng, 40000), bytes(rng.randrange(256) for _ in range(3000)), b'\x00' * 200000]
    datas += [lzms_x86_data(rng, size) for size in (16, 17, 18, 19, 40)]
    words = [bytes(rng.choice(b'abcdefgh') for _ in range(rng.randint(2, 9))) for _ in range(50)]
    datas.append(b' '.join(rng.choice(words) for _ in range(5000)))
    for data in datas:
        compressed = lzms_compress(data, rng, stats)
        assert decompress(compressed, len(data)) == data
    kinds = ['literal', 'lz', 'delta']
    for kind in ('lz', 'delta'):
        kinds += ['%s_rep%s%s' % (kind, index, delayed) for index in range(3) for delayed in ('', '_delayed')]
    assert sorted(stats) == sorted(kinds)

    assert decompress(lzms_compress(b'', rng), 0) == b''
    # the data is made of 16 bit words, at least two of them
    for data in [b'', b'\x00', b'\x00' * 3, b'\x00' * 101]:
        with pytest.raises(DecompressionError):
            decompress(data, 100)

def test_lzms_x86():
    rng = random.Random(10)
    translated = 0
    for size in [0, 16, 17, 18, 30, 5000, 200000]:
        data = lzms_x86_data(rng, size)
        filtered = lzms_do_x86(data)
        translated += 1 if filtered != data else 0
        assert len(filtered) == size
        restored = bytearray(filtered)
        lzms_undo_x86(restored)
        assert restored == data
    assert translated >= 2

def test_lzms_code_lengths():
    # the lengths are of a complete prefix code not longer than the limit, the more frequent symbols have shorter codes
    rng = random.Random(11)
    fibonacci = [1, 1]
    while len(fibonacci) < 40:
        fibonacci.append(fibonacci[-1] + fibonacci[-2])
    cases = [[1] * 256, [1] * 54, [1] * 8, [1, 1], [5], fibonacci, fibonacci[::-1], [1] * 799]
    cases += [[rng.randint(1, 1 << rng.randint(1, 24)) for _ in range(rng.randint(2, 800))] for _ in range(50)]
    for freqs in cases:
        lens = lzms_code_lengths(freqs)
        assert len(lens) == len(freqs)
        if len(freqs) == 1:
            continue
        assert min(lens) >= 1 and max(lens) <= 15
        assert sum(1 << (15 - length) for length in lens) == 1 << 15
        for i in range(len(freqs) - 1):
            if freqs[i] > freqs[i+1]:
                assert lens[i] <= lens[i+1]
    assert lzms_code_lengths([1] * 256) == [8] * 256
    assert max(lzms_code_lengths(fibonacci)) == 15
    assert max(lzms_code_lengths(fibonacci, 12)) == 12

@pytest.mark.parametrize('compress, decompress', DECOMPRESSORS)
def test_decompress(compress, decompress):
    rng = random.Random(5)
    alphabet = bytes(rng.randrange(256) for _ in range(40)) + b'\xe8' * 3
    for size in [0, 1, 2, 3, 11, 300, 5000, 32767, 32768]:
        for data in [bytes(rng.choice(alphabet) for _ in range(size)), (b'abcabcabd' * 4000)[:size], b'\x00' * size]:
            assert decompress(compress(data, rng), len(data)) == data

@pytest.mark.parametrize('compress, decompress', DECOMPRESSORS)
def test_decompress_damaged(compress, decompress):
    # damaged data either fails with DecompressionError or gives data of the expected size
    rng = random.Random(6)
    words = [bytes(rng.choice(b'abcdefgh') for _ in range(rng.randint(2, 9))) for _ in range(50)]
    data = b' '.join(rng.choice(words) for _ in range(1500))
    compressed = compress(data, rng)
    for i in range(300):
        damaged = bytearray(compressed)
        if i % 3 == 0:
            damaged = damaged[:rng.randrange(len(damaged))]
        else:
            for _ in range(rng.randint(1, 4)):
                damaged[rng.randrange(len(damaged))] = rng.randrange(256)
        try:
            assert len(decompress(bytes(damaged), len(data))) == len(data)
        except DecompressionError:
            pass

@needs_native
@pytest.mark.parametrize('compress, decompress, decompress_py', [
    (xpress_compress, xpress_decompress, xpress_decompress_py),
    (lzx_compress, lzx_decompress, lzx_decompress_py),
    (lzms_compress, lzms_decompress, lzms_decompress_py),
])
def test_decompress_native_same(compress, decompress, decompress_py):
    # the native decompressors accept and reject the same input as the python ones
    def run(func, data, size):
        try:
            return func(data, size)
        except (DecompressionError, IndexError):
            return None

    assert decompress is not decompress_py
    rng = random.Random(7)
    words = [bytes(rng.randrange(256) for _ in range(rng.randint(1, 9))) for _ in range(50)]
    for size in [1, 300, 5000, 32768]:
        data = b''.join(rng.choice(words) for _ in range(size))[:size]
        compressed = compress(data, rng)
        assert decompress(compressed, size) == data
        for i in range(200):
            damaged = bytearray(compressed)
            if i % 3 == 0:
                damaged = damaged[:rng.randrange(len(damaged))]
            else:
                for _ in range(rng.randint(1, 4)):
                    damaged[rng.randrange(len(damaged))] = rng.randrange(256)
            out_size = size if i % 5 != 0 else rng.randint(1, 40000)
            assert run(decompress, bytes(damaged), out_size) == run(decompress_py, bytes(damaged), out_size)
