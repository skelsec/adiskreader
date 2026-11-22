
import io
import math
import uuid
from typing import List
from adiskreader.disks import Disk
from adiskreader.disks.parent import open_parent_disk, bitmap_runs
from adiskreader.datasource import DataSource
from adiskreader.disks.vhdx.structures.headers import Headers, VHDX_KNOWN_REGIONS, BAT, MetaDataRegion
from adiskreader.disks.vhdx.log import LogReplaySource, find_active_sequence
from cachetools import LRUCache

# states of the payload blocks in the BAT
BLOCK_NOT_PRESENT = 0
BLOCK_UNDEFINED = 1
BLOCK_ZERO = 2
BLOCK_UNMAPPED = 3
BLOCK_FULLY_PRESENT = 6
BLOCK_PARTIALLY_PRESENT = 7 # the sector bitmap tells which sectors are in this file and which are in the parent

class VHDXDisk(Disk):
    """
    VHDX disk (fixed, dynamic or differencing).
    The parent disk of a differencing disk is opened from the directory of the file, using the paths stored in it.
    """
    def __init__(self):
        self.__stream = None
        self.headers = None
        self.parent:VHDXDisk = None
        self.capacity = 0 # in sectors
        self.__siblings = [] # datasources opened by this disk
        self.__bitmap_cache = LRUCache(maxsize=1024)
        self.log_entries_replayed = 0
        self.active_region = None
        self.active_meta:MetaDataRegion = None
        self.active_bat:BAT = None
        self.lba_per_block = None
        self.use_buffer = True
        self.block_cache_size = 1024*1024*1024 # max bytes of blocks kept in memory
        self.__lba_cache = LRUCache(maxsize=1000)
        self.__block_cache = LRUCache(maxsize=1)
        self.__block_stats = {}

        self.__stats = {
            'read_block_full': 0,
            'read_block_partial': 0,
            'read_lba': 0,
            'read_lbas': 0,
            'single_block_read': 0,
            'multiple_block_read': 0,
            'block_cache_hit': 0,
            'block_cache_miss': 0,
            'lba_cache_hit': 0,
            'lba_cache_miss': 0
        }
    
    async def setup(self, ds:DataSource):
        self.__stream = ds
        await self.read_headers()
    
    @staticmethod
    async def from_datasource(ds:DataSource):
        disk = VHDXDisk()
        await disk.setup(ds)
        return disk

    async def read_headers(self):
        data = await self.__stream.read(1*1024*1024)
        self.headers = Headers.from_bytes(data)
        await self.replay_log()
        await self.switch_active_region(1)
        self.capacity = self.active_meta.VirtualDiskSize // self.active_meta.LogicalSectorSize
        if self.active_meta.HasParent is True:
            await self.open_parent()

    async def open_parent(self):
        locator = self.active_meta.ParentLocator
        if locator is None:
            raise Exception('Differencing disk without parent locator')
        linkage = []
        for key in ['parent_linkage', 'parent_linkage2']:
            try:
                linkage.append(uuid.UUID(locator.kv[key]))
            except Exception:
                pass
        names = [locator.kv.get(key) for key in ['relative_path', 'absolute_win32_path', 'volume_path']]
        self.parent, ds = await open_parent_disk(
            self.__stream, names, VHDXDisk,
            lambda disk: disk.headers.get_current_header().DataWriteGuid in linkage
        )
        self.__siblings.append(ds)

    async def close(self):
        """Closes the files opened by the disk, the datasource of the disk is to be closed by the caller"""
        if self.parent is not None:
            await self.parent.close()
            self.parent = None
        for ds in self.__siblings:
            await ds.close()
        self.__siblings = []

    async def replay_log(self):
        header = self.headers.get_current_header()
        if header.LogGuid.int == 0:
            # nothing to replay
            return

        await self.__stream.seek(header.LogOffset, 0)
        log = await self.__stream.read(header.LogLength)
        entries = find_active_sequence(log, header.LogGuid)
        if len(entries) == 0:
            return
        self.log_entries_replayed = len(entries)
        self.__stream = LogReplaySource(self.__stream, entries)

    def get_stats(self):
        return self.__stats

    async def switch_active_region(self, regionidx:int=None):
        if regionidx not in [1,2]:
            regionidx = 1
        
        if self.active_region == regionidx:
            return
        
        self.active_region = regionidx
        
        entries = self.headers.RegionTable.entries
        if self.active_region == 2:
            entries = self.headers.RegionTable2.entries
        
        for region in entries:
            region_type, region = await region.get_region(self.__stream)
            if region_type == '2DC27766-F623-4200-9D64-115E9BFD4A08':
                self.active_bat = region
            elif region_type == '8B7CA206-4790-4B9A-B8FE-575F050F886E':
                self.active_meta = region

        self.__lba_cache.clear()
        self.__bitmap_cache.clear()
        # blocks can be up to 256MB each, so the cache is limited by total size
        max_blocks = max(1, self.block_cache_size // self.active_meta.BlockSize)
        self.__block_cache = LRUCache(maxsize=max_blocks)

    def __add_to_cache_return(self, lba:int, data:bytes):
        self.__lba_cache[lba] = data
        return data
    
    #async def read_block(self, block_idx:int):
    #    print('Reading block %s' % block_idx)
    #    if self.active_meta.LeaveBlockAllocated is False:
    #        # this is a dynamic disk, every chunk has a bitmap
    #        bitmap_block_cnt = block_idx // self.active_meta.ChunkRatio
    #    else:
    #        # fixed disk, it doesn't have a bitmap
    #        bitmap_block_cnt = 0
    #    
    #    entry = self.active_bat.entries[block_idx + bitmap_block_cnt]
    #    if entry[0] != 6:
    #        print('Block %s is not allocated' % block_idx)
    #        return b'\x00' * self.active_meta.BlockSize
    #    
    #    offset = entry[1]
    #    await self.__stream.seek(offset, 0)
    #    data = await self.__stream.read(self.active_meta.BlockSize)
    #    if self.use_buffer is True:
    #        self.__block_cache[block_idx] = data
    #    if len(data) != self.active_meta.BlockSize:
    #        raise Exception('Block size mismatch')
    #    return data
    
    async def read_block(self, block_idx:int, start_offset:int=0, length:int=None):
        # a read from the start of the block is still partial if it has a length
        partial = start_offset != 0 or length is not None
        if length is None:
            length = self.active_meta.BlockSize - start_offset
        if partial is True:
            self.__stats['read_block_partial'] += 1
            self.__block_stats[block_idx] = self.__block_stats.get(block_idx, 0) + 1
            if self.__block_stats[block_idx] > 50:
                #print('Block %s is read partially more than 5000 times' % block_idx)
                # performing a full read as the block is heavily used
                await self.read_block(block_idx)
            #print('Partial read. IDX: %s, Offset: %s, Length: %s' % (block_idx, start_offset, length))
        else:
            self.__stats['read_block_full'] += 1
        # the entry of the sector bitmap block follows the entries of the payload blocks of each chunk
        state, block_offset = self.active_bat.entries[block_idx + block_idx // self.active_meta.ChunkRatio]
        if state not in (BLOCK_FULLY_PRESENT, BLOCK_PARTIALLY_PRESENT):
            if self.parent is not None and state in (BLOCK_NOT_PRESENT, BLOCK_UNDEFINED):
                return await self.read_parent(block_idx, start_offset, length)
            return b'\x00' * length

        merge = state == BLOCK_PARTIALLY_PRESENT and self.parent is not None
        if partial is True:
            # partial read, not storing in cache
            if block_idx in self.__block_cache:
                return self.__block_cache[block_idx][start_offset:start_offset+length]
            block_offset += start_offset
            await self.__stream.seek(block_offset, 0)
            data = await self.__stream.read(length)
            if merge is True:
                data = await self.merge_parent(block_idx, start_offset, data)
            return data

        else:
            if block_idx in self.__block_cache:
                return self.__block_cache[block_idx]
            await self.__stream.seek(block_offset, 0)
            data = await self.__stream.read(self.active_meta.BlockSize)
            if len(data) != self.active_meta.BlockSize:
                raise Exception('Block size mismatch')
            if merge is True:
                data = await self.merge_parent(block_idx, 0, data)
            if self.use_buffer is True:
                # the cached block has the data of the parent as well
                self.__block_cache[block_idx] = data
        return data

    async def read_parent(self, block_idx:int, start_offset:int, length:int):
        # the parent can have a different block size
        sector_size = self.active_meta.LogicalSectorSize
        lba = block_idx * self.active_meta.lba_per_block + start_offset // sector_size
        data = await self.parent.read_sectors(lba, length // sector_size)
        return data + b'\x00' * (length - len(data))

    async def read_sector_bitmap(self, block_idx:int):
        """Returns the part of the sector bitmap which belongs to the block"""
        bitmap = self.__bitmap_cache.get(block_idx)
        if bitmap is None:
            chunk_ratio = self.active_meta.ChunkRatio
            size = self.active_meta.lba_per_block // 8
            chunk_idx = block_idx // chunk_ratio
            state, offset = self.active_bat.entries[chunk_idx * (chunk_ratio + 1) + chunk_ratio]
            if state != BLOCK_FULLY_PRESENT:
                raise Exception('Sector bitmap of block %s is not present' % block_idx)
            await self.__stream.seek(offset + (block_idx % chunk_ratio) * size, 0)
            bitmap = await self.__stream.read(size)
            if len(bitmap) != size:
                raise Exception('Sector bitmap of block %s is truncated' % block_idx)
            self.__bitmap_cache[block_idx] = bitmap
        return bitmap

    async def merge_parent(self, block_idx:int, start_offset:int, data:bytes):
        """Replaces the sectors of the partially present block which are not stored in this file"""
        sector_size = self.active_meta.LogicalSectorSize
        bitmap = await self.read_sector_bitmap(block_idx)
        runs = bitmap_runs(bitmap, start_offset // sector_size, len(data) // sector_size, False)
        if len(runs) == 1 and runs[0][1] is True:
            return data
        res = []
        offset = 0
        for size, present in runs:
            size *= sector_size
            if present is True:
                res.append(data[offset:offset+size])
            else:
                res.append(await self.read_parent(block_idx, start_offset + offset, size))
            offset += size
        return b''.join(res)

    async def read_LBAs(self, lbas:List[int]):
        if len(lbas) == 1:
            return await self.read_LBA(lbas[0])

        res = []
        start = 0
        for i in range(1, len(lbas) + 1):
            if i == len(lbas) or lbas[i] != lbas[i-1] + 1:
                res.append(await self.read_sectors(lbas[start], i - start))
                start = i
        return b''.join(res)

    async def read_sectors(self, sector:int, count:int):
        """Reads consecutive sectors, the data is shorter if the end of the disk is reached"""
        count = min(count, self.capacity - sector)
        if count <= 0:
            return b''

        # Get the range of blocks to read
        first_lba = sector
        last_lba = sector + count - 1

        first_block_idx = math.floor(first_lba / self.active_meta.lba_per_block)
        #last_block_idx = math.ceil(last_lba / self.active_meta.lba_per_block)

        last_block_idx = math.floor(last_lba / self.active_meta.lba_per_block)

        # If the range is small enough and within one block, do partial read on the block and extract the data
        if first_block_idx == last_block_idx:
            self.__stats['single_block_read'] += 1
            offset = (first_lba % self.active_meta.lba_per_block) * self.active_meta.LogicalSectorSize
            if first_block_idx in self.__block_cache:
                # only the part needed is copied, the blocks are large
                self.__stats['block_cache_hit'] += 1
                return self.__block_cache[first_block_idx][offset:offset + count * self.active_meta.LogicalSectorSize]
            if count < 3000:
                return await self.read_block(first_block_idx, start_offset=offset, length=count * self.active_meta.LogicalSectorSize)
        else:
            self.__stats['multiple_block_read'] += 1

        # Read the blocks
        block_data = io.BytesIO()
        for block_idx in range(first_block_idx, last_block_idx + 1):
            if block_idx in self.__block_cache:
                #print('Block %s found in cache' % block_idx)
                self.__stats['block_cache_hit'] += 1
                temp = self.__block_cache[block_idx]
                block_data.write(temp)
            else:
                #print('Reading FULL block %s for %s lbas' % (block_idx, len(lbas)))
                self.__stats['block_cache_miss'] += 1
                temp = await self.read_block(block_idx)
                block_data.write(temp)

        #if (last_block_idx - first_block_idx) > 1:
        #    print('Multiple blocks read')
        # Calculate the start offset
        start_block_lba = first_block_idx * self.active_meta.lba_per_block
        start_offset = (first_lba - start_block_lba) * self.active_meta.LogicalSectorSize

        # Calculate the total length of data to extract
        total_length = ((last_lba - first_lba + 1) * self.active_meta.LogicalSectorSize)

        # Extract and return the relevant portion of block data
        block_data.seek(start_offset, 0)
        return block_data.read(total_length)


    async def read_LBA(self, lba:int):
        self.__stats['read_lba'] += 1
        if lba in self.__lba_cache:
            self.__stats['lba_cache_hit'] += 1
            return self.__lba_cache[lba]
        
        self.__stats['lba_cache_miss'] += 1
        offset_in_block = lba % self.active_meta.lba_per_block
        offset = offset_in_block * self.active_meta.LogicalSectorSize

        block_idx = lba // self.active_meta.lba_per_block
        if block_idx in self.__block_cache:
            self.__stats['block_cache_hit'] += 1
            block = self.__block_cache[block_idx]
            data = block[offset:offset+self.active_meta.LogicalSectorSize]

        block = self.__block_cache.get(block_idx, None)
        if block is None:
            self.__stats['block_cache_miss'] += 1
            data = await self.read_block(block_idx, start_offset=offset, length=self.active_meta.LogicalSectorSize)
        
        return self.__add_to_cache_return(lba, data)

    def __str__(self):
        t = []
        t.append('Type       : VHDX Disk')
        t.append('Datasource : %s' % self.__stream)
        t.append('Capacity   : %d' % self.active_meta.VirtualDiskSize)
        t.append('Block size : %d' % self.active_meta.BlockSize)
        if self.log_entries_replayed > 0:
            t.append('Log entries: %d' % self.log_entries_replayed)
        if self.parent is not None:
            t.append('Parent     : %s' % self.active_meta.ParentLocator.kv.get('absolute_win32_path'))
        return '\n'.join(t)
