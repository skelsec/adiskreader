from cachetools import LRUCache
from adiskreader.compression import DecompressionError
from adiskreader.filesystems.wim.structures import WIMResourceFlag, WIMResourceHeader

# compressed chunks read from the datasource with one read
MAX_READ_CHUNKS = 64

def get_decompressor(compression:str):
    if compression == 'XPRESS':
        from adiskreader.compression.xpress import xpress_decompress
        return lambda data, out_size, chunk_size: xpress_decompress(data, out_size)
    if compression == 'LZX':
        from adiskreader.compression.lzx import lzx_decompress
        return lzx_decompress
    raise NotImplementedError('WIM compression %s is not supported' % compression)

class WIMResource:
    """
    Reads the data of a resource of the WIM file.
    Compressed resources are made of chunks compressed on their own, only the chunks needed by a read are decompressed.
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

        if WIMResourceFlag.SOLID in header.flags:
            raise NotImplementedError('Solid WIM resources (ESD) are not supported')
        self.compressed = WIMResourceFlag.COMPRESSED in header.flags
        if self.compressed is True:
            if chunk_size is None or chunk_size <= 0:
                raise Exception('Compressed WIM resource without chunk size')
            self.__decompress = get_decompressor(compression)

    async def read_raw(self, offset:int, size:int):
        await self.__stream.seek(self.header.offset + offset, 0)
        data = await self.__stream.read(size)
        if len(data) != size:
            raise Exception('WIM resource is truncated')
        return data

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
            while end < last and end - index + 1 < MAX_READ_CHUNKS and (end + 1) not in self.__cache:
                end += 1
            start_offset = self.__chunk_offsets[index]
            data = await self.read_raw(start_offset, self.__chunk_offsets[end + 1] - start_offset)
            for i in range(index, end + 1):
                cdata = data[self.__chunk_offsets[i] - start_offset : self.__chunk_offsets[i+1] - start_offset]
                original_size = self.chunk_original_size(i)
                if len(cdata) == original_size:
                    # chunks which could not be compressed are stored as they are
                    chunk = cdata
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
        size = min(size, self.size - offset)
        if offset < 0 or size <= 0:
            return b''
        if self.compressed is False:
            return await self.read_raw(offset, size)
        if self.__chunk_offsets is None:
            await self.read_chunk_table()

        first = offset // self.chunk_size
        last = (offset + size - 1) // self.chunk_size
        res = []
        # large reads are done in parts, the cache is smaller than them
        for start in range(first, last + 1, MAX_READ_CHUNKS):
            end = min(last, start + MAX_READ_CHUNKS - 1)
            chunks = await self.load_chunks(start, end)
            for i in range(start, end + 1):
                res.append(chunks[i])
        data = b''.join(res)
        start = offset - first * self.chunk_size
        return data[start:start+size]

    async def read_all(self):
        return await self.read(0, self.size)
