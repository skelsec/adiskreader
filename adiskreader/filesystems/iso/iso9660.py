import datetime

from adiskreader import logger
from adiskreader.filesystems.iso.common import OpticalEntry, OpticalFileSystem, MAX_DIRECTORY_SIZE

# ISO9660 (ECMA-119) with the Joliet and the Rock Ridge (SUSP / RRIP) extensions.
# Joliet is a second directory tree with UTF-16 names, Rock Ridge stores the POSIX names, permissions and symlinks
# in the system use area of the directory records of the primary tree.

SECTOR_SIZE = 2048
FIRST_DESCRIPTOR_SECTOR = 16
MAX_DESCRIPTORS = 128
ISO_MAGIC = b'CD001'
JOLIET_ESCAPES = (b'%/@', b'%/C', b'%/E')
MAX_CONTINUATIONS = 32

VD_BOOT = 0
VD_PRIMARY = 1
VD_SUPPLEMENTARY = 2
VD_TERMINATOR = 255

FLAG_HIDDEN = 0x01
FLAG_DIRECTORY = 0x02
FLAG_MULTI_EXTENT = 0x80

def decode_name(data:bytes):
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('latin-1')

def strip_version(name:str):
    # NAME.EXT;1 -> NAME.EXT, NAME.;1 -> NAME
    m = name.rfind(';')
    if m != -1 and name[m+1:].isdigit() is True:
        name = name[:m]
    if name.endswith('.') is True:
        name = name[:-1]
    return name

def parse_short_time(data:bytes):
    """7 bytes: years since 1900, month, day, hour, minute, second, offset from GMT in 15 minute units"""
    if len(data) < 7 or data[1] == 0:
        return None
    try:
        res = datetime.datetime(1900 + data[0], data[1], data[2], data[3], data[4], data[5], tzinfo=datetime.timezone.utc)
        offset = data[6] - 256 if data[6] > 127 else data[6]
        return res - datetime.timedelta(minutes=15 * offset)
    except (ValueError, OverflowError):
        return None

def parse_long_time(data:bytes):
    """17 bytes: YYYYMMDDHHMMSSCC as digits and the offset from GMT in 15 minute units"""
    try:
        text = data[:16].decode('ascii')
        if len(text) != 16 or int(text[:4]) == 0:
            return None
        res = datetime.datetime(int(text[0:4]), int(text[4:6]), int(text[6:8]), int(text[8:10]), int(text[10:12]),
            int(text[12:14]), int(text[14:16]) * 10000, tzinfo=datetime.timezone.utc)
        offset = data[16] - 256 if data[16] > 127 else data[16]
        return res - datetime.timedelta(minutes=15 * offset)
    except (ValueError, OverflowError, IndexError):
        return None


class ISODirectoryRecord:
    def __init__(self):
        self.length = 0
        self.ext_attr_length = 0
        self.extent = 0
        self.data_length = 0
        self.time = None
        self.flags = 0
        self.file_unit_size = 0
        self.interleave_gap = 0
        self.raw_name = b''
        self.system_use = b''

    @staticmethod
    def from_bytes(data:bytes):
        if len(data) < 34 or data[0] < 34 or data[0] > len(data):
            raise Exception('Invalid ISO9660 directory record')
        rec = ISODirectoryRecord()
        rec.length = data[0]
        rec.ext_attr_length = data[1]
        rec.extent = int.from_bytes(data[2:6], 'little')
        rec.data_length = int.from_bytes(data[10:14], 'little')
        rec.time = parse_short_time(data[18:25])
        rec.flags = data[25]
        rec.file_unit_size = data[26]
        rec.interleave_gap = data[27]
        name_length = data[32]
        if 33 + name_length > rec.length:
            raise Exception('Invalid ISO9660 directory record')
        rec.raw_name = data[33:33+name_length]
        pos = 33 + name_length
        if name_length % 2 == 0:
            pos += 1
        rec.system_use = data[pos:rec.length]
        return rec


class RockRidgeInfo:
    """What the system use entries of a directory record say about the file"""
    def __init__(self):
        self.found = False
        self.name = None
        self.mode = None
        self.uid = None
        self.gid = None
        self.inode = None
        self.times = {}
        self.symlink = None
        self.child_link = None
        self.relocated = False


class ISO9660Entry(OpticalEntry):
    def __init__(self, fs, parent, name:str, record:ISODirectoryRecord):
        super().__init__(fs, parent, name)
        self.record = record
        self.directory = bool(record.flags & FLAG_DIRECTORY)
        # (sector, size in bytes) of the parts of the data
        self.extents = [(record.extent + record.ext_attr_length, record.data_length)]
        self.size = record.data_length
        self.mtime = record.time
        self.hidden = bool(record.flags & FLAG_HIDDEN)
        self.inode = record.extent

    def is_directory(self):
        return self.directory

    def same_directory(self, other):
        return self.extents[0][0] == other.extents[0][0]

    async def get_extents(self):
        if self.record.file_unit_size != 0 or self.record.interleave_gap != 0:
            raise NotImplementedError('Interleaved ISO9660 files are not supported')
        return [(sector * self.fs.block_size, size) for sector, size in self.extents], None

    async def read_children(self):
        return await self.fs.read_directory(self)


class ISO9660(OpticalFileSystem):
    """
    One directory tree of an ISO9660 image.
    joliet: read the tree of the Joliet volume descriptor instead of the primary one
    rock_ridge: use the Rock Ridge entries of the primary tree if there are any
    """
    def __init__(self, ds, joliet:bool = False, rock_ridge:bool = True):
        super().__init__(ds)
        self.joliet = joliet
        self.use_rock_ridge = rock_ridge and joliet is False
        self.rock_ridge = False
        self.susp_skip = 0
        self.block_size = SECTOR_SIZE
        self.descriptor = None
        self.volume_size = 0

    @staticmethod
    async def from_datasource(ds, joliet:bool = False, rock_ridge:bool = True):
        fs = ISO9660(ds, joliet, rock_ridge)
        await fs.setup()
        return fs

    @staticmethod
    async def read_descriptors(ds):
        """Returns the volume descriptors as (type, data), an empty list if the image is not ISO9660"""
        res = []
        pos = await ds.tell()
        try:
            for index in range(MAX_DESCRIPTORS):
                await ds.seek((FIRST_DESCRIPTOR_SECTOR + index) * SECTOR_SIZE, 0)
                data = await ds.read(SECTOR_SIZE)
                if len(data) != SECTOR_SIZE or data[1:6] != ISO_MAGIC:
                    break
                if data[0] == VD_TERMINATOR:
                    break
                res.append((data[0], data))
        finally:
            await ds.seek(pos, 0)
        return res

    @staticmethod
    def is_joliet(data:bytes):
        return data[0] == VD_SUPPLEMENTARY and data[88:91] in JOLIET_ESCAPES

    async def setup(self):
        for vdtype, data in await ISO9660.read_descriptors(self.ds):
            if self.joliet is True and ISO9660.is_joliet(data) is True:
                self.descriptor = data
                break
            if self.joliet is False and vdtype == VD_PRIMARY:
                self.descriptor = data
                break
        if self.descriptor is None:
            raise Exception('No %s volume descriptor in the image' % ('Joliet' if self.joliet is True else 'ISO9660 primary'))

        data = self.descriptor
        self.volume_name = self.decode(data[40:72]).rstrip(' \x00')
        self.volume_size = int.from_bytes(data[80:84], 'little')
        self.block_size = int.from_bytes(data[128:130], 'little')
        if self.block_size not in (512, 1024, 2048):
            raise Exception('Invalid ISO9660 block size: %s' % self.block_size)
        record = ISODirectoryRecord.from_bytes(data[156:190])
        self.root = ISO9660Entry(self, None, '', record)
        if self.root.is_directory() is False:
            raise Exception('The root of the ISO9660 image is not a directory')

        if self.use_rock_ridge is True and record.data_length > 0:
            # the first record of the root directory (.) tells if the system use areas are used
            first = await self.read_at(self.root.extents[0][0] * self.block_size, min(self.block_size, record.data_length))
            try:
                await self.detect_rock_ridge(ISODirectoryRecord.from_bytes(first[:first[0]]))
            except Exception as e:
                logger.debug('ISO9660: Rock Ridge detection failed: %s' % e)

    def decode(self, data:bytes):
        if self.joliet is True:
            return data[:len(data) & ~1].decode('utf-16-be', errors='replace')
        return decode_name(data)

    async def detect_rock_ridge(self, record:ISODirectoryRecord):
        su = record.system_use
        # CD-XA discs have 14 bytes before the SP entry
        for start in (0, 14):
            if su[start:start+2] == b'SP' and len(su) >= start + 7 and su[start+4:start+6] == b'\xbe\xef':
                self.susp_skip = su[start+6]
                info = await self.parse_system_use(su[start:])
                self.rock_ridge = info.found
                return
        info = await self.parse_system_use(su)
        self.rock_ridge = info.found

    async def parse_system_use(self, data:bytes):
        info = RockRidgeInfo()
        name = b''
        name_done = False
        symlink = []
        component = b''
        hops = 0
        while True:
            continuation = None
            pos = 0
            while pos + 4 <= len(data):
                sig = data[pos:pos+2]
                length = data[pos+2]
                if length < 4 or pos + length > len(data):
                    break
                body = data[pos+4:pos+length]
                pos += length
                if sig == b'CE' and len(body) >= 24:
                    continuation = (int.from_bytes(body[0:4], 'little'), int.from_bytes(body[8:12], 'little'), int.from_bytes(body[16:20], 'little'))
                elif sig == b'ST':
                    break
                elif sig in (b'RR', b'ER'):
                    info.found = True
                elif sig == b'NM' and len(body) >= 1:
                    info.found = True
                    # the names of . and .. are not used
                    if body[0] & 6 == 0 and name_done is False:
                        name += body[1:]
                        if body[0] & 1 == 0:
                            name_done = True
                elif sig == b'PX' and len(body) >= 32:
                    info.found = True
                    info.mode = int.from_bytes(body[0:4], 'little')
                    info.uid = int.from_bytes(body[16:20], 'little')
                    info.gid = int.from_bytes(body[24:28], 'little')
                    if len(body) >= 40:
                        info.inode = int.from_bytes(body[32:36], 'little')
                elif sig == b'TF' and len(body) >= 1:
                    info.found = True
                    size = 17 if body[0] & 0x80 else 7
                    tpos = 1
                    for bit, key in enumerate(('creation', 'modify', 'access', 'attributes')):
                        if body[0] & (1 << bit):
                            raw = body[tpos:tpos+size]
                            info.times[key] = parse_long_time(raw) if size == 17 else parse_short_time(raw)
                            tpos += size
                elif sig == b'SL' and len(body) >= 1:
                    info.found = True
                    cpos = 1
                    while cpos + 2 <= len(body):
                        cflags = body[cpos]
                        clen = body[cpos+1]
                        content = body[cpos+2:cpos+2+clen]
                        cpos += 2 + clen
                        if cflags & 2:
                            content = b'.'
                        elif cflags & 4:
                            content = b'..'
                        elif cflags & 8:
                            content = b''
                        component += content
                        if cflags & 1 == 0:
                            symlink.append(component)
                            component = b''
                elif sig == b'CL' and len(body) >= 4:
                    info.found = True
                    info.child_link = int.from_bytes(body[0:4], 'little')
                elif sig == b'RE':
                    info.found = True
                    info.relocated = True

            if continuation is None:
                break
            hops += 1
            if hops > MAX_CONTINUATIONS:
                raise Exception('Too many continuation areas in the ISO9660 directory record')
            sector, offset, length = continuation
            if length > 16 * self.block_size:
                raise Exception('Invalid continuation area in the ISO9660 directory record')
            data = await self.read_at(sector * self.block_size + offset, length)

        if name != b'':
            info.name = decode_name(name)
        if len(symlink) > 0:
            info.symlink = decode_name(b'/'.join(symlink))
        return info

    async def read_directory(self, directory:ISO9660Entry):
        res = []
        data = b''
        for sector, size in directory.extents:
            if size > MAX_DIRECTORY_SIZE:
                raise Exception('ISO9660 directory is too large')
            data += await self.read_at(sector * self.block_size, size)

        pending = None
        pos = 0
        while pos < len(data):
            length = data[pos]
            block_end = (pos // self.block_size + 1) * self.block_size
            if length == 0:
                # the records do not cross the blocks
                pos = block_end
                continue
            if length < 34 or pos + length > min(block_end, len(data)):
                logger.debug('ISO9660: invalid directory record at %s of %s' % (pos, await directory.resolve_full_path()))
                pos = block_end
                continue
            record = ISODirectoryRecord.from_bytes(data[pos:pos+length])
            pos += length
            if record.raw_name in (b'\x00', b'\x01'):
                continue

            name = strip_version(self.decode(record.raw_name))
            entry = ISO9660Entry(self, directory, name, record)
            if self.rock_ridge is True:
                info = await self.parse_system_use(record.system_use[self.susp_skip:])
                if info.relocated is True:
                    # listed at its original place with a child link
                    continue
                await self.apply_rock_ridge(entry, info)

            if pending is not None:
                # the next part of a file stored in more extents
                pending.extents += entry.extents
                pending.size += entry.size
                if record.flags & FLAG_MULTI_EXTENT == 0:
                    pending = None
                continue
            if record.flags & FLAG_MULTI_EXTENT:
                pending = entry
            res.append(entry)
        return res

    async def apply_rock_ridge(self, entry:ISO9660Entry, info:RockRidgeInfo):
        if info.name is not None:
            entry.name = info.name
        if info.mode is not None:
            entry.mode = info.mode
            entry.uid = info.uid
            entry.gid = info.gid
        if info.inode is not None:
            entry.inode = info.inode
        if info.times.get('modify') is not None:
            entry.mtime = info.times['modify']
        entry.atime = info.times.get('access')
        entry.ctime = info.times.get('attributes')
        if info.symlink is not None:
            entry.symlink_target = info.symlink
        if info.child_link is not None:
            # a directory moved because of the depth limit of ISO9660, its own record (.) has its size
            first = await self.read_at(info.child_link * self.block_size, self.block_size)
            record = ISODirectoryRecord.from_bytes(first[:first[0]])
            entry.record = record
            entry.directory = True
            entry.extents = [(record.extent + record.ext_attr_length, record.data_length)]
            entry.size = record.data_length

    def __str__(self):
        if self.joliet is True:
            kind = 'Joliet'
        elif self.rock_ridge is True:
            kind = 'ISO9660 with Rock Ridge'
        else:
            kind = 'ISO9660'
        return '%s volume %s' % (kind, self.volume_name)
