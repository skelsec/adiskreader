import posixpath
from urllib.parse import urlparse, parse_qs, quote

def sibling_parts(name:str):
    """Splits the relative path of a sibling file to its parts"""
    parts = [part for part in name.replace('\\', '/').split('/') if part not in ['', '.']]
    if len(parts) == 0:
        raise Exception('Invalid sibling name: %s' % name)
    if name[0] in '/\\' or parts[0].endswith(':'):
        raise Exception('Sibling name must be relative: %s' % name)
    return parts

def sibling_url(url:str, parts:list):
    """Replaces the file in the url with the sibling, only used for showing and file type detection"""
    url_e = urlparse(url)
    if url_e.path == '':
        # file urls with relative path have the path in the netloc
        return url_e._replace(netloc = posixpath.join(posixpath.dirname(url_e.netloc), *parts)).geturl()
    return url_e._replace(path = posixpath.join(posixpath.dirname(url_e.path), *[quote(part) for part in parts])).geturl()

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

    async def open_sibling(self, name:str):
        """
        Opens another file from the directory of this one, using the same connection.
        Used by disk formats which are made of multiple files (extents, parent disks).
        name is a path relative to the directory of this file, both / and \\ are accepted as separator.
        The returned datasource must be closed before this one.
        """
        raise NotImplementedError('%s can not open sibling files' % type(self).__name__)

    def sibling_config(self, **kwargs):
        # the config of the sibling keeps the schemes, so it is opened the same way as this one
        config = dict(self.config)
        for key in ['file', 'connection']:
            config.pop(key, None)
        config.update(kwargs)
        return config

    async def from_url(url:str):
        if url.find('://') == -1:
            # Assume file://
            url = 'file://' + url

        url_e = urlparse(url)
        schemes = url_e.scheme.upper().split('+')
        connection_tags = schemes[0].split('-')

        if 'SMB' in schemes or 'SMB2' in schemes or 'SMB3' in schemes or 'SMBQ' in schemes:
            from adiskreader.datasource.smb import SMBFileSource
            return await SMBFileSource.from_url(url)
        
        if 'SSH' in schemes:
            from adiskreader.datasource.ssh import SSHFileSource
            return await SSHFileSource.from_url(url)

        if 'NFS' in schemes:
            from adiskreader.datasource.nfs import NFSFileSource
            return await NFSFileSource.from_url(url)

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