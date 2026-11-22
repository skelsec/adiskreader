
class WIMFile:
    """File object of a stream of a file in a WIM image"""
    def __init__(self, resource, name:str = ''):
        # resource is None for empty files
        self.__resource = resource
        self.__size = resource.size if resource is not None else 0
        self.__pos = 0
        self.name = name

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def close(self):
        pass

    async def seekable(self):
        return True

    async def writable(self):
        return False

    async def readable(self):
        return True

    @property
    def size(self):
        return self.__size

    async def tell(self):
        return self.__pos

    async def seek(self, pos:int, whence:int = 0):
        if whence == 0:
            newpos = pos
        elif whence == 1:
            newpos = self.__pos + pos
        elif whence == 2:
            newpos = self.__size + pos
        else:
            raise Exception('Invalid whence value')
        if newpos < 0:
            raise Exception('Invalid position')
        self.__pos = newpos
        return self.__pos

    async def read(self, size:int = -1):
        if size is None or size < 0:
            size = self.__size - self.__pos
        size = min(size, self.__size - self.__pos)
        if size <= 0:
            return b''
        data = await self.__resource.read(self.__pos, size)
        self.__pos += len(data)
        return data
