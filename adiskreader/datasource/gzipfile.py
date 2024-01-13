import os
import gzip
from urllib.parse import urlparse, parse_qs
from adiskreader.datasource import DataSource
import shutil


class GzipFileSource(DataSource):
    """ A DataSource that reads from a gzip file """
    def __init__(self, config):
        super().__init__(config)
        self.__stream = None
        self.__size = None
        self.__offset = 0

    @staticmethod
    async def from_config(config):
        ds = GzipFileSource(config)
        await ds.setup()
        return ds
    
    @staticmethod
    async def from_file(path:str):
        ds = GzipFileSource({'path': path})
        await ds.setup()
        return ds
    
    async def setup(self):
        self.__stream = gzip.open(self.config['path'], 'rb')
        self.__size = os.stat(self.config['path']).st_size
    
    async def read(self, size:int):
        self.__stream.seek(self.__offset, 0)
        return self.__stream.read(size)
    
    async def seek(self, offset:int, whence:int=0):
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
    
    async def decompress(self, outpath:str):
        self.__stream.seek(0, 0)
        with open(outpath, 'wb') as f_out:
            shutil.copyfileobj(self.__stream, f_out)
        

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
        return await GzipFileSource.from_config(config)