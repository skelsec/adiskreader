"""
Tests of the datasource backed by a file of a filesystem (disk images stored inside other images)
"""

import pytest
import hashlib
import os
import random
from config import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource.file import FileSource
from adiskreader.datasource.fsfile import FSFileSource, CHUNK_SIZE
from adiskreader.disks import Disk
from adiskreader.disks.vhd import VHDDisk
from adiskreader.filesystems.fat import FAT
from adiskreader.filesystems.ntfs import NTFS

FILESYSTEMS = [
    pytest.param(NTFS_TEST_FILE_GZ, 'ntfs', id='ntfs'),
    pytest.param(VFAT_16_TEST_FILE_GZ, 'vfat_16', id='vfat_16'),
    pytest.param(VFAT_32_TEST_FILE_GZ, 'vfat_32', id='vfat_32'),
]

async def open_fs(image):
    ds = await FileSource.from_file(str(get_image(image)))
    try:
        disk = await Disk.from_datasource(ds)
        partitions = await disk.list_partitions()
        return ds, await partitions[0].mount()
    except Exception:
        await ds.close()
        raise


@pytest.mark.asyncio
@pytest.mark.parametrize('image, manifest_name', FILESYSTEMS)
async def test_fsfile_read(image, manifest_name):
    manifest = load_manifest(manifest_name)
    rng = random.Random(0)
    ds, fs = await open_fs(image)
    try:
        for path, expected in manifest['files'].items():
            size = expected['size']
            file = await fs.open_datasource(path)
            try:
                assert isinstance(file, FSFileSource)
                assert await file.tell() == 0
                data = await file.read(size + 100)
                assert len(data) == size
                assert hashlib.sha256(data).hexdigest() == expected['sha256']
                assert await file.tell() == size
                assert await file.read(100) == b''

                # sequential reads continue where the previous one ended
                await file.seek(0, 0)
                assert await file.read(10) + await file.read(size) == data
                await file.seek(-min(size, 7), 2)
                assert await file.read(100) == data[-min(size, 7):]
                await file.seek(0, 0)
                await file.seek(min(size, 5), 1)
                assert await file.read(3) == data[5:8]

                for _ in range(20):
                    pos = rng.randint(0, size)
                    length = rng.randint(0, min(size - pos, 3 * CHUNK_SIZE))
                    await file.seek(pos, 0)
                    assert await file.read(length) == data[pos:pos+length]
            finally:
                await file.close()
    finally:
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('image, manifest_name', FILESYSTEMS)
async def test_fsfile_open_sibling(image, manifest_name):
    manifest = load_manifest(manifest_name)
    # a file in a directory and one in the root
    nested = [path for path in manifest['files'] if '\\' in path][0]
    top = [path for path in manifest['files'] if '\\' not in path][0]
    ds, fs = await open_fs(image)
    try:
        file = await fs.open_datasource(top)
        try:
            sibling = await file.open_sibling(nested.replace('\\', '/'))
            try:
                assert sibling.config['path'] == '\\' + nested
                assert hashlib.sha256(await sibling.read(manifest['files'][nested]['size'])).hexdigest() == manifest['files'][nested]['sha256']
                for name in ['..\\' + top, '.\\..\\..\\' + top]:
                    sibling2 = await sibling.open_sibling(name)
                    try:
                        assert sibling2.config['path'] == '\\' + top
                        assert hashlib.sha256(await sibling2.read(manifest['files'][top]['size'])).hexdigest() == manifest['files'][top]['sha256']
                    finally:
                        await sibling2.close()
            finally:
                await sibling.close()
            with pytest.raises(Exception):
                await file.open_sibling('doesnotexist.img')
            with pytest.raises(Exception):
                await file.open_sibling('\\' + top)
        finally:
            await file.close()
    finally:
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['vhd_base', 'vhd_child1'])
async def test_fsfile_nested_disk(name):
    # the disk and its parent are read from the ntfs filesystem of another image
    path, image = get_diff_image(name)
    expected = get_diff_expected(name)
    ds, fs = await open_fs(NESTED_NTFS_TEST_FILE_GZ)
    try:
        inner_ds = await fs.open_datasource('\\' + path.name)
        try:
            disk = await Disk.from_datasource(inner_ds)
            assert isinstance(disk, VHDDisk)
            assert (disk.parent is not None) == (name == 'vhd_child1')
            try:
                for lba in range(0, disk.capacity, 4096):
                    assert await disk.read_sectors(lba, 4096) == expected[lba*512:(lba+4096)*512]

                partitions = await disk.list_partitions()
                inner_fs = await partitions[0].mount()
                assert isinstance(inner_fs, NTFS)
                file = await inner_fs.open('hello.txt')
                assert (await file.read()).strip() == (b'child1' if name == 'vhd_child1' else b'base')
            finally:
                await disk.close()
        finally:
            await inner_ds.close()
    finally:
        await ds.close()
