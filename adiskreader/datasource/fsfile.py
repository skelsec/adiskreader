import ntpath
from cachetools import LRUCache
from adiskreader.datasource import DataSource, sibling_parts

CHUNK_SIZE = 1024 * 1024

class FSFileSource(DataSource):
    """
    Datasource backed by a file of an already opened filesystem.
    Used to open disk images (or other containers) stored inside another image.
    The filesystem and the datasource under it must stay open while this one is used.
    """
    def __init__(self, config):
        super().__init__(config)
        self.__fs = config['filesystem']
        self.__file = None
        self.__size = None
        self.__offset = 0
        # the files of the filesystems read whole clusters and runs for small reads as well
        self.__cache = LRUCache(maxsize=config.get('cache_chunks', 32))

    @staticmethod
    async def from_config(config):
        ds = FSFileSource(config)
        await ds.setup()
        return ds

    @staticmethod
    async def from_filesystem(fs, path:str):
        """path is the full path of the file in the filesystem, like \\\\dir\\\\disk.vhdx"""
        return await FSFileSource.from_config({'filesystem': fs, 'path': path})

    async def setup(self):
        path = self.config['path']
        stat = await self.__fs.stat(path)
        self.__file = await self.__fs.open(path)
        self.__size = stat.st_size

    async def read_chunk(self, index:int):
        chunk = self.__cache.get(index)
        if chunk is None:
            start = index * CHUNK_SIZE
            await self.__file.seek(start, 0)
            chunk = await self.__file.read(min(CHUNK_SIZE, self.__size - start))
            self.__cache[index] = chunk
        return chunk

    async def read(self, size:int):
        size = min(size, self.__size - self.__offset)
        if size <= 0:
            return b''
        res = []
        end = self.__offset + size
        while self.__offset < end:
            index = self.__offset // CHUNK_SIZE
            start = self.__offset % CHUNK_SIZE
            data = (await self.read_chunk(index))[start:start + end - self.__offset]
            if len(data) == 0:
                # the file is shorter than its size in the directory
                break
            res.append(data)
            self.__offset += len(data)
        return b''.join(res)

    async def seek(self, offset:int, whence:int = 0):
        if whence == 0:
            self.__offset = offset
        elif whence == 1:
            self.__offset += offset
        elif whence == 2:
            self.__offset = self.__size + offset
        else:
            raise Exception('Invalid whence value')

    async def tell(self):
        return self.__offset

    async def close(self):
        self.__cache.clear()
        if self.__file is not None:
            await self.__file.close()
            self.__file = None

    async def open_sibling(self, name:str):
        parts = sibling_parts(name)
        directory = ntpath.dirname(self.config['path'].replace('/', '\\'))
        path = ntpath.normpath(ntpath.join('\\', directory, *parts))
        return await FSFileSource.from_config(self.sibling_config(path = path))

    def __str__(self):
        return 'File %s in %s' % (self.config['path'], type(self.__fs).__name__)
