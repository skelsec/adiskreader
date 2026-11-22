import struct
import datetime

# all numbers of the QCOW2 format are big endian

QCOW2_MAGIC = b'QFI\xfb'
MAX_HEADER_SIZE = 64 * 1024 # the header and its extensions are in the first cluster
MAX_NAME_SIZE = 1023
MAX_L1_SIZE = 32 * 1024 * 1024 // 8
MAX_SNAPSHOTS = 65536
MAX_SNAPSHOT_SIZE = 1024 + 2 * 65536
MAX_SNAPSHOT_TABLE_SIZE = 64 * 1024 * 1024

INCOMPAT_DIRTY       = 1 # the refcounts might be wrong, those are not used for reading
INCOMPAT_CORRUPT     = 2
INCOMPAT_DATA_FILE   = 4
INCOMPAT_COMPRESSION = 8
INCOMPAT_EXTENDED_L2 = 16
INCOMPAT_SUPPORTED = INCOMPAT_DIRTY | INCOMPAT_CORRUPT | INCOMPAT_DATA_FILE | INCOMPAT_COMPRESSION | INCOMPAT_EXTENDED_L2

AUTOCLEAR_DATA_FILE_RAW = 2

COMPRESSION_DEFLATE = 0
COMPRESSION_ZSTD = 1

EXTENSION_END = 0
EXTENSION_BACKING_FORMAT = 0xE2792ACA
EXTENSION_DATA_FILE = 0x44415441

# offset of a L2 table / cluster in the L1 and L2 entries
OFFSET_MASK = 0x00fffffffffffe00
L2E_ZERO = 1
L2E_COMPRESSED = 1 << 62
L2E_COPIED = 1 << 63


class QCOW2Header:
    def __init__(self):
        self.version = 0
        self.backing_file_offset = 0
        self.backing_file_size = 0
        self.cluster_bits = 0
        self.size = 0 # of the disk, in bytes
        self.crypt_method = 0
        self.l1_size = 0 # number of entries
        self.l1_table_offset = 0
        self.refcount_table_offset = 0
        self.refcount_table_clusters = 0
        self.nb_snapshots = 0
        self.snapshots_offset = 0
        self.incompatible_features = 0
        self.compatible_features = 0
        self.autoclear_features = 0
        self.refcount_order = 4
        self.header_length = 72
        self.compression_type = COMPRESSION_DEFLATE
        self.extensions = [] # (type, data)
        self.backing_file:str = None # read by the disk
        self.backing_format:str = None
        self.data_file:str = None

    @staticmethod
    def from_bytes(data:bytes):
        if data[:4] != QCOW2_MAGIC:
            raise Exception('Not a QCOW2 file')
        if len(data) < 72:
            raise Exception('QCOW2 header is truncated')
        header = QCOW2Header()
        (header.version, header.backing_file_offset, header.backing_file_size, header.cluster_bits, header.size,
            header.crypt_method, header.l1_size, header.l1_table_offset, header.refcount_table_offset,
            header.refcount_table_clusters, header.nb_snapshots, header.snapshots_offset) = struct.unpack_from('>IQIIQIIQQIIQ', data, 4)
        if header.version not in (2, 3):
            raise NotImplementedError('QCOW version %s is not supported' % header.version)
        if not 9 <= header.cluster_bits <= 21:
            raise Exception('Invalid cluster size in the QCOW2 header')
        if header.version >= 3:
            if len(data) < 104:
                raise Exception('QCOW2 header is truncated')
            (header.incompatible_features, header.compatible_features, header.autoclear_features,
                header.refcount_order, header.header_length) = struct.unpack_from('>QQQII', data, 72)
            if header.header_length < 104 or header.header_length > len(data):
                raise Exception('Invalid header length in the QCOW2 header')
            if header.header_length > 104 and header.incompatible_features & INCOMPAT_COMPRESSION:
                header.compression_type = data[104]

        # the extensions are between the header and the name of the backing file
        pos = header.header_length
        end = len(data)
        if header.backing_file_offset != 0:
            end = min(end, header.backing_file_offset)
        while pos + 8 <= end:
            exttype, extlen = struct.unpack_from('>II', data, pos)
            pos += 8
            if exttype == EXTENSION_END or pos + extlen > end:
                break
            extdata = data[pos:pos+extlen]
            header.extensions.append((exttype, extdata))
            if exttype == EXTENSION_BACKING_FORMAT:
                header.backing_format = extdata.decode('utf-8', errors='replace')
            elif exttype == EXTENSION_DATA_FILE:
                header.data_file = extdata.decode('utf-8', errors='replace')
            pos += (extlen + 7) & ~7
        return header


class QCOW2Snapshot:
    def __init__(self):
        self.id:str = None
        self.name:str = None
        self.l1_table_offset = 0
        self.l1_size = 0
        self.date:datetime.datetime = None
        self.vm_clock_nsec = 0
        self.vm_state_size = 0
        self.disk_size:int = None # size of the disk when the snapshot was taken

    @staticmethod
    def from_bytes(data:bytes, pos:int):
        """Returns the snapshot and the position of the next one"""
        if pos + 40 > len(data):
            raise Exception('QCOW2 snapshot table is truncated')
        snapshot = QCOW2Snapshot()
        (snapshot.l1_table_offset, snapshot.l1_size, id_size, name_size, date_sec, date_nsec,
            snapshot.vm_clock_nsec, snapshot.vm_state_size, extra_size) = struct.unpack_from('>QIHHIIQII', data, pos)
        pos += 40
        if pos + extra_size + id_size + name_size > len(data):
            raise Exception('QCOW2 snapshot table is truncated')
        if extra_size >= 8:
            snapshot.vm_state_size = struct.unpack_from('>Q', data, pos)[0]
        if extra_size >= 16:
            snapshot.disk_size = struct.unpack_from('>Q', data, pos + 8)[0]
        pos += extra_size
        snapshot.id = data[pos:pos+id_size].decode('utf-8', errors='replace')
        pos += id_size
        snapshot.name = data[pos:pos+name_size].decode('utf-8', errors='replace')
        pos += name_size
        snapshot.date = datetime.datetime.fromtimestamp(date_sec, datetime.timezone.utc) + datetime.timedelta(microseconds=date_nsec // 1000)
        # the entries are aligned to 8 bytes
        return snapshot, (pos + 7) & ~7

    def __str__(self):
        return '%s %s %s' % (self.id, self.name, self.date)
