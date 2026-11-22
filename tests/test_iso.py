"""
Tests of the ISO9660 (Joliet, Rock Ridge) and UDF filesystems. The images are built by iso_builder.py,
the ones made on Windows are tested by test_iso_windows.py.
"""

import datetime
import hashlib
import os
import shutil
import subprocess

import pytest

from adiskreader.datasource.file import FileSource
from adiskreader.filesystems.iso import ISOImage, VIEW_UDF, VIEW_ROCKRIDGE, VIEW_JOLIET, VIEW_ISO9660
from adiskreader.filesystems.iso.iso9660 import ISO9660
from adiskreader.filesystems.udf import UDF
from checks import check_file_list, check_file_open, check_file_stat, check_file_fullread, check_file_ops
from iso_builder import ISOBuilder, UDFBuilder, sample_entries

# name -> options of the ISO9660 builder and the views of the image with the names of their manifests
ISO_VARIANTS = {
    'rockridge-joliet': ({}, [(VIEW_ROCKRIDGE, 'rockridge'), (VIEW_JOLIET, 'joliet')]),
    'rockridge': ({'joliet': False}, [(VIEW_ROCKRIDGE, 'rockridge')]),
    'joliet': ({'rock_ridge': False}, [(VIEW_JOLIET, 'joliet'), (VIEW_ISO9660, 'iso')]),
    'plain': ({'joliet': False, 'rock_ridge': False}, [(VIEW_ISO9660, 'iso')]),
}
ISO_VIEWS = [(name, index) for name, (_, views) in ISO_VARIANTS.items() for index in range(len(views))]

# options of the UDF builder
UDF_VARIANTS = {
    'udf102': {'revision': 0x0102},
    'udf201': {},
    'udf201-long': {'ad': 'long'},
    'udf201-ext-efe': {'ad': 'ext', 'efe': True},
    'udf201-512': {'block_size': 512},
    'udf201-4096': {'block_size': 4096},
    'udf201-anchor-at-end': {'anchor256': False},
    'udf150-vat': {'mode': 'vat', 'revision': 0x0150},
    'udf201-vat': {'mode': 'vat', 'padding': 7},
    'udf201-sparable': {'mode': 'sparable'},
    'udf250-metadata': {'mode': 'metadata', 'revision': 0x0250, 'efe': True},
    'udf250-metadata-mirror': {'mode': 'metadata', 'revision': 0x0250, 'efe': True, 'broken_metadata': True},
    'udf260-metadata-512': {'mode': 'metadata', 'revision': 0x0260, 'block_size': 512},
}

_images = {}

def get_built(name, folder):
    """Builds the image, returns its path and its builder"""
    if name not in _images:
        if name in ISO_VARIANTS:
            builder = ISOBuilder(sample_entries(), **ISO_VARIANTS[name][0])
        else:
            builder = UDFBuilder(sample_entries(), **UDF_VARIANTS[name])
        path = folder.joinpath('%s.iso' % name)
        path.write_bytes(builder.build())
        _images[name] = (path, builder)
    return _images[name]

@pytest.fixture(scope='module')
def folder(tmp_path_factory):
    yield tmp_path_factory.mktemp('iso')
    _images.clear()

async def open_view(folder, name, index = 0):
    path, builder = get_built(name, folder)
    ds = await FileSource.from_file(str(path))
    image = await ISOImage.from_datasource(ds)
    views = await image.list_partitions()
    fs = await views[index].mount()
    if name in ISO_VARIANTS:
        return fs, builder.manifest(ISO_VARIANTS[name][1][index][1])
    return fs, builder.manifest()

async def read_file(fs, path):
    f = await fs.open(path)
    return await f.read()

async def list_tree(fs):
    dirs = []
    files = []
    async for root, dirnames, filenames in fs.walk():
        prefix = root.rstrip('\\') + '\\'
        dirs += [(prefix + name).lstrip('\\') for name in dirnames]
        files += [(prefix + name).lstrip('\\') for name in filenames]
    return dirs, files


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ISO_VARIANTS)
async def test_iso_views(name, folder):
    path, _ = get_built(name, folder)
    ds = await FileSource.from_file(str(path))
    assert await ISOImage.is_iso(ds) is True
    image = await ISOImage.from_datasource(ds)
    views = await image.list_partitions()
    assert [view.kind for view in views] == [kind for kind, _ in ISO_VARIANTS[name][1]]
    assert all(view.volume_name == 'ADR_TEST' for view in views)
    assert 'ADR_TEST' in str(image)
    with pytest.raises(Exception):
        await image.mount(len(views) + 1)

@pytest.mark.asyncio
@pytest.mark.parametrize('name, index', ISO_VIEWS)
async def test_iso_files(name, index, folder):
    fs, manifest = await open_view(folder, name, index)
    await check_file_list(fs, manifest)
    await check_file_open(fs, manifest)
    await check_file_stat(fs, manifest)
    await check_file_fullread(fs, manifest)
    await check_file_ops(fs, manifest)

@pytest.mark.asyncio
@pytest.mark.parametrize('name, index', ISO_VIEWS)
async def test_iso_tree(name, index, folder):
    # nothing else is listed than what the image was built from
    fs, manifest = await open_view(folder, name, index)
    dirs, files = await list_tree(fs)
    if ISO_VARIANTS[name][1][index][0] == VIEW_ROCKRIDGE:
        # the place where the relocated directories are stored
        dirs.remove('rr_moved')
    assert sorted(dirs) == sorted(manifest['dirs'])
    assert sorted(files) == sorted(list(manifest['files']) + list(manifest['symlinks']))

@pytest.mark.asyncio
async def test_iso_rock_ridge(folder):
    fs, manifest = await open_view(folder, 'rockridge-joliet')
    assert fs.rock_ridge is True
    assert len(manifest['symlinks']) == 2
    for path, target in manifest['symlinks'].items():
        entry = await fs.get_record_by_path(path)
        assert entry.is_symlink() is True
        assert entry.symlink_target == target
        assert await read_file(fs, path) == target.encode()
        assert (await fs.stat(path)).st_mode & 0o170000 == 0o120000

    st = await fs.stat('script.sh')
    assert st.st_mode == 0o100755
    assert st.st_mtime == datetime.datetime(2001, 2, 3, 4, 5, 6, tzinfo=datetime.timezone.utc).timestamp()
    st = await fs.stat('small file with a long name and spaces.txt')
    assert (st.st_mode, st.st_uid, st.st_gid) == (0o100600, 1000, 100)
    # the long form of the timestamps
    entry = await fs.get_record_by_path('many extents.bin')
    assert entry.mtime == datetime.datetime(2024, 5, 6, 7, 8, 9, tzinfo=datetime.timezone.utc)
    assert (await fs.stat('DOCS')).st_mode == 0o040755
    assert (await fs.get_record_by_path('hidden.dat')).hidden is True
    assert (await fs.get_record_by_path('README.TXT')).hidden is False

    # the relocated directory is listed at its original place
    entry = await fs.get_record_by_path('a\\b\\c\\d\\e\\f\\g\\h')
    assert entry.is_directory() is True
    assert await entry.resolve_full_path() == '\\a\\b\\c\\d\\e\\f\\g\\h'
    assert sorted(child.name for child in await entry.list_children()) == ['deep.txt', 'i']
    moved = await fs.get_record_by_path('rr_moved')
    assert await moved.list_children() == []

@pytest.mark.asyncio
async def test_iso_plain_names(folder):
    # without Rock Ridge the names of the primary tree are used, the version numbers are not shown
    fs, manifest = await open_view(folder, 'plain')
    assert fs.rock_ridge is False
    root = await fs.get_root()
    names = [child.name for child in await root.list_children()]
    assert 'README.TXT' in names and 'DOCS' in names
    assert all(';' not in name for name in names)
    assert await read_file(fs, 'readme.txt') == b'adiskreader optical test image\n'
    # the primary tree of an image with Rock Ridge can be read without it too
    path, builder = get_built('rockridge', folder)
    fs = await ISO9660.from_datasource(await FileSource.from_file(str(path)), rock_ridge=False)
    manifest = builder.manifest('iso')
    # the relocated directory is at an other place for the readers not knowing Rock Ridge
    manifest['files'] = {name: item for name, item in manifest['files'].items() if name.startswith('A\\B\\C\\D\\E\\F\\G\\H\\') is False}
    assert len(manifest['files']) > 80
    await check_file_fullread(fs, manifest)
    assert await read_file(fs, 'RR_MOVED\\H\\DEEP.TXT') == b'deep file\n'

@pytest.mark.asyncio
async def test_iso_entry_api(folder):
    fs, manifest = await open_view(folder, 'rockridge-joliet', 1)
    root = await fs.get_root()
    assert await root.resolve_full_path() == '\\'
    assert await root.get_parent() is root
    docs = await root.get_child('docs')
    assert docs.name == 'DOCS' and docs.is_directory() is True
    assert await docs.get_parent() is root
    assert await root.get_child('no such file') is None
    kinds = {name: kind async for kind, name, entry in root.get_children()}
    assert kinds['DOCS'] == 'dir' and kinds['README.TXT'] == 'file'
    lines = [line async for line in root.get_console_output()]
    assert len(lines) == len(kinds)
    with pytest.raises(FileNotFoundError):
        await fs.open('no such file')
    with pytest.raises(IsADirectoryError):
        await fs.open('DOCS')
    with pytest.raises(FileNotFoundError):
        await fs.stat('DOCS\\no such file')
    assert sorted([root async for root, _, _ in fs.walk('a\\b\\c\\d\\e\\f\\g')]) == ['\\a\\b\\c\\d\\e\\f\\g', '\\a\\b\\c\\d\\e\\f\\g\\h', '\\a\\b\\c\\d\\e\\f\\g\\h\\i']


@pytest.mark.asyncio
@pytest.mark.parametrize('name', UDF_VARIANTS)
async def test_udf_files(name, folder):
    fs, manifest = await open_view(folder, name)
    assert isinstance(fs, UDF)
    await check_file_list(fs, manifest)
    await check_file_open(fs, manifest)
    await check_file_stat(fs, manifest)
    await check_file_fullread(fs, manifest)
    await check_file_ops(fs, manifest)

@pytest.mark.asyncio
@pytest.mark.parametrize('name', UDF_VARIANTS)
async def test_udf_tree(name, folder):
    path, builder = get_built(name, folder)
    ds = await FileSource.from_file(str(path))
    assert await ISOImage.is_iso(ds) is True
    image = await ISOImage.from_datasource(ds)
    views = await image.list_partitions()
    assert [(view.kind, view.volume_name) for view in views] == [(VIEW_UDF, 'ADR_UDF')]
    fs = await views[0].mount()
    options = UDF_VARIANTS[name]
    assert fs.block_size == options.get('block_size', 2048)
    assert fs.revision == options.get('revision', 0x0201)

    # the deleted file of the root is not listed
    manifest = builder.manifest()
    dirs, files = await list_tree(fs)
    assert sorted(dirs) == sorted(manifest['dirs'])
    assert sorted(files) == sorted(list(manifest['files']) + list(manifest['symlinks']))

    for path, target in manifest['symlinks'].items():
        entry = await fs.get_record_by_path(path)
        assert entry.is_symlink() is True
        assert entry.symlink_target == target
        assert await read_file(fs, path) == target.encode()

    st = await fs.stat('script.sh')
    assert st.st_mode == 0o100755
    assert st.st_mtime == datetime.datetime(2001, 2, 3, 4, 5, 6, tzinfo=datetime.timezone.utc).timestamp()
    st = await fs.stat('small file with a long name and spaces.txt')
    assert (st.st_mode, st.st_uid, st.st_gid) == (0o100600, 1000, 100)
    assert (await fs.stat('DOCS')).st_mode == 0o040755
    assert (await fs.get_record_by_path('hidden.dat')).hidden is True
    assert (await fs.get_record_by_path('README.TXT')).hidden is False

@pytest.mark.asyncio
async def test_udf_truncated(folder, tmp_path):
    # the data of the files is missing from the end of the image
    path, builder = get_built('udf201', folder)
    data = path.read_bytes()
    cut = tmp_path.joinpath('cut.iso')
    cut.write_bytes(data[:len(data) // 2])
    fs = await UDF.from_datasource(await FileSource.from_file(str(cut)))
    with pytest.raises(Exception):
        for file_path in builder.manifest()['files']:
            await read_file(fs, file_path)

@pytest.mark.asyncio
async def test_not_an_iso(tmp_path):
    path = tmp_path.joinpath('zeros.img')
    path.write_bytes(b'\x00' * (400 * 2048))
    ds = await FileSource.from_file(str(path))
    assert await ISOImage.is_iso(ds) is False
    with pytest.raises(Exception):
        await ISOImage.from_datasource(ds)
    with pytest.raises(Exception):
        await UDF.from_datasource(ds)
    with pytest.raises(Exception):
        await ISO9660.from_datasource(ds)
    path = tmp_path.joinpath('short.img')
    path.write_bytes(b'short')
    assert await ISOImage.is_iso(await FileSource.from_file(str(path))) is False

@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['rockridge-joliet', 'udf201', 'udf201-vat', 'udf250-metadata'])
async def test_iso_damaged(name, folder, tmp_path):
    # damaged images must end in an exception or in a listing, not in an endless loop
    import random
    path, _ = get_built(name, folder)
    original = path.read_bytes()
    rng = random.Random(42)
    damaged = tmp_path.joinpath('damaged.iso')
    for _ in range(40):
        data = bytearray(original)
        for _ in range(rng.randint(1, 30)):
            # the descriptors are in the first part of the images
            pos = rng.randrange(16 * 2048, min(len(data), 700 * 2048))
            data[pos] = rng.randrange(256)
        damaged.write_bytes(data)
        ds = await FileSource.from_file(str(damaged))
        try:
            image = await ISOImage.from_datasource(ds)
            for view in await image.list_partitions():
                fs = await view.mount()
                await list_tree(fs)
        except Exception:
            pass
        finally:
            await ds.close()


def extract_7z(path, folder):
    subprocess.run(['7z', 'x', '-y', '-o%s' % folder, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    res = {}
    for root, _, files in os.walk(folder):
        for name in files:
            full = os.path.join(root, name)
            if os.path.islink(full) is True:
                continue
            with open(full, 'rb') as f:
                data = f.read()
            res[os.path.relpath(full, folder).replace('/', '\\')] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    return res

@pytest.mark.skipif(shutil.which('7z') is None, reason='7z is not installed')
def test_iso_builder_7z(tmp_path):
    # 7z reads the same files from the images of the builder (it uses the Joliet names)
    builder = ISOBuilder(sample_entries())
    path = tmp_path.joinpath('test.iso')
    path.write_bytes(builder.build())
    assert extract_7z(path, tmp_path.joinpath('out')) == builder.manifest('joliet')['files']

@pytest.mark.skipif(shutil.which('7z') is None, reason='7z is not installed')
@pytest.mark.parametrize('options', [{}, {'ad': 'long'}, {'efe': True}, {'revision': 0x0102}])
def test_udf_builder_7z(options, tmp_path):
    # 7z only knows the basic features of UDF: no symlinks, no extents which are not recorded and no allocation extents
    entries = [entry for entry in sample_entries() if 'symlink' not in entry and entry.get('layout') not in ('sparse', 'aed', 'strategy4096')]
    builder = UDFBuilder(entries, **options)
    path = tmp_path.joinpath('test.iso')
    path.write_bytes(builder.build())
    assert extract_7z(path, tmp_path.joinpath('out')) == builder.manifest()['files']

# disc images installed by other software, if there are any
LOCAL_IMAGES = [
    '/usr/share/virtualbox/VBoxGuestAdditions.iso',
    '/usr/lib/vmware/isoimages/windows.iso',
    '/usr/lib/vmware/isoimages/linux.iso',
]

@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which('7z') is None, reason='7z is not installed')
@pytest.mark.parametrize('path', LOCAL_IMAGES)
async def test_iso_local_7z(path, tmp_path):
    if os.path.isfile(path) is False:
        pytest.skip('%s is not installed' % path)
    expected = extract_7z(path, tmp_path.joinpath('out'))
    assert len(expected) > 0
    ds = await FileSource.from_file(path)
    image = await ISOImage.from_datasource(ds)
    views = await image.list_partitions()
    contents = {}
    for view in views:
        fs = await view.mount()
        _, files = await list_tree(fs)
        found = {}
        for file_path in files:
            entry = await fs.get_record_by_path(file_path)
            if entry.is_symlink() is True:
                continue
            data = await read_file(fs, file_path)
            found[file_path] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        contents[view.kind] = found
    # 7z shows the Joliet tree if there is one
    if VIEW_JOLIET in contents:
        assert contents[VIEW_JOLIET] == expected
    # the trees have the same files, apart from the name translation tables of the primary tree
    hashes = [sorted(item['sha256'] for name, item in found.items() if name.split('\\')[-1] != 'TRANS.TBL') for found in contents.values()]
    assert all(item == hashes[0] for item in hashes)
