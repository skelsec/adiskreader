import enum
import io
import uuid
import datetime

WIM_MAGIC = b'MSWIM\x00\x00\x00'
WIM_PIPABLE_MAGIC = b'WLPWM\x00\x00\x00'
WIM_HEADER_SIZE = 208
WIM_VERSION_DEFAULT = 0x10d00
WIM_VERSION_SOLID = 0xe00
ZERO_HASH = b'\x00' * 20
# original size in the lookup entry of the solid resources. The entries of the data stored in them have the offset
# in the uncompressed data of the solid resources before them and the size of the data instead of the size in the file.
SOLID_RESOURCE_MAGIC = 0x100000000

class WIMHeaderFlag(enum.IntFlag):
    RESERVED = 0x00000001
    COMPRESSION = 0x00000002
    READONLY = 0x00000004
    SPANNED = 0x00000008
    RESOURCE_ONLY = 0x00000010
    METADATA_ONLY = 0x00000020
    WRITE_IN_PROGRESS = 0x00000040
    RP_FIX = 0x00000080
    COMPRESS_RESERVED = 0x00010000
    COMPRESS_XPRESS = 0x00020000
    COMPRESS_LZX = 0x00040000
    COMPRESS_LZMS = 0x00080000
    COMPRESS_XPRESS_2 = 0x00200000

class WIMResourceFlag(enum.IntFlag):
    FREE = 0x01
    METADATA = 0x02
    COMPRESSED = 0x04
    SPANNED = 0x08
    SOLID = 0x10

class WIMFileAttribute(enum.IntFlag):
    READONLY = 0x00000001
    HIDDEN = 0x00000002
    SYSTEM = 0x00000004
    DIRECTORY = 0x00000010
    ARCHIVE = 0x00000020
    DEVICE = 0x00000040
    NORMAL = 0x00000080
    TEMPORARY = 0x00000100
    SPARSE_FILE = 0x00000200
    REPARSE_POINT = 0x00000400
    COMPRESSED = 0x00000800
    OFFLINE = 0x00001000
    NOT_CONTENT_INDEXED = 0x00002000
    ENCRYPTED = 0x00004000
    VIRTUAL = 0x00010000

def filetime_to_dt(filetime:int):
    try:
        return datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(microseconds=filetime // 10)
    except OverflowError:
        return datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)


class WIMResourceHeader:
    """Location of a resource (file contents, metadata of an image, tables) in the WIM file"""
    SIZE = 24
    def __init__(self):
        self.size = 0 # in the file
        self.flags = WIMResourceFlag(0)
        self.offset = 0
        self.original_size = 0

    @staticmethod
    def from_bytes(data:bytes):
        return WIMResourceHeader.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff:io.BytesIO):
        hdr = WIMResourceHeader()
        hdr.size = int.from_bytes(buff.read(7), 'little')
        hdr.flags = WIMResourceFlag(buff.read(1)[0])
        hdr.offset = int.from_bytes(buff.read(8), 'little')
        hdr.original_size = int.from_bytes(buff.read(8), 'little')
        return hdr

    def __str__(self):
        return 'offset: %s size: %s original size: %s flags: %s' % (self.offset, self.size, self.original_size, str(self.flags))


class WIMHeader:
    def __init__(self):
        self.magic = None
        self.header_size = None
        self.version = None
        self.flags = None
        self.chunk_size = None
        self.guid = None
        self.part_number = None
        self.total_parts = None
        self.image_count = None
        self.lookup_table:WIMResourceHeader = None
        self.xml_data:WIMResourceHeader = None
        self.boot_metadata:WIMResourceHeader = None
        self.boot_index = None
        self.integrity:WIMResourceHeader = None

    @staticmethod
    def from_bytes(data:bytes):
        return WIMHeader.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff:io.BytesIO):
        hdr = WIMHeader()
        hdr.magic = buff.read(8)
        if hdr.magic == WIM_PIPABLE_MAGIC:
            raise NotImplementedError('Pipable WIM files are not supported')
        if hdr.magic != WIM_MAGIC:
            raise Exception('Not a WIM file')
        hdr.header_size = int.from_bytes(buff.read(4), 'little')
        hdr.version = int.from_bytes(buff.read(4), 'little')
        hdr.flags = WIMHeaderFlag(int.from_bytes(buff.read(4), 'little'))
        hdr.chunk_size = int.from_bytes(buff.read(4), 'little')
        hdr.guid = uuid.UUID(bytes_le=buff.read(16))
        hdr.part_number = int.from_bytes(buff.read(2), 'little')
        hdr.total_parts = int.from_bytes(buff.read(2), 'little')
        hdr.image_count = int.from_bytes(buff.read(4), 'little')
        hdr.lookup_table = WIMResourceHeader.from_buffer(buff)
        hdr.xml_data = WIMResourceHeader.from_buffer(buff)
        hdr.boot_metadata = WIMResourceHeader.from_buffer(buff)
        hdr.boot_index = int.from_bytes(buff.read(4), 'little')
        hdr.integrity = WIMResourceHeader.from_buffer(buff)
        return hdr

    @property
    def compression(self):
        """Name of the compression of the resources, None if they are not compressed"""
        if WIMHeaderFlag.COMPRESSION not in self.flags:
            return None
        if WIMHeaderFlag.COMPRESS_LZX in self.flags:
            return 'LZX'
        if WIMHeaderFlag.COMPRESS_XPRESS in self.flags or WIMHeaderFlag.COMPRESS_XPRESS_2 in self.flags:
            return 'XPRESS'
        if WIMHeaderFlag.COMPRESS_LZMS in self.flags:
            return 'LZMS'
        return 'UNKNOWN'

    def __str__(self):
        t = []
        t.append('Version      : 0x%x' % self.version)
        t.append('Flags        : %s' % str(self.flags))
        t.append('Compression  : %s' % self.compression)
        t.append('Chunk size   : %s' % self.chunk_size)
        t.append('GUID         : %s' % self.guid)
        t.append('Part         : %s/%s' % (self.part_number, self.total_parts))
        t.append('Images       : %s' % self.image_count)
        t.append('Boot index   : %s' % self.boot_index)
        return '\n'.join(t)


class WIMLookupEntry:
    """Entry of the lookup table: the resource holding the data with the given SHA1 hash"""
    SIZE = 50
    def __init__(self):
        self.resource:WIMResourceHeader = None
        self.part_number = None
        self.refcount = None
        self.hash = None
        self.solid_resources = None # WIMResource list, set by the WIM for the data stored in solid resources

    @property
    def data_size(self):
        """Size of the uncompressed data"""
        if WIMResourceFlag.SOLID in self.resource.flags:
            return self.resource.size
        return self.resource.original_size

    @staticmethod
    def from_bytes(data:bytes):
        return WIMLookupEntry.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff:io.BytesIO):
        entry = WIMLookupEntry()
        entry.resource = WIMResourceHeader.from_buffer(buff)
        entry.part_number = int.from_bytes(buff.read(2), 'little')
        entry.refcount = int.from_bytes(buff.read(4), 'little')
        entry.hash = buff.read(20)
        return entry

    def __str__(self):
        return '%s part: %s refcount: %s %s' % (self.hash.hex(), self.part_number, self.refcount, self.resource)


class WIMStreamEntry:
    """Extra stream of a file: a named data stream, or the unnamed one if the file has named streams as well"""
    FIXED_SIZE = 38
    def __init__(self):
        self.length = None
        self.hash = None
        self.name = ''

    @staticmethod
    def from_bytes(data:bytes):
        entry = WIMStreamEntry()
        if len(data) < WIMStreamEntry.FIXED_SIZE:
            raise Exception('WIM stream entry is truncated')
        entry.length = int.from_bytes(data[0:8], 'little')
        entry.hash = data[16:36]
        name_size = int.from_bytes(data[36:38], 'little')
        entry.name = data[38:38+name_size].decode('utf-16-le', errors='replace')
        return entry


class WIMDentryHeader:
    """Directory entry of the metadata resource, without its extra streams"""
    FIXED_SIZE = 102
    def __init__(self):
        self.length = None
        self.attributes = None
        self.security_id = None
        self.subdir_offset = None
        self.creation_time = None
        self.last_access_time = None
        self.last_write_time = None
        self.hash = None
        self.reparse_tag = None
        self.hard_link_group_id = None
        self.num_extra_streams = None
        self.short_name = ''
        self.name = ''

    @staticmethod
    def from_bytes(data:bytes):
        if len(data) < WIMDentryHeader.FIXED_SIZE:
            raise Exception('WIM directory entry is truncated')
        entry = WIMDentryHeader()
        entry.length = int.from_bytes(data[0:8], 'little')
        entry.attributes = WIMFileAttribute(int.from_bytes(data[8:12], 'little'))
        entry.security_id = int.from_bytes(data[12:16], 'little', signed=True)
        entry.subdir_offset = int.from_bytes(data[16:24], 'little')
        entry.creation_time = int.from_bytes(data[40:48], 'little')
        entry.last_access_time = int.from_bytes(data[48:56], 'little')
        entry.last_write_time = int.from_bytes(data[56:64], 'little')
        entry.hash = data[64:84]
        if WIMFileAttribute.REPARSE_POINT in entry.attributes:
            entry.reparse_tag = int.from_bytes(data[88:92], 'little')
        else:
            entry.hard_link_group_id = int.from_bytes(data[88:96], 'little')
        entry.num_extra_streams = int.from_bytes(data[96:98], 'little')
        short_name_size = int.from_bytes(data[98:100], 'little')
        name_size = int.from_bytes(data[100:102], 'little')
        pos = WIMDentryHeader.FIXED_SIZE
        entry.name = data[pos:pos+name_size].decode('utf-16-le', errors='replace')
        if name_size > 0:
            # the names are null terminated, the terminator is not included in their size
            pos += name_size + 2
        entry.short_name = data[pos:pos+short_name_size].decode('utf-16-le', errors='replace')
        return entry
