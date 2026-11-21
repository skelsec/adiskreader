import pytest
import os
from config import *
from checks import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource.file import FileSource
from adiskreader.datasource.gzipfile import GzipFileSource
from adiskreader.disks.raw import RAWDisk
from adiskreader.disks.vhdx import VHDXDisk
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

# the vhdx with the log is a different image, but holds all files of the manifest
DISKS = [open_raw, open_raw_gz, open_vhdx_dynamic, open_vhdx_fixed, open_vhdx_log]

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
