import struct
from cachetools import LRUCache
from adiskreader.compression import DecompressionError
from adiskreader.filesystems.wim.structures import WIMResourceFlag, WIMResourceHeader, SOLID_RESOURCE_MAGIC

# compressed chunks read from the datasource with one read
MAX_READ_CHUNKS = 64
MAX_READ_SIZE = 16 * 1024 * 1024
# the solid resources have a header: size of the data, chunk size, compression. The sizes of the compressed chunks follow it.
SOLID_HEADER_SIZE = 16
SOLID_COMPRESSION = {0 : None, 1 : 'XPRESS', 2 : 'LZX', 3 : 'LZMS'}
# the chunks of the solid resources are large (64 MB), only a few of them are kept
SOLID_CACHE_SIZE = 128 * 1024 * 1024

def get_decompressor(compression:str):
    if compression == 'XPRESS':
        from adiskreader.compression.xpress import xpress_decompress
        return lambda data, out_size, chunk_size: xpress_decompress(data, out_size)
    if compression == 'LZX':
        from adiskreader.compression.lzx import lzx_decompress
        return lzx_decompress
    if compression == 'LZMS':
        from adiskreader.compression.lzms import lzms_decompress
        return lambda data, out_size, chunk_size: lzms_decompress(data, out_size)
    raise NotImplementedError('WIM compression %s is not supported' % compression)

class WIMResource:
    """
    Reads the data of a resource of the WIM file.
    Compressed resources are made of chunks compressed on their own, only the chunks needed by a read are decompressed.
    Solid resources (ESD files) hold the data of many files, they store their own chunk size and compression.
    """
    def __init__(self, stream, header:WIMResourceHeader, compression:str, chunk_size:int, cache_chunks:int = 8):
        self.__stream = stream
        self.header = header
        self.size = header.original_size
        self.compression = compression
        self.chunk_size = chunk_size
        self.__chunk_offsets = None
        self.__cache = LRUCache(maxsize=cache_chunks)
        self.__decompress = None

        self.solid = WIMResourceFlag.SOLID in header.flags
        self.compressed = WIMResourceFlag.COMPRESSED in header.flags or self.solid
        if self.solid is True:
            if header.original_size != SOLID_RESOURCE_MAGIC:
                raise Exception('Invalid solid WIM resource')
            # known after setup
            self.size = None
            self.compression = None
            self.chunk_size = None
        elif self.compressed is True:
            if chunk_size is None or chunk_size <= 0:
                raise Exception('Compressed WIM resource without chunk size')
            self.__decompress = get_decompressor(compression)

    async def read_raw(self, offset:int, size:int):
        await self.__stream.seek(self.header.offset + offset, 0)
        data = await self.__stream.read(size)
        if len(data) != size:
            raise Exception('WIM resource is truncated')
        return data

    async def setup(self):
        """Reads the chunk table, the size of the solid resources is known after this"""
        if self.compressed is True and self.__chunk_offsets is None:
            if self.solid is True:
                await self.read_solid_chunk_table()
            else:
                await self.read_chunk_table()

    async def read_solid_chunk_table(self):
        if self.header.size < SOLID_HEADER_SIZE:
            raise Exception('Invalid solid WIM resource')
        size, chunk_size, compression = struct.unpack('<QII', await self.read_raw(0, SOLID_HEADER_SIZE))
        if compression not in SOLID_COMPRESSION:
            raise NotImplementedError('Compression %s of the solid WIM resource is not supported' % compression)
        if chunk_size == 0:
            raise Exception('Invalid solid WIM resource')
        count = (size + chunk_size - 1) // chunk_size
        table_size = count * 4
        if SOLID_HEADER_SIZE + table_size > self.header.size:
            raise Exception('Invalid WIM chunk table')
        offsets = [SOLID_HEADER_SIZE + table_size]
        for chunk in struct.unpack('<%dI' % count, await self.read_raw(SOLID_HEADER_SIZE, table_size)):
            offsets.append(offsets[-1] + chunk)
        if offsets[-1] > self.header.size:
            raise Exception('Invalid WIM chunk table')

        if SOLID_COMPRESSION[compression] is not None:
            self.__decompress = get_decompressor(SOLID_COMPRESSION[compression])
        self.compression = SOLID_COMPRESSION[compression]
        self.size = size
        self.chunk_size = chunk_size
        self.__cache = LRUCache(maxsize=max(2, min(self.__cache.maxsize, SOLID_CACHE_SIZE // chunk_size)))
        self.__chunk_offsets = offsets

    async def read_chunk_table(self):
        # offsets of the chunks after the first one, relative to the end of the table
        count = (self.size + self.chunk_size - 1) // self.chunk_size
        entry_size = 8 if self.size > 0xFFFFFFFF else 4
        table_size = (count - 1) * entry_size
        if table_size > self.header.size:
            raise Exception('Invalid WIM chunk table')
        table = await self.read_raw(0, table_size)
        offsets = [table_size]
        for i in range(0, table_size, entry_size):
            offsets.append(table_size + int.from_bytes(table[i:i+entry_size], 'little'))
        offsets.append(self.header.size)
        for i in range(count):
            if offsets[i] > offsets[i+1]:
                raise Exception('Invalid WIM chunk table')
        self.__chunk_offsets = offsets

    def chunk_original_size(self, index:int):
        return min(self.chunk_size, self.size - index * self.chunk_size)

    async def load_chunks(self, first:int, last:int):
        """Reads and decompresses the chunks first..last which are not in the cache, returns all of them"""
        res = {}
        index = first
        while index <= last:
            chunk = self.__cache.get(index)
            if chunk is not None:
                res[index] = chunk
                index += 1
                continue
            end = index
            start_offset = self.__chunk_offsets[index]
            while end < last and end - index + 1 < MAX_READ_CHUNKS and (end + 1) not in self.__cache:
                if self.__chunk_offsets[end + 2] - start_offset > MAX_READ_SIZE:
                    break
                end += 1
            data = await self.read_raw(start_offset, self.__chunk_offsets[end + 1] - start_offset)
            for i in range(index, end + 1):
                cdata = data[self.__chunk_offsets[i] - start_offset : self.__chunk_offsets[i+1] - start_offset]
                original_size = self.chunk_original_size(i)
                if len(cdata) == original_size:
                    # chunks which could not be compressed are stored as they are
                    chunk = cdata
                elif self.__decompress is None:
                    raise Exception('Invalid size of chunk %s of the WIM resource at %s' % (i, self.header.offset))
                else:
                    try:
                        chunk = self.__decompress(cdata, original_size, self.chunk_size)
                    except (DecompressionError, IndexError) as e:
                        raise DecompressionError('Failed to decompress chunk %s of the WIM resource at %s: %s' % (i, self.header.offset, e))
                self.__cache[i] = chunk
                res[i] = chunk
            index = end + 1
        return res

    async def read(self, offset:int, size:int):
        """Reads from the uncompressed data of the resource, the result is shorter at the end of the resource"""
        await self.setup()
        size = min(size, self.size - offset)
        if offset < 0 or size <= 0:
            return b''
        if self.compressed is False:
            return await self.read_raw(offset, size)

        first = offset // self.chunk_size
        last = (offset + size - 1) // self.chunk_size
        res = []
        # large reads are done in parts, the cache is smaller than them
        batch = max(1, min(MAX_READ_CHUNKS, MAX_READ_SIZE // self.chunk_size))
        for start in range(first, last + 1, batch):
            end = min(last, start + batch - 1)
            chunks = await self.load_chunks(start, end)
            for i in range(start, end + 1):
                chunk_start = max(0, offset - i * self.chunk_size)
                chunk_end = min(self.chunk_size, offset + size - i * self.chunk_size)
                if chunk_start == 0 and chunk_end >= len(chunks[i]):
                    res.append(chunks[i])
                else:
                    res.append(chunks[i][chunk_start:chunk_end])
        return b''.join(res)

    async def read_all(self):
        await self.setup()
        return await self.read(0, self.size)


class WIMSolidBlob:
    """
    Data of a file stored in solid resources. The offset is counted from the beginning of the first resource,
    the data is in one of the resources following each other in the lookup table.
    """
    def __init__(self, resources:list, offset:int, size:int):
        self.__resources = resources
        self.__resource = None
        self.__offset = offset
        self.size = size

    async def locate(self):
        offset = self.__offset
        for resource in self.__resources:
            await resource.setup()
            if offset + self.size <= resource.size:
                self.__resource = resource
                self.__offset = offset
                return
            offset -= resource.size
            if offset < 0:
                break
        raise Exception('The data of the file is not in the solid resources of the WIM file')

    async def read(self, offset:int, size:int):
        """Reads from the data, the result is shorter at the end of it"""
        size = min(size, self.size - offset)
        if offset < 0 or size <= 0:
            return b''
        if self.__resource is None:
            await self.locate()
        data = await self.__resource.read(self.__offset + offset, size)
        if len(data) != size:
            raise Exception('WIM resource is truncated')
        return data

    async def read_all(self):
        return await self.read(0, self.size)
