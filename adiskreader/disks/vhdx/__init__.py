import io
import copy
import math
from typing import List
from adiskreader.disks import Disk
from adiskreader.disks.vhdx.structures.headers import Headers, VHDX_KNOWN_REGIONS, BAT, MetaDataRegion
from cachetools import cached, LRUCache

class VHDXDisk(Disk):
    def __init__(self):
        self.__stream = None
        self.headers = None
        self.active_region = None
        self.active_meta:MetaDataRegion = None
        self.active_bat:BAT = None
        self.boot_record = None
        self.lba_per_block = None
        self.use_buffer = True
        self.__lba_cache = {}
        self.__block_cache = {}

    def set_stream(self, stream:io.BytesIO):
        self.__stream = stream
    
    @staticmethod
    async def from_file(filepath:str):
        stream = open(filepath, 'rb')
        return await VHDXDisk.from_buffer(stream)
    
    @staticmethod
    async def from_bytes(data:bytes):
        await VHDXDisk.from_buffer(io.BytesIO(data))
    
    @staticmethod
    async def from_buffer(buffer:io.BytesIO):
        disk = VHDXDisk()
        disk.set_stream(buffer)
        await disk.read_headers()
        return disk

    async def read_headers(self):
        data = self.__stream.read(1*1024*1024)
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
        #print('Reading block %s Offset: %s State: %s' % (block_idx, entry[1], entry[0]))
        offset = entry[1]
        self.__stream.seek(offset, 0)
        data = self.__stream.read(self.active_meta.BlockSize)
        if self.use_buffer is True:
            self.__block_cache[block_idx] = data
        return data

    async def read_LBAs(self, lbas:List[int], debug = False):
        #input(f'Reading LBAs: {lbas}')

        # Check if LBAs are contiguous
        sorted_lbas = sorted(lbas)
        if any(sorted_lbas[i] + 1 != sorted_lbas[i + 1] for i in range(len(sorted_lbas) - 1)):
            raise Exception('LBAs are not contiguous')

        # Get the range of blocks to read
        first_lba = sorted_lbas[0]
        last_lba = sorted_lbas[-1]
        #first_block_idx = first_lba // self.active_meta.lba_per_block
        #last_block_idx = last_lba // self.active_meta.lba_per_block

        first_block_idx = math.floor(first_lba / self.active_meta.lba_per_block)
        last_block_idx = math.ceil(last_lba / self.active_meta.lba_per_block)

        if debug:
            print(f'first_lba: {first_lba}')
            print(f'last_lba: {last_lba}')
            print(f'first_block_idx: {first_block_idx}')
            print(f'last_block_idx: {last_block_idx}')

        # Read the blocks
        block_data = io.BytesIO()
        for block_idx in range(first_block_idx, last_block_idx + 1):
            if block_idx in self.__block_cache:
                if debug:
                    print(f'Using block cache for block {block_idx}')
                temp = self.__block_cache[block_idx]
                block_data.write(temp)
            else:
                if debug:
                    print(f'Reading block {block_idx}')
                temp = await self.read_block(block_idx)
                block_data.write(temp)

        # Calculate the start offset
        start_block_lba = first_block_idx * self.active_meta.lba_per_block
        start_offset = (first_lba - start_block_lba) * self.active_meta.LogicalSectorSize
        #(first_lba % self.active_meta.lba_per_block) * self.active_meta.LogicalSectorSize

        # Calculate the total length of data to extract
        total_length = ((last_lba - first_lba + 1) * self.active_meta.LogicalSectorSize)

        if debug:
            print(f'start_block_lba: {start_block_lba}')
            print(f'start_offset: {start_offset}')
            print(f'total_length: {total_length}')
            input()

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
        #print('Reading LBA {} from offset {} with state {}'.format(lba, offset, entry[0]))    

        