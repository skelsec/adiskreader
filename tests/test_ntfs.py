import pytest
import os
from config import *
from checks import *
from vmdk_builder import build_test_disks

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource.file import FileSource
from adiskreader.datasource.gzipfile import GzipFileSource
from adiskreader.disks.raw import RAWDisk
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.disks.vmdk import VMDKDisk
from adiskreader.filesystems.ntfs import NTFS


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
