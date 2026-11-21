import asyncio
import posixpath
from urllib.parse import urlparse, parse_qs
from adiskreader.datasource import DataSource, sibling_parts, sibling_url
from amurex.common.factory import SSHConnectionFactory
from amurex.common.settings import SSHClientSettings
import traceback

class SSHFileSource(DataSource):
    """ A DataSource that reads from a remote file over SSH (SFTP) protocol"""
    def __init__(self, config):
        super().__init__(config)
        self.__factory = None
        self.__sftp = None
        self.__path = None
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
        ds = SSHFileSource(config)
        await ds.setup()
        return ds
    
    async def setup(self):
        if 'sftp' in self.config and self.config['sftp'] is not None:
            # sibling file, using the session of the datasource which opened it
            self.__sftp = self.config['sftp']
            self.__path = self.config['path']
        else:
            self.__factory = SSHConnectionFactory.from_url(self.config['url'])
            # host key verification is controlled by the known_hosts=<file> and verify=false url parameters
            query = parse_qs(urlparse(self.config['url']).query)
            settings = SSHClientSettings()
            if 'known_hosts' in query:
                settings.known_hosts.load_file(query['known_hosts'][0])
            if 'verify' in query:
                settings.skip_hostkey_verification = query['verify'][0].lower() in ['0', 'false', 'no']
            client = self.__factory.get_client(settings)
            _, err = await client.connect()
            if err is not None:
                raise err

            self.__sftp, err = await client.get_sftp()
            if err is not None:
                raise err
            self.__path = self.__factory.get_target().path

        self.__stream, err = await self.__sftp.open(self.__path, 'rb')
        if err is not None:
            raise err
        
        fstat = await self.__stream.stat()
        self.__size = fstat.size

        ###### DEBUG
        #asyncio.create_task(self.debug_total_read())
    
    async def read(self, size:int):
        self.__total_read += size
        self.__stream.seek(self.__offset, 0)
        try:
            data = await self.__stream.read(size)
        except Exception as e:
            # call stack on the caller
            traceback.print_stack()
            print('Exception: %s' % e)
            traceback.print_exc()
            print('Offset: %s' % self.__offset)
            print('Size: %s' % size)
            raise e
        if len(data) != size:
            raise Exception('Size mismatch')
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
    
    async def tell(self):
        return self.__offset

    async def open_sibling(self, name:str):
        parts = sibling_parts(name)
        config = self.sibling_config(
            sftp = self.__sftp,
            path = posixpath.join(posixpath.dirname(self.__path), *parts),
            url = sibling_url(self.config['url'], parts),
        )
        return await SSHFileSource.from_config(config)

    @staticmethod
    async def from_url(url:str):
        url_e = urlparse(url)
        schemes = url_e.scheme.upper().split('+')
        connection_tags = schemes[0].split('-')
        config = {
            'url': url,
            'schemes': schemes,
        }
        return await SSHFileSource.from_config(config)