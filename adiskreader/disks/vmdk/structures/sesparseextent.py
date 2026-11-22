from adiskreader import logger
from adiskreader.disks.vmdk.structures.extent import SparseExtent, SECTOR_SIZE, GRAIN_UNALLOCATED, GRAIN_ZERO, GRAIN_DATA

SESPARSE_CONST_MAGIC = 0xcafebabe
SESPARSE_VOLATILE_MAGIC = 0xcafecafe

class SESparseExtent(SparseExtent):
    """SESPARSE extent, the delta file (-sesparse.vmdk) of the snapshots of ESXi on VMFS6 and of disks over 2TB"""
    async def setup(self):
        await self.stream.seek(0, 0)
        data = await self.stream.read(SECTOR_SIZE)
        fields = [int.from_bytes(data[i:i+8], 'little') for i in range(0, 26*8, 8)]
        if fields[0] != SESPARSE_CONST_MAGIC:
            raise Exception('Invalid SESPARSE magic')
        self.version = fields[1]
        self.capacity = fields[2]
        self.grain_sectors = fields[3]
        self.grainTableSize = fields[4] # in sectors
        self.flags = fields[5]
        self.volatileHeaderOffset = fields[10]
        self.grainDirOffset = fields[16]
        self.grainDirSize = fields[17]
        self.grainTablesOffset = fields[18]
        self.grainsOffset = fields[24]
        if self.grain_sectors == 0 or self.grainTableSize == 0:
            raise Exception('Invalid SESPARSE header')

        await self.stream.seek(self.volatileHeaderOffset * SECTOR_SIZE, 0)
        data = await self.stream.read(SECTOR_SIZE)
        if int.from_bytes(data[0:8], 'little') != SESPARSE_VOLATILE_MAGIC:
            raise Exception('Invalid SESPARSE volatile header magic')
        self.replayJournal = int.from_bytes(data[24:32], 'little')
        if self.replayJournal != 0:
            logger.warning('SESPARSE extent %s has a journal which is not replayed, data might be inconsistent' % self.descriptor.filename)

        self.entry_size = 8
        self.gt_entries = self.grainTableSize * SECTOR_SIZE // self.entry_size
        self.init_caches()
        await self.read_directory(self.grainDirOffset * SECTOR_SIZE, self.grainDirSize * SECTOR_SIZE // self.entry_size)

    def get_table_offset(self, gde:int):
        if gde == 0:
            return None
        if gde & 0xffffffff00000000 != 0x1000000000000000:
            raise Exception('Invalid SESPARSE grain directory entry: %x' % gde)
        return (self.grainTablesOffset + (gde & 0xffffffff) * self.grainTableSize) * SECTOR_SIZE

    def get_grain(self, gte:int):
        # the type of the entry is stored in the top 4 bits
        gtype = gte >> 60
        if gtype == 0:
            if gte != 0:
                raise Exception('Invalid SESPARSE grain table entry: %x' % gte)
            return GRAIN_UNALLOCATED, None
        if gtype in [1, 2]:
            # 1: unmapped (SCSI UNMAP), 2: zero
            return GRAIN_ZERO, None
        if gtype == 3:
            grain = ((gte & 0x0fff000000000000) >> 48) | ((gte & 0x0000ffffffffffff) << 12)
            return GRAIN_DATA, (self.grainsOffset + grain * self.grain_sectors) * SECTOR_SIZE
        raise Exception('Invalid SESPARSE grain table entry: %x' % gte)
