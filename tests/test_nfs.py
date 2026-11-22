"""
Tests of the NFS datasource. The server is nfs_server.py, started by the tests on a port of the loopback interface:
it serves the testfiles folder. With ADISKREADER_NFS_URL set, the tests reading the images are run against that
server as well: it must point to an exported directory holding the decompressed test images of the testfiles folder.
"""

import pytest
import os
from config import *
from checks import *
from nfs_server import NFSTestServer

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource import DataSource
from adiskreader.datasource.nfs import NFSFileSource
from adiskreader.disks import Disk
from adiskreader.disks.raw import RAWDisk
from adiskreader.disks.vhd import VHDDisk
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.filesystems.ntfs import NTFS

NFS_URL = os.environ.get('ADISKREADER_NFS_URL')

IMAGES = [
    pytest.param(NTFS_TEST_FILE_GZ, RAWDisk, id='raw'),
    pytest.param(NTFS_TEST_FILE_VHDX_DYNAMIC_GZ, VHDXDisk, id='vhdx_dynamic'),
    pytest.param(NTFS_TEST_FILE_VHDX_GZ, VHDXDisk, id='vhdx_log'),
]

SERVERS = [
    pytest.param(False, id='local'),
    pytest.param(True, id='server', marks=pytest.mark.skipif(NFS_URL is None, reason='ADISKREADER_NFS_URL is not set')),
]

class Server:
    """The server of the tests, or the one of ADISKREADER_NFS_URL: url gives the url of a file in the testfiles folder"""
    def __init__(self, external = False, **kwargs):
        self.external = external
        self.server = NFSTestServer(TESTFILES_FOLDER_PATH, **kwargs) if external is False else None

    async def __aenter__(self):
        if self.server is not None:
            await self.server.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.server is not None:
            await self.server.__aexit__(exc_type, exc, tb)

    def url(self, path, params = '', scheme = 'nfs'):
        if self.server is not None:
            return self.server.url(path, params, scheme)
        return NFS_URL.rstrip('/') + '/' + path


@pytest.mark.asyncio
@pytest.mark.parametrize('external', SERVERS)
async def test_nfs_read_seek(external):
    filepath = get_image(NTFS_TEST_FILE_GZ)
    with open(filepath, 'rb') as f:
        expected = f.read()

    async with Server(external) as server:
        ds = await NFSFileSource.from_url(server.url(filepath.name))
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

            # larger than the max read size of the server
            await ds.seek(0, 0)
            assert await ds.read(20*1024*1024) == expected[:20*1024*1024]
        finally:
            await ds.close()

@pytest.mark.asyncio
async def test_nfs_read_size():
    # the server returns less than what was asked for, the datasource asks for the rest
    filepath = get_image(VFAT_16_TEST_FILE_GZ)
    with open(filepath, 'rb') as f:
        expected = f.read()
    async with NFSTestServer(TESTFILES_FOLDER_PATH, max_read = 1000) as server:
        ds = await NFSFileSource.from_url(server.url(filepath.name))
        try:
            await ds.seek(123, 0)
            assert await ds.read(10000) == expected[123:10123]
            assert server.read_calls == 10
            assert await ds.tell() == 10123
        finally:
            await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('external', SERVERS)
@pytest.mark.parametrize('image, disktype', IMAGES)
async def test_nfs_ntfs(image, disktype, external):
    # the disk type is detected from the extension in the url, like in the console
    filepath = get_image(image)
    async with Server(external) as server:
        ds = await DataSource.from_url(server.url(filepath.name))
        try:
            assert isinstance(ds, NFSFileSource)
            disk = await Disk.from_datasource(ds)
            assert isinstance(disk, disktype)
            partitions = await disk.list_partitions()
            fs = await NTFS.from_partition(partitions[0])
            manifest = load_manifest('ntfs')
            await check_file_list(fs, manifest)
            await check_file_stat(fs, manifest)
            await check_file_fullread(fs, manifest)
        finally:
            await ds.close()

@pytest.mark.asyncio
async def test_nfs_scheme():
    # the disk type in the scheme of the url
    filepath = get_image(NTFS_TEST_FILE_GZ)
    async with NFSTestServer(TESTFILES_FOLDER_PATH) as server:
        ds = await DataSource.from_url(server.url(filepath.name, scheme = 'nfs+raw'))
        try:
            assert isinstance(ds, NFSFileSource)
            assert isinstance(await Disk.from_datasource(ds), RAWDisk)
        finally:
            await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('list_exports', [True, False])
async def test_nfs_export(list_exports):
    # the export of the file is taken from the list of the server, or found by trying the directories of the path
    path, _ = get_diff_image('vhd_sub')
    with open(path, 'rb') as f:
        expected = f.read(4096)
    async with NFSTestServer(TESTFILES_FOLDER_PATH, export = '/srv/nfs/images', list_exports = list_exports) as server:
        ds = await NFSFileSource.from_url(server.url(path.relative_to(TESTFILES_FOLDER_PATH).as_posix()))
        try:
            assert await ds.read(4096) == expected
            assert server.mounted == ['/srv/nfs/images']
        finally:
            await ds.close()

@pytest.mark.asyncio
async def test_nfs_credential():
    filepath = get_image(VFAT_16_TEST_FILE_GZ)
    async with NFSTestServer(TESTFILES_FOLDER_PATH) as server:
        for params, expected in [('', (0, 0)), ('?uid=1000&gid=100', (1000, 100)), ('?uid=1234', (1234, 0))]:
            server.credentials.clear()
            ds = await NFSFileSource.from_url(server.url(filepath.name, params))
            try:
                assert len(await ds.read(512)) == 512
                assert server.credentials == {expected}
            finally:
                await ds.close()

@pytest.mark.asyncio
async def test_nfs_errors():
    filepath = get_image(VFAT_16_TEST_FILE_GZ)
    get_diff_fixtures()
    async with NFSTestServer(TESTFILES_FOLDER_PATH) as server:
        with pytest.raises(FileNotFoundError):
            await NFSFileSource.from_url(server.url('doesnotexist.img'))
        with pytest.raises(Exception, match='Not a file'):
            await NFSFileSource.from_url(server.url(DIFF_FOLDER_NAME))
        with pytest.raises(Exception, match='could be mounted'):
            await NFSFileSource.from_url('nfs://127.0.0.1:%s/other/%s' % (server.port, filepath.name))
        with pytest.raises(Exception, match='No file in the url'):
            await NFSFileSource.from_url('nfs://127.0.0.1:%s/' % server.port)

@pytest.mark.asyncio
@pytest.mark.parametrize('external', SERVERS)
async def test_nfs_open_sibling(external):
    filepath = get_image(NTFS_TEST_FILE_GZ)
    get_image(VFAT_16_TEST_FILE_GZ)
    async with Server(external) as server:
        ds = await NFSFileSource.from_url(server.url(get_image(VFAT_16_TEST_FILE_GZ).name))
        try:
            sibling = await ds.open_sibling(filepath.name)
            try:
                assert sibling.config['url'] == server.url(filepath.name)
                await check_sibling(ds, sibling, filepath)
                # siblings can open siblings, the .. in the path is resolved by the datasource
                sibling2 = await sibling.open_sibling('.\\sub\\..\\' + filepath.name)
                try:
                    await check_sibling(sibling, sibling2, filepath)
                finally:
                    await sibling2.close()
            finally:
                await sibling.close()
            # closing the sibling keeps the connection open
            assert len(await ds.read(512)) == 512
            if server.server is not None:
                # one connection for the mount and one for the files
                assert server.server.connections == 2
            with pytest.raises(Exception):
                await ds.open_sibling('doesnotexist.img')
            with pytest.raises(Exception, match='outside of the export'):
                await ds.open_sibling('..\\' + filepath.name)
        finally:
            await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('external', SERVERS)
@pytest.mark.parametrize('name, disktype', [('vhd_sub', VHDDisk), ('vhdx_sub', VHDXDisk), ('vhdxfixed_child2', VHDXDisk)])
async def test_nfs_vhd_diff(name, disktype, external):
    # the parents are opened from the export, the ones of the sub images are one directory up
    path, _ = get_diff_image(name)
    expected = get_diff_expected(name)
    async with Server(external) as server:
        ds = await DataSource.from_url(server.url(path.relative_to(TESTFILES_FOLDER_PATH).as_posix()))
        try:
            disk = await Disk.from_datasource(ds)
            assert isinstance(disk, disktype)
            try:
                for lba in range(0, disk.capacity, 4096):
                    assert await disk.read_sectors(lba, 4096) == expected[lba*512:(lba+4096)*512]
            finally:
                await disk.close()
            await ds.seek(0, 0)
            assert len(await ds.read(4)) == 4
        finally:
            await ds.close()

def make_links(root):
    """A folder with symbolic links for the server, returns the data of its files"""
    files = {'data/file.bin': os.urandom(5000), 'data/other.bin': os.urandom(3000), 'top.bin': os.urandom(1000)}
    root.joinpath('data', 'sub').mkdir(parents = True)
    for path, data in files.items():
        root.joinpath(path).write_bytes(data)
    links = {
        'file_link.bin' : 'data/file.bin',
        'dir_link' : 'data',
        'absolute.bin' : '/export/data/file.bin',
        'absolute_dir' : '/export/data/',
        'data/sub/up.bin' : '../file.bin',
        'data/sub/top.bin' : '../../top.bin',
        'data/sub/dir_up' : './..',
        'chain.bin' : 'data/sub/up.bin',
        'loop.bin' : 'loop.bin',
        'outside.bin' : '/etc/hostname',
        'outside_up.bin' : '../file.bin',
        'data/sub/outside_up.bin' : '../../../file.bin',
        'missing.bin' : 'data/doesnotexist.bin',
    }
    for path, target in links.items():
        os.symlink(target, root.joinpath(path))
    return files

@pytest.mark.asyncio
async def test_nfs_symlink(tmp_path):
    # the server does not follow the symbolic links, the datasource does
    files = make_links(tmp_path)
    paths = {
        'file_link.bin' : 'data/file.bin',
        'dir_link/file.bin' : 'data/file.bin',
        'absolute.bin' : 'data/file.bin',
        'absolute_dir/other.bin' : 'data/other.bin',
        'data/sub/up.bin' : 'data/file.bin',
        'data/sub/top.bin' : 'top.bin',
        'data/sub/dir_up/other.bin' : 'data/other.bin',
        'chain.bin' : 'data/file.bin',
        'dir_link/sub/dir_up/sub/up.bin' : 'data/file.bin',
    }
    async with NFSTestServer(tmp_path) as server:
        for path, target in paths.items():
            ds = await NFSFileSource.from_url(server.url(path))
            try:
                assert await ds.read(10000) == files[target], path
            finally:
                await ds.close()

        for path, error in [
            ('loop.bin', 'Too many symbolic links'),
            ('outside.bin', 'outside of the export'),
            ('outside_up.bin', 'outside of the export'),
            ('data/sub/outside_up.bin', 'outside of the export'),
            ('missing.bin', 'not found'),
            ('dir_link', 'Not a file'),
            ('file_link.bin/file.bin', 'Not a directory'),
        ]:
            with pytest.raises(Exception, match=error):
                await NFSFileSource.from_url(server.url(path))

@pytest.mark.asyncio
async def test_nfs_symlink_sibling(tmp_path):
    # the siblings of a link to a file are the files next to the file, the parent of a disk is there
    files = make_links(tmp_path)
    async with NFSTestServer(tmp_path) as server:
        for path in ['file_link.bin', 'dir_link/file.bin', 'chain.bin']:
            ds = await NFSFileSource.from_url(server.url(path))
            try:
                for name, target in [('other.bin', 'data/other.bin'), ('../top.bin', 'top.bin'), ('sub/up.bin', 'data/file.bin')]:
                    sibling = await ds.open_sibling(name)
                    assert await sibling.read(10000) == files[target], (path, name)
                    await sibling.close()
                with pytest.raises(Exception, match='outside of the export'):
                    await ds.open_sibling('../../top.bin')
            finally:
                await ds.close()
