import pytest
import os
from config import *
from checks import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource import DataSource, sibling_parts, sibling_url
from adiskreader.datasource.file import FileSource
from adiskreader.datasource.gzipfile import GzipFileSource
from adiskreader.disks import Disk
from adiskreader.disks.vhdx import VHDXDisk


def test_sibling_parts():
    assert sibling_parts('disk-flat.vmdk') == ['disk-flat.vmdk']
    assert sibling_parts('.\\parent.vhdx') == ['parent.vhdx']
    assert sibling_parts('..\\base/parent.vhdx') == ['..', 'base', 'parent.vhdx']
    for name in ['', '.', '/etc/passwd', '\\\\server\\share\\a.vhdx', 'C:\\a.vhdx']:
        with pytest.raises(Exception):
            sibling_parts(name)

def test_sibling_url():
    assert sibling_url('smb2+ntlm-password://u:p@host:445/share/dir/a.vmdk?timeout=5', ['a flat.vmdk']) == 'smb2+ntlm-password://u:p@host:445/share/dir/a%20flat.vmdk?timeout=5'
    assert sibling_url('ssh+password://u:p@host/share/a.vhdx', ['..', 'b.vhdx']) == 'ssh+password://u:p@host/share/../b.vhdx'
    assert sibling_url('file://a.vmdk', ['b.vmdk']) == 'file://b.vmdk'
    assert sibling_url('file:///tmp/a.vmdk', ['b.vmdk']) == 'file:///tmp/b.vmdk'

@pytest.mark.asyncio
async def test_base_open_sibling():
    with pytest.raises(NotImplementedError):
        await DataSource({}).open_sibling('a.img')

@pytest.mark.asyncio
@pytest.mark.parametrize('from_url', [False, True])
async def test_file_open_sibling(from_url):
    filepath = get_image(NTFS_TEST_FILE_GZ)
    path = str(get_image(VFAT_16_TEST_FILE_GZ))
    ds = await DataSource.from_url('file://' + path) if from_url else await FileSource.from_file(path)
    try:
        sibling = await ds.open_sibling(filepath.name)
        try:
            assert sibling.config['path'] == str(filepath)
            await check_sibling(ds, sibling, filepath)
            sibling2 = await sibling.open_sibling('..\\testfiles/' + filepath.name)
            try:
                await check_sibling(sibling, sibling2, filepath)
            finally:
                await sibling2.close()
        finally:
            await sibling.close()
        with pytest.raises(FileNotFoundError):
            await ds.open_sibling('doesnotexist.img')
    finally:
        await ds.close()

@pytest.mark.asyncio
async def test_file_sibling_disk_detection():
    # the type of the sibling is detected from its own name
    get_image(NTFS_TEST_FILE_VHDX_DYNAMIC_GZ)
    ds = await FileSource.from_file(str(get_image(NTFS_TEST_FILE_GZ)))
    sibling = await ds.open_sibling('ntfs_test_dynamic.vhdx')
    try:
        assert isinstance(await Disk.from_datasource(sibling), VHDXDisk)
    finally:
        await sibling.close()
        await ds.close()

@pytest.mark.asyncio
async def test_gzip_open_sibling():
    filepath = get_image(NTFS_TEST_FILE_GZ)
    ds = await GzipFileSource.from_file(str(get_image(VFAT_16_TEST_FILE_GZ, compressed=True)))
    try:
        # the compressed sibling is preferred
        sibling = await ds.open_sibling(filepath.name)
        try:
            assert sibling.config['path'] == str(get_image(NTFS_TEST_FILE_GZ, compressed=True))
            await sibling.seek(0x100000, 0)
            with open(filepath, 'rb') as f:
                f.seek(0x100000)
                assert await sibling.read(4096) == f.read(4096)
        finally:
            await sibling.close()
    finally:
        await ds.close()
