import struct

# Structures of the Veeam backup storage files (vbk, vib), all integers are little endian.
# The format has no public description, notes/vbk_format.md tells what is known and how it was checked.

PAGE_SIZE = 4096
NO_PAGE = -1
SLOT_OFFSET = PAGE_SIZE
SLOT_FIXED_SIZE = 124
BANK_REF_SIZE = 16
MAX_BANKS = 0x100000
MAX_BLOCK_SIZE = 64 * 1024 * 1024

VERSION_7_VARIANT = 0x10008

DIR_ENTRY_SIZE = 192
MAX_NAME_LENGTH = 128
BLOCKS_PER_TABLE = 1088
TABLE_RECORD_SIZE = 24

KIND_FOLDER = 1
KIND_EXTERNAL = 2
KIND_FILE = 3
KIND_PATCH = 4
KIND_INCREMENT = 5

BLOCK_ZERO = 0x01
BLOCK_STORED = (0x00, 0x10)

COMPRESSION_NONE = -1
COMPRESSION_RLE = 2
COMPRESSION_ZLIB_HIGH = 3
COMPRESSION_ZLIB_LOW = 4
COMPRESSION_LZ4 = 7
COMPRESSION_ZSTD = (8, 9)
COMPRESSION_NAMES = {-1 : 'none', 2 : 'RLE', 3 : 'zlib', 4 : 'zlib', 7 : 'LZ4', 8 : 'zstd', 9 : 'zstd'}

LZ4_BLOCK_MAGIC = 0xF800000F
LZ4_BLOCK_HEADER_SIZE = 12

ZERO_KEY = b'\x00' * 16

def page_bank(page_id:int):
    return page_id >> 32

def page_index(page_id:int):
    return page_id & 0xFFFFFFFF


class VBKHeader:
    """First page of the file"""
    def __init__(self):
        self.version = None
        self.initialized = None
        self.digest_type = None
        self.slot_format = None
        self.block_size = None

    @staticmethod
    def from_bytes(data:bytes):
        if len(data) < 271:
            raise Exception('VBK header is truncated')
        header = VBKHeader()
        header.version, header.initialized, namelen = struct.unpack_from('<III', data, 0)
        if namelen >= 64:
            raise Exception('Not a VBK file')
        header.digest_type = data[12:12+namelen].decode('ascii', 'replace')
        header.slot_format, header.block_size = struct.unpack_from('<II', data, 263)
        return header

    @property
    def long_records(self):
        """The records of the blocks have the id of the encryption key as well"""
        return self.version in (7, VERSION_7_VARIANT) or self.version >= 9

    @property
    def vector_v2(self):
        """The pages of the vectors are listed in table pages (linked to each other in the older files)"""
        return self.version >= 12 and self.version != VERSION_7_VARIANT

    @property
    def block_ref_size(self):
        return 46 if self.long_records else 30

    @property
    def increment_ref_size(self):
        return 53 if self.long_records else 37

    @property
    def store_record_size(self):
        return 60 if self.long_records else 44

    def __str__(self):
        t = []
        t.append('Version      : %s' % self.version)
        t.append('Digest       : %s' % self.digest_type)
        t.append('Block size   : %s' % self.block_size)
        return '\n'.join(t)


class VBKBank:
    """Location of a bank, a run of metadata pages. The first page of the bank is its header."""
    def __init__(self, checksum:int, offset:int, size:int):
        self.checksum = checksum
        self.offset = offset
        self.size = size


class VBKSlot:
    """Root of the metadata, the file has two of them"""
    def __init__(self):
        self.checksum = None
        self.generation = None
        self.storage_eof = None
        self.directory_root = None
        self.directory_count = None
        self.store_root = None
        self.store_count = None
        self.crypto_root = None
        self.max_banks = None
        self.banks = []

    @staticmethod
    def get_size(max_banks:int):
        """Size of the data the checksum is calculated for"""
        return SLOT_FIXED_SIZE + max_banks * BANK_REF_SIZE

    @staticmethod
    def get_padded_size(max_banks:int):
        return (VBKSlot.get_size(max_banks) + PAGE_SIZE - 1) // PAGE_SIZE * PAGE_SIZE

    @staticmethod
    def from_bytes(data:bytes):
        slot = VBKSlot()
        slot.checksum, _, slot.generation, slot.storage_eof = struct.unpack_from('<IIQQ', data, 0)
        slot.directory_root, slot.directory_count, slot.store_root, slot.store_count = struct.unpack_from('<qQqQ', data, 28)
        slot.crypto_root, = struct.unpack_from('<q', data, 92)
        slot.max_banks, bank_count = struct.unpack_from('<II', data, 116)
        if bank_count > slot.max_banks or len(data) < SLOT_FIXED_SIZE + bank_count * BANK_REF_SIZE:
            raise Exception('Invalid bank count in the VBK slot')
        for i in range(bank_count):
            slot.banks.append(VBKBank(*struct.unpack_from('<IqI', data, SLOT_FIXED_SIZE + i * BANK_REF_SIZE)))
        return slot


class VBKDirectoryRecord:
    """Entry of a folder: a folder, a file or the changed blocks of a file (increment)"""
    def __init__(self):
        self.kind = None
        self.name = None
        self.properties_page = None
        self.root = None  # folder: the vector of its entries, file: its block table
        self.count = None # number of entries / blocks / changed blocks
        self.size = 0

    @staticmethod
    def from_bytes(data:bytes):
        record = VBKDirectoryRecord()
        record.kind, namelen = struct.unpack_from('<II', data, 0)
        if namelen > MAX_NAME_LENGTH:
            raise Exception('Invalid name in the VBK directory')
        record.name = data[8:8+namelen].decode('utf-8', 'replace')
        record.properties_page, = struct.unpack_from('<q', data, 136)
        if record.kind == KIND_FOLDER:
            record.root, record.count = struct.unpack_from('<qI', data, 148)
        else:
            record.root, record.count, record.size = struct.unpack_from('<qQQ', data, 152)
        return record


class VBKBlockRef:
    """Block of a file, the data is in the record of the block store with the same index"""
    def __init__(self):
        self.size = None
        self.kind = None
        self.digest = None
        self.store_index = None
        self.block_index = None # increments only

    @staticmethod
    def from_bytes(data:bytes, increment:bool = False):
        ref = VBKBlockRef()
        ref.size, ref.kind = struct.unpack_from('<IB', data, 0)
        ref.digest = data[5:21]
        ref.store_index, = struct.unpack_from('<Q', data, 21)
        if increment is True:
            ref.block_index, = struct.unpack_from('<Q', data, 29)
        return ref


class VBKStoreRecord:
    """Where and how the data of a block is stored in the file"""
    def __init__(self):
        self.offset = None
        self.allocated_size = None
        self.digest = None
        self.compression = None
        self.stored_size = None
        self.source_size = None
        self.key_id = ZERO_KEY

    @staticmethod
    def from_bytes(data:bytes):
        record = VBKStoreRecord()
        record.offset, record.allocated_size = struct.unpack_from('<qI', data, 5)
        record.digest = data[18:34]
        record.compression, _, record.stored_size, record.source_size = struct.unpack_from('<bBII', data, 34)
        if len(data) >= 60:
            record.key_id = data[44:60]
        return record
