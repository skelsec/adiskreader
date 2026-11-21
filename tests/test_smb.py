"""
Tests of the SMB datasource, skipped unless ADISKREADER_SMB_URL is set.
ADISKREADER_SMB_URL must point to a share holding the decompressed test images of the testfiles folder.
smb/start.sh starts a local samba container for this and prints the URL to use.
"""

import pytest
import os
from config import *
from checks import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource import DataSource
from adiskreader.datasource.smb import SMBFileSource
from adiskreader.disks import Disk
from adiskreader.disks.raw import RAWDisk
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.filesystems.ntfs import NTFS

SMB_URL = os.environ.get('ADISKREADER_SMB_URL')
pytestmark = pytest.mark.skipif(SMB_URL is None, reason='ADISKREADER_SMB_URL is not set')

IMAGES = [
    pytest.param(NTFS_TEST_FILE_GZ, RAWDisk, id='raw'),
    pytest.param(NTFS_TEST_FILE_VHDX_DYNAMIC_GZ, VHDXDisk, id='vhdx_dynamic'),
    pytest.param(NTFS_TEST_FILE_VHDX_FIXED_GZ, VHDXDisk, id='vhdx_fixed'),
    pytest.param(NTFS_TEST_FILE_VHDX_GZ, VHDXDisk, id='vhdx_log'),
]

def get_url(image):
    # makes sure the decompressed image exists in the shared folder
    filepath = get_image(image)
    return SMB_URL.rstrip('/') + '/' + filepath.name


@pytest.mark.asyncio
async def test_smb_read_seek():
    filepath = get_image(NTFS_TEST_FILE_GZ)
    with open(filepath, 'rb') as f:
        expected = f.read()

    ds = await SMBFileSource.from_url(get_url(NTFS_TEST_FILE_GZ))
    try:
        # sequential reads continue where the previous one ended
        assert await ds.tell() == 0
        assert await ds.read(512) == expected[:512]
        assert await ds.read(1000) == expected[512:1512]
        assert await ds.tell() == 1512

        await ds.seek(0x100000, 0)
        assert await ds.read(4096) == expected[0x100000:0x101000]
        await ds.seek(-4096, 1)
        assert await ds.read(4096) == expected[0x100000:0x101000]
        await ds.seek(-100, 2)
        assert await ds.read(100) == expected[-100:]

        # reads at the end of the file are truncated
        await ds.seek(-100, 2)
        assert await ds.read(4096) == expected[-100:]
        assert await ds.tell() == len(expected)
        assert await ds.read(4096) == b''

        # larger than the max read size of the connection
        await ds.seek(0, 0)
        assert await ds.read(20*1024*1024) == expected[:20*1024*1024]
    finally:
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('image, disktype', IMAGES)
async def test_smb_ntfs(image, disktype):
    # the disk type is detected from the extension in the url, like in the console
    ds = await DataSource.from_url(get_url(image))
    try:
        assert isinstance(ds, SMBFileSource)
        disk = await Disk.from_datasource(ds)
        assert isinstance(disk, disktype)
        partitions = await disk.list_partitions()
        fs = await NTFS.from_partition(partitions[0])
        manifest = load_manifest('ntfs')
        await check_file_list(fs, manifest)
        await check_file_stat(fs, manifest)
        await check_file_fullread(fs, manifest)
        await check_file_ops(fs, manifest)
    finally:
        await ds.close()
