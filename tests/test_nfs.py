"""
Tests of the NFS datasource. The server is nfs_server.py, started by the tests on a port of the loopback interface:
it serves the testfiles folder. With ADISKREADER_NFS_URL set, the tests reading the images are run against that
server as well: it must point to an exported directory holding the decompressed test images of the testfiles folder.
The test_nfs_lab tests are run against the docker lab of anfs (the lab folder of https://github.com/skelsec/anfs),
they are skipped when its servers are not running. The NFSv4 and the kerberos tests are there.
"""

import pytest
import os
import socket
from config import *
from checks import *
from nfs_server import NFSTestServer

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource import DataSource
from adiskreader.datasource.nfs import NFSFileSource
from anfs.protocol.nfs3.common.factory import NFS3ConnectionFactory
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
        for params, expected in [('', (0, 0)), ('&uid=1000&gid=100', (1000, 100)), ('&uid=1234', (1234, 0))]:
            server.credentials.clear()
            ds = await NFSFileSource.from_url(server.url(filepath.name, params))
            try:
                assert len(await ds.read(512)) == 512
                assert server.credentials == {expected}
            finally:
                await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('as_owner', [False, True])
async def test_nfs_from_client(as_owner):
    # a connected anfs client is used, as_owner makes the calls as the owner of the directory and of the file
    filepath = get_image(NTFS_TEST_FILE_GZ)
    owner = (os.stat(filepath).st_uid, os.stat(filepath).st_gid)
    async with Server() as server:
        factory = NFS3ConnectionFactory.from_url(server.url('', '&uid=4242&gid=4343'))
        mount = factory.get_mount()
        _, err = await mount.connect()
        assert err is None
        handle, err = await mount.mount(server.server.export)
        assert err is None
        await mount.disconnect()
        client = factory.get_client(handle)
        _, err = await client.connect()
        assert err is None
        try:
            ds = await NFSFileSource.from_client(client, '/' + filepath.name, export = server.server.export, as_owner = as_owner)
            try:
                assert ds.config['path'] == filepath.name
                server.server.credentials.clear()
                assert await ds.read(512) == open(filepath, 'rb').read(512)
                assert server.server.credentials == {owner if as_owner is True else (4242, 4343)}
                sibling = await ds.open_sibling(get_image(VFAT_16_TEST_FILE_GZ).name)
                try:
                    assert sibling.config['path'] == get_image(VFAT_16_TEST_FILE_GZ).name
                    await check_sibling(ds, sibling, get_image(VFAT_16_TEST_FILE_GZ))
                finally:
                    await sibling.close()
                disk = await Disk.from_datasource(ds)
                assert isinstance(disk, RAWDisk)
            finally:
                await ds.close()
            # the client is not closed by the datasource
            attrs, err = await client.getattr(0)
            assert err is None
        finally:
            await client.disconnect()

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
            await NFSFileSource.from_url('nfs://127.0.0.1:%s/other/%s?mountport=%s' % (server.port, filepath.name, server.port))
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

# The lab of anfs: a Linux kernel server and NFS-Ganesha with the same files, and a KDC (realm ANFS.LAB).
# The exports are /srv/exports/<name> for NFSv3 and /<name> in the namespace of NFSv4.
LAB_SERVERS = {'knfsd': '172.30.111.10', 'ganesha': '172.30.111.11'}
LAB_KDC = '172.30.111.5'
LAB_USER = 'alice'
LAB_PASSWORD = 'Passw0rd!'
LAB_VERSIONS = ['3', '4.0', '4.1', '4.2']
lab_reachable = {}

def lab_address(name, port = 2049):
    """The address of a server of the lab, the test is skipped when it is not running"""
    address = LAB_SERVERS.get(name, name)
    if (address, port) not in lab_reachable:
        try:
            socket.create_connection((address, port), timeout = 2).close()
            lab_reachable[(address, port)] = True
        except OSError:
            lab_reachable[(address, port)] = False
    if lab_reachable[(address, port)] is False:
        pytest.skip('The lab server %s (%s) is not running' % (name, address))
    return address

def lab_url(name, version, path, export = 'rw', params = ''):
    root = '/srv/exports/' if version == '3' else '/'
    return 'nfs://%s%s%s/%s?version=%s%s' % (lab_address(name), root, export, path, version, params)

@pytest.mark.asyncio
@pytest.mark.parametrize('version', LAB_VERSIONS)
@pytest.mark.parametrize('name', LAB_SERVERS)
async def test_nfs_lab_read(name, version):
    # big.bin is 3MiB of 'A'
    ds = await NFSFileSource.from_url(lab_url(name, version, 'big.bin'))
    try:
        assert await ds.read(3*1024*1024 + 100) == b'A' * (3*1024*1024)
        await ds.seek(-100, 2)
        assert await ds.read(4096) == b'A' * 100
        await ds.seek(1024*1024 - 10, 0)
        assert await ds.read(20) == b'A' * 20
        assert await ds.tell() == 1024*1024 + 10

        for sibling_name, expected in [('hello.txt', b'hello from rw\n'), ('dir1\\dir2\\dir3\\deep.txt', b'nested\n'), ('link_to_hello', b'hello from rw\n')]:
            sibling = await ds.open_sibling(sibling_name)
            try:
                assert await sibling.read(100) == expected
            finally:
                await sibling.close()
        # the connection is still there
        await ds.seek(0, 0)
        assert await ds.read(10) == b'A' * 10
    finally:
        await ds.close()

    for path, expected in [('link_to_hello', b'hello from rw\n'), ('dir1/dir2/dir3/deep.txt', b'nested\n'), ('dir1/../file with space.txt', b'with space\n'),
                           ('árvíztűrő tükörfúrógép.txt', b'unicode\n'), ('empty.txt', b'')]:
        ds = await NFSFileSource.from_url(lab_url(name, version, path))
        try:
            assert await ds.read(100) == expected
        finally:
            await ds.close()

    with pytest.raises(FileNotFoundError):
        await NFSFileSource.from_url(lab_url(name, version, 'doesnotexist.img'))
    with pytest.raises(Exception, match='Not a file'):
        await NFSFileSource.from_url(lab_url(name, version, 'dir1'))
    # link_abs points to /etc/passwd: outside of the export, and not in the namespace of the NFSv4 servers
    with pytest.raises(Exception, match='outside of the export' if version == '3' else 'not found'):
        await NFSFileSource.from_url(lab_url(name, version, 'link_abs'))

@pytest.mark.asyncio
async def test_nfs_lab_other_export():
    # NFSv4: every export is in the namespace, the files of the other ones can be opened as siblings
    ds = await NFSFileSource.from_url(lab_url('knfsd', '4.2', 'hello.txt'))
    try:
        sibling = await ds.open_sibling('..\\ro\\hello.txt')
        try:
            assert await sibling.read(100) == b'hello from ro\n'
        finally:
            await sibling.close()
        with pytest.raises(Exception, match='outside of the export'):
            await ds.open_sibling('..\\..\\rw\\hello.txt')
    finally:
        await ds.close()
    # NFSv3: the other export would have to be mounted for it
    ds = await NFSFileSource.from_url(lab_url('knfsd', '3', 'hello.txt'))
    try:
        with pytest.raises(Exception, match='outside of the export'):
            await ds.open_sibling('..\\ro\\hello.txt')
    finally:
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('name', LAB_SERVERS)
async def test_nfs_lab_user(name):
    # user1000 is a directory of the uid 1000 with the mode 0700, the root user of the client is squashed in the ro export
    for version in LAB_VERSIONS:
        ds = await NFSFileSource.from_url(lab_url(name, version, 'user1000/mine.txt', 'ro', '&uid=1000&gid=1000'))
        try:
            assert await ds.read(100) == b'user 1000 only\n'
        finally:
            await ds.close()
        with pytest.raises(Exception, match='ACCES'):
            await NFSFileSource.from_url(lab_url(name, version, 'user1000/mine.txt', 'ro'))

@pytest.mark.asyncio
@pytest.mark.parametrize('sec', ['krb5', 'krb5i', 'krb5p'])
@pytest.mark.parametrize('version', LAB_VERSIONS)
@pytest.mark.parametrize('name', LAB_SERVERS)
async def test_nfs_lab_kerberos(name, version, sec):
    # the krb5 export takes kerberos only, alice is the uid 1000 on the servers
    lab_address(LAB_KDC, 88)
    params = '&dc=%s&spn=nfs/%s.anfs.lab&sec=%s' % (LAB_KDC, name, sec)
    url = lab_url(name, version, 'user1000/mine.txt', 'krb5', params)
    url = url.replace('nfs://', 'nfs+kerberos-password://ANFS.LAB\\%s:%s@' % (LAB_USER, LAB_PASSWORD))
    ds = await NFSFileSource.from_url(url)
    try:
        assert await ds.read(100) == b'user 1000 only\n'
        sibling = await ds.open_sibling('..\\big.bin')
        try:
            assert await sibling.read(4*1024*1024) == b'A' * (3*1024*1024)
        finally:
            await sibling.close()
        with pytest.raises(Exception, match='ACCES'):
            await ds.open_sibling('..\\private\\rootonly.txt')
    finally:
        await ds.close()
    with pytest.raises(Exception):
        await NFSFileSource.from_url(lab_url(name, version, 'user1000/mine.txt', 'krb5', '&uid=1000&gid=1000'))

async def lab_upload(address, files, links):
    """Uploads the files to a new directory of the scratch directory of the rw export, returns the name of it and the function removing it"""
    factory = NFS3ConnectionFactory.from_url('nfs://%s/' % address)
    mount = factory.get_mount()
    _, err = await mount.connect()
    assert err is None, err
    handle, err = await mount.mount('/srv/exports/rw')
    assert err is None, err
    await mount.disconnect()
    client = factory.get_client(handle)
    _, err = await client.connect()
    assert err is None, err

    dirname = 'adiskreader_%s' % os.urandom(4).hex()
    created = []
    async def makedirs(parts):
        handle = 0
        for i, part in enumerate(parts):
            entry, err = await client.lookup(handle, part)
            if entry is False:
                entry, err = await client.mkdir(handle, part)
                assert err is None, err
                created.append((handle, part, True))
            handle = entry.handle
        return handle

    async def cleanup():
        for handle, name, is_dir in reversed(created):
            if is_dir is True:
                await client.rmdir(handle, name)
            else:
                await client.remove(handle, name)
        await client.disconnect()

    try:
        for path, local_path in files.items():
            parts = ['scratch', dirname] + path.split('/')
            handle = await makedirs(parts[:-1])
            _, err = await client.upload_file(handle, parts[-1], str(local_path))
            assert err is None, err
            created.append((handle, parts[-1], False))
        for path, target in links.items():
            parts = ['scratch', dirname] + path.split('/')
            handle = await makedirs(parts[:-1])
            _, err = await client.symlink(handle, parts[-1], target)
            assert err is None, err
            created.append((handle, parts[-1], False))
    except BaseException:
        await cleanup()
        raise
    return 'scratch/' + dirname, cleanup

@pytest.mark.asyncio
@pytest.mark.parametrize('name', LAB_SERVERS)
async def test_nfs_lab_disks(name):
    # disk images uploaded to the server: a filesystem is read from one, the parents of a differencing disk are opened next to it
    ntfs_path = get_image(NTFS_TEST_FILE_VHDX_DYNAMIC_GZ)
    diff_path, _ = get_diff_image('vhd_sub')
    diff_expected = get_diff_expected('vhd_sub')
    diff_folder = diff_path.parent.parent
    files = {ntfs_path.name: ntfs_path, 'diff/sub/' + diff_path.name: diff_path}
    for parent in ['diff_vhd_base.vhd', 'diff_vhd_child1.vhd', 'diff_vhd_child2.vhd']:
        files['diff/' + parent] = diff_folder.joinpath(parent)
    links = {'ntfs_link.vhdx': ntfs_path.name, 'diff_link.vhd': 'diff/sub/' + diff_path.name}

    folder, cleanup = await lab_upload(lab_address(name), files, links)
    try:
        for version in LAB_VERSIONS:
            for path in [ntfs_path.name, 'ntfs_link.vhdx']:
                ds = await DataSource.from_url(lab_url(name, version, '%s/%s' % (folder, path)))
                try:
                    assert isinstance(ds, NFSFileSource)
                    disk = await Disk.from_datasource(ds)
                    assert isinstance(disk, VHDXDisk)
                    partitions = await disk.list_partitions()
                    fs = await NTFS.from_partition(partitions[0])
                    manifest = load_manifest('ntfs')
                    await check_file_list(fs, manifest)
                    await check_file_fullread(fs, manifest)
                finally:
                    await ds.close()

            for path in ['diff/sub/' + diff_path.name, 'diff_link.vhd']:
                ds = await DataSource.from_url(lab_url(name, version, '%s/%s' % (folder, path)))
                try:
                    disk = await Disk.from_datasource(ds)
                    assert isinstance(disk, VHDDisk)
                    try:
                        for lba in range(0, disk.capacity, 4096):
                            assert await disk.read_sectors(lba, 4096) == diff_expected[lba*512:(lba+4096)*512]
                    finally:
                        await disk.close()
                finally:
                    await ds.close()
    finally:
        await cleanup()
