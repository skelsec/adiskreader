import asyncio
from urllib.parse import urlparse, parse_qs
from adiskreader.datasource import DataSource
from aiosmb.commons.connection.factory import SMBConnectionFactory

class SMBFileSource(DataSource):
    """ A DataSource that reads from a remote file over SMB2/3 protocol"""
    def __init__(self, config):
        super().__init__(config)
        self.__factory = None
        self.__connection = None
        self.__stream = None
        self.__size = None
        self.__offset = 0
        self.__total_read = 0
    
    async def debug_total_read(self):
        while True:
            await asyncio.sleep(10)
            print('Total read: %s MB' % (self.__total_read//1024//1024))

    @staticmethod
    async def from_config(config):
        ds = SMBFileSource(config)
        await ds.setup()
        return ds
    
    async def setup(self):
        if 'file' in self.config and self.config['file'] is not None:
            self.__stream = self.config['file']
            self.__size = self.__stream.size
        elif 'url' in self.config and self.config['url'] is not None:
            self.__factory = SMBConnectionFactory.from_url(self.config['url'])
            self.__connection = self.__factory.get_connection()
            _, err = await self.__connection.login()
            if err is not None:
                raise err
            self.__stream = self.__factory.get_file()
            _, err = await self.__stream.open(self.__connection)
            if err is not None:
                raise err
            self.__size = self.__stream.size
        else:
            raise Exception('No file or url provided in config')

        ###### DEBUG
        #asyncio.create_task(self.debug_total_read())
    
    async def read(self, size:int):
        self.__total_read += size
        if self.__offset >= self.__size:
            return b''
        _, err = await self.__stream.seek(self.__offset, 0)
        if err is not None:
            raise err
        data, err = await self.__stream.read(size)
        if err is not None:
            raise err
        self.__offset += len(data)
        return data

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
        await self.__stream.close()
        if self.__connection is not None:
            # only the connection created by this object is terminated
            await self.__connection.terminate()
            self.__connection = None
    
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
        }
        return await SMBFileSource.from_config(config)
    
    @staticmethod
    async def from_smb_file(smb_file):
        config = {
            'file': smb_file,
            'url': None,
            'schemes': ['SMB'],
        }
        return await SMBFileSource.from_config(config)