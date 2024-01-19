import io
import math
from typing import List
from adiskreader.disks import Disk
from adiskreader.datasource import DataSource
from adiskreader.disks.vmdk.structures.descriptorfile import DescriptorFile
from cachetools import LRUCache

class VMDKDisk(Disk):
    def __init__(self):
        self.__stream = None
        self.descriptorfile = None
        self.__current_extent = None

        self.__lba_cache = LRUCache(maxsize=1000)
        self.__block_cache = LRUCache(maxsize=1000)
    
    async def setup(self, ds:DataSource):
        self.__stream = ds
        header = await self.__stream.read(0x200)
        dfdata = await self.__stream.read(20*1024) #max data
        self.descriptorfile = DescriptorFile.from_bytes(dfdata)
        input(self.descriptorfile)
        if len(self.descriptorfile.extentdescritpors) == 0:
            raise Exception("No extent descriptors found")
        await self.switch_extent(0)
    
    async def switch_extent(self, index:int):
        if index >= len(self.descriptorfile.extentdescritpors):
            raise Exception("Invalid extent index")
        self.__current_extent_index = index
        self.__current_extent_descriptor = self.descriptorfile.extentdescritpors[index]
        self.__current_extent = await self.__current_extent_descriptor.get_extent(self.__stream)
        print(self.__current_extent)

        
    @staticmethod
    async def from_datasource(ds:DataSource):
        disk = VMDKDisk()
        await disk.setup(ds)
        return disk
    
    async def read_LBA(self, lba: int):
        extent_index, extent_lba_start = await self.find_extent_for_lba(lba)
        await self.switch_extent(extent_index)

        # Step 2: Calculate Grain Index
        # Offset LBA by the starting LBA of the extent, and find corresponding grain
        offset_lba = lba - extent_lba_start
        grain_index = offset_lba // self.__current_extent.header.grainSize

        # Step 3: Read the Grain
        grain_data = await self.__current_extent.read_grain(grain_index)

        # Step 4: Extract the Required Data from Grain
        # Calculate the offset within the grain
        offset_within_grain = (offset_lba % self.__current_extent.header.grainSize) * 512  # Assuming 512 bytes/sector
        sector_data = grain_data[offset_within_grain: offset_within_grain + 512]  # Read one sector

        return sector_data
    
    async def find_extent_for_lba(self, lba: int):
        current_lba = 0
        for index, extent in enumerate(self.descriptorfile.extentdescritpors):
            # Calculate the LBA range covered by this extent
            extent_start_lba = current_lba
            extent_end_lba = current_lba + extent.size_in_sector - 1

            # Check if the LBA falls within this extent
            if extent_start_lba <= lba <= extent_end_lba:
                return index, extent_start_lba

            # Update the current LBA for the next extent
            current_lba = extent_end_lba + 1