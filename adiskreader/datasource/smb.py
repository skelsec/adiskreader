import asyncio
from urllib.parse import urlparse, parse_qs
from adiskreader.datasource import DataSource, sibling_parts, sibling_url
from aiosmb.commons.connection.factory import SMBConnectionFactory
from aiosmb.commons.interfaces.file import SMBFile

class SMBFileSource(DataSource):
    """ A DataSource that reads from a remote file over SMB2/3 protocol"""
    def __init__(self, config):
        super().__init__(config)
        self.__factory = None
        self.__connection = None
        self.__owns_connection = False
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
            self.__connection = self.config.get('connection')
            self.__size = self.__stream.size
        elif 'url' in self.config and self.config['url'] is not None:
            self.__factory = SMBConnectionFactory.from_url(self.config['url'])
            self.__connection = self.__factory.get_connection()
            self.__owns_connection = True
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
        if self.__owns_connection is True:
            # only the connection created by this object is terminated
            await self.__connection.terminate()
            self.__owns_connection = False
        self.__connection = None
    
    async def tell(self):
        return self.__offset

    async def open_sibling(self, name:str):
        if self.__connection is None:
            raise Exception('The SMB connection is needed to open sibling files')
        parts = sibling_parts(name)
        # \\server\share\dir\file, the server does not resolve the .. in the path
        unc = self.__stream.unc_path.lstrip('\\').split('\\')
        path = unc[2:-1]
        for part in parts:
            if part != '..':
                path.append(part)
            elif len(path) == 0:
                raise Exception('Sibling is outside of the share: %s' % name)
            else:
                path.pop()
        smb_file = SMBFile.from_uncpath('\\\\' + '\\'.join(unc[:2] + path))
        _, err = await smb_file.open(self.__connection)
        if err is not None:
            raise err
        config = self.sibling_config(file = smb_file, connection = self.__connection)
        if config.get('url') is not None:
            config['url'] = sibling_url(config['url'], parts)
        return await SMBFileSource.from_config(config)

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
    async def from_smb_file(smb_file, connection = None):
        """connection is the SMBConnection the file is opened on, only needed for opening sibling files"""
        config = {
            'file': smb_file,
            'connection': connection,
            'url': None,
            'schemes': ['SMB'],
        }
        return await SMBFileSource.from_config(config)