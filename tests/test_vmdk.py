import pytest
import os
import random
import shutil
import subprocess
import tempfile
from config import *
from vmdk_builder import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource import DataSource
from adiskreader.datasource.file import FileSource
from adiskreader.disks import Disk
from adiskreader.disks.vmdk import VMDKDisk
from adiskreader.disks.vmdk.structures.descriptorfile import DescriptorFile
from adiskreader.disks.vmdk.structures.extent import trim_runs

DISK_NAMES = [
    'monolithicSparse', 'streamOptimized', 'snapshot', 'snapshot2',
    'vmware', 'vmware-000001', 'vmware_ova',
    'flat', 'vmfs', 'split_sparse', 'multi', 'multi_zero', 'stream_footer',
    'cowd', 'cowd2', 'sesparse',
]

def test_disk_names():
    assert sorted(build_test_disks().keys()) == sorted(DISK_NAMES)

async def open_disk(name):
    filename, expected, _ = build_test_disks()[name]
    ds = await FileSource.from_file(str(TESTFILES_FOLDER_PATH.joinpath(filename)))
    disk = await VMDKDisk.from_datasource(ds)
    return ds, disk, expected


@pytest.mark.asyncio
@pytest.mark.parametrize('name', DISK_NAMES)
@pytest.mark.parametrize('lbas_per_read', [2048, 1000])
async def test_vmdk_disk(name, lbas_per_read):
    ds, disk, expected = await open_disk(name)
    try:
        assert disk.capacity * 512 == len(expected)
        for lba in range(0, disk.capacity, lbas_per_read):
            data = await disk.read_LBAs(list(range(lba, min(disk.capacity, lba + lbas_per_read))))
            assert data == expected[lba*512:(lba + lbas_per_read)*512]
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('name', DISK_NAMES)
async def test_vmdk_random_reads(name):
    ds, disk, expected = await open_disk(name)
    try:
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
        lbas = [5, 6, 7, 100, 3, 4, 50000, 50001]
        assert await disk.read_LBAs(lbas) == b''.join(expected[lba*512:(lba+1)*512] for lba in lbas)
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.skipif(shutil.which('qemu-img') is None, reason='qemu-img is not installed')
@pytest.mark.parametrize('name', DISK_NAMES)
def test_vmdk_expected_matches_qemu(name):
    # the expected data of the test disks is what qemu reads from them
    filename, expected, qemu = build_test_disks()[name]
    if qemu is False:
        pytest.skip('not readable by qemu')
    with tempfile.TemporaryDirectory() as tmpdir:
        rawpath = os.path.join(tmpdir, 'disk.raw')
        subprocess.run(['qemu-img', 'convert', '-f', 'vmdk', '-O', 'raw', str(TESTFILES_FOLDER_PATH.joinpath(filename)), rawpath], check=True)
        with open(rawpath, 'rb') as f:
            assert f.read() == expected

@pytest.mark.asyncio
async def test_vmdk_parents():
    ds, disk, _ = await open_disk('snapshot2')
    try:
        assert disk.parent.descriptorfile.parent_filename == 'ntfs_test_monolithicSparse.vmdk'
        assert disk.parent.parent.parent is None
        assert 'Parent' in str(disk)
        assert 'Parent' not in str(disk.parent.parent)
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
async def test_vmdk_detection():
    # the disk type is detected from the extension, like in the console
    filename, expected, _ = build_test_disks()['cowd']
    ds = await DataSource.from_url(str(TESTFILES_FOLDER_PATH.joinpath(filename)))
    try:
        disk = await Disk.from_datasource(ds)
        assert isinstance(disk, VMDKDisk)
        partitions = await disk.list_partitions()
        assert len(partitions) > 0
        await disk.close()
    finally:
        await ds.close()

@pytest.mark.asyncio
async def test_vmdk_extent_file():
    # the delta file of a snapshot opened without its descriptor has no parent, only the changed sectors are there
    build_test_disks()
    ds = await FileSource.from_file(str(TESTFILES_FOLDER_PATH.joinpath('built_cowd-delta.vmdk')))
    try:
        disk = await VMDKDisk.from_datasource(ds)
        assert disk.capacity == len(build_test_disks()['cowd'][1]) // 512
        assert await disk.read_sectors(4, 4) == b'\x00'*512 + b'\x01'*1024 + b'\x00'*512
    finally:
        await ds.close()

@pytest.mark.asyncio
async def test_vmdk_missing_files(tmp_path):
    for descriptor in [
        build_descriptor([(100, 'FLAT', 'doesnotexist-flat.vmdk', 0)], 'monolithicFlat'),
        build_descriptor([(100, 'ZERO', None, None)], 'custom', parent = 'doesnotexist.vmdk'),
    ]:
        path = tmp_path.joinpath('disk.vmdk')
        path.write_bytes(descriptor)
        ds = await FileSource.from_file(str(path))
        try:
            with pytest.raises(FileNotFoundError):
                disk = await VMDKDisk.from_datasource(ds)
                await disk.read_LBA(0)
        finally:
            await ds.close()

@pytest.mark.asyncio
async def test_vmdk_unsupported_extent(tmp_path):
    path = tmp_path.joinpath('disk.vmdk')
    path.write_bytes(build_descriptor([(100, 'ZERO', None, None), (100, 'VMFSRDM', 'device', None)], 'vmfsRawDeviceMap'))
    ds = await FileSource.from_file(str(path))
    try:
        disk = await VMDKDisk.from_datasource(ds)
        assert await disk.read_sectors(0, 100) == b'\x00' * 51200
        with pytest.raises(NotImplementedError):
            await disk.read_sectors(100, 1)
    finally:
        await ds.close()

@pytest.mark.asyncio
async def test_vmdk_not_a_vmdk():
    ds = await FileSource.from_file(str(get_image(NTFS_TEST_FILE_GZ)))
    try:
        with pytest.raises(Exception, match='Invalid descriptor file'):
            await VMDKDisk.from_datasource(ds)
    finally:
        await ds.close()

def test_descriptor():
    df = DescriptorFile.from_bytes(
        b'# Disk DescriptorFile\r\nversion=1\r\nCID=fffffffe\r\nparentCID=ffffffff\r\ncreateType="vmfs"\r\n\r\n'
        b'RW 100 VMFS "my disk-flat.vmdk"\r\nRDONLY 20 ZERO\r\nrw 30 FLAT "b.vmdk" 128\r\n'
        b'ddb.adapterType = "lsilogic"\r\n' + b'\x00' * 100 + b'garbage'
    )
    assert df.create_type == 'vmfs'
    assert df.parent_filename is None
    assert df.entries['ddb.adapterType'] == 'lsilogic'
    assert [(e.access, e.size_in_sector, e.type, e.filename, e.offset) for e in df.extentdescriptors] == [
        ('RW', 100, 'VMFS', 'my disk-flat.vmdk', 0),
        ('RDONLY', 20, 'ZERO', None, 0),
        ('RW', 30, 'FLAT', 'b.vmdk', 128),
    ]
    df = DescriptorFile.from_bytes(b'createType="vmfsSparse"\nparentCID=1234abcd\nparentFileNameHint="base.vmdk"\nRW 1 VMFSSPARSE "a-delta.vmdk"\n')
    assert df.parent_filename == 'base.vmdk'

def test_trim_runs():
    runs = [(2, b'a'*1024), (3, None), (1, b'b'*512)]
    assert trim_runs(runs, 0, 6) == runs
    assert trim_runs(runs, 1, 3) == [(1, b'a'*512), (2, None)]
    assert trim_runs(runs, 4, 5) == [(1, None), (1, b'b'*512)]
    # data shorter than the run is padded
    assert trim_runs([(2, b'a'*600)], 0, 2) == [(2, b'a'*600 + b'\x00'*424)]
