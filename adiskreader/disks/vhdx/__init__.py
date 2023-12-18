import io
from adiskreader.disks.vhdx.structures.headers import Headers, VHDX_KNOWN_REGIONS, BAT, MetaDataRegion
from adiskreader.disks.MBR import MBR
from adiskreader.disks.GPT import GPT

class VHDXDisk:
    def __init__(self):
        self.__stream = None
        self.headers = None
        self.active_region = None
        self.active_meta:MetaDataRegion = None
        self.active_bat:BAT = None
        self.boot_record = None
        self.sector_size = 512

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
        await disk.read_boot_record()
        return disk
    
    async def read_boot_record(self):
        data = await self.read_LBA(0)
        try:
            self.boot_record = MBR.from_bytes(data)
        except:
            self.boot_record = await GPT.from_disk(self)
        else:
            if len(self.boot_record.partition_table) == 1 and self.boot_record.partition_table[0].partition_type == b'\xEE':
                self.boot_record = await GPT.from_disk(self)
        print(self.boot_record)

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

    async def read_LBA(self, lba:int):
        # find the correct LBA from BAT
        #start_idx = lba * 256
        #temp_lba = 0
        #res_entry = None
        #for entry in self.active_bat.entries:
        #    res_entry = entry
        #    if entry[0] == 0:
        #        continue
        #    if temp_lba == lba:
        #        break
        #    if temp_lba > lba:
        #        raise Exception('LBA not found')
        #    temp_lba += 1
        #state, offset = res_entry
        #state, offset = self.active_bat.entries[start_idx]
        # todo: check state
        # read the data
        
        lba_per_block = self.active_meta.BlockSize // self.active_meta.LogicalSectorSize
        print(lba_per_block)
        block_idx, offset_in_block = divmod(lba,lba_per_block)
        print(block_idx)
        print(offset_in_block)
        entry = self.active_bat.entries[block_idx]
        offset = entry[1] * (1024*1024)  + offset_in_block * self.active_meta.LogicalSectorSize
        
        
        print('Reading LBA {} from offset {} with state {}'.format(lba, offset, entry[0]))
        self.__stream.seek(offset, 0)
        data = self.__stream.read(512)
        return data

        