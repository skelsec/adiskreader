"""
Tests of the SSH datasource, skipped unless ADISKREADER_SSH_URL is set.
ADISKREADER_SSH_URL must point to a directory holding the decompressed test images of the testfiles folder.
smb/start.sh starts a local container for this and prints the URL to use.
"""

import pytest
import os
from urllib.parse import urlparse
from config import *
from checks import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource import DataSource
from adiskreader.datasource.ssh import SSHFileSource
from adiskreader.disks import Disk
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.filesystems.ntfs import NTFS

SSH_URL = os.environ.get('ADISKREADER_SSH_URL')
pytestmark = pytest.mark.skipif(SSH_URL is None, reason='ADISKREADER_SSH_URL is not set')

def get_url(image):
    # makes sure the decompressed image exists in the shared folder
    filepath = get_image(image)
    url_e = urlparse(SSH_URL)
    return url_e._replace(path = url_e.path.rstrip('/') + '/' + filepath.name).geturl()


@pytest.mark.asyncio
async def test_ssh_ntfs():
    ds = await DataSource.from_url(get_url(NTFS_TEST_FILE_VHDX_DYNAMIC_GZ))
    try:
        assert isinstance(ds, SSHFileSource)
        disk = await Disk.from_datasource(ds)
        assert isinstance(disk, VHDXDisk)
        partitions = await disk.list_partitions()
        fs = await NTFS.from_partition(partitions[0])
        manifest = load_manifest('ntfs')
        await check_file_list(fs, manifest)
        await check_file_fullread(fs, manifest)
    finally:
        await ds.close()

@pytest.mark.asyncio
async def test_ssh_open_sibling():
    filepath = get_image(NTFS_TEST_FILE_GZ)
    ds = await SSHFileSource.from_url(get_url(VFAT_16_TEST_FILE_GZ))
    try:
        sibling = await ds.open_sibling(filepath.name)
        try:
            assert sibling.config['url'] == get_url(NTFS_TEST_FILE_GZ)
            await check_sibling(ds, sibling, filepath)
            sibling2 = await sibling.open_sibling('./../share/' + filepath.name)
            try:
                await check_sibling(sibling, sibling2, filepath)
            finally:
                await sibling2.close()
        finally:
            await sibling.close()
        assert len(await ds.read(512)) == 512
        with pytest.raises(Exception):
            await ds.open_sibling('doesnotexist.img')
    finally:
        await ds.close()
