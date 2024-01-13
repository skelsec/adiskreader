import io
import math
from typing import List
from adiskreader.disks import Disk
from adiskreader.datasource import DataSource
from adiskreader.disks.vhdx.structures.headers import Headers, VHDX_KNOWN_REGIONS, BAT, MetaDataRegion
from cachetools import LRUCache

class VHDXDisk(Disk):
    def __init__(self):
        self.__stream = None
        self.headers = None
        self.active_region = None
        self.active_meta:MetaDataRegion = None
        self.active_bat:BAT = None
        self.lba_per_block = None
        self.use_buffer = True
        self.__lba_cache = LRUCache(maxsize=1000)
        self.__block_cache = LRUCache(maxsize=1000)
    
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
        await self.switch_active_region(1)

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

        self.__lba_cache = {}
        if self.use_buffer is True:
            self.__block_cache = {}

    def __add_to_cache_return(self, lba:int, data:bytes):
        self.__lba_cache[lba] = data
        return data
    
    async def read_block(self, block_idx:int):
        print('Reading block %s' % block_idx)
        if self.active_meta.LeaveBlockAllocated is False:
            # this is a dynamic disk, every chunk has a bitmap
            bitmap_block_cnt = block_idx // self.active_meta.ChunkRatio
        else:
            # fixed disk, it doesn't have a bitmap
            bitmap_block_cnt = 0
        
        entry = self.active_bat.entries[block_idx + bitmap_block_cnt]
        if entry[0] != 6:
            print('Block %s is not allocated' % block_idx)
            return b'\x00' * self.active_meta.BlockSize
        
        offset = entry[1]
        await self.__stream.seek(offset, 0)
        data = await self.__stream.read(self.active_meta.BlockSize)
        if self.use_buffer is True:
            self.__block_cache[block_idx] = data
        if len(data) != self.active_meta.BlockSize:
            raise Exception('Block size mismatch')
        return data

    async def read_LBAs(self, lbas:List[int]):
        # Check if LBAs are contiguous
        sorted_lbas = sorted(lbas)
        if any(sorted_lbas[i] + 1 != sorted_lbas[i + 1] for i in range(len(sorted_lbas) - 1)):
            raise Exception('LBAs are not contiguous')

        # Get the range of blocks to read
        first_lba = sorted_lbas[0]
        last_lba = sorted_lbas[-1]

        first_block_idx = math.floor(first_lba / self.active_meta.lba_per_block)
        last_block_idx = math.ceil(last_lba / self.active_meta.lba_per_block)

        # Read the blocks
        block_data = io.BytesIO()
        for block_idx in range(first_block_idx, last_block_idx + 1):
            if block_idx in self.__block_cache:
                temp = self.__block_cache[block_idx]
                block_data.write(temp)
            else:
                temp = await self.read_block(block_idx)
                block_data.write(temp)

        if (last_block_idx - first_block_idx) > 1:
            print('Multiple blocks read')
        # Calculate the start offset
        start_block_lba = first_block_idx * self.active_meta.lba_per_block
        start_offset = (first_lba - start_block_lba) * self.active_meta.LogicalSectorSize

        # Calculate the total length of data to extract
        total_length = ((last_lba - first_lba + 1) * self.active_meta.LogicalSectorSize)

        # Extract and return the relevant portion of block data
        block_data.seek(start_offset, 0)
        return block_data.read(total_length)


    async def read_LBA(self, lba:int):
        if lba in self.__lba_cache:
            return self.__lba_cache[lba]
        
        block_idx = lba // self.active_meta.lba_per_block
        block = self.__block_cache.get(block_idx, None)
        if block is None:
            block = await self.read_block(block_idx)
        offset_in_block = lba % self.active_meta.lba_per_block
        offset = offset_in_block * self.active_meta.LogicalSectorSize
        return self.__add_to_cache_return(lba, block[offset:offset+self.active_meta.LogicalSectorSize])
        