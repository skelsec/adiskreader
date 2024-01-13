import pytest
import random
import os
import asyncio
from config import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource.file import FileSource
from adiskreader.datasource.gzipfile import GzipFileSource
from adiskreader.disks.raw import RAWDisk
from adiskreader.filesystems.ntfs import NTFS
import ntpath



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
                    #print(original_file_path)
                    #print('POS: %s' % pos)
                    #print('SIZE: %s' % size)
                    data = await fs_read(ffile, pos, size)
                    odata = file_read(ofile, pos, size)
                    #print('DATA: %s' % data)
                    #print('ODATA: %s' % odata)
                    #print(data == odata)
                    assert data == odata

@unmount_on_finish()
async def test():
    filepath, mountpoint = setup_ntfs_gz()
    ds = await GzipFileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[0])
    assert fs is not None

    original_file_path = '/mnt/diskreadtest/longfilename1234567sdvkolrtiswvnne.txt'
    file_path = original_file_path[len(str(mountpoint)):]
    file_path = file_path.replace('/', '\\')
    file_path = file_path[1:]
    ffile = await fs.open(file_path)
    assert ffile is not None
    st = os.stat(original_file_path)
    fsize = st.st_size
    with open(original_file_path, 'rb') as ofile:
        pos = 31
        size = 11
        print('POS: %s' % pos)
        print('SIZE: %s' % size)
        data = await fs_read(ffile, pos, size)
        odata = file_read(ofile, pos, size)
        print('DATA: %s' % data)
        print('ODATA: %s' % odata)
        print(data == odata)
        assert data == odata


asyncio.run(test_ntfs_gz_file_ops())
#asyncio.run(test())