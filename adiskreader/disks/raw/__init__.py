from typing import List
from adiskreader.disks import Disk
from adiskreader.datasource import DataSource
from cachetools import LRUCache

class RAWDisk(Disk):
    def __init__(self):
        self.__stream:DataSource = None
        self.__sector_size = 512
        self.__lba_cache = LRUCache(maxsize=100)
    
    async def setup(self, ds:DataSource):
        self.__stream = ds
    
    @staticmethod
    async def from_datasource(ds:DataSource):
        disk = RAWDisk()
        await disk.setup(ds)
        return disk
    
    def __add_to_cache_return(self, lba:int, data:bytes):
        self.__lba_cache[lba] = data
        return data
    
    async def read_LBAs(self, lbas:List[int]):
        # Check if LBAs are contiguous
        sorted_lbas = sorted(lbas)
        if any(sorted_lbas[i] + 1 != sorted_lbas[i + 1] for i in range(len(sorted_lbas) - 1)):
            raise Exception('LBAs are not contiguous')

        start_offset = sorted_lbas[0] * self.__sector_size
        size = len(sorted_lbas) * self.__sector_size
        await self.__stream.seek(start_offset, 0)
        return await self.__stream.read(size)


    async def read_LBA(self, lba:int):
        if lba in self.__lba_cache:
            return self.__lba_cache[lba]
        
        await self.__stream.seek(lba * self.__sector_size, 0)
        data = await self.__stream.read(self.__sector_size)
        return self.__add_to_cache_return(lba, data)
    
    def __str__(self):
        t = []
        t.append('Type       : RAW Disk')
        t.append('Datasource : %s' % self.__stream)
        t.append('Sector Size: %d' % self.__sector_size)
        return '\n'.join(t)