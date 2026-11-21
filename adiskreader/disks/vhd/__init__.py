
# VHD disk reader

from typing import List
from adiskreader.disks import Disk
from adiskreader.disks.parent import open_parent_disk, bitmap_runs
from adiskreader.datasource import DataSource
from adiskreader.disks.vhd.structures import *
from cachetools import LRUCache

SECTOR_SIZE = 512
BAT_UNUSED = 0xFFFFFFFF
MAX_LOCATOR_SIZE = 64 * 1024

# platform code of the parent locator -> encoding of the path, relative paths first
PARENT_LOCATOR_ENCODINGS = {
    b'W2ru' : 'utf-16-le',
    b'Wi2r' : 'utf-8',
    b'W2ku' : 'utf-16-le',
    b'Wi2k' : 'utf-8',
}

class VHDDisk(Disk):
    """
    VHD disk (fixed, dynamic or differencing).
    The parent disk of a differencing disk is opened from the directory of the file, using the paths stored in it.
    """
    def __init__(self):
        self.__stream = None
        self.footer_top:HardDiskFooter = None # copy of the footer at the start of the file, fixed disks don't have it
        self.footer_bottom:HardDiskFooter = None
        self.active_footer:HardDiskFooter = None
        self.dynamic_header:VHDDynamicHeader = None
        self.parent:VHDDisk = None
        self.parent_names = [] # paths of the parent stored in the file
        self.capacity = 0 # in sectors
        self.BAT = []
        self.__siblings = [] # datasources opened by this disk
        self.__bitmap_cache = LRUCache(maxsize=1024)
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
        await self.__stream.seek(0, 0)
        data = await self.__stream.read(SECTOR_SIZE)
        if data[:8] == b'conectix':
            self.footer_top = HardDiskFooter.from_bytes(data)
        await self.__stream.seek(-SECTOR_SIZE, 2)
        data = await self.__stream.read(SECTOR_SIZE)
        if data[:8] == b'conectix':
            self.footer_bottom = HardDiskFooter.from_bytes(data)

        # the one at the end is the current, the copy is used if that is damaged
        footers = [footer for footer in (self.footer_bottom, self.footer_top) if footer is not None]
        if len(footers) == 0:
            raise Exception('Not a VHD file')
        self.active_footer = footers[0]
        for footer in footers:
            if footer.valid is True:
                self.active_footer = footer
                break

        self.capacity = self.active_footer.CurrentSize // SECTOR_SIZE
        if self.active_footer.DiskType in [VHDType.DYNAMIC, VHDType.DIFFERENCING]:
            await self.read_dynamic_header()
        if self.active_footer.DiskType == VHDType.DIFFERENCING:
            await self.open_parent()

    async def read_dynamic_header(self):
        await self.__stream.seek(self.active_footer.DataOffset, 0)
        data = await self.__stream.read(1024)
        self.dynamic_header = VHDDynamicHeader.from_bytes(data)
        await self.__stream.seek(self.dynamic_header.TableOffset, 0)
        tdata = await self.__stream.read(self.dynamic_header.MaxTableEntries * 4)
        for i in range(len(tdata)//4):
            data = tdata[i*4:(i+1)*4]
            self.BAT.append(int.from_bytes(data, byteorder='big'))

    async def open_parent(self):
        for code, encoding in PARENT_LOCATOR_ENCODINGS.items():
            for entry in self.dynamic_header.ParentLocatorEntries:
                if entry.PlatformCode != code or not 0 < entry.PlatformDataLength <= MAX_LOCATOR_SIZE:
                    continue
                await self.__stream.seek(entry.PlatformDataOffset, 0)
                data = await self.__stream.read(entry.PlatformDataLength)
                self.parent_names.append(data.decode(encoding, errors='replace').split('\x00')[0])
        self.parent_names.append(self.dynamic_header.parent_name)

        parent_id = self.dynamic_header.ParentUniqueId
        self.parent, ds = await open_parent_disk(
            self.__stream, self.parent_names, VHDDisk,
            lambda disk: disk.active_footer.UniqueId == parent_id
        )
        self.__siblings.append(ds)

    async def close(self):
        """Closes the files opened by the disk, the datasource of the disk is to be closed by the caller"""
        if self.parent is not None:
            await self.parent.close()
            self.parent = None
        for ds in self.__siblings:
            await ds.close()
        self.__siblings = []

    async def read_bitmap(self, block_index:int):
        bitmap = self.__bitmap_cache.get(block_index)
        if bitmap is None:
            await self.__stream.seek(self.BAT[block_index] * SECTOR_SIZE, 0)
            bitmap = await self.__stream.read(self.dynamic_header.BlockBitmapSectorCount * SECTOR_SIZE)
            self.__bitmap_cache[block_index] = bitmap
        return bitmap

    async def get_block_runs(self, block_index:int, sector_in_block:int, count:int):
        """Returns list of (sector count, True if the sectors are stored in this file)"""
        if block_index >= len(self.BAT) or self.BAT[block_index] == BAT_UNUSED:
            return [(count, False)]
        if self.parent is None:
            # without a parent the sectors not marked in the bitmap are zeroes in the file as well
            return [(count, True)]
        bitmap = await self.read_bitmap(block_index)
        if len(bitmap) * 8 < sector_in_block + count:
            raise Exception('Bitmap of block %s is truncated' % block_index)
        return bitmap_runs(bitmap, sector_in_block, count, True)

    async def read_sectors(self, sector:int, count:int):
        """Reads consecutive sectors, the data is shorter if the end of the disk is reached"""
        count = min(count, self.capacity - sector)
        if count <= 0:
            return b''
        if self.active_footer.DiskType == VHDType.FIXED:
            # no BAT, the data starts at the beginning of the file
            await self.__stream.seek(sector * SECTOR_SIZE, 0)
            return await self.__stream.read(count * SECTOR_SIZE)
        if self.dynamic_header is None:
            raise NotImplementedError('VHD disk type %s is not supported' % self.active_footer.DiskType)

        res = []
        sectors_per_block = self.dynamic_header.SectorsPerBlock
        while count > 0:
            block_index = sector // sectors_per_block
            sector_in_block = sector % sectors_per_block
            for size, present in await self.get_block_runs(block_index, sector_in_block, min(count, sectors_per_block - sector_in_block)):
                data = b''
                if present is True:
                    # each block starts with its bitmap
                    start = self.BAT[block_index] + self.dynamic_header.BlockBitmapSectorCount + sector_in_block
                    await self.__stream.seek(start * SECTOR_SIZE, 0)
                    data = await self.__stream.read(size * SECTOR_SIZE)
                elif self.parent is not None:
                    data = await self.parent.read_sectors(sector, size)
                data += b'\x00' * (size * SECTOR_SIZE - len(data))
                res.append(data)
                sector += size
                sector_in_block += size
                count -= size
        return b''.join(res)

    async def read_LBAs(self, lbas: List[int]):
        res = []
        start = 0
        for i in range(1, len(lbas) + 1):
            if i == len(lbas) or lbas[i] != lbas[i-1] + 1:
                res.append(await self.read_sectors(lbas[start], i - start))
                start = i
        return b''.join(res)

    async def read_LBA(self, lba: int):
        if lba in self.__lba_cache:
            return self.__lba_cache[lba]
        data = await self.read_sectors(lba, 1)
        self.__lba_cache[lba] = data
        return data

    def __str__(self):
        t = []
        t.append('Type       : VHD Disk')
        t.append('Datasource : %s' % self.__stream)
        t.append('Disk type  : %s' % self.active_footer.DiskType.name)
        t.append('Capacity   : %d' % (self.capacity * SECTOR_SIZE))
        if self.parent is not None:
            t.append('Parent     : %s' % self.dynamic_header.parent_name)
        return '\n'.join(t)
