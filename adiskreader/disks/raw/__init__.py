import io
import math
from typing import List
from adiskreader.disks import Disk
from cachetools import cached, LRUCache

class RAWDisk(Disk):
    def __init__(self):
        self.__stream = None
        self.LogicalSectorSize = 512
        self.__lba_cache = {}
        self.__block_cache = {}
    
    def set_stream(self, stream:io.BytesIO):
        self.__stream = stream
    
    @staticmethod
    async def from_file(filepath:str):
        stream = open(filepath, 'rb')
        return await RAWDisk.from_buffer(stream)
    
    @staticmethod
    async def from_bytes(data:bytes):
        await RAWDisk.from_buffer(io.BytesIO(data))
    
    @staticmethod
    async def from_buffer(buffer:io.BytesIO):
        disk = RAWDisk()
        disk.set_stream(buffer)
        return disk

    def __add_to_cache_return(self, lba:int, data:bytes):
        self.__lba_cache[lba] = data
        return data
    
    

    async def read_LBAs(self, lbas:List[int], debug = False):
        #input(f'Reading LBAs: {lbas}')

        # Check if LBAs are contiguous
        sorted_lbas = sorted(lbas)
        if any(sorted_lbas[i] + 1 != sorted_lbas[i + 1] for i in range(len(sorted_lbas) - 1)):
            raise Exception('LBAs are not contiguous')

        start_offset = sorted_lbas[0] * self.LogicalSectorSize
        size = len(sorted_lbas) * self.LogicalSectorSize
        self.__stream.seek(start_offset, 0)
        data = self.__stream.read(size)
        return data


    async def read_LBA(self, lba:int):
        if lba in self.__lba_cache:
            return self.__lba_cache[lba]
        
        self.__stream.seek(lba * self.LogicalSectorSize, 0)
        data = self.__stream.read(self.LogicalSectorSize)
        return self.__add_to_cache_return(lba, data)
       