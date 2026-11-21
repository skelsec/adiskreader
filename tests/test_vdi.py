"""
Tests of the VDI disks made by VirtualBox and qemu (make_qemu_fixtures.py), the expected contents is what qemu reads from them.
"""

import pytest
import os
import shutil
import struct
import subprocess
import uuid
from config import *
from checks import *
from test_qcow2 import check_disk, check_random_reads

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource import DataSource
from adiskreader.datasource.file import FileSource
from adiskreader.disks import Disk
from adiskreader.disks.raw import RAWDisk
from adiskreader.disks.vdi import VDIDisk, VDIHeader, VDIType
from adiskreader.filesystems.ntfs import NTFS

DISK_NAMES = ['vbox_dynamic', 'vbox_fixed', 'qemu_dynamic', 'qemu_fixed', 'vbox_written', 'vbox_discarded', 'vbox_diff1', 'vbox_diff2']

async def open_disk(name):
    """Opens the disk with its parents, returns the datasources, the disk and its entry in the manifest"""
    folder, images = get_qemu_fixtures()
    info = images[name]
    datasources = [await FileSource.from_file(str(folder.joinpath(file))) for file in info['files']]
    disk = await VDIDisk.from_chain(datasources)
    return datasources, disk, info

async def close_all(datasources):
    for ds in datasources:
        await ds.close()


def test_disk_names():
    _, images = get_qemu_fixtures()
    assert sorted(name for name, info in images.items() if info['format'] == 'vdi') == sorted(DISK_NAMES)

@pytest.mark.asyncio
@pytest.mark.parametrize('name', DISK_NAMES)
@pytest.mark.parametrize('lbas_per_read', [2048, 1000])
async def test_vdi_disk(name, lbas_per_read):
    datasources, disk, info = await open_disk(name)
    try:
        await check_disk(disk, get_qemu_expected(info['expected']), lbas_per_read)
    finally:
        await close_all(datasources)

@pytest.mark.asyncio
@pytest.mark.parametrize('name', DISK_NAMES)
async def test_vdi_random_reads(name):
    datasources, disk, info = await open_disk(name)
    try:
        await check_random_reads(disk, get_qemu_expected(info['expected']))
    finally:
        await close_all(datasources)

@pytest.mark.asyncio
async def test_vdi_header():
    for name, disktype in [('vbox_dynamic', VDIType.NORMAL), ('vbox_fixed', VDIType.FIXED), ('qemu_dynamic', VDIType.NORMAL), ('qemu_fixed', VDIType.FIXED), ('vbox_diff1', VDIType.DIFF)]:
        datasources, disk, info = await open_disk(name)
        header = disk.header
        assert header.version == (1, 1)
        assert header.type == disktype
        assert header.block_size == 1024 * 1024
        assert header.block_count == 6
        assert header.text.startswith('<<<')
        assert 'VDI' in str(disk)
        assert ('Parent' in str(disk)) is (disktype == VDIType.DIFF)
        await close_all(datasources)

@pytest.mark.skipif(shutil.which('vbox-img') is None, reason='vbox-img is not installed')
def test_vdi_uuid():
    # the uuids are read the same way as VirtualBox shows them
    folder, images = get_qemu_fixtures()
    for name in ['vbox_dynamic', 'vbox_diff2']:
        path = folder.joinpath(images[name]['file'])
        header = VDIHeader.from_bytes(path.read_bytes()[:1024])
        output = subprocess.run(['vbox-img', 'info', '--filename', str(path)], check=True, capture_output=True).stdout.decode()
        assert str(header.uuid) in output
        if name == 'vbox_diff2':
            assert str(header.uuid_parent) in output

@pytest.mark.asyncio
async def test_vdi_parents():
    # the files of the chain are given in any order
    folder, images = get_qemu_fixtures()
    info = images['vbox_diff2']
    for files in [info['files'], list(reversed(info['files'])), [info['files'][1], info['files'][0], info['files'][2]]]:
        datasources = [await FileSource.from_file(str(folder.joinpath(file))) for file in files]
        disk = await VDIDisk.from_chain(datasources)
        assert disk.header.uuid_parent == disk.parent.header.uuid
        assert disk.parent.header.uuid_parent == disk.parent.parent.header.uuid
        assert disk.parent.parent.parent is None
        await check_random_reads(disk, get_qemu_expected(info['expected']))
        await close_all(datasources)

    # a disk of the chain is missing
    datasources = [await FileSource.from_file(str(folder.joinpath(file))) for file in info['files'][:2]]
    with pytest.raises(Exception, match='parent of the VDI disk'):
        await VDIDisk.from_chain(datasources)
    await close_all(datasources)
    # two disks which have nothing to do with each other
    datasources = [await FileSource.from_file(str(folder.joinpath(images[name]['file']))) for name in ['vbox_dynamic', 'vbox_fixed']]
    with pytest.raises(Exception, match='not one chain'):
        await VDIDisk.from_chain(datasources)
    await close_all(datasources)

@pytest.mark.asyncio
async def test_vdi_without_parent():
    # only the blocks stored in the differencing disk are read, the rest is zeroes
    folder, images = get_qemu_fixtures()
    info = images['vbox_diff1']
    expected = get_qemu_expected(info['expected'])
    ds = await FileSource.from_file(str(folder.joinpath(info['file'])))
    disk = await VDIDisk.from_datasource(ds)
    assert disk.needs_parent() is True
    assert disk.parent is None
    block = 1024 * 1024
    data = await disk.read_sectors(0, disk.capacity)
    assert data[:block] == b'\x00' * block
    assert data[block:3*block] == expected[block:3*block]
    assert data != expected
    await ds.close()

@pytest.mark.asyncio
async def test_vdi_other_parent(tmp_path):
    # the parent can be any disk, here the raw image of the parent
    folder, images = get_qemu_fixtures()
    info = images['vbox_diff1']
    rawpath = tmp_path.joinpath('parent.raw')
    rawpath.write_bytes(get_qemu_expected(images['vbox_dynamic']['expected']))
    parent_ds = await FileSource.from_file(str(rawpath))
    ds = await FileSource.from_file(str(folder.joinpath(info['file'])))
    disk = await VDIDisk.from_datasource(ds, parent = await RAWDisk.from_datasource(parent_ds))
    await check_random_reads(disk, get_qemu_expected(info['expected']))
    await disk.close()
    await ds.close()
    await parent_ds.close()

@pytest.mark.asyncio
async def test_vdi_detection(tmp_path):
    folder, images = get_qemu_fixtures()
    info = images['vbox_dynamic']
    ds = await DataSource.from_url(str(folder.joinpath(info['file'])))
    disk = await Disk.from_datasource(ds)
    assert isinstance(disk, VDIDisk)
    await ds.close()

    # by the signature when the extension is not known
    shutil.copy(folder.joinpath(info['file']), tmp_path.joinpath('disk.bin'))
    ds = await DataSource.from_url(str(tmp_path.joinpath('disk.bin')))
    disk = await Disk.from_datasource(ds)
    assert isinstance(disk, VDIDisk)
    await ds.close()

@pytest.mark.asyncio
async def test_vdi_old_header(tmp_path):
    # version 0.1 of the format: the header has no offsets, the block map and the data follow it. Built here, no tool writes it anymore.
    block_size = 4096
    blocks = [os.urandom(block_size), os.urandom(block_size)]
    disk_size = 5 * block_size - 512
    header = b'<<< InnoTek VirtualBox Disk Image >>>\n'.ljust(64, b'\x00') + struct.pack('<II', 0xBEDA107F, 0x00000001)
    header += struct.pack('<II', 1, 0) + b'comment'.ljust(256, b'\x00') + struct.pack('<IIII', 1, 16, 63, 512)
    header += struct.pack('<QIII', disk_size, block_size, 5, 2)
    diskuuid = uuid.uuid4()
    header += diskuuid.bytes_le + uuid.uuid4().bytes_le + bytes(16)
    assert len(header) == 72 + 348
    block_map = struct.pack('<5I', 0xFFFFFFFF, 1, 0xFFFFFFFE, 0, 0xFFFFFFFF)
    path = tmp_path.joinpath('old.vdi')
    path.write_bytes(header + block_map + blocks[0] + blocks[1])

    ds = await FileSource.from_file(str(path))
    disk = await VDIDisk.from_datasource(ds)
    assert disk.header.version == (0, 1)
    assert disk.header.comment == 'comment'
    assert disk.header.uuid == diskuuid
    assert disk.needs_parent() is False
    expected = (bytes(block_size) + blocks[1] + bytes(block_size) + blocks[0] + bytes(block_size))[:disk_size]
    await check_disk(disk, expected, 3)
    await ds.close()

@pytest.mark.asyncio
async def test_vdi_invalid(tmp_path):
    folder, images = get_qemu_fixtures()
    data = folder.joinpath(images['vbox_dynamic']['file']).read_bytes()
    for patches, size, error, message in [
            ([(64, b'\x00\x00\x00\x00')], None, Exception, 'Not a VDI file'),
            ([(68, struct.pack('<I', 0x00020000))], None, NotImplementedError, 'version'),
            ([(376, struct.pack('<I', 0))], None, Exception, 'Invalid VDI header'),
            ([(384, struct.pack('<I', 2))], None, Exception, 'larger than its blocks'),
            ([(340, struct.pack('<I', 0x7fffff00))], None, Exception, 'truncated'),
            ([], 100, Exception, 'Not a VDI file'),
        ]:
        patched = bytearray(data[:size])
        for offset, value in patches:
            patched[offset:offset+len(value)] = value
        path = tmp_path.joinpath('disk.vdi')
        path.write_bytes(patched)
        ds = await FileSource.from_file(str(path))
        try:
            with pytest.raises(error, match=message):
                await VDIDisk.from_datasource(ds)
        finally:
            await ds.close()

@pytest.mark.skipif(shutil.which('qemu-img') is None, reason='qemu-img is not installed')
@pytest.mark.asyncio
@pytest.mark.parametrize('options', [[], ['-o', 'static=on']])
async def test_vdi_ntfs(tmp_path, options):
    # the ntfs test image converted to vdi has all files of the manifest
    path = tmp_path.joinpath('ntfs.vdi')
    subprocess.run(['qemu-img', 'convert', '-f', 'raw', '-O', 'vdi'] + options + [str(get_image(NTFS_TEST_FILE_GZ)), str(path)], check=True)
    ds = await FileSource.from_file(str(path))
    disk = await VDIDisk.from_datasource(ds)
    try:
        partitions = await disk.list_partitions()
        fs = await NTFS.from_partition(partitions[0])
        manifest = load_manifest('ntfs')
        await check_file_list(fs, manifest)
        await check_file_fullread(fs, manifest)
    finally:
        await ds.close()
