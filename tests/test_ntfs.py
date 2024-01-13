import pytest
import random
import os
from config import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource.file import FileSource
from adiskreader.datasource.gzipfile import GzipFileSource
from adiskreader.disks.raw import RAWDisk
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.filesystems.ntfs import NTFS
import ntpath

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_raw_init():
    filepath, _ = setup_ntfs()
    ds = await FileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_file_list():
    filepath, mountpoint = setup_ntfs()
    ds = await FileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    paths = {}
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None
    async for root, dirs, files in fs.walk():
        for dir in dirs:
            paths[ntpath.join(str(root), str(dir))] = 1
        for file in files:
            paths[ntpath.join(str(root), str(file))] = 1

    for root, dirs, files in os.walk(mountpoint):
        root = root[len(str(mountpoint))+1:]
        root = '\\' + root
        for dir in dirs:
            dir_path = ntpath.join(root, dir)
            assert dir_path in paths
        for file in files:
            file_path = ntpath.join(str(root), str(file))
            assert file_path in paths

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_file_open():
    filepath, mountpoint = setup_ntfs()
    ds = await FileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

    for root, dirs, files in os.walk(mountpoint):
        for file in files:
            file_path = os.path.join(str(root), str(file))
            file_path = file_path[len(str(mountpoint)):]
            file_path = file_path.replace('/', '\\')
            file_path = file_path[1:]
            ffile = await fs.open(file_path)
            assert ffile is not None

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_file_stat():
    filepath, mountpoint = setup_ntfs()
    ds = await FileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

    for root, dirs, files in os.walk(mountpoint):
        for file in files:
            original_file_path = os.path.join(str(root), str(file))
            file_path = original_file_path[len(str(mountpoint)):]
            file_path = file_path.replace('/', '\\')
            file_path = file_path[1:]
            st_fs = await fs.stat(file_path)
            st_orig = os.stat(original_file_path)
            assert st_fs.st_size == st_orig.st_size

##########################################################################

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_gz_raw_init():
    filepath, _ = setup_ntfs_gz()
    ds = await GzipFileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_gz_file_list():
    filepath, mountpoint = setup_ntfs_gz()
    ds = await GzipFileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    paths = {}
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None
    async for root, dirs, files in fs.walk():
        for dir in dirs:
            paths[ntpath.join(str(root), str(dir))] = 1
        for file in files:
            paths[ntpath.join(str(root), str(file))] = 1

    for root, dirs, files in os.walk(mountpoint):
        for dir in dirs:
            if dir.startswith('/') is True:
                dir = dir[1:]
                dir = '\\' + dir
            dir_path = ntpath.join(str(root), str(dir))
            dir_path = dir_path[len(str(mountpoint)):]
            assert dir_path in paths
        for file in files:
            file_path = ntpath.join(str(root), str(file))
            file_path = '\\' + file_path[len(str(mountpoint))+1:]
            assert file_path in paths

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_gz_file_open():
    filepath, mountpoint = setup_ntfs_gz()
    ds = await GzipFileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

    for root, dirs, files in os.walk(mountpoint):
        for file in files:
            file_path = os.path.join(str(root), str(file))
            file_path = file_path[len(str(mountpoint)):]
            file_path = file_path.replace('/', '\\')
            file_path = file_path[1:]
            ffile = await fs.open(file_path)
            assert ffile is not None


@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_gz_file_fullread():
    filepath, mountpoint = setup_ntfs_gz()
    ds = await GzipFileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

    for root, dirs, files in os.walk(mountpoint):
        for file in files:
            original_file_path = os.path.join(str(root), str(file))
            file_path = original_file_path[len(str(mountpoint)):]
            file_path = file_path.replace('/', '\\')
            file_path = file_path[1:]
            ffile = await fs.open(file_path)
            assert ffile is not None
            ofile = open(original_file_path, 'rb')
            data = await ffile.read()
            odata = ofile.read()
            assert data == odata

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_gz_file_ops():
    filepath, mountpoint = setup_ntfs_gz()
    ds = await GzipFileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

    for root, dirs, files in os.walk(mountpoint):
        for file in files:
            original_file_path = os.path.join(str(root), str(file))
            file_path = original_file_path[len(str(mountpoint)):]
            file_path = file_path.replace('/', '\\')
            file_path = file_path[1:]
            ffile = await fs.open(file_path)
            assert ffile is not None
            st = os.stat(original_file_path)
            fsize = st.st_size
            with open(original_file_path, 'rb') as ofile:
                for _ in range(0, 50):
                    pos = random.randint(0, fsize)
                    size = random.randint(0, fsize-pos)

                    #print('POS: %s' % pos)
                    #print('SIZE: %s' % size)
                    data = await fs_read(ffile, pos, size)
                    odata = file_read(ofile, pos, size)
                    #print('DATA: %s' % data)
                    #print('ODATA: %s' % odata)
                    #print(data == odata)
                    assert data == odata

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_gz_file_stat():
    filepath, mountpoint = setup_ntfs_gz()
    ds = await GzipFileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

    for root, dirs, files in os.walk(mountpoint):
        for file in files:
            original_file_path = os.path.join(str(root), str(file))
            file_path = original_file_path[len(str(mountpoint)):]
            file_path = file_path.replace('/', '\\')
            file_path = file_path[1:]
            st_fs = await fs.stat(file_path)
            st_orig = os.stat(original_file_path)
            assert st_fs.st_size == st_orig.st_size

##########################################################################

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_vhdx_init():
    filepath, _ = setup_ntfs_vhdx()
    print(filepath)
    ds = await FileSource.from_file(filepath)
    disk = await VHDXDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_vhdx_file_list():
    filepath, mountpoint = setup_ntfs_vhdx()
    ds = await FileSource.from_file(filepath)
    disk = await VHDXDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    paths = {}
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None
    async for root, dirs, files in fs.walk():
        for dir in dirs:
            paths[ntpath.join(str(root), str(dir))] = 1
        for file in files:
            paths[ntpath.join(str(root), str(file))] = 1

    for root, dirs, files in os.walk(mountpoint):
        root = root[len(str(mountpoint))+1:]
        root = '\\' + root
        for dir in dirs:
            dir_path = ntpath.join(root, dir)
            assert dir_path in paths
        for file in files:
            file_path = ntpath.join(str(root), str(file))
            assert file_path in paths

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_vhdx_file_open():
    filepath, mountpoint = setup_ntfs_vhdx()
    ds = await FileSource.from_file(filepath)
    disk = await VHDXDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

    for root, dirs, files in os.walk(mountpoint):
        for file in files:
            file_path = os.path.join(str(root), str(file))
            file_path = file_path[len(str(mountpoint)):]
            file_path = file_path.replace('/', '\\')
            file_path = file_path[1:]
            ffile = await fs.open(file_path)
            assert ffile is not None

@pytest.mark.asyncio
@unmount_on_finish()
async def test_ntfs_vhdx_file_stat():
    filepath, mountpoint = setup_ntfs_vhdx()
    ds = await FileSource.from_file(filepath)
    disk = await VHDXDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

    for root, dirs, files in os.walk(mountpoint):
        for file in files:
            original_file_path = os.path.join(str(root), str(file))
            file_path = original_file_path[len(str(mountpoint)):]
            file_path = file_path.replace('/', '\\')
            file_path = file_path[1:]
            st_fs = await fs.stat(file_path)
            st_orig = os.stat(original_file_path)
            assert st_fs.st_size == st_orig.st_size