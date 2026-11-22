import zlib
from cachetools import LRUCache

SECTOR_SIZE = 512

# states of a grain
GRAIN_UNALLOCATED = 0 # not stored in this extent, read from the parent disk
GRAIN_ZERO = 1
GRAIN_DATA = 2

def trim_runs(runs, skip, count):
    """Cuts the list of (sector count, data) runs to count sectors, after skipping the first skip sectors"""
    res = []
    for size, data in runs:
        if skip >= size:
            skip -= size
            continue
        if count <= 0:
            break
        take = min(size - skip, count)
        if data is not None:
            data = data[skip*SECTOR_SIZE:(skip+take)*SECTOR_SIZE]
            if len(data) < take*SECTOR_SIZE:
                # truncated file
                data += b'\x00' * (take*SECTOR_SIZE - len(data))
        res.append((take, data))
        count -= take
        skip = 0
    return res


class Extent:
    """
    A part of the virtual disk stored in one file.
    Positions are in sectors, relative to the start of the extent.
    """
    def __init__(self, stream, descriptor):
        self.stream = stream
        self.descriptor = descriptor
        self.capacity = None # in sectors

    async def setup(self):
        pass

    async def read(self, sector:int, count:int):
        """
        Returns the sectors as a list of (sector count, data) runs.
        The data is None for the sectors which are not stored in this extent, those are to be read from the parent disk.
        """
        raise NotImplementedError()


class ZeroExtent(Extent):
    async def read(self, sector:int, count:int):
        return [(count, b'\x00' * (count*SECTOR_SIZE))]


class FlatExtent(Extent):
    """The data of the disk as is, starting at the offset given in the descriptor"""
    async def read(self, sector:int, count:int):
        await self.stream.seek((self.descriptor.offset + sector) * SECTOR_SIZE, 0)
        data = await self.stream.read(count * SECTOR_SIZE)
        return trim_runs([(count, data)], 0, count)


class SparseExtent(Extent):
    """
    Base of the extents storing the data in grains, which are found using a two level table:
    the grain directory points to the grain tables, the entries of the grain tables point to the grains.
    """
    table_cache_size = 16*1024*1024
    grain_cache_size = 16*1024*1024

    def __init__(self, stream, descriptor):
        super().__init__(stream, descriptor)
        self.grain_sectors = None
        self.gt_entries = None # number of entries in a grain table
        self.entry_size = 4
        self.compressed = False
        self.GD = []
        self.__gt_cache = None
        self.__grain_cache = None

    def init_caches(self):
        self.__gt_cache = LRUCache(maxsize=max(1, self.table_cache_size // (self.gt_entries * self.entry_size)))
        self.__grain_cache = LRUCache(maxsize=max(1, self.grain_cache_size // (self.grain_sectors * SECTOR_SIZE)))

    async def read_directory(self, offset:int, entries:int):
        await self.stream.seek(offset, 0)
        data = await self.stream.read(entries * self.entry_size)
        self.GD = [int.from_bytes(data[i:i+self.entry_size], 'little') for i in range(0, len(data), self.entry_size)]

    def get_table_offset(self, gde:int):
        """Returns the position of the grain table in the file, None if the table is not allocated"""
        raise NotImplementedError()

    def get_grain(self, gte:int):
        """Returns the state of the grain and its position in the file"""
        raise NotImplementedError()

    async def read_table(self, gd_index:int):
        if gd_index in self.__gt_cache:
            return self.__gt_cache[gd_index]
        table = None
        if gd_index < len(self.GD):
            offset = self.get_table_offset(self.GD[gd_index])
            if offset is not None:
                await self.stream.seek(offset, 0)
                table = await self.stream.read(self.gt_entries * self.entry_size)
        self.__gt_cache[gd_index] = table
        return table

    async def lookup(self, grain_index:int):
        table = await self.read_table(grain_index // self.gt_entries)
        if table is None:
            return GRAIN_UNALLOCATED, None
        pos = (grain_index % self.gt_entries) * self.entry_size
        return self.get_grain(int.from_bytes(table[pos:pos+self.entry_size], 'little'))

    async def read_compressed_grain(self, offset:int):
        # the grain starts with a marker: lba (8 bytes), size of the compressed data (4 bytes)
        await self.stream.seek(offset, 0)
        data = await self.stream.read(SECTOR_SIZE)
        size = int.from_bytes(data[8:12], 'little')
        if 12 + size > len(data):
            await self.stream.seek(offset + len(data), 0)
            data += await self.stream.read(12 + size - len(data))
        data = data[12:12+size]
        try:
            return zlib.decompress(data)
        except zlib.error:
            # deflate stream without the zlib header
            return zlib.decompress(data, wbits=-15)

    async def read_grains(self, offset:int, count:int):
        """Reads grains which are next to each other in the file"""
        grain_size = self.grain_sectors * SECTOR_SIZE
        if count > 1:
            await self.stream.seek(offset, 0)
            return await self.stream.read(count * grain_size)
        # small reads are usually followed by others close to them
        if offset in self.__grain_cache:
            return self.__grain_cache[offset]
        if self.compressed is True:
            data = await self.read_compressed_grain(offset)
        else:
            await self.stream.seek(offset, 0)
            data = await self.stream.read(grain_size)
        self.__grain_cache[offset] = data
        return data

    async def read(self, sector:int, count:int):
        grain_size = self.grain_sectors * SECTOR_SIZE
        first = sector // self.grain_sectors
        last = (sector + count - 1) // self.grain_sectors

        # consecutive grains in the same state are merged to (state, offset in file, grain count)
        merged = []
        for grain_index in range(first, last + 1):
            state, offset = await self.lookup(grain_index)
            if len(merged) > 0 and merged[-1][0] == state:
                pstate, poffset, pcount = merged[-1]
                if state != GRAIN_DATA:
                    merged[-1] = (pstate, poffset, pcount + 1)
                    continue
                if self.compressed is False and poffset + pcount * grain_size == offset:
                    merged[-1] = (pstate, poffset, pcount + 1)
                    continue
            merged.append((state, offset, 1))

        runs = []
        for state, offset, grains in merged:
            data = None
            if state == GRAIN_ZERO:
                data = b'\x00' * (grains * grain_size)
            elif state == GRAIN_DATA:
                data = await self.read_grains(offset, grains)
            runs.append((grains * self.grain_sectors, data))
        return trim_runs(runs, sector - first * self.grain_sectors, count)
