from urllib.parse import urlparse, parse_qs

class DataSource:
    def __init__(self, config):
        self.config = config
    
    async def setup(self):
        pass

    async def read(self, size:int):
        raise NotImplementedError()

    async def seek(self, offset:int, whence:int):
        raise NotImplementedError()

    async def close(self):
        raise NotImplementedError()

    async def tell(self):
        raise NotImplementedError()

    async def from_url(url:str):
        if url.find('://') == -1:
            # Assume file://
            url = 'file://' + url

        url_e = urlparse(url)
        schemes = url_e.scheme.upper().split('+')
        connection_tags = schemes[0].split('-')
        print(schemes)
        print(connection_tags)
        print(url_e)

        if 'SMB' in schemes:
            from adiskreader.datasource.smb import SMBFileSource
            return await SMBFileSource.from_url(url)
        
        if 'SSH' in schemes:
            from adiskreader.datasource.ssh import SSHFileSource
            return await SSHFileSource.from_url(url)

        if 'FILE' in schemes:
            if 'GZ' in schemes or 'GZIP' in schemes:
                from adiskreader.datasource.gzipfile import GzipFileSource
                return await GzipFileSource.from_url(url)
            else:
                from adiskreader.datasource.file import FileSource
                return await FileSource.from_url(url)
        
        if 'GZ' in schemes or 'GZIP' in schemes:
            from adiskreader.datasource.gzipfile import GzipFileSource
            return await GzipFileSource.from_file(url)