"""
Tests of the QCOW2 disks made by qemu (make_qemu_fixtures.py), the expected contents is what qemu reads from them.
"""

import pytest
import os
import random
import shutil
import struct
import subprocess
from config import *
from checks import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource import DataSource
from adiskreader.datasource.file import FileSource
from adiskreader.disks import Disk
from adiskreader.disks.raw import RAWDisk
from adiskreader.disks.vmdk import VMDKDisk
from adiskreader.disks.qcow2 import QCOW2Disk, get_zstd_decompressor
from adiskreader.filesystems.ntfs import NTFS

DISK_NAMES = [
    'v3', 'v2', 'cluster512', 'cluster2m', 'compressed', 'compressed_v2_4k', 'compressed_2m', 'zstd',
    'extl2', 'extl2_compressed', 'prealloc', 'dirty', 'zeroes', 'zeroes_v2',
    'chain_base', 'chain_mid', 'chain_top', 'extl2_overlay', 'raw_overlay', 'vmdk_overlay',
    'datafile', 'datafile_raw', 'snapshots', 'snapshots_compressed',
]
SNAPSHOTS = [('snapshots', 'first'), ('snapshots', 'second snapshot'), ('snapshots_compressed', 'one')]

def has_zstd():
    try:
        get_zstd_decompressor()
        return True
    except NotImplementedError:
        return False

def get_info(name):
    folder, images = get_qemu_fixtures()
    info = images[name]
    if info['requires'] == 'zstd' and has_zstd() is False:
        pytest.skip('no zstd module')
    return folder.joinpath(info['file']), info

async def open_disk(name):
    path, info = get_info(name)
    ds = await FileSource.from_file(str(path))
    disk = await QCOW2Disk.from_datasource(ds)
    return ds, disk, info

async def check_disk(disk, expected, lbas_per_read):
    assert disk.capacity * 512 == len(expected)
    for lba in range(0, disk.capacity, lbas_per_read):
        data = await disk.read_LBAs(list(range(lba, min(disk.capacity, lba + lbas_per_read))))
        assert data == expected[lba*512:(lba + lbas_per_read)*512]

async def check_random_reads(disk, expected):
    rng = random.Random(0)
    for _ in range(300):
        lba = rng.randint(0, disk.capacity - 1)
        count = rng.choice([1, 1, 2, 7, 8, 127, 128, 129, 5000])
        assert await disk.read_sectors(lba, count) == expected[lba*512:(lba+count)*512]
        assert await disk.read_LBA(lba) == expected[lba*512:(lba+1)*512]
    # the last sectors, the read is truncated at the end of the disk
    assert await disk.read_sectors(disk.capacity - 3, 10) == expected[-3*512:]
    assert await disk.read_sectors(disk.capacity, 10) == b''
    # LBAs which are not next to each other
    lbas = [5, 6, 7, 100, 3, 4, 9000, 9001]
    assert await disk.read_LBAs(lbas) == b''.join(expected[lba*512:(lba+1)*512] for lba in lbas)


def test_disk_names():
    _, images = get_qemu_fixtures()
    assert sorted(name for name, info in images.items() if info['format'] == 'qcow2') == sorted(DISK_NAMES)

@pytest.mark.asyncio
@pytest.mark.parametrize('name', DISK_NAMES)
@pytest.mark.parametrize('lbas_per_read', [2048, 1000])
async def test_qcow2_disk(name, lbas_per_read):
    ds, disk, info = await open_disk(name)
    try:
        await check_disk(disk, get_qemu_expected(info['expected']), lbas_per_read)
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('name', DISK_NAMES)
async def test_qcow2_random_reads(name):
    ds, disk, info = await open_disk(name)
    try:
        await check_random_reads(disk, get_qemu_expected(info['expected']))
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('name, snapshot', SNAPSHOTS)
async def test_qcow2_snapshot(name, snapshot):
    ds, disk, info = await open_disk(name)
    try:
        assert [s.name for s in disk.snapshots] == list(info['snapshots'].keys())
        expected = get_qemu_expected(info['snapshots'][snapshot])
        assert expected != get_qemu_expected(info['expected'])
        snapdisk = await disk.open_snapshot(snapshot)
        assert snapdisk.snapshot.name == snapshot
        # qemu converts the snapshot using the size the disk has now, the disk of the snapshot has the size it had then
        assert expected[snapdisk.size:].count(0) == len(expected) - snapdisk.size
        expected = expected[:snapdisk.size]
        await check_disk(snapdisk, expected, 2048)
        await check_random_reads(snapdisk, expected)
        assert 'Snapshot   : %s' % snapshot in str(snapdisk)
        assert snapshot in str(disk)
        await snapdisk.close()
        # the disk is still usable after the snapshot is closed
        await check_random_reads(disk, get_qemu_expected(info['expected']))
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
async def test_qcow2_snapshot_by_id():
    ds, disk, info = await open_disk('snapshots')
    try:
        assert [s.id for s in disk.snapshots] == ['1', '2']
        assert disk.snapshots[0].date.year >= 2024
        # the disk was resized after the first snapshot
        assert disk.snapshots[0].disk_size < disk.snapshots[1].disk_size == disk.size
        snapdisk = await disk.open_snapshot('2')
        assert snapdisk.snapshot.name == 'second snapshot'
        with pytest.raises(Exception, match='No such snapshot'):
            await disk.open_snapshot('third')
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
async def test_qcow2_header():
    for name, version, cluster_size in [('v2', 2, 65536), ('v3', 3, 65536), ('cluster512', 3, 512), ('cluster2m', 3, 2*1024*1024)]:
        ds, disk, info = await open_disk(name)
        assert disk.header.version == version
        assert disk.cluster_size == cluster_size
        assert disk.parent is None
        assert 'QCOW2' in str(disk)
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
async def test_qcow2_parents():
    ds, disk, _ = await open_disk('chain_top')
    try:
        assert disk.header.backing_file == 'chain_mid.qcow2'
        assert disk.header.backing_format == 'qcow2'
        assert isinstance(disk.parent, QCOW2Disk)
        assert disk.parent.cluster_size == 4096
        assert disk.parent.parent.parent is None
        # the disk is larger than its backing file
        assert disk.capacity > disk.parent.capacity
        assert 'Parent' in str(disk)
        assert 'Parent' not in str(disk.parent.parent)
    finally:
        await disk.close()
        await ds.close()

    ds, disk, _ = await open_disk('raw_overlay')
    assert isinstance(disk.parent, RAWDisk)
    await disk.close()
    await ds.close()
    ds, disk, _ = await open_disk('vmdk_overlay')
    assert isinstance(disk.parent, VMDKDisk)
    await disk.close()
    await ds.close()

@pytest.mark.asyncio
async def test_qcow2_data_file():
    ds, disk, _ = await open_disk('datafile_raw')
    assert disk.header.data_file == 'datafile_raw.data'
    assert 'Data file' in str(disk)
    await disk.close()
    await ds.close()

def patched(tmp_path, name, patches, files = []):
    """Copy of a test disk with the bytes at the given offsets replaced"""
    path, info = get_info(name)
    data = bytearray(path.read_bytes())
    for offset, value in patches:
        data[offset:offset+len(value)] = value
    tmp_path.joinpath('disk.qcow2').write_bytes(data)
    for file in files:
        shutil.copy(path.with_name(file), tmp_path.joinpath(file))
    return str(tmp_path.joinpath('disk.qcow2')), info

@pytest.mark.asyncio
async def test_qcow2_backing_format_detection(tmp_path):
    # without the format of the backing file in the header its type is detected: the extension is removed by changing its type
    for name, backing, parenttype in [('chain_mid', 'chain_base.qcow2', QCOW2Disk), ('vmdk_overlay', 'backing.vmdk', VMDKDisk), ('raw_overlay', 'backing.img', RAWDisk)]:
        path, info = get_info(name)
        data = path.read_bytes()
        pos = data.index(struct.pack('>I', 0xE2792ACA))
        assert pos < 512
        filepath, info = patched(tmp_path, name, [(pos, b'\x12\x34\x56\x78')], [backing])
        ds = await FileSource.from_file(filepath)
        disk = await QCOW2Disk.from_datasource(ds)
        try:
            assert disk.header.backing_format is None
            assert isinstance(disk.parent, parenttype)
            await check_random_reads(disk, get_qemu_expected(info['expected']))
        finally:
            await disk.close()
            await ds.close()

@pytest.mark.asyncio
async def test_qcow2_detection(tmp_path):
    # by the extension, like in the console
    path, info = get_info('compressed')
    ds = await DataSource.from_url(str(path))
    disk = await Disk.from_datasource(ds)
    assert isinstance(disk, QCOW2Disk)
    await disk.close()
    await ds.close()

    # the disks made by qemu are often named .img, those are detected by their signature
    shutil.copy(path, tmp_path.joinpath('disk.img'))
    ds = await DataSource.from_url(str(tmp_path.joinpath('disk.img')))
    disk = await Disk.from_datasource(ds)
    assert isinstance(disk, QCOW2Disk)
    await check_random_reads(disk, get_qemu_expected(info['expected']))
    await disk.close()
    await ds.close()

    ds = await DataSource.from_url('file+qcow2://' + str(tmp_path.joinpath('disk.img')))
    disk = await Disk.from_datasource(ds)
    assert isinstance(disk, QCOW2Disk)
    await disk.close()
    await ds.close()

@pytest.mark.asyncio
async def test_qcow2_unsupported(tmp_path):
    for patches, error, message in [
            ([(32, struct.pack('>I', 1))], NotImplementedError, 'Encrypted'),
            ([(32, struct.pack('>I', 2))], NotImplementedError, 'Encrypted'),
            ([(72, struct.pack('>Q', 1 << 5))], NotImplementedError, 'features'),
            ([(4, struct.pack('>I', 1))], NotImplementedError, 'version'),
            ([(4, struct.pack('>I', 4))], NotImplementedError, 'version'),
            ([(20, struct.pack('>I', 30))], Exception, 'cluster size'),
            ([(36, struct.pack('>I', 0x7fffffff))], Exception, 'L1 table'),
            ([(40, struct.pack('>Q', 1 << 40))], Exception, 'truncated'),
            ([(0, b'QFI\x00')], Exception, 'Not a QCOW2 file'),
        ]:
        filepath, _ = patched(tmp_path, 'v3', patches)
        ds = await FileSource.from_file(filepath)
        try:
            with pytest.raises(error, match=message):
                await QCOW2Disk.from_datasource(ds)
        finally:
            await ds.close()

@pytest.mark.asyncio
async def test_qcow2_zstd_without_module(tmp_path, monkeypatch):
    import adiskreader.disks.qcow2 as qcow2
    def missing():
        raise NotImplementedError('needs zstd')
    monkeypatch.setattr(qcow2, 'get_zstd_decompressor', missing)
    folder, images = get_qemu_fixtures()
    ds = await FileSource.from_file(str(folder.joinpath(images['zstd']['file'])))
    try:
        with pytest.raises(NotImplementedError, match='zstd'):
            await QCOW2Disk.from_datasource(ds)
    finally:
        await ds.close()

@pytest.mark.asyncio
async def test_qcow2_missing_files(tmp_path):
    for name in ['chain_mid', 'datafile']:
        filepath, _ = patched(tmp_path, name, [])
        ds = await FileSource.from_file(filepath)
        try:
            with pytest.raises(Exception, match='not found|No such file'):
                await QCOW2Disk.from_datasource(ds)
        finally:
            await ds.close()

@pytest.mark.asyncio
async def test_qcow2_truncated(tmp_path):
    # the tables are missing: the error is raised either when the disk is opened or when it is read
    path, info = get_info('v3')
    data = path.read_bytes()
    for size in [50, 100, 0x20000, 0x30800]:
        tmp_path.joinpath('disk.qcow2').write_bytes(data[:size])
        ds = await FileSource.from_file(str(tmp_path.joinpath('disk.qcow2')))
        try:
            with pytest.raises(Exception):
                disk = await QCOW2Disk.from_datasource(ds)
                for lba in range(0, disk.capacity, 128):
                    await disk.read_sectors(lba, 128)
                # nothing is missing which the disk needs
                assert size > 100
                raise Exception('readable')
        finally:
            await ds.close()

@pytest.mark.skipif(shutil.which('qemu-img') is None, reason='qemu-img is not installed')
@pytest.mark.asyncio
@pytest.mark.parametrize('options', [['-c'], ['-o', 'cluster_size=4k,extended_l2=off'], ['-o', 'extended_l2=on']])
async def test_qcow2_ntfs(tmp_path, options):
    # the ntfs test image converted to qcow2 has all files of the manifest
    path = tmp_path.joinpath('ntfs.qcow2')
    subprocess.run(['qemu-img', 'convert', '-f', 'raw', '-O', 'qcow2'] + options + [str(get_image(NTFS_TEST_FILE_GZ)), str(path)], check=True)
    ds = await FileSource.from_file(str(path))
    disk = await QCOW2Disk.from_datasource(ds)
    try:
        partitions = await disk.list_partitions()
        fs = await NTFS.from_partition(partitions[0])
        manifest = load_manifest('ntfs')
        await check_file_list(fs, manifest)
        await check_file_fullread(fs, manifest)
    finally:
        await disk.close()
        await ds.close()
