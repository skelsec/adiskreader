"""
Tests of the differencing VHD and VHDX disks. The images are made by Windows (make_diff_vhd.ps1),
the expected contents of each disk is what Windows read from it.
"""

import pytest
import gzip
import hashlib
import ntpath
import os
import random
import shutil
from config import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource import DataSource
from adiskreader.datasource.file import FileSource
from adiskreader.disks import Disk
from adiskreader.disks.parent import parent_candidates, bitmap_runs
from adiskreader.disks.vhd import VHDDisk
from adiskreader.disks.vhd.structures import VHDType
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.filesystems.ntfs import NTFS

CHAINS = ['vhd', 'vhdfixed', 'vhdx', 'vhdxfixed']
LEVELS = ['base', 'child1', 'child2', 'sub']
IMAGES = ['%s_%s' % (chain, level) for chain in CHAINS for level in LEVELS]

async def open_disk(path):
    ds = await FileSource.from_file(str(path))
    try:
        return ds, await Disk.from_datasource(ds)
    except Exception:
        await ds.close()
        raise

def pattern(size, seed):
    # PatternFile of make_diff_vhd.ps1
    return b''.join(bytes([(seed + i) & 0xFF]) * 4096 for i in range(size // 4096))


def test_manifest():
    _, images = get_diff_fixtures()
    assert sorted('%s_%s' % (image['chain'], image['level']) for image in images) == sorted(IMAGES)

@pytest.mark.asyncio
@pytest.mark.parametrize('name', IMAGES)
async def test_diff_disk(name):
    path, image = get_diff_image(name)
    folder, _ = get_diff_fixtures()
    ds, disk = await open_disk(path)
    try:
        assert isinstance(disk, VHDXDisk if path.suffix == '.vhdx' else VHDDisk)
        assert disk.capacity * 512 == image['disk_size']

        # every level of the chain is opened
        depth = 0
        parent = disk.parent
        while parent is not None:
            depth += 1
            parent = parent.parent
        assert depth == LEVELS.index(image['level'])

        h = hashlib.sha256()
        with gzip.open(folder.joinpath(image['raw']), 'rb') as raw:
            for lba in range(0, disk.capacity, 4096):
                data = await disk.read_LBAs(list(range(lba, min(disk.capacity, lba + 4096))))
                assert data == raw.read(4096 * 512)
                h.update(data)
        assert h.hexdigest() == image['raw_sha256']
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['vhd_sub', 'vhdfixed_base', 'vhdfixed_child1', 'vhdx_sub', 'vhdxfixed_child2'])
async def test_diff_random_reads(name):
    path, image = get_diff_image(name)
    expected = get_diff_expected(name)
    ds, disk = await open_disk(path)
    try:
        rng = random.Random(0)
        # around the data written directly to the disk and anywhere else
        offsets = [write[0] // 512 for write in image['raw_writes']] + [(write[0] + write[1]) // 512 for write in image['raw_writes']]
        for i in range(400):
            if i % 2 == 0:
                lba = max(0, rng.choice(offsets) + rng.randint(-20, 20))
            else:
                lba = rng.randint(0, disk.capacity - 1)
            count = rng.choice([1, 1, 2, 7, 8, 127, 128, 129, 2999, 3000, 5000, 70000])
            assert await disk.read_sectors(lba, count) == expected[lba*512:(lba+count)*512]
            assert await disk.read_LBA(lba) == expected[lba*512:(lba+1)*512]
        # the last sectors, the read is truncated at the end of the disk
        assert await disk.read_sectors(disk.capacity - 3, 10) == expected[-3*512:]
        assert await disk.read_sectors(disk.capacity, 10) == b''
        # LBAs which are not next to each other
        lbas = [5, 6, 7, 100, 3, 4, 300000, 300001]
        assert await disk.read_LBAs(lbas) == b''.join(expected[lba*512:(lba+1)*512] for lba in lbas)
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('chain', CHAINS)
@pytest.mark.parametrize('level', LEVELS)
async def test_diff_ntfs(chain, level):
    # the files each level of make_diff_vhd.ps1 created, changed or deleted
    depth = LEVELS.index(level)
    overwrite = pattern(8*1024*1024, 3)
    if depth >= 2:
        overwrite = overwrite[:3*1024*1024] + b'\xBB' * (1024*1024) + overwrite[4*1024*1024:]
    files = {
        'hello.txt' : b'base\r\n' if depth == 0 else b'child1\r\n',
        'dir1\\pattern_a.bin' : pattern(4*1024*1024, 1),
        'dir1\\delete_me.bin' : pattern(2*1024*1024, 2) if depth == 0 else None,
        'dir2\\pattern_c.bin' : pattern(6*1024*1024, 4) if depth >= 1 else None,
        'overwrite.bin' : overwrite,
        'child2.txt' : b'child2\r\n' if depth >= 2 else None,
        'sub.txt' : b'sub\r\n' if depth >= 3 else None,
    }

    path, _ = get_diff_image('%s_%s' % (chain, level))
    ds, disk = await open_disk(path)
    try:
        partitions = await disk.list_partitions()
        fs = await NTFS.from_partition(partitions[0])
        paths = []
        async for root, dirs, filenames in fs.walk():
            for filename in filenames:
                paths.append(ntpath.join(str(root), str(filename)).lstrip('\\'))
        for filepath, expected in files.items():
            if expected is None:
                assert filepath not in paths
                continue
            assert filepath in paths
            f = await fs.open(filepath)
            assert await f.read() == expected
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
async def test_diff_detection():
    # the type of the disk is detected from the extension of the url
    for name, disktype in [('vhd_child1', VHDDisk), ('vhdx_child1', VHDXDisk)]:
        path, _ = get_diff_image(name)
        ds = await DataSource.from_url('file://' + str(path))
        try:
            disk = await Disk.from_datasource(ds)
            assert isinstance(disk, disktype)
            assert isinstance(disk.parent, disktype)
            assert len(await disk.read_sectors(0, 8)) == 8 * 512
            assert str(disk).find('Parent') != -1
            await disk.close()
        finally:
            await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('chain', ['vhd', 'vhdx'])
async def test_diff_moved_chain(chain, tmp_path):
    # all files of the chain are in one directory, the relative path stored in the last disk (..\) is not valid anymore
    for level in LEVELS:
        path, image = get_diff_image('%s_%s' % (chain, level))
        shutil.copy(path, tmp_path.joinpath(path.name))
    ds, disk = await open_disk(tmp_path.joinpath(path.name))
    try:
        assert disk.parent.parent.parent is not None
        h = hashlib.sha256()
        for lba in range(0, disk.capacity, 8192):
            h.update(await disk.read_sectors(lba, 8192))
        assert h.hexdigest() == image['raw_sha256']
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('chain', ['vhd', 'vhdx'])
async def test_diff_missing_parent(chain, tmp_path):
    path, _ = get_diff_image('%s_child1' % chain)
    shutil.copy(path, tmp_path.joinpath(path.name))
    with pytest.raises(Exception, match='Parent disk not found'):
        await open_disk(tmp_path.joinpath(path.name))

@pytest.mark.asyncio
@pytest.mark.parametrize('chain', ['vhd', 'vhdx'])
async def test_diff_wrong_parent(chain, tmp_path, caplog):
    # the file found at the path of the parent is another disk, it is used but a warning is logged
    path, _ = get_diff_image('%s_child2' % chain)
    base_path, base_image = get_diff_image('%s_base' % chain)
    shutil.copy(path, tmp_path.joinpath(path.name))
    shutil.copy(base_path, tmp_path.joinpath(path.name.replace('child2', 'child1')))
    ds, disk = await open_disk(tmp_path.joinpath(path.name))
    try:
        assert 'is not the one the disk was created from' in caplog.text
        assert disk.parent.parent is None
        # sectors not changed by any of the children
        with gzip.open(get_diff_fixtures()[0].joinpath(base_image['raw']), 'rb') as raw:
            raw.seek(120*1024*1024 + 8192)
            assert await disk.read_sectors((120*1024*1024 + 8192) // 512, 16) == raw.read(16 * 512) == b'\x42' * (16 * 512)
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
async def test_vhd_footer_copy(tmp_path):
    # the footer at the end of the file is damaged, the copy at the start is used
    path, image = get_diff_image('vhd_base')
    damaged = tmp_path.joinpath(path.name)
    shutil.copy(path, damaged)
    with open(damaged, 'r+b') as f:
        f.seek(-512 + 48, 2)
        f.write(b'\x00' * 8) # CurrentSize
    ds, disk = await open_disk(damaged)
    try:
        assert disk.footer_bottom.valid is False
        assert disk.active_footer is disk.footer_top
        assert disk.active_footer.DiskType == VHDType.DYNAMIC
        assert disk.capacity * 512 == image['disk_size']
    finally:
        await disk.close()
        await ds.close()

@pytest.mark.asyncio
async def test_not_a_vhd(tmp_path):
    path = tmp_path.joinpath('notavhd.vhd')
    path.write_bytes(b'\x01' * 4096)
    with pytest.raises(Exception, match='Not a VHD file'):
        await open_disk(path)

def test_parent_candidates():
    assert parent_candidates(['.\\base.vhdx', 'C:\\dir\\base.vhdx', '\\\\?\\Volume{1}\\dir\\base.vhdx']) == ['base.vhdx']
    assert parent_candidates(['..\\base.vhd', 'C:\\dir\\base.vhd']) == ['..\\base.vhd', 'base.vhd']
    assert parent_candidates(['sub/dir/base.vhd', None, '']) == ['sub\\dir\\base.vhd', 'base.vhd']
    assert parent_candidates(['C:\\dir\\a.vhd', 'D:\\other\\b.vhd']) == ['a.vhd', 'b.vhd']
    assert parent_candidates([None, '', '..', 'C:\\']) == []

def test_bitmap_runs():
    assert bitmap_runs(b'\xff\xff', 0, 16, True) == [(16, True)]
    assert bitmap_runs(b'\x00\x00', 3, 10, False) == [(10, False)]
    # the first sector is the most significant bit in vhd, the least significant in vhdx
    assert bitmap_runs(b'\x80\x01', 0, 16, True) == [(1, True), (14, False), (1, True)]
    assert bitmap_runs(b'\x80\x01', 0, 16, False) == [(7, False), (2, True), (7, False)]
    assert bitmap_runs(b'\x0e', 0, 8, False) == [(1, False), (3, True), (4, False)]
    assert bitmap_runs(b'\x70', 0, 8, True) == [(1, False), (3, True), (4, False)]
    assert bitmap_runs(b'\xff\x00\xff\x0f', 4, 24, False) == [(4, True), (8, False), (12, True)]
    assert bitmap_runs(b'\xff', 2, 0, True) == []
    for msb_first in [True, False]:
        rng = random.Random(1)
        bitmap = bytes(rng.choice([0, 0xFF, rng.randint(0, 255)]) for _ in range(64))
        for _ in range(200):
            start = rng.randint(0, 511)
            count = rng.randint(0, 512 - start)
            bits = []
            for size, value in bitmap_runs(bitmap, start, count, msb_first):
                bits += [value] * size
            assert bits == [(bitmap[i >> 3] >> ((7 - (i & 7)) if msb_first else (i & 7))) & 1 == 1 for i in range(start, start + count)]
