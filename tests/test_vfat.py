import pytest
import os
from config import *
from checks import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource.file import FileSource
from adiskreader.datasource.gzipfile import GzipFileSource
from adiskreader.disks.raw import RAWDisk
from adiskreader.filesystems.fat import FAT


# (manifest name, image, read the gz directly)
IMAGES = [
    pytest.param('vfat_16', VFAT_16_TEST_FILE_GZ, False, id='vfat_16_raw'),
    pytest.param('vfat_16', VFAT_16_TEST_FILE_GZ, True, id='vfat_16_gz'),
    pytest.param('vfat_32', VFAT_32_TEST_FILE_GZ, False, id='vfat_32_raw'),
    pytest.param('vfat_32', VFAT_32_TEST_FILE_GZ, True, id='vfat_32_gz'),
]

async def open_fs(image, compressed):
    filepath = get_image(image, compressed=compressed)
    if compressed is True:
        ds = await GzipFileSource.from_file(filepath)
    else:
        ds = await FileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await FAT.from_partition(partitions[0])
    assert fs is not None
    return fs


@pytest.mark.asyncio
@pytest.mark.parametrize('manifest, image, compressed', IMAGES)
async def test_vfat_init(manifest, image, compressed):
    fs = await open_fs(image, compressed)
    test = fs._is_dirty()

@pytest.mark.asyncio
@pytest.mark.parametrize('manifest, image, compressed', IMAGES)
async def test_vfat_file_list(manifest, image, compressed):
    fs = await open_fs(image, compressed)
    await check_file_list(fs, load_manifest(manifest))

@pytest.mark.asyncio
@pytest.mark.parametrize('manifest, image, compressed', IMAGES)
async def test_vfat_file_open(manifest, image, compressed):
    fs = await open_fs(image, compressed)
    await check_file_open(fs, load_manifest(manifest))

@pytest.mark.asyncio
@pytest.mark.parametrize('manifest, image, compressed', IMAGES)
async def test_vfat_file_stat(manifest, image, compressed):
    fs = await open_fs(image, compressed)
    await check_file_stat(fs, load_manifest(manifest))

@pytest.mark.asyncio
@pytest.mark.parametrize('manifest, image, compressed', IMAGES)
async def test_vfat_file_fullread(manifest, image, compressed):
    fs = await open_fs(image, compressed)
    await check_file_fullread(fs, load_manifest(manifest))

@pytest.mark.asyncio
@pytest.mark.parametrize('manifest, image, compressed', IMAGES)
async def test_vfat_file_ops(manifest, image, compressed):
    fs = await open_fs(image, compressed)
    await check_file_ops(fs, load_manifest(manifest))
