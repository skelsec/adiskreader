"""
Tests of the Veeam backup files (vbk, vib, vbm). The files are made by Veeam Backup & Replication 13 with make_vbk.ps1
from a Hyper-V machine with two small disks, the contents of the disks at each run of the backup jobs are stored with them.
"""

import pytest
import hashlib
import json
import os
import random
import shutil
import struct
from config import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader import compression
from adiskreader.compression import DecompressionError, native
from adiskreader.compression.lz4 import lz4_decompress, lz4_decompress_py
from adiskreader.compression.veeamrle import veeam_rle_decompress, veeam_rle_decompress_py
from adiskreader.datasource.file import FileSource
from adiskreader.disks.raw import RAWDisk
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.utils import crc32c as crc32c_py
from adiskreader.backups.vbk import VBK, VBM, VBKStorage, VBKEncryptedError, VBKPasswordVerifier, vector_slots
from adiskreader.backups.vbk.structures import *

pytestmark = pytest.mark.skipif(has_vbk_fixtures() is False, reason='the Veeam backup files are not available')

needs_native = pytest.mark.skipif(native is None, reason='adiskreader_native is not installed')
LZ4_DECOMPRESSORS = [
    pytest.param(lz4_decompress_py, id='python'),
    pytest.param(lz4_decompress, id='native', marks=needs_native),
]
RLE_DECOMPRESSORS = [
    pytest.param(veeam_rle_decompress_py, id='python'),
    pytest.param(veeam_rle_decompress, id='native', marks=needs_native),
]

# the scenarios with one full backup and the compression method their blocks are stored with
SINGLE = {
    'comp_none' : COMPRESSION_NONE,
    'comp_dedupe' : COMPRESSION_RLE,
    'comp_high' : 8,
    'comp_extreme' : 9,
    'block_wan' : COMPRESSION_LZ4,
    'block_lan' : COMPRESSION_LZ4,
    'block_large' : COMPRESSION_LZ4,
    'dedup_off' : COMPRESSION_LZ4,
    'zip' : COMPRESSION_LZ4,
}
# scenario, run: the backup files needed for reading the restore point (indexes of the runs)
CHAINS = [
    ('chain_default', 0, [0]),
    ('chain_default', 1, [0, 1]),
    ('chain_default', 2, [0, 1, 2]),
    ('chain_active_full', 0, [0]),
    ('chain_active_full', 1, [0, 1]),
    ('chain_active_full', 2, [2]),
]
POINTS = [(name, 0, [0]) for name in SINGLE] + CHAINS
# from https://github.com/hashcat/hashcat/issues/3623
HASHCAT_EXAMPLE = '$vbk$*4e2df2b59029911ac171a2d07fdc8d5d68681ced7c2192d84ed4452d7fa7c8e44569e880bc27573663e185c80566ec49427363f17f4be0c294aade02799f07ed*10000*213e6e18c587f4cc196a24ce4509bbdb'
POINT_IDS = ['%s-%s' % (name, run + 1) for name, run, _ in POINTS]


def get_scenario(name):
    folder, manifest = get_vbk_fixtures()
    for scenario in manifest['scenarios']:
        if scenario['name'] == name:
            return folder, scenario
    raise KeyError(name)

def get_run_file(folder, scenario, run):
    """Returns the path of the backup file made by the run"""
    for file in scenario['runs'][run]['files']:
        if file['name'].lower().endswith(('.vbk', '.vib')):
            return folder.joinpath(scenario['name'], file['name'])
    raise KeyError(run)

def get_vbm_file(folder, scenario):
    for file in scenario['runs'][-1]['files']:
        if file['name'].lower().endswith('.vbm'):
            return folder.joinpath(scenario['name'], file['name'])
    raise KeyError(scenario['name'])

def get_state(folder, scenario, run):
    """Returns the folder with the contents of the disks at the run and the list of the files on them"""
    state = folder.joinpath(scenario['runs'][run]['ground_truth'])
    return state, json.loads(state.joinpath('files.json').read_text(encoding='utf-8-sig'))

async def open_chain(name, runs):
    folder, scenario = get_scenario(name)
    sources = [await FileSource.from_file(str(get_run_file(folder, scenario, run))) for run in runs]
    return sources, await VBK.from_datasources(sources)

async def close_all(sources):
    for ds in sources:
        await ds.close()

async def check_disks(vbk, files):
    """Compares the files on the disks of the restore point against the list made when the backup ran"""
    disks = vbk.list_disks()
    assert len(disks) == len(files['disks'])
    for expected in files['disks']:
        path = [path for path in disks if path.endswith('\\' + expected['source_vhdx'])]
        assert len(path) == 1
        assert vbk.get_entry(path[0]).size == expected['virtual_size']
        disk = await vbk.open_disk(path[0])
        assert isinstance(disk, RAWDisk)
        partitions = [part for part in await disk.list_partitions() if part.FileSytemType is not None]
        assert len(partitions) == 1
        fs = await partitions[0].mount()

        found = {}
        async for root, dirs, names in fs.walk():
            root = str(root).replace('\\', '/').strip('/')
            if root.startswith(('$', 'System Volume Information')):
                continue
            for dirname in dirs:
                found[(root + '/' + str(dirname)).strip('/')] = 'dir'
            for filename in names:
                found[(root + '/' + str(filename)).strip('/')] = 'file'
        found = {path : kind for path, kind in found.items() if path.startswith(('$', 'System Volume Information')) is False}
        assert found == {entry['path'] : entry['type'] for entry in expected['entries']}

        for entry in expected['entries']:
            if entry['type'] != 'file':
                continue
            ffile = await fs.open(entry['path'].replace('/', '\\'))
            data = await ffile.read()
            assert len(data) == entry['size']
            assert hashlib.sha256(data).hexdigest() == entry['sha256']


#### the restore points

@pytest.mark.asyncio
@pytest.mark.parametrize('name, run, runs', POINTS, ids=POINT_IDS)
async def test_restore_point(name, run, runs):
    folder, scenario = get_scenario(name)
    sources, vbk = await open_chain(name, runs)
    try:
        assert vbk.encrypted is False
        assert len(vbk.storages) == len(runs)
        assert vbk.storages[0].header.block_size == scenario['settings']['block_size_kb'] * 1024
        names = [path.split('\\')[-1] for path in vbk.list_files()]
        assert 'summary.xml' in names and 'disk1.vhdx' in names and 'disk2.vhdx' in names
        await check_disks(vbk, get_state(folder, scenario, run)[1])
    finally:
        await vbk.close()
        await close_all(sources)

@pytest.mark.asyncio
@pytest.mark.parametrize('name, run, runs', CHAINS, ids=['%s-%s' % (name, run + 1) for name, run, _ in CHAINS])
async def test_restore_point_vbm(name, run, runs):
    # the vbm file lists the backup files, the ones the restore point needs are opened from its directory
    folder, scenario = get_scenario(name)
    ds = await FileSource.from_file(str(get_vbm_file(folder, scenario)))
    vbk = await VBK.from_datasource(ds, point=run)
    try:
        assert len(vbk.vbm.storages) == 3
        assert [storage.header.block_size for storage in vbk.storages] == [1024 * 1024] * len(runs)
        assert vbk.vbm.storages[run].filename == get_run_file(folder, scenario, run).name
        await check_disks(vbk, get_state(folder, scenario, run)[1])
        assert 'Veeam backup' in str(vbk)
    finally:
        await vbk.close()
        await ds.close()

@pytest.mark.asyncio
async def test_vbm_latest():
    folder, scenario = get_scenario('chain_default')
    ds = await FileSource.from_file(str(get_vbm_file(folder, scenario)))
    vbk = await VBK.from_datasource(ds)
    try:
        assert vbk.vbm.job_name.startswith('vbkfix_chain_default')
        assert [storage.is_full() for storage in vbk.vbm.storages] == [True, False, False]
        assert len(vbk.storages) == 3
        assert len(await vbk.list_partitions()) == 3
    finally:
        await vbk.close()
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('name, runs', [('chain_default', [0]), ('chain_default', [0, 1]), ('block_wan', [0]), ('block_large', [0]), ('comp_extreme', [0])])
async def test_disk_raw(name, runs):
    # the disks stored in the backup are the same sector by sector as the disks which were backed up
    folder, scenario = get_scenario(name)
    state, files = get_state(folder, scenario, runs[-1])
    sources, vbk = await open_chain(name, runs)
    try:
        for expected in files['disks']:
            path = [path for path in vbk.list_disks() if path.endswith('\\' + expected['source_vhdx'])][0]
            vbkfile = await vbk.open(path)
            ds = await FileSource.from_file(str(state.joinpath(expected['source_vhdx'])))
            disk = await VHDXDisk.from_datasource(ds)
            chunk = 4 * 1024 * 1024
            for offset in range(0, expected['virtual_size'], chunk):
                assert await vbkfile.read(chunk) == await disk.read_sectors(offset // 512, chunk // 512), offset
            assert await vbkfile.read(1) == b''
            await ds.close()
    finally:
        await vbk.close()
        await close_all(sources)

@pytest.mark.asyncio
async def test_file_read():
    folder, scenario = get_scenario('block_wan')
    restored = json.loads(folder.joinpath('ground_truth', 'veeam_restore.json').read_text(encoding='utf-8-sig'))
    sources, vbk = await open_chain('chain_default', [0])
    try:
        # a small file, Veeam restored the same
        expected = [file for file in restored[0]['restored'] if file['name'] == 'config.wmi'][0]
        path = [path for path in vbk.list_files() if path.endswith('\\config.wmi')][0]
        ffile = await vbk.open(path.upper())
        assert ffile.size == expected['size']
        data = await ffile.read()
        assert hashlib.sha256(data).hexdigest() == expected['sha256']
        assert await ffile.read() == b''
        await ffile.seek(-10, 2)
        assert await ffile.read(100) == data[-10:]
        assert await ffile.tell() == len(data)

        # partial reads over the block boundaries
        ffile = await vbk.open(vbk.list_disks()[0])
        rng = random.Random(3)
        await ffile.seek(0, 0)
        whole = await ffile.read(8 * 1024 * 1024)
        for _ in range(50):
            size = rng.randrange(3 * 1024 * 1024)
            pos = rng.randrange(len(whole) - size)
            assert await fs_read(ffile, pos, size) == whole[pos:pos+size]

        with pytest.raises(FileNotFoundError):
            await vbk.open('nothing\\here')
        with pytest.raises(IsADirectoryError):
            await vbk.open(path.split('\\')[0])
        assert vbk.get_entry('nothing') is None
    finally:
        await vbk.close()
        await close_all(sources)

@pytest.mark.asyncio
@pytest.mark.parametrize('name', list(SINGLE))
async def test_compression_method(name):
    # each scenario stores its blocks with the method it is there for
    sources, vbk = await open_chain(name, [0])
    try:
        storage = vbk.storages[0]
        methods = set()
        store = storage._VBKStorage__store
        for index in range(store.count):
            record = VBKStoreRecord.from_bytes(await store.get(index))
            if record.stored_size > 0:
                methods.add(record.compression)
        assert SINGLE[name] in methods
        assert methods <= {SINGLE[name], COMPRESSION_NONE}
    finally:
        await close_all(sources)

@pytest.mark.asyncio
async def test_increment_alone():
    # an incremental backup lists its files, the disks can not be read without the full backup
    sources, vbk = await open_chain('chain_default', [1])
    try:
        disks = vbk.list_disks()
        assert len(disks) == 2
        assert vbk.get_entry(disks[0]).is_increment() is True
        with pytest.raises(FileNotFoundError):
            await vbk.open(disks[0])
        assert await vbk.list_partitions() == []
    finally:
        await close_all(sources)

@pytest.mark.asyncio
@pytest.mark.parametrize('verify', [True, False])
async def test_verify(verify, tmp_path):
    folder, scenario = get_scenario('block_wan')
    source = get_run_file(folder, scenario, 0)
    ds = await FileSource.from_file(str(source))
    storage = await VBKStorage.from_datasource(ds, verify)
    bank = storage.slot.banks[0]
    record = None
    store = storage._VBKStorage__store
    for index in range(store.count):
        record = VBKStoreRecord.from_bytes(await store.get(index))
        if record.stored_size > 0 and record.compression == COMPRESSION_LZ4:
            break
    await ds.close()
    assert storage.verify is verify

    async def read_all(path):
        ds = await FileSource.from_file(str(path))
        try:
            vbk = await VBK.from_datasource(ds, verify=verify)
            for path in vbk.list_files():
                ffile = await vbk.open(path)
                while len(await ffile.read(8 * 1024 * 1024)) > 0:
                    pass
        finally:
            await ds.close()

    # a byte of the metadata which is not used and the checksum of a block
    for name, offset in [('bank.vbk', bank.offset + PAGE_SIZE - 1), ('block.vbk', record.offset + 4)]:
        path = tmp_path.joinpath(name)
        shutil.copyfile(source, path)
        with open(path, 'r+b') as f:
            f.seek(offset)
            value = f.read(1)[0]
            f.seek(offset)
            f.write(bytes([value ^ 0xFF]))
        if verify is True:
            with pytest.raises(Exception, match='[Cc]hecksum'):
                await read_all(path)
        else:
            await read_all(path)

@pytest.mark.asyncio
async def test_damaged(tmp_path):
    folder, scenario = get_scenario('block_wan')
    source = get_run_file(folder, scenario, 0)
    data = bytearray(source.read_bytes())

    async def open_data(data):
        path = tmp_path.joinpath('test.vbk')
        path.write_bytes(data)
        ds = await FileSource.from_file(str(path))
        try:
            return await VBKStorage.is_vbk(ds), await VBKStorage.from_datasource(ds)
        finally:
            await ds.close()

    is_vbk, storage = await open_data(data)
    assert is_vbk is True
    generation = storage.slot.generation
    slot2 = SLOT_OFFSET + VBKSlot.get_padded_size(storage.slot.max_banks)

    # one of the slots is damaged: the other one is used
    for offset in [SLOT_OFFSET, slot2]:
        damaged = bytearray(data)
        damaged[offset + 30] ^= 1
        _, storage = await open_data(damaged)
        assert storage.slot.generation <= generation
        assert len(storage.root) > 0
    damaged = bytearray(data)
    damaged[SLOT_OFFSET + 30] ^= 1
    damaged[slot2 + 30] ^= 1
    with pytest.raises(Exception, match='no valid metadata slot'):
        await open_data(damaged)

    # the backup job did not finish the file
    damaged = bytearray(data)
    damaged[4:8] = b'\x00' * 4
    with pytest.raises(Exception, match='not finished'):
        await open_data(damaged)

    with pytest.raises(Exception, match='truncated'):
        await open_data(data[:SLOT_OFFSET + 100])
    with pytest.raises(Exception):
        await open_data(data[:slot2 + PAGE_SIZE])

    for other in [b'', b'\x00' * 8192, b'vhdxfile' + b'\x00' * 8192, os.urandom(8192)]:
        path = tmp_path.joinpath('other.bin')
        path.write_bytes(other)
        ds = await FileSource.from_file(str(path))
        assert await VBK.is_vbk(ds) is False
        await ds.close()

@pytest.mark.asyncio
async def test_is_vbk():
    folder, manifest = get_vbk_fixtures()
    count = 0
    for path in folder.rglob('*'):
        if path.suffix.lower() not in ('.vbk', '.vib', '.vbm', '.vhdx', '.json'):
            continue
        ds = await FileSource.from_file(str(path))
        assert await VBK.is_vbk(ds) is (path.suffix.lower() in ('.vbk', '.vib', '.vbm'))
        assert await VBM.is_vbm(ds) is (path.suffix.lower() == '.vbm')
        await ds.close()
        count += 1
    assert count > 30


#### encryption

@pytest.mark.asyncio
async def test_encrypted():
    folder, scenario = get_scenario('encrypted')
    sources, vbk = await open_chain('encrypted', [0])
    try:
        assert vbk.encrypted is True
        for func in [vbk.list_files, vbk.list_disks, lambda: vbk.get_entry('summary.xml')]:
            with pytest.raises(VBKEncryptedError):
                func()
        with pytest.raises(NotImplementedError):
            await vbk.open('summary.xml')
        with pytest.raises(VBKEncryptedError):
            await vbk.storages[0].read_block(0)
        assert 'Encrypted    : True' in str(vbk)

        verifier = await vbk.get_password_verifier()
        assert len(verifier.salt) == 64 and len(verifier.ciphertext) == 16
        assert verifier.check_password(scenario['settings']['password']) == 'PBKDF2-HMAC-SHA256-600000'
        assert verifier.check_password(scenario['settings']['password'] + 'x') is None
        assert verifier.kdf == 'PBKDF2-HMAC-SHA256-600000'
        assert verifier.to_hashcat() == '$vbk$*%s*600000*%s' % (verifier.salt.hex(), verifier.ciphertext.hex())
        assert str(verifier) == verifier.to_hashcat()
    finally:
        await close_all(sources)

@pytest.mark.asyncio
async def test_not_encrypted():
    sources, vbk = await open_chain('comp_none', [0])
    try:
        assert await vbk.get_password_verifier() is None
    finally:
        await close_all(sources)

def test_password_verifier_old():
    # the example of the hashcat mode of the older Veeam versions (31200), its password is password1
    salt = bytes.fromhex(HASHCAT_EXAMPLE.split('*')[1])
    ciphertext = bytes.fromhex(HASHCAT_EXAMPLE.split('*')[3])
    verifier = VBKPasswordVerifier(salt, ciphertext, b'')
    assert verifier.to_hashcat() == HASHCAT_EXAMPLE
    assert verifier.check_password('password1') == 'PBKDF2-HMAC-SHA1-10000'
    verifier.kdf = 'PBKDF2-HMAC-SHA1-10000'
    assert verifier.to_hashcat() == HASHCAT_EXAMPLE
    assert verifier.check_password('password1') == 'PBKDF2-HMAC-SHA1-10000'
    assert verifier.check_password('password2') is None


#### the vbm file

def test_vbm():
    data = b'''\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>
<BackupMeta Version="6"><Backup JobName="job"/><BackupMetaInfo><Storages>
<Storage Id="c" LinkId="b" FilePath="x\\c.vib" CreationTimeUtc="03/01/2024 10:00:00"/>
<Storage Id="a" FilePath="C:\\repo\\a.vbk" CreationTimeUtc="01/01/2024 10:00:00"/>
<Storage Id="b" LinkId="a" FilePath="b.vib" CreationTimeUtc="02/01/2024 10:00:00"/>
<Storage Id="e" LinkId="x" FilePath="e.vib" CreationTimeUtc="05/01/2024 10:00:00"/>
<Storage Id="d" FilePath="../d.vbk" CreationTimeUtc="04/01/2024 10:00:00"/>
</Storages></BackupMetaInfo></BackupMeta>'''
    vbm = VBM.from_bytes(data)
    assert vbm.job_name == 'job'
    # the files are next to the vbm file wherever they were when it was written
    assert [storage.filename for storage in vbm.storages] == ['a.vbk', 'b.vib', 'c.vib', 'd.vbk', 'e.vib']
    assert [storage.filename for storage in vbm.get_chain(vbm.storages[2])] == ['a.vbk', 'b.vib', 'c.vib']
    assert [storage.filename for storage in vbm.get_chain(vbm.storages[3])] == ['d.vbk']
    with pytest.raises(Exception, match='is based on'):
        vbm.get_chain(vbm.storages[4])
    assert 'a.vbk' in str(vbm)

    with pytest.raises(Exception, match='declarations'):
        VBM.from_bytes(b'<!DOCTYPE a [<!ENTITY b "c">]>' + data)

@pytest.mark.asyncio
async def test_vbm_missing_file(tmp_path):
    folder, scenario = get_scenario('chain_default')
    path = tmp_path.joinpath('test.vbm')
    shutil.copyfile(get_vbm_file(folder, scenario), path)
    shutil.copyfile(get_run_file(folder, scenario, 0), tmp_path.joinpath(get_run_file(folder, scenario, 0).name))
    ds = await FileSource.from_file(str(path))
    try:
        with pytest.raises(Exception):
            await VBK.from_datasource(ds)
        vbk = await VBK.from_datasource(ds, point=0)
        assert len(vbk.list_disks()) == 2
        await vbk.close()
    finally:
        await ds.close()


#### the metadata

def test_vector_slots():
    slots = list(vector_slots(3000))
    assert slots[:3] == [2, 3, 4]
    assert slots[509:512] == [511, 516, 517]
    assert len(set(slots)) == 3000 and slots == sorted(slots)
    # the first entry of each table page is the next table page
    assert all(slot % 512 != 0 for slot in slots)

def test_structures():
    record = bytearray(192)
    struct.pack_into('<II', record, 0, KIND_FILE, 5)
    record[8:13] = b'a.txt'
    struct.pack_into('<qQQ', record, 152, 7, 2, 12345)
    entry = VBKDirectoryRecord.from_bytes(bytes(record))
    assert (entry.kind, entry.name, entry.root, entry.count, entry.size) == (KIND_FILE, 'a.txt', 7, 2, 12345)
    assert page_bank(0x500000003) == 5 and page_index(0x500000003) == 3
    assert VBKSlot.get_padded_size(32512) == 524288


#### the decompressors

def lz4_sequence(literals, offset = None, length = 0):
    """Builds a sequence of an LZ4 block, the last one has no match"""
    def extra(value):
        res = b''
        while value >= 255:
            res += b'\xff'
            value -= 255
        return res + bytes([value])

    token = min(len(literals), 15) << 4
    res = b''
    if len(literals) >= 15:
        res += extra(len(literals) - 15)
    res += literals
    if offset is not None:
        token |= min(length - 4, 15)
        res += struct.pack('<H', offset)
        if length - 4 >= 15:
            res += extra(length - 4 - 15)
    return bytes([token]) + res

@pytest.mark.parametrize('decompress', LZ4_DECOMPRESSORS)
def test_lz4(decompress):
    assert decompress(lz4_sequence(b'hello'), 5) == b'hello'
    assert decompress(lz4_sequence(b'abc', 3, 9) + lz4_sequence(b'!'), 13) == b'abcabcabcabc!'
    assert decompress(lz4_sequence(b'a', 1, 700) + lz4_sequence(b'b' * 300), 1001) == b'a' * 701 + b'b' * 300
    assert decompress(lz4_sequence(b'x' * 20, 20, 4) + lz4_sequence(b''), 24) == b'x' * 24
    for data, size in [
        (lz4_sequence(b'hello'), 4), # more data than the size
        (lz4_sequence(b'hello'), 6), # less
        (lz4_sequence(b'abc', 4, 9) + lz4_sequence(b'!'), 13), # match before the start
        (lz4_sequence(b'abc', 0, 9) + lz4_sequence(b'!'), 13), # offset 0
        (lz4_sequence(b'hello')[:-1], 5), # truncated literals
        (lz4_sequence(b'abc', 3, 9)[:-1], 12), # truncated offset
        (b'\xf0\xff', 300), # truncated length
        (b'', 1),
    ]:
        with pytest.raises(DecompressionError):
            decompress(data, size)

def rle_compress(data):
    res = bytearray()
    pos = 0
    while pos < len(data):
        run = 1
        while run < 255 and pos + run < len(data) and data[pos + run] == data[pos]:
            run += 1
        if run == 1:
            res.append(data[pos])
        else:
            res += bytes([data[pos], data[pos], run])
        pos += run
    return bytes(res)

@pytest.mark.parametrize('decompress', RLE_DECOMPRESSORS)
def test_veeam_rle(decompress):
    assert decompress(b'abc', 3) == b'abc'
    assert decompress(b'aa\x02', 2) == b'aa'
    # the length has the pair in it
    assert decompress(b'aa\x01b', 2) == b'ab'
    assert decompress(b'xaa\x05y\x00\x00\xff', 262) == b'xaaaaay' + b'\x00' * 255
    rng = random.Random(5)
    for _ in range(200):
        data = b''.join(bytes([rng.randrange(4)]) * rng.choice([1, 1, 1, 2, 3, 254, 255, 256, 600]) for _ in range(rng.randrange(30)))
        assert decompress(rle_compress(data), len(data)) == data
    for data, size in [
        (b'abc', 2), (b'abc', 4),
        (b'aa', 2), # no length after the pair
        (b'aa\x05', 4), (b'aa\x05', 6),
    ]:
        with pytest.raises(DecompressionError):
            decompress(data, size)

@needs_native
def test_decompress_native_same():
    # the native decompressors accept and reject the same input as the python ones
    def run(func, data, size):
        try:
            return func(data, size)
        except (DecompressionError, IndexError):
            return None

    rng = random.Random(7)
    for decompress, decompress_py in [(lz4_decompress, lz4_decompress_py), (veeam_rle_decompress, veeam_rle_decompress_py)]:
        assert decompress is not decompress_py
        for _ in range(3000):
            data = bytes(rng.choice([0, 0, 1, 2, 0x10, 0x11, 0x44, 0xf0, 0xff, rng.randrange(256)]) for _ in range(rng.randrange(40)))
            for size in [rng.randrange(64), rng.randrange(2000)]:
                assert run(decompress, data, size) == run(decompress_py, data, size)

def test_crc32c():
    assert crc32c_py(b'123456789') == 0xE3069283
    assert compression.crc32c(b'123456789') == 0xE3069283
    assert compression.crc32c(b'') == 0
    data = random.Random(1).randbytes(100000)
    assert compression.crc32c(data) == crc32c_py(data)

def test_zstd():
    decompress = compression.get_zstd_decompressor()
    # a frame with one raw block
    frame = b'\x28\xb5\x2f\xfd' + b'\x20\x05' + b'\x29\x00\x00' + b'hello'
    assert decompress(frame, 5) == b'hello'

