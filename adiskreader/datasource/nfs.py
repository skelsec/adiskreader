from urllib.parse import urlparse, parse_qs, unquote
from adiskreader.datasource import DataSource, sibling_parts, sibling_url
from anfs.protocol.nfs3.common.factory import NFS3ConnectionFactory
from anfs.protocol.rpc.messages import AUTH_SYS

# NF3REG, NF3DIR and NF3LNK of the ftype3 of NFSv3
NFS_TYPE_FILE = 1
NFS_TYPE_DIR = 2
NFS_TYPE_SYMLINK = 5
# symbolic links followed for one file, as Linux does
NFS_MAX_LINKS = 40
# the server returns less when it does not support this much in one READ
NFS_READ_SIZE = 1024*1024

class NFSFileSource(DataSource):
    """
    A DataSource that reads from a remote file over the NFSv3 protocol.
    nfs://server/export/dir/file: the export is found by the datasource, it is the longest one the path starts with.
    Symbolic links are followed when they stay inside of the export.
    The uid and gid sent to the server (AUTH_SYS, 0 by default) can be set with the uid=<int> and gid=<int> url parameters.
    """
    def __init__(self, config):
        super().__init__(config)
        self.__factory = None
        self.__client = None
        self.__owns_client = False
        # handles of the directories from the root of the export to the directory of the file
        self.__dir_handles = None
        # parts of the path of the export, the absolute symbolic links are resolved with it
        self.__export = None
        self.__handle = None
        self.__size = None
        self.__offset = 0

    @staticmethod
    async def from_config(config):
        ds = NFSFileSource(config)
        await ds.setup()
        return ds

    async def mount(self, path:str):
        """Mounts the export of the path, returns its handle, its parts and the parts of the path inside of it"""
        parts = [part for part in path.split('/') if part != '']
        if len(parts) == 0:
            raise Exception('No file in the url: %s' % self.config['url'])
        mount = self.__factory.get_mount()
        _, err = await mount.connect()
        if err is not None:
            raise err
        try:
            # the exports of the server first, some servers do not list them: every directory of the path is tried then
            candidates = []
            exports, err = await mount.export()
            if err is None:
                for export in exports:
                    export_parts = [part for part in export.filesys.split('/') if part != '']
                    if export_parts == parts[:len(export_parts)] and len(export_parts) < len(parts):
                        candidates.append(export_parts)
                candidates.sort(key = len, reverse = True)
            for i in range(len(parts) - 1, -1, -1):
                if parts[:i] not in candidates:
                    candidates.append(parts[:i])

            for candidate in candidates:
                handle, err = await mount.mount('/' + '/'.join(candidate))
                if err is None:
                    return handle, candidate, parts[len(candidate):]
            raise Exception('No export of the server could be mounted for %s' % path)
        finally:
            try:
                await mount.disconnect()
            except Exception:
                pass

    async def lookup(self, dir_handles:list, parts:list):
        """
        Walks the parts from the directory, returns the handle and the attributes of the file and the handles of its directories.
        The symbolic links are followed (NFSv3 leaves this to the client): for a link to a file the directories are the ones of the file.
        """
        # the .. parts of the path are resolved here: the server would step out of the export
        names = []
        up = 0
        for part in parts:
            if part != '..':
                names.append(part)
            elif len(names) > 0:
                names.pop()
            else:
                up += 1
        if up >= len(dir_handles):
            raise Exception('File is outside of the export: %s' % '/'.join(parts))
        dir_handles = dir_handles[:len(dir_handles) - up]

        links = 0
        while len(names) > 0:
            name = names.pop(0)
            if name == '..':
                # from the target of a link
                if len(dir_handles) == 1:
                    raise Exception('File is outside of the export: %s' % '/'.join(parts))
                dir_handles.pop()
                continue
            entry, err = await self.__client.lookup(dir_handles[-1], name)
            if err is not None:
                raise err
            if entry is False:
                raise FileNotFoundError('%s not found on the server (%s)' % (name, '/'.join(parts)))
            attrs, err = await self.__client.getattr(entry.handle)
            if err is not None:
                raise err
            if attrs.type == NFS_TYPE_SYMLINK:
                links += 1
                if links > NFS_MAX_LINKS:
                    raise Exception('Too many symbolic links: %s' % '/'.join(parts))
                target, err = await self.__client.readlink(entry.handle)
                if err is not None:
                    raise err
                target_names = [part for part in target.split('/') if part not in ['', '.']]
                if target.startswith('/'):
                    # a path on the server, it can be followed in the export of the file only
                    if target_names[:len(self.__export)] != self.__export:
                        raise Exception('The symbolic link %s points outside of the export: %s' % (name, target))
                    target_names = target_names[len(self.__export):]
                    dir_handles = dir_handles[:1]
                names = target_names + names
            elif len(names) > 0:
                if attrs.type != NFS_TYPE_DIR:
                    raise Exception('Not a directory: %s (%s)' % (name, '/'.join(parts)))
                dir_handles.append(entry.handle)
            elif attrs.type != NFS_TYPE_FILE:
                raise Exception('Not a file: %s' % '/'.join(parts))
            else:
                return entry.handle, attrs, dir_handles
        raise Exception('Not a file: %s' % '/'.join(parts))

    async def setup(self):
        if self.config.get('client') is not None:
            # sibling file, using the connection of the datasource which opened it
            self.__client = self.config['client']
            dir_handles = self.config['dir_handles']
            self.__export = self.config['export']
            parts = self.config['parts']
        elif self.config.get('url') is not None:
            url_e = urlparse(self.config['url'])
            self.__factory = NFS3ConnectionFactory.from_url(self.config['url'])
            query = parse_qs(url_e.query)
            if 'uid' in query or 'gid' in query:
                uid = int(query['uid'][0]) if 'uid' in query else 0
                gid = int(query['gid'][0]) if 'gid' in query else 0
                self.__factory.credential = AUTH_SYS(uid = uid, gid = gid, gids = [gid])

            root_handle, self.__export, parts = await self.mount(unquote(url_e.path))
            self.__client = self.__factory.get_client(root_handle)
            _, err = await self.__client.connect()
            if err is not None:
                raise err
            self.__owns_client = True
            # the root of the export is always the handle 0 of the client
            dir_handles = [0]
        else:
            raise Exception('No client or url provided in config')

        try:
            self.__handle, attrs, self.__dir_handles = await self.lookup(dir_handles, parts)
            self.__size = attrs.size
        except Exception:
            await self.close()
            raise

    async def read(self, size:int):
        if self.__offset >= self.__size:
            return b''
        # reads at the end of the file are truncated
        size = min(size, self.__size - self.__offset)
        parts = []
        while size > 0:
            data, err = await self.__client.read(self.__handle, self.__offset, min(size, NFS_READ_SIZE))
            if err is not None:
                raise err
            if len(data) == 0:
                raise Exception('The file is shorter than its size on the server')
            # the server may return more than what was asked for
            data = data[:size]
            parts.append(data)
            self.__offset += len(data)
            size -= len(data)
        return b''.join(parts)

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
        # NFSv3 has no open files, there is nothing to close on the server
        if self.__owns_client is True:
            # only the connection created by this object is closed
            await self.__client.disconnect()
            self.__owns_client = False
        self.__client = None

    async def tell(self):
        return self.__offset

    async def open_sibling(self, name:str):
        if self.__client is None:
            raise Exception('The NFS connection is needed to open sibling files')
        parts = sibling_parts(name)
        config = self.sibling_config(
            client = self.__client,
            dir_handles = self.__dir_handles,
            export = self.__export,
            parts = parts,
        )
        if config.get('url') is not None:
            config['url'] = sibling_url(config['url'], parts)
        return await NFSFileSource.from_config(config)

    @staticmethod
    async def from_url(url:str):
        url_e = urlparse(url)
        schemes = url_e.scheme.upper().split('+')
        config = {
            'url': url,
            'schemes': schemes,
        }
        return await NFSFileSource.from_config(config)
