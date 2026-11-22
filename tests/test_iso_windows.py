"""
Tests of the disc images made by Windows (IMAPI2), see make_iso.ps1.
Skipped when testfiles/adiskreader_iso_fixtures.zip is not there.
"""

import pytest
import hashlib
import ntpath
import os
from config import *
from checks import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource.file import FileSource
from adiskreader.filesystems.iso import ISOImage, VIEW_UDF, VIEW_ROCKRIDGE, VIEW_JOLIET, VIEW_ISO9660

pytestmark = pytest.mark.skipif(has_iso_fixtures() is False, reason='disc images made by Windows are not available')

# names of the images in the manifest, the ones Windows could not create are skipped
IMAGES = ['iso9660', 'joliet', 'bridge', 'udf102', 'udf150', 'udf200', 'udf201', 'udf250', 'udf260']
FILETIME_EPOCH = 116444736000000000
UDF_NAME_MAX = 127

def imapi_name(path):
    """IMAPI2 cuts the names longer than 127 characters, the same way as the short names are made"""
    head, name = ntpath.split(path)
    if len(name) > UDF_NAME_MAX:
        stem, ext = ntpath.splitext(name)
        name = stem[:UDF_NAME_MAX - len(ext) - 2] + '~1' + ext
    return ntpath.join(head, name)

def get_iso(name):
    folder, manifest = get_iso_fixtures()
    for image in manifest['images']:
        if image['name'] == name:
            # the trees are written to the root of the image
            tree = {'dirs': [], 'files': {}}
            for tree_name in image['trees']:
                tree['dirs'] += manifest['trees'][tree_name]['dirs']
                for file_path, item in manifest['trees'][tree_name]['files'].items():
                    tree['files'][imapi_name(file_path)] = item
            return folder.joinpath(image['file']), image, tree
    pytest.skip('%s was not created by Windows' % name)

async def check_tree(fs, tree):
    await check_file_list(fs, tree)
    count = 0
    async for root, dirs, files in fs.walk():
        count += len(dirs) + len(files)
    assert count == len(tree['dirs']) + len(tree['files'])
    await check_file_stat(fs, tree)
    await check_file_fullread(fs, tree)
    await check_file_ops(fs, tree)

    for dir_path in tree['dirs']:
        assert (await fs.get_record_by_path(dir_path)).is_directory() is True
    for file_path, expected in tree['files'].items():
        entry = await fs.get_record_by_path(file_path)
        assert entry.is_directory() is False
        # ISO9660 stores the seconds only
        mtime = (expected['mtime'] - FILETIME_EPOCH) / 10000000
        assert abs(entry.mtime.timestamp() - mtime) < 2

async def check_contents(fs, tree):
    """The names of the primary tree of ISO9660 are made up by the writer, only the contents can be compared"""
    found = []
    dircount = 0
    async for root, dirs, files in fs.walk():
        dircount += len(dirs)
        for name in files:
            f = await fs.open(root.rstrip('\\') + '\\' + name)
            data = await f.read()
            found.append((len(data), hashlib.sha256(data).hexdigest()))
    assert dircount == len(tree['dirs'])
    assert sorted(found) == sorted((item['size'], item['sha256']) for item in tree['files'].values())


@pytest.mark.asyncio
@pytest.mark.parametrize('name', IMAGES)
async def test_iso_windows(name):
    path, info, tree = get_iso(name)
    ds = await FileSource.from_file(str(path))
    assert await ISOImage.is_iso(ds) is True
    image = await ISOImage.from_datasource(ds)
    views = await image.list_partitions()

    expected = []
    if info['udf'] is True:
        expected.append(VIEW_UDF)
    if info['joliet'] is True:
        expected.append(VIEW_JOLIET)
    if info['iso9660'] is True:
        expected.append(VIEW_ISO9660)
    assert [view.kind for view in views] == expected

    for view in views:
        assert view.volume_name == info['volume_name']
        fs = await view.mount()
        if view.kind == VIEW_ISO9660:
            await check_contents(fs, tree)
            continue
        await check_tree(fs, tree)
        if view.kind == VIEW_UDF:
            assert fs.revision == info['udf_revision']
            assert fs.block_size == info['block_size']
