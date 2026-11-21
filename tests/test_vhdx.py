import pytest
import hashlib
import os
import uuid
from config import *

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from adiskreader.datasource import DataSource
from adiskreader.datasource.file import FileSource
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.disks.vhdx.log import LogReplaySource, find_active_sequence, LOG_SECTOR_SIZE
from adiskreader.utils import crc32c

# sha256 of the virtual disk of small_ntfs_test.vhdx after its log is replayed, created with:
#   qemu-img check -r all small_ntfs_test.vhdx
#   qemu-img convert -f vhdx -O raw small_ntfs_test.vhdx small_ntfs_test.img
VHDX_LOG_DISK_SHA256 = '759cc6aaf5af4887c977d8b0638e3f6567dea807a0b1b0cc7310bcaec36ed2fc'


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

async def sha256_disk(disk, lbas_per_read):
    meta = disk.active_meta
    total = meta.VirtualDiskSize // meta.LogicalSectorSize
    h = hashlib.sha256()
    for lba in range(0, total, lbas_per_read):
        data = await disk.read_LBAs(list(range(lba, min(total, lba + lbas_per_read))))
        h.update(data)
    return h.hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize('image', [NTFS_TEST_FILE_VHDX_DYNAMIC_GZ, NTFS_TEST_FILE_VHDX_FIXED_GZ])
@pytest.mark.parametrize('lbas_per_read', [2048, 100000])
async def test_vhdx_disk_matches_raw(image, lbas_per_read):
    ds = await FileSource.from_file(get_image(image))
    disk = await VHDXDisk.from_datasource(ds)
    assert disk.log_entries_replayed == 0
    assert await sha256_disk(disk, lbas_per_read) == sha256_file(get_image(NTFS_TEST_FILE_GZ))

@pytest.mark.asyncio
@pytest.mark.parametrize('lbas_per_read', [2048, 100000])
async def test_vhdx_log_replay(lbas_per_read):
    ds = await FileSource.from_file(get_image(NTFS_TEST_FILE_VHDX_GZ))
    disk = await VHDXDisk.from_datasource(ds)
    assert disk.log_entries_replayed == 2
    assert await sha256_disk(disk, lbas_per_read) == VHDX_LOG_DISK_SHA256

@pytest.mark.asyncio
async def test_vhdx_first_lba_of_block():
    ds = await FileSource.from_file(get_image(NTFS_TEST_FILE_VHDX_DYNAMIC_GZ))
    disk = await VHDXDisk.from_datasource(ds)
    for block_idx in range(2):
        data = await disk.read_LBA(block_idx * disk.active_meta.lba_per_block)
        assert len(data) == disk.active_meta.LogicalSectorSize

########################################## LOG ##########################################

LOG_GUID = uuid.UUID('11111111-2222-3333-4444-555555555555')

def build_log_entry(sequence, tail, writes, logguid = LOG_GUID):
    """writes: list of (fileoffset, 4096 bytes of data) or (fileoffset, length of the zeroed range)"""
    descriptors = b''
    sectors = b''
    for fileoffset, data in writes:
        if isinstance(data, int):
            descriptors += b'zero' + b'\x00'*4 + data.to_bytes(8, 'little')
        else:
            descriptors += b'desc' + data[-4:] + data[:8]
            seq = sequence.to_bytes(8, 'little')
            sectors += b'data' + seq[4:] + data[8:-4] + seq[:4]
        descriptors += fileoffset.to_bytes(8, 'little') + sequence.to_bytes(8, 'little')

    header_len = 64 + len(descriptors)
    header_len += -header_len % LOG_SECTOR_SIZE
    entry = b'loge' + b'\x00'*4
    entry += (header_len + len(sectors)).to_bytes(4, 'little')
    entry += tail.to_bytes(4, 'little')
    entry += sequence.to_bytes(8, 'little')
    entry += len(writes).to_bytes(4, 'little') + b'\x00'*4
    entry += logguid.bytes_le
    entry += (0).to_bytes(8, 'little') + (0).to_bytes(8, 'little')
    entry += descriptors
    entry = entry.ljust(header_len, b'\x00') + sectors
    return entry[:4] + crc32c(entry).to_bytes(4, 'little') + entry[8:]

def build_log(length, entries):
    """entries: list of (offset in the log, entry), entries wrap around the end of the log"""
    log = bytearray(length)
    for offset, entry in entries:
        for i, b in enumerate(entry):
            log[(offset + i) % length] = b
    return bytes(log)

def sector(b):
    return bytes([b]) * LOG_SECTOR_SIZE

def test_log_empty():
    assert find_active_sequence(b'\x00' * 0x10000, LOG_GUID) == []

def test_log_sequence():
    e1 = build_log_entry(10, 0x2000, [(0x300000, sector(1))])
    e2 = build_log_entry(11, 0x2000, [(0x301000, sector(2)), (0x400000, 0x8000)])
    log = build_log(0x10000, [(0x2000, e1), (0x2000 + len(e1), e2)])
    active = find_active_sequence(log, LOG_GUID)
    assert [e.header.SequenceNumber for e in active] == [10, 11]
    assert active[0].writes == [(0x300000, 4096, sector(1))]
    assert active[1].writes == [(0x301000, 4096, sector(2)), (0x400000, 0x8000, None)]

def test_log_wraps_around():
    e1 = build_log_entry(10, 0xE000, [(0x300000, sector(1))])
    e2 = build_log_entry(11, 0xE000, [(0x301000, sector(2))])
    log = build_log(0x10000, [(0xE000, e1), (0xE000 + len(e1), e2)])
    assert log[:4] == b'loge' # the second entry starts at the beginning of the log
    active = find_active_sequence(log, LOG_GUID)
    assert [e.header.SequenceNumber for e in active] == [10, 11]

def test_log_data_sector_wraps_around():
    e1 = build_log_entry(10, 0xF000, [(0x300000, sector(1))])
    log = build_log(0x10000, [(0xF000, e1)])
    active = find_active_sequence(log, LOG_GUID)
    assert [e.writes for e in active] == [[(0x300000, 4096, sector(1))]]

def test_log_newest_sequence_wins():
    old = build_log_entry(5, 0x0000, [(0x300000, sector(1))])
    new = build_log_entry(20, 0x8000, [(0x300000, sector(2))])
    log = build_log(0x10000, [(0x0000, old), (0x8000, new)])
    active = find_active_sequence(log, LOG_GUID)
    assert [e.header.SequenceNumber for e in active] == [20]

def test_log_starts_at_tail_of_head():
    # the first entry is already flushed, the head says the sequence starts at the second
    e1 = build_log_entry(10, 0x0000, [(0x300000, sector(1))])
    e2 = build_log_entry(11, 0x2000, [(0x301000, sector(2))])
    e3 = build_log_entry(12, 0x2000, [(0x302000, sector(3))])
    log = build_log(0x10000, [(0x0000, e1), (0x2000, e2), (0x4000, e3)])
    active = find_active_sequence(log, LOG_GUID)
    assert [e.header.SequenceNumber for e in active] == [11, 12]

def test_log_corrupt_entry_ends_sequence():
    e1 = build_log_entry(10, 0x0000, [(0x300000, sector(1))])
    e2 = build_log_entry(11, 0x0000, [(0x301000, sector(2))])
    log = bytearray(build_log(0x10000, [(0x0000, e1), (0x2000, e2)]))
    log[0x3000 + 100] ^= 0xFF # in the data sector of the second entry
    active = find_active_sequence(bytes(log), LOG_GUID)
    assert [e.header.SequenceNumber for e in active] == [10]

def test_log_other_guid_ignored():
    e1 = build_log_entry(10, 0x0000, [(0x300000, sector(1))], logguid = uuid.UUID(int=7))
    log = build_log(0x10000, [(0x0000, e1)])
    assert find_active_sequence(log, LOG_GUID) == []


class BytesSource(DataSource):
    def __init__(self, data):
        super().__init__({})
        self.data = data
        self.offset = 0

    async def read(self, size:int):
        data = self.data[self.offset:self.offset+size]
        self.offset += len(data)
        return data

    async def seek(self, offset:int, whence:int = 0):
        self.offset = offset

    async def tell(self):
        return self.offset

@pytest.mark.asyncio
async def test_log_replay_source():
    original = bytes(range(256)) * 64 # 16KB
    e1 = build_log_entry(10, 0, [(0x1000, sector(1)), (0x2000, sector(2))])
    # the later entry overwrites the earlier one, zeroes a range and writes after the end of the file
    e2 = build_log_entry(11, 0, [(0x1000, sector(3)), (0x3000, 0x1000), (0x5000, sector(4))])
    log = build_log(0x10000, [(0, e1), (len(e1), e2)])
    ds = LogReplaySource(BytesSource(original), find_active_sequence(log, LOG_GUID))

    expected = original[:0x1000] + sector(3) + sector(2) + sector(0) + sector(0) + sector(4)
    assert await ds.read(0x10000) == expected
    assert await ds.tell() == len(expected)

    # reads crossing the boundaries of the writes
    for offset, size in [(0, 10), (0xFF0, 0x20), (0x1FF0, 0x1020), (0x2FFF, 2), (0x3800, 0x3000), (0x100, 0x200)]:
        await ds.seek(offset, 0)
        assert await ds.read(size) == expected[offset:offset+size]
    # sequential reads continue where the previous one ended
    await ds.seek(0xF00, 0)
    assert await ds.read(0x200) + await ds.read(0x200) == expected[0xF00:0x1300]
