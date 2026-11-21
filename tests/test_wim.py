"""
Tests of the WIM files. The test file is made by 7z without compression, the XPRESS and LZX compressed ones and
the files with more images, named data streams and more parts are built by wim_builder.py.
7z reads all of them, the files are compared against it when it is installed.
"""

import pytest
import hashlib
import os
import random
import shutil
import subprocess
from config import *
from checks import *
from wim_builder import build_wim, xpress_compress, lzx_compress

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.compression import DecompressionError
from adiskreader.compression.xpress import xpress_decompress
from adiskreader.compression.lzx import lzx_decompress
from adiskreader.datasource.file import FileSource
from adiskreader.datasource.fsfile import FSFileSource
from adiskreader.filesystems.wim import WIM, WIMImage

COMPRESSIONS = [None, 'xpress', 'lzx']

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

@pytest.mark.asyncio
async def test_wim_solid_not_supported(tmp_path):
    # ESD files (solid resources, LZMS)
    data = bytearray(build_wim(IMAGES, 'lzx')[0])
    data[12:16] = (0xe00).to_bytes(4, 'little')
    data[16:20] = (0x80002).to_bytes(4, 'little')
    ds = await FileSource.from_file(str(write_parts(tmp_path, [bytes(data)])))
    try:
        wim = await WIM.from_datasource(ds)
        assert wim.header.compression == 'LZMS'
        with pytest.raises(NotImplementedError):
            await wim.mount(1)
    finally:
        await ds.close()

@pytest.mark.parametrize('compress, decompress', [(xpress_compress, xpress_decompress), (lzx_compress, lzx_decompress)])
def test_decompress(compress, decompress):
    rng = random.Random(5)
    alphabet = bytes(rng.randrange(256) for _ in range(40)) + b'\xe8' * 3
    for size in [1, 2, 3, 11, 300, 5000, 32767, 32768]:
        for data in [bytes(rng.choice(alphabet) for _ in range(size)), (b'abcabcabd' * 4000)[:size], b'\x00' * size]:
            assert decompress(compress(data, rng), len(data)) == data

@pytest.mark.parametrize('compress, decompress', [(xpress_compress, xpress_decompress), (lzx_compress, lzx_decompress)])
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
