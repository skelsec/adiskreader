
# VHD disk reader
# TODO: optimize the code, it's very slow
# TODO: implement the differencing disk


import io
import math
from typing import List
from adiskreader.disks import Disk
from adiskreader.datasource import DataSource
from adiskreader.disks.vhd.structures import *
from cachetools import LRUCache




class VHDDisk(Disk):
    def __init__(self):
        self.__stream = None
        self.footer_top:HardDiskFooter = None # header but it's called footer
        self.footer_bottom:HardDiskFooter = None # header at the end of file XD
        self.active_footer:HardDiskFooter = None
        self.BAT = []
        self.__block_cache = LRUCache(maxsize=1000)
        self.__lba_cache = LRUCache(maxsize=1000)

    
    async def setup(self, ds:DataSource):
        self.__stream = ds
        await self.read_headers()
    
    @staticmethod
    async def from_datasource(ds:DataSource):
        disk = VHDDisk()
        await disk.setup(ds)
        return disk

    async def read_headers(self):
        data = await self.__stream.read(512)
        self.footer_top = HardDiskFooter.from_bytes(data)
        self.active_footer = self.footer_top # for now
        if self.footer_top.DiskType in [VHDType.DYNAMIC, VHDType.DIFFERENCING]:
            await self.read_dynamic_header()

    async def read_dynamic_header(self):
        await self.__stream.seek(self.active_footer.DataOffset, 0)
        data = await self.__stream.read(1024)
        self.dynamic_header = VHDDynamicHeader.from_bytes(data)
        await self.__stream.seek(self.dynamic_header.TableOffset, 0)
        tdata = await self.__stream.read(self.dynamic_header.MaxTableEntries * 4)
        for i in range(len(tdata)//4):
            data = tdata[i*4:(i+1)*4]
            self.BAT.append(int.from_bytes(data, byteorder='big'))
    
    async def read_block(self, block_index):
        if block_index in self.__block_cache:
            return self.__block_cache[block_index]
        
        if self.BAT[block_index] == 0xFFFFFFFF:
            return b'\x00' * self.dynamic_header.BlockSize
        
        ActualSectorLocation = self.BAT[block_index] + self.dynamic_header.BlockBitmapSectorCount #because each block has a bitmap header which we don't need now
        await self.__stream.seek(ActualSectorLocation * 512)
        data = await self.__stream.read(self.dynamic_header.BlockSize)
        self.__block_cache[block_index] = data
        return data
    
    async def read_LBAs(self, lbas: List[int]):
        data = b''
        for lba in lbas:
            data += await self.read_LBA(lba)
        return data
    
    async def read_LBA(self, lba: int):
        if lba in self.__lba_cache:
            return self.__lba_cache[lba]
        
        if self.active_footer.DiskType == VHDType.FIXED:
            # no BAT, just read the sector
            await self.__stream.seek((lba+1) * 512) # +1 because the first sector is the header
            data = await self.__stream.read(512)
            self.__lba_cache[lba] = data
            return data
        
        block_index = math.floor(lba / self.dynamic_header.SectorsPerBlock )
        SectorInBlock = lba % self.dynamic_header.SectorsPerBlock
        block_data = await self.read_block(block_index)
        data = block_data[SectorInBlock * 512:(SectorInBlock+1) * 512]
        self.__lba_cache[lba] = data
        return data