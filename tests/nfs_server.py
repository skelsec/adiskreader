"""
A small read-only NFSv3 server for the tests of the NFS datasource: it serves a directory as an export.
The MOUNT and NFS programs are on the same TCP port (no portmapper), only the procedures the datasource needs are there:
MOUNT: NULL, MNT, UMNT, UMNTALL, EXPORT. NFS: NULL, GETATTR, LOOKUP, READLINK, READ.
The symbolic links are not followed by it, as a real server does not follow them either.
"""

import asyncio
import os
import stat
import struct

MOUNT_PROGRAM = 100005
NFS_PROGRAM = 100003

NFS3_OK = 0
NFS3ERR_NOENT = 2
NFS3ERR_NOTDIR = 20
NFS3ERR_INVAL = 22
NFS3ERR_STALE = 70
MNT3ERR_NOENT = 2
# accept_stat of the RPC reply
SUCCESS = 0
PROG_UNAVAIL = 1
PROC_UNAVAIL = 3

def pack_opaque(data):
    return struct.pack('>I', len(data)) + data + b'\x00' * (-len(data) % 4)

class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def uint32(self):
        self.pos += 4
        return struct.unpack_from('>I', self.data, self.pos - 4)[0]

    def uint64(self):
        self.pos += 8
        return struct.unpack_from('>Q', self.data, self.pos - 8)[0]

    def opaque(self):
        size = self.uint32()
        data = self.data[self.pos:self.pos + size]
        self.pos += size + (-size % 4)
        return data

class NFSTestServer:
    def __init__(self, root, export = '/export', list_exports = True, max_read = 32768):
        self.root = os.path.realpath(root)
        self.export = export
        # servers do not have to list their exports
        self.list_exports = list_exports
        # reads are cut to this size, like the rtmax of a server
        self.max_read = max_read
        self.port = None
        self.connections = 0
        self.mounted = []
        # (uid, gid) of the AUTH_SYS credentials of the NFS calls
        self.credentials = set()
        self.read_calls = 0
        self.__server = None
        self.__writers = []
        self.__handles = {}
        self.__paths = {}

    async def __aenter__(self):
        self.__server = await asyncio.start_server(self.handle_client, '127.0.0.1', 0)
        self.port = self.__server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, exc_type, exc, tb):
        for writer in self.__writers:
            writer.close()
        self.__server.close()
        await self.__server.wait_closed()

    def url(self, path, params = '', scheme = 'nfs'):
        return '%s://127.0.0.1:%s%s/%s%s' % (scheme, self.port, self.export, path, params)

    def get_handle(self, path):
        if path not in self.__handles:
            handle = b'ADRNFS' + struct.pack('>I', len(self.__handles)) + os.urandom(22)
            self.__handles[path] = handle
            self.__paths[handle] = path
        return self.__handles[path]

    def fattr3(self, path):
        st = os.lstat(path)
        ftype = 2 if stat.S_ISDIR(st.st_mode) else 5 if stat.S_ISLNK(st.st_mode) else 1
        res = struct.pack('>IIIIIQQIIQQ', ftype, stat.S_IMODE(st.st_mode), st.st_nlink, st.st_uid, st.st_gid, st.st_size, st.st_size, 0, 0, 1, st.st_ino)
        for timestamp in (st.st_atime, st.st_mtime, st.st_ctime):
            res += struct.pack('>II', int(timestamp), 0)
        return res

    def mount_call(self, proc, args):
        if proc == 0:
            return b''
        if proc == 1:
            path = args.opaque().decode()
            if path != self.export:
                return struct.pack('>I', MNT3ERR_NOENT)
            self.mounted.append(path)
            # the handle and the list of the auth flavors: AUTH_SYS
            return struct.pack('>I', 0) + pack_opaque(self.get_handle(self.root)) + struct.pack('>II', 1, 1)
        if proc in (3, 4):
            return b''
        if proc == 5:
            res = b''
            if self.list_exports is True:
                # an export which is not the one of the files, and the one of the files: no groups for them
                for export in ('/other', self.export):
                    res += struct.pack('>I', 1) + pack_opaque(export.encode()) + struct.pack('>I', 0)
            return res + struct.pack('>I', 0)
        return None

    def nfs_call(self, proc, args):
        if proc == 0:
            return b''
        if proc not in (1, 3, 5, 6):
            return None
        path = self.__paths.get(args.opaque())
        if path is None:
            if proc == 1:
                return struct.pack('>I', NFS3ERR_STALE)
            return struct.pack('>II', NFS3ERR_STALE, 0)
        if proc == 1:
            return struct.pack('>I', NFS3_OK) + self.fattr3(path)
        if proc == 3:
            name = args.opaque().decode()
            if os.path.islink(path) or not os.path.isdir(path):
                return struct.pack('>II', NFS3ERR_NOTDIR, 0)
            target = os.path.join(path, name)
            # nothing outside of the export is served
            if '/' in name or name == '..' or not os.path.lexists(target):
                return struct.pack('>II', NFS3ERR_NOENT, 0)
            res = struct.pack('>I', NFS3_OK) + pack_opaque(self.get_handle(target))
            return res + struct.pack('>I', 1) + self.fattr3(target) + struct.pack('>I', 0)
        if proc == 5:
            if not os.path.islink(path):
                return struct.pack('>II', NFS3ERR_INVAL, 0)
            return struct.pack('>II', NFS3_OK, 0) + pack_opaque(os.readlink(path).encode())
        if os.path.islink(path) or not os.path.isfile(path):
            return struct.pack('>II', NFS3ERR_INVAL, 0)
        offset = args.uint64()
        count = min(args.uint32(), self.max_read)
        self.read_calls += 1
        with open(path, 'rb') as f:
            f.seek(offset, 0)
            data = f.read(count)
            eof = f.tell() >= os.path.getsize(path)
        return struct.pack('>II', NFS3_OK, 0) + struct.pack('>II', len(data), int(eof)) + pack_opaque(data)

    def call(self, message):
        msg = Reader(message)
        xid = msg.uint32()
        msg.uint32() # CALL
        msg.uint32() # RPC version
        program = msg.uint32()
        msg.uint32() # program version
        proc = msg.uint32()
        flavor = msg.uint32()
        cred = Reader(msg.opaque())
        msg.uint32() # verifier
        msg.opaque()

        res = None
        status = PROG_UNAVAIL
        if program == MOUNT_PROGRAM:
            status = PROC_UNAVAIL
            res = self.mount_call(proc, msg)
        elif program == NFS_PROGRAM:
            status = PROC_UNAVAIL
            if flavor == 1 and proc != 0:
                cred.uint32() # stamp
                cred.opaque() # machine name
                self.credentials.add((cred.uint32(), cred.uint32()))
            res = self.nfs_call(proc, msg)
        if res is not None:
            status = SUCCESS
        # REPLY, MSG_ACCEPTED, no verifier
        return struct.pack('>IIIIII', xid, 1, 0, 0, 0, status) + (res or b'')

    async def handle_client(self, reader, writer):
        self.connections += 1
        self.__writers.append(writer)
        try:
            while True:
                message = b''
                last = False
                while last is False:
                    header = struct.unpack('>I', await reader.readexactly(4))[0]
                    last = bool(header & 0x80000000)
                    message += await reader.readexactly(header & 0x7fffffff)
                reply = self.call(message)
                writer.write(struct.pack('>I', 0x80000000 | len(reply)) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
