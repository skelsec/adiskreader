from adiskreader.disks.vmdk.structures.extent import SparseExtent, SECTOR_SIZE, GRAIN_UNALLOCATED, GRAIN_DATA

COWD_MAGIC = b'COWD'

class COWDExtent(SparseExtent):
    """VMFSSPARSE extent, the delta file (-delta.vmdk) of the snapshots of ESXi"""
    async def setup(self):
        await self.stream.seek(0, 0)
        data = await self.stream.read(SECTOR_SIZE)
        if data[:4] != COWD_MAGIC:
            raise Exception('Invalid COWD magic')
        self.version = int.from_bytes(data[4:8], 'little')
        self.flags = int.from_bytes(data[8:12], 'little')
        self.capacity = int.from_bytes(data[12:16], 'little')
        self.grain_sectors = int.from_bytes(data[16:20], 'little')
        self.gdOffset = int.from_bytes(data[20:24], 'little')
        self.numGDEntries = int.from_bytes(data[24:28], 'little')
        if self.grain_sectors == 0:
            raise Exception('Invalid COWD grain size')
        self.gt_entries = 4096
        self.init_caches()
        await self.read_directory(self.gdOffset * SECTOR_SIZE, self.numGDEntries)

    def get_table_offset(self, gde:int):
        if gde == 0:
            return None
        return gde * SECTOR_SIZE

    def get_grain(self, gte:int):
        if gte == 0:
            return GRAIN_UNALLOCATED, None
        return GRAIN_DATA, gte * SECTOR_SIZE
