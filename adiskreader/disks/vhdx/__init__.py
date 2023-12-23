import io
import copy
from typing import List
from adiskreader.disks.vhdx.structures.headers import Headers, VHDX_KNOWN_REGIONS, BAT, MetaDataRegion
from cachetools import cached, LRUCache

class VHDXDisk:
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

        self.lba_per_block = self.active_meta.BlockSize // self.active_meta.LogicalSectorSize
        self.__lba_cache = {}
        if self.use_buffer is True:
            self.__block_cache = {}

    def __add_to_cache_return(self, lba:int, data:bytes):
        self.__lba_cache[lba] = data
        return data
    
    async def read_block(self, block_idx:int):
        entry = self.active_bat.entries[block_idx]
        print('Reading block %s Offset: %s State: %s' % (block_idx, entry[1], entry[0]))
        offset = entry[1]
        self.__stream.seek(offset, 0)
        data = self.__stream.read(self.active_meta.BlockSize)
        if self.use_buffer is True:
            self.__block_cache[block_idx] = data
        return data

    async def read_LBAs(self, lbas:List[int]):
        #print(f'Reading LBAs: {lbas}')

        # Check if LBAs are contiguous
        sorted_lbas = sorted(lbas)
        if any(sorted_lbas[i] + 1 != sorted_lbas[i + 1] for i in range(len(sorted_lbas) - 1)):
            raise Exception('LBAs are not contiguous')

        # Get the range of blocks to read
        first_lba = sorted_lbas[0]
        last_lba = sorted_lbas[-1]
        first_block_idx = first_lba // self.lba_per_block
        last_block_idx = last_lba // self.lba_per_block

        # Read the blocks
        block_data = b''
        for block_idx in range(first_block_idx, last_block_idx + 1):
            if block_idx in self.__block_cache:
                block_data += self.__block_cache[block_idx]
            else:
                block_data += await self.read_block(block_idx)

        # Calculate the start offset
        start_offset = (first_lba % self.lba_per_block) * self.active_meta.LogicalSectorSize

        # Calculate the total length of data to extract
        total_length = ((last_lba - first_lba + 1) * self.active_meta.LogicalSectorSize)

        # Extract and return the relevant portion of block data
        print(f'Data Size: {len(block_data)} Start: {start_offset} Length: {total_length}')
        return block_data[start_offset:start_offset + total_length]


    async def read_LBA(self, lba:int):
        if lba in self.__lba_cache:
            return self.__lba_cache[lba]
        
        block_idx = lba // self.lba_per_block
        block = self.__block_cache.get(block_idx, None)
        if block is None:
            block = await self.read_block(block_idx)
        offset_in_block = lba % self.lba_per_block
        #block_idx, offset_in_block = divmod(lba, self.lba_per_block)
        #entry = self.active_bat.entries[block_idx]
        #offset = entry[1] * (1024*1024)  + offset_in_block * self.active_meta.LogicalSectorSize
        #offset = entry[1] + offset_in_block * self.active_meta.LogicalSectorSize

        offset = offset_in_block * self.active_meta.LogicalSectorSize
        return self.__add_to_cache_return(lba, block[offset:offset+self.active_meta.LogicalSectorSize])
        #print('Reading LBA {} from offset {} with state {}'.format(lba, offset, entry[0]))
        
        if self.use_buffer is True:
            if len(self.__buffer) == 0:
                await self.refresh_buffer(offset)
                result = self.__buffer[:self.active_meta.LogicalSectorSize]
                #print('Firstrun res: %s' % result)
                return self.__add_to_cache_return(lba, result)
            
            if self.__buffer_file_offset <= offset <= self.__buffer_file_offset + len(self.__buffer):
                if offset + self.active_meta.LogicalSectorSize <= self.__buffer_file_offset + len(self.__buffer):
                    #print('Using buffer')
                    corrected_offset = offset - self.__buffer_file_offset
                    result = self.__buffer[corrected_offset: corrected_offset + self.active_meta.LogicalSectorSize]
                    #input('Cache res: %s' % result)
                    return self.__add_to_cache_return(lba, result)
            await self.refresh_buffer(offset)
            result = self.__buffer[:self.active_meta.LogicalSectorSize]
            return self.__add_to_cache_return(lba, result)
                
        
        else:
            self.__stream.seek(offset, 0)
            result = self.__stream.read(self.active_meta.LogicalSectorSize)
            return self.__add_to_cache_return(lba, result)

    

        