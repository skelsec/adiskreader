"""
Tests of the WIM files made by Windows (DISM), see make_wim.ps1.
Skipped when testfiles/adiskreader_wim_fixtures.zip is not there.
"""

import pytest
import hashlib
import os
from config import *
from checks import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource.file import FileSource
from adiskreader.filesystems.wim import WIM
from adiskreader.filesystems.wim.structures import WIMFileAttribute

pytestmark = pytest.mark.skipif(has_wim_fixtures() is False, reason='WIM files made by Windows are not available')

# name in the manifest -> compression
WIMS = {'none' : None, 'fast' : 'XPRESS', 'max' : 'LZX', 'split' : 'LZX'}
# the other attributes are up to DISM
CHECKED_ATTRIBUTES = WIMFileAttribute.READONLY | WIMFileAttribute.HIDDEN | WIMFileAttribute.DIRECTORY

def get_wim(name):
    folder, manifest = get_wim_fixtures()
    for wim in manifest['wims']:
        if wim['name'] == name:
            return folder.joinpath(wim['file']), wim, manifest['trees']
    return None, None, None

async def check_tree(fs, tree):
    await check_file_list(fs, tree)
    count = 0
    async for root, dirs, files in fs.walk():
        count += len(dirs) + len(files)
    assert count == len(tree['dirs']) + len(tree['files'])
    await check_file_fullread(fs, tree)
    await check_file_ops(fs, tree)

    for dir_path in tree['dirs']:
        assert (await fs.get_record_by_path(dir_path)).is_directory() is True
    for file_path, expected in tree['files'].items():
        entry = await fs.get_record_by_path(file_path)
        assert entry.is_directory() is False
        st = await fs.stat(file_path)
        assert st.st_size == expected['size']
        assert st.st_mtime == expected['mtime'] // 10000000 - 11644473600
        assert entry.header.attributes & CHECKED_ATTRIBUTES == expected['attributes'] & CHECKED_ATTRIBUTES
        assert sorted(entry.get_stream_names()) == sorted(expected['streams'])
        for stream, expected_stream in expected['streams'].items():
            assert entry.get_size(stream) == expected_stream['size']
            f = await fs.open('%s:%s' % (file_path, stream))
            assert hashlib.sha256(await f.read()).hexdigest() == expected_stream['sha256']

@pytest.mark.asyncio
@pytest.mark.parametrize('name', list(WIMS))
async def test_wim_windows(name):
    path, expected, trees = get_wim(name)
    ds = await FileSource.from_file(str(path))
    wim = await WIM.from_datasource(ds)
    try:
        assert wim.header.compression == WIMS[name]
        assert wim.header.total_parts == len(expected['parts'])
        assert len(wim.images) == len(expected['images'])
        for image in expected['images']:
            info = wim.images[image['index'] - 1]
            assert info.name == image['name']
            assert info.description == image['description']
            fs = await info.mount()
            await check_tree(fs, trees[image['tree']])
    finally:
        await wim.close()
        await ds.close()

@pytest.mark.asyncio
async def test_wim_windows_hardlink():
    path, _, trees = get_wim('max')
    ds = await FileSource.from_file(str(path))
    wim = await WIM.from_datasource(ds)
    try:
        fs = await wim.mount(1)
        first = await fs.stat('hello.txt')
        second = await fs.stat('dir1\\hardlink.txt')
        assert first.st_ino != 0
        assert first.st_ino == second.st_ino
        assert first.st_ino != (await fs.stat('text.txt')).st_ino
    finally:
        await wim.close()
        await ds.close()

@pytest.mark.asyncio
async def test_wim_windows_esd():
    # LZMS and solid resources are not supported yet, the file must be recognized
    path, expected, _ = get_wim('recovery')
    if path is None:
        pytest.skip('No ESD file in the fixtures')
    ds = await FileSource.from_file(str(path))
    try:
        assert await WIM.is_wim(ds) is True
        with pytest.raises(NotImplementedError):
            wim = await WIM.from_datasource(ds)
            await wim.mount(1)
    finally:
        await ds.close()
