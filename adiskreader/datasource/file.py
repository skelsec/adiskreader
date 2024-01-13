import os
from adiskreader.datasource import DataSource
from urllib.parse import urlparse, parse_qs

class FileSource(DataSource):
    def __init__(self, config):
        super().__init__(config)
        self.__stream = None
        self.__size = None
        self.__offset = 0
    
    @staticmethod
    async def from_config(config):
        ds = FileSource(config)
        await ds.setup()
        return ds

    @staticmethod
    async def from_file(path:str):
        ds = FileSource({'path': path})
        await ds.setup()
        return ds

    async def setup(self):
        self.__stream = open(self.config['path'], 'rb')
        self.__size = os.stat(self.config['path']).st_size
    
    async def read(self, size:int):
        self.__stream.seek(self.__offset, 0)
        return self.__stream.read(size)
    
    async def seek(self, offset:int, whence:int = 0):
        if whence == 0:
            self.__offset = offset
        elif whence == 1:
            self.__offset += offset
        elif whence == 2:
            self.__offset = self.__size + offset
        else:
            raise Exception('Invalid whence value')
    
    async def close(self):
        self.__stream.close()
    
    async def tell(self):
        return self.__offset
    
    @staticmethod
    async def from_url(url:str):
        url_e = urlparse(url)
        schemes = url_e.scheme.upper().split('+')
        connection_tags = schemes[0].split('-')
        config = {
            'url': url,
            'schemes': schemes,
            'path': url_e.path if url_e.path != '' else url_e.netloc
        }
        return await FileSource.from_config(config)