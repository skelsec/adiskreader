import struct
import hashlib
import ntpath
import datetime
import xml.etree.ElementTree as ET
from cachetools import LRUCache

from adiskreader import logger
from adiskreader import compression
from adiskreader.compression import DecompressionError
from adiskreader.compression.lz4 import lz4_decompress
from adiskreader.compression.veeamrle import veeam_rle_decompress
from adiskreader.datasource import DataSource
from adiskreader.backups.vbk.structures import *

# Veeam Backup & Replication backup files.
# vbk: full backup, vib: incremental backup (the blocks changed since the previous file of the chain),
# vbm: XML description of the chain, it is not needed for reading the other two.
# A backup file is a storage: a directory tree of files, the files are lists of blocks and the blocks
# are stored once per storage. The disks of the machines are stored as files holding the raw contents of the disk.
# notes/vbk_format.md describes the format. Checked with files made by Veeam 13 (format version 13) for Hyper-V machines,
# the older versions of the format are read as third party tools do it and were not tested with real files.
# Encrypted backups are not supported, the data needed for checking a password can be read from them.

BANK_CACHE_SIZE = 256
BLOCK_CACHE_BYTES = 32 * 1024 * 1024
MAX_BANK_SIZE = 0x10001 * PAGE_SIZE
MAX_DIRECTORY_DEPTH = 32
MAX_DIRECTORY_ENTRIES = 1000000
VBM_MAX_SIZE = 256 * 1024 * 1024
DISK_EXTENSIONS = ('.vhdx', '.vhd', '.avhdx', '.avhd', '-flat.vmdk')

PASSWORD_MARKER = struct.pack('<III', 0x60, 0x10, 0x40)
# what the key of the password check is derived with: name, hash, iterations (newest first)
PASSWORD_KDFS = [
    ('PBKDF2-HMAC-SHA256-600000', 'sha256', 600000),
    ('PBKDF2-HMAC-SHA1-10000', 'sha1', 10000),
]
# the byte 8 bytes before the marker tells them apart in the files seen so far (one file of each)
PASSWORD_KDF_IDS = {0 : 'PBKDF2-HMAC-SHA1-10000', 2 : 'PBKDF2-HMAC-SHA256-600000'}

def split_path(path:str):
    return [part for part in path.replace('/', '\\').split('\\') if part != '']

def vector_slots(count:int):
    """Yields the place of the data pages of a vector in its table pages (all the table pages joined)"""
    # the first two entries of the first table page are taken, the following pages have room for 508, 511, 511, 508 ...
    capacities = (511, 508, 511)
    table = 0
    capacity = 510
    used = 0
    for _ in range(count):
        if used == capacity:
            table += 1
            capacity = capacities[table % 3]
            used = 0
        yield table * 512 + (512 - capacity) + used
        used += 1


class VBKEncryptedError(NotImplementedError):
    pass


class VBKPasswordVerifier:
    """
    The data the password of an encrypted backup is checked with.
    The key and the IV are derived from the password (UTF-16LE) with PBKDF2, the ciphertext is 4 bytes
    encrypted with AES-256-CBC, the padding of it tells if the password is right.
    """
    def __init__(self, salt:bytes, ciphertext:bytes, blob:bytes, kdf:str = None):
        self.salt = salt
        self.ciphertext = ciphertext
        self.blob = blob # 96 bytes stored before the ciphertext, not known what it is
        self.kdf = kdf # name of the key derivation (PASSWORD_KDFS), None if the file does not tell it

    def to_hashcat(self):
        """
        Hash in the format of hashcat mode 31200 (Veeam VBK).
        The mode is PBKDF2-HMAC-SHA1 with 10000 iterations. Newer Veeam versions (13 for sure) use PBKDF2-HMAC-SHA256
        with 600000 iterations: the hash of those files has the same parts and 600000 as its iteration count,
        mode 31200 does not find the password of them.
        """
        iterations = 10000
        for name, _, count in PASSWORD_KDFS:
            if name == self.kdf:
                iterations = count
        return '$vbk$*%s*%s*%s' % (self.salt.hex(), iterations, self.ciphertext.hex())

    def check_password(self, password:str):
        """Returns the name of the key derivation the password is right with, None if the password is wrong"""
        from unicrypto.symmetric import AES, MODE_CBC
        # the one the file tells first, the others are tried as well: how the file tells it is a guess
        for name, digest, iterations in sorted(PASSWORD_KDFS, key = lambda kdf: kdf[0] != self.kdf):
            key = hashlib.pbkdf2_hmac(digest, password.encode('utf-16-le'), self.salt, iterations, 48)
            plaintext = AES(key[:32], MODE_CBC, key[32:]).decrypt(self.ciphertext)
            if plaintext[4:] == b'\x0c' * 12:
                return name
        return None

    def __str__(self):
        return self.to_hashcat()


class VBKVector:
    """Array of fixed size records stored in metadata pages"""
    def __init__(self, storage, root:int, count:int, record_size:int):
        self.storage = storage
        self.root = root
        self.count = count
        self.record_size = record_size
        self.v2 = storage.header.vector_v2
        self.per_page = (PAGE_SIZE if self.v2 is True else PAGE_SIZE - 8) // record_size
        self.__pages = None

    async def load_pages(self):
        """Collects the ids of the pages holding the records"""
        needed = (self.count + self.per_page - 1) // self.per_page
        pages = []
        if self.v2 is True:
            slots = list(vector_slots(needed))
            table = []
            page_id = self.root
            while needed > 0 and len(table) <= slots[-1]:
                if page_id == NO_PAGE:
                    raise Exception('VBK metadata vector is shorter than its record count')
                entries = struct.unpack('<512q', await self.storage.read_page(page_id))
                table += entries
                page_id = entries[0]
            pages = [table[slot] for slot in slots]
        else:
            # each page starts with the id of the next one
            page_id = self.root
            for _ in range(needed):
                if page_id == NO_PAGE:
                    raise Exception('VBK metadata vector is shorter than its record count')
                pages.append(page_id)
                page_id, = struct.unpack_from('<q', await self.storage.read_page(page_id), 0)
        self.__pages = pages

    async def get(self, index:int):
        """Returns the bytes of the record"""
        if index < 0 or index >= self.count:
            raise IndexError('No record %s in the VBK metadata vector' % index)
        if self.__pages is None:
            await self.load_pages()
        page = await self.storage.read_page(self.__pages[index // self.per_page])
        start = (index % self.per_page) * self.record_size
        if self.v2 is False:
            start += 8
        return page[start:start+self.record_size]

    async def get_all(self):
        return [await self.get(index) for index in range(self.count)]


class VBKEntry:
    """Folder or file of a storage"""
    def __init__(self, storage, record:VBKDirectoryRecord, parent):
        self.storage = storage
        self.record = record
        self.parent = parent
        self.name = record.name
        self.kind = record.kind
        self.size = record.size
        self.children = []

    def is_directory(self):
        return self.kind == KIND_FOLDER

    def is_increment(self):
        """The entry has the blocks changed since the previous backup only"""
        return self.kind == KIND_INCREMENT

    def get_path(self):
        parts = []
        entry = self
        while entry is not None:
            parts.append(entry.name)
            entry = entry.parent
        return '\\'.join(reversed(parts))

    def get_child(self, name:str):
        for child in self.children:
            if child.name == name:
                return child
        lname = name.lower()
        for child in self.children:
            if child.name.lower() == lname:
                return child
        return None

    def __str__(self):
        return '%s %s' % ('DIR ' if self.is_directory() is True else 'FILE', self.get_path())


class VBKStorage:
    """One backup file (vbk or vib)"""
    def __init__(self):
        self.__stream = None
        self.__banks = LRUCache(maxsize=BANK_CACHE_SIZE)
        self.__store = None
        self.__zstd = None
        self.header:VBKHeader = None
        self.slot:VBKSlot = None
        self.encrypted = False
        self.verify = False
        self.root = [] # the entries of the top folder

    @staticmethod
    async def from_datasource(ds, verify:bool = None):
        """
        verify: check the checksums of the metadata and of the blocks which have one.
        By default it is done when the native module is there, calculating them is slow without it.
        """
        storage = VBKStorage()
        await storage.setup(ds, verify)
        return storage

    @staticmethod
    async def is_vbk(ds):
        """Checks if the file looks like a backup storage, restores the position of the datasource"""
        pos = await ds.tell()
        await ds.seek(0, 0)
        data = await ds.read(PAGE_SIZE + 8)
        await ds.seek(pos, 0)
        if len(data) < PAGE_SIZE + 8:
            return False
        version, initialized, namelen = struct.unpack_from('<III', data, 0)
        if (version & 0xFFFF) < 5 or (version & 0xFFFF) > 64 or (version >> 16) > 1 or initialized > 1 or namelen < 2 or namelen >= 64:
            return False
        if data[12:12+namelen].isalnum() is False or any(data[12+namelen:76]):
            return False
        return data[PAGE_SIZE+4:PAGE_SIZE+8] == b'\x01\x00\x00\x00'

    async def setup(self, ds, verify:bool = None):
        self.__stream = ds
        self.verify = verify if verify is not None else hasattr(compression.native, 'crc32c')
        self.header = VBKHeader.from_bytes(await self.read_at(0, PAGE_SIZE))
        if (self.header.version & 0xFFFF) < 5 or (self.header.version & 0xFFFF) > 64:
            raise Exception('Not a VBK file')
        if self.header.initialized == 0:
            raise Exception('The backup file was not finished by the backup job')
        if self.header.block_size == 0 or self.header.block_size > MAX_BLOCK_SIZE:
            raise Exception('Invalid block size in the VBK header: %s' % self.header.block_size)

        self.slot = await self.read_slots()
        self.encrypted = self.slot.crypto_root != NO_PAGE
        if self.encrypted is True:
            # the metadata is encrypted as well
            return
        self.__store = VBKVector(self, self.slot.store_root, self.slot.store_count, self.header.store_record_size)
        self.root = await self.read_directory(self.slot.directory_root, self.slot.directory_count, None, 0)

    async def read_at(self, offset:int, size:int):
        await self.__stream.seek(offset, 0)
        data = await self.__stream.read(size)
        if len(data) != size:
            raise Exception('The VBK file is truncated')
        return data

    async def read_slots(self):
        # the two slots are after each other, the one which was written later is used
        max_banks, = struct.unpack_from('<I', await self.read_at(SLOT_OFFSET, PAGE_SIZE), 116)
        if max_banks > MAX_BANKS:
            raise Exception('Invalid bank count in the VBK slot')
        best = None
        for index in range(2):
            try:
                data = await self.read_at(SLOT_OFFSET + index * VBKSlot.get_padded_size(max_banks), VBKSlot.get_size(max_banks))
                slot = VBKSlot.from_bytes(data)
                if slot.max_banks != max_banks or compression.crc32c(data[4:]) != slot.checksum:
                    raise Exception('checksum mismatch')
            except Exception as e:
                logger.debug('VBK slot %s is not valid: %s' % (index, e))
                continue
            if best is None or slot.generation > best.generation:
                best = slot
        if best is None:
            raise Exception('The VBK file has no valid metadata slot')
        return best

    async def read_bank(self, index:int):
        data = self.__banks.get(index)
        if data is None:
            if index >= len(self.slot.banks):
                raise Exception('Invalid page id in the VBK metadata')
            bank = self.slot.banks[index]
            if bank.size < 2 * PAGE_SIZE or bank.size > MAX_BANK_SIZE:
                raise Exception('Invalid bank size in the VBK metadata')
            data = await self.read_at(bank.offset, bank.size)
            if self.verify is True and compression.crc32c(data) != bank.checksum:
                raise Exception('Checksum mismatch in bank %s of the VBK metadata' % index)
            self.__banks[index] = data
        return data

    async def read_page(self, page_id:int):
        if page_id < 0:
            raise Exception('Invalid page id in the VBK metadata')
        data = await self.read_bank(page_bank(page_id))
        # the first page of the bank is its header
        start = (page_index(page_id) + 1) * PAGE_SIZE
        if start + PAGE_SIZE > len(data):
            raise Exception('Invalid page id in the VBK metadata')
        return data[start:start+PAGE_SIZE]

    async def read_directory(self, root:int, count:int, parent, depth:int):
        if depth > MAX_DIRECTORY_DEPTH or count > MAX_DIRECTORY_ENTRIES:
            raise Exception('Invalid directory in the VBK metadata')
        entries = []
        for data in await VBKVector(self, root, count, DIR_ENTRY_SIZE).get_all():
            entry = VBKEntry(self, VBKDirectoryRecord.from_bytes(data), parent)
            if entry.is_directory() is True:
                entry.children = await self.read_directory(entry.record.root, entry.record.count, entry, depth + 1)
            entries.append(entry)
        return entries

    def get_entry(self, path:str):
        """Returns the entry of the path, None if it does not exist"""
        entry = None
        for part in split_path(path):
            children = self.root if entry is None else entry.children
            entry = None
            for child in children:
                if child.name == part or (entry is None and child.name.lower() == part.lower()):
                    entry = child
                    if child.name == part:
                        break
            if entry is None:
                return None
        return entry

    def walk(self):
        """Yields all the entries, the folders before their contents"""
        todo = list(reversed(self.root))
        while len(todo) > 0:
            entry = todo.pop()
            yield entry
            todo += reversed(entry.children)

    async def read_block(self, store_index:int):
        """Returns the data of a block of the block store"""
        if self.__store is None:
            raise VBKEncryptedError('The backup file is encrypted')
        record = VBKStoreRecord.from_bytes(await self.__store.get(store_index))
        if record.key_id != ZERO_KEY:
            raise VBKEncryptedError('The block is encrypted')
        if record.source_size > MAX_BLOCK_SIZE or record.stored_size > MAX_BLOCK_SIZE + PAGE_SIZE or record.offset <= 0:
            raise Exception('Invalid block in the VBK block store')
        data = await self.read_at(record.offset, record.stored_size)
        try:
            return self.decompress(data, record.compression, record.source_size)
        except DecompressionError as e:
            raise Exception('Block %s of the VBK file is damaged: %s' % (store_index, e))

    def decompress(self, data:bytes, method:int, size:int):
        if method == COMPRESSION_NONE:
            if len(data) != size:
                raise DecompressionError('The size of the block does not match')
            return data
        if method == COMPRESSION_LZ4:
            if len(data) < LZ4_BLOCK_HEADER_SIZE:
                raise DecompressionError('LZ4 block is truncated')
            magic, checksum, source_size = struct.unpack_from('<III', data, 0)
            if magic != LZ4_BLOCK_MAGIC or source_size != size:
                raise DecompressionError('Invalid LZ4 block header')
            res = lz4_decompress(data[LZ4_BLOCK_HEADER_SIZE:], size)
            if self.verify is True and compression.crc32c(res) != checksum:
                raise DecompressionError('Checksum mismatch')
            return res
        if method == COMPRESSION_RLE:
            if len(data) < 4 or int.from_bytes(data[:4], 'little') != size:
                raise DecompressionError('Invalid RLE block header')
            return veeam_rle_decompress(data[4:], size)
        if method in COMPRESSION_ZSTD:
            if self.__zstd is None:
                self.__zstd = compression.get_zstd_decompressor()
            try:
                res = self.__zstd(data, size)
            except NotImplementedError:
                raise
            except Exception as e:
                raise DecompressionError(str(e))
            if len(res) != size:
                raise DecompressionError('The size of the block does not match')
            return res
        raise NotImplementedError('Compression method %s (%s) of the VBK file is not supported' % (method, COMPRESSION_NAMES.get(method, 'unknown')))

    async def get_password_verifier(self):
        """Returns the VBKPasswordVerifier of an encrypted backup file, None if it is not found"""
        if self.encrypted is False:
            return None
        # the bank of the keys is the one which is not encrypted
        first = page_bank(self.slot.crypto_root)
        for index in [first] + [i for i in range(len(self.slot.banks)) if i != first]:
            try:
                data = await self.read_bank(index)
            except Exception as e:
                logger.debug('Bank %s of the VBK file could not be read: %s' % (index, e))
                continue
            pos = data.find(PASSWORD_MARKER)
            if pos < 8 or pos + 188 > len(data):
                continue
            kdf = PASSWORD_KDF_IDS.get(data[pos-8])
            pos += len(PASSWORD_MARKER)
            return VBKPasswordVerifier(data[pos+112:pos+176], data[pos+96:pos+112], data[pos:pos+96], kdf)
        return None

    def __str__(self):
        t = []
        t.append('Datasource   : %s' % self.__stream)
        t.append(str(self.header))
        t.append('Encrypted    : %s' % self.encrypted)
        return '\n'.join(t)


class VBKFile(DataSource):
    """
    File of a restore point. Reads like a file and it is a datasource as well, so the disks stored in the backup can be opened from it.
    layers are the entries of the file in the storages of the chain, the newest first, ending with the one in a full backup.
    """
    def __init__(self, path:str, layers:list):
        super().__init__({'path': path})
        self.__layers = layers
        self.__increments = [] # (storage, block index -> record) of the incremental backups, the newest first
        self.__base = None # storage of the full backup
        self.__tables = [] # the block table of the full backup is made of tables of BLOCKS_PER_TABLE blocks: (root page, count)
        self.__refs = LRUCache(maxsize=64) # table index -> records
        self.__offset = 0
        self.block_size = layers[0].storage.header.block_size
        self.size = layers[0].size
        self.__blocks = LRUCache(maxsize=max(2, BLOCK_CACHE_BYTES // self.block_size))

    async def setup(self):
        for entry in self.__layers:
            storage = entry.storage
            if storage.header.block_size != self.block_size:
                raise NotImplementedError('The backup files of the chain have different block sizes')
            if entry.kind == KIND_INCREMENT:
                refs = {}
                for data in await VBKVector(storage, entry.record.root, entry.record.count, storage.header.increment_ref_size).get_all():
                    refs[int.from_bytes(data[29:37], 'little')] = data
                self.__increments.append((storage, refs))
                continue
            if entry.kind != KIND_FILE:
                raise NotImplementedError('VBK directory entries of type %s are not supported' % entry.kind)
            table_count = (entry.record.count + BLOCKS_PER_TABLE - 1) // BLOCKS_PER_TABLE
            for index, data in enumerate(await VBKVector(storage, entry.record.root, table_count, TABLE_RECORD_SIZE).get_all()):
                root, _, count = struct.unpack('<qqq', data)
                self.__tables.append((root, max(0, min(count, BLOCKS_PER_TABLE, entry.record.count - index * BLOCKS_PER_TABLE))))
            self.__base = storage
            break
        if self.__base is None:
            raise FileNotFoundError('%s is stored as an increment, the full backup it is based on is not opened' % self.config['path'])

    async def get_block_ref(self, index:int):
        """Returns the storage and the reference of the block, None if the block is not stored"""
        for storage, refs in self.__increments:
            data = refs.get(index)
            if data is not None:
                return storage, VBKBlockRef.from_bytes(data, True)
        table_index = index // BLOCKS_PER_TABLE
        if table_index >= len(self.__tables):
            return None, None
        root, count = self.__tables[table_index]
        if root == NO_PAGE or index % BLOCKS_PER_TABLE >= count:
            return None, None
        refs = self.__refs.get(table_index)
        if refs is None:
            refs = VBKVector(self.__base, root, count, self.__base.header.block_ref_size)
            await refs.load_pages()
            self.__refs[table_index] = refs
        return self.__base, VBKBlockRef.from_bytes(await refs.get(index % BLOCKS_PER_TABLE))

    async def read_block(self, index:int):
        """Returns the block of the file, it is always block_size long"""
        data = self.__blocks.get(index)
        if data is None:
            storage, ref = await self.get_block_ref(index)
            if ref is None or ref.kind == BLOCK_ZERO:
                data = b'\x00' * self.block_size
            elif ref.kind in BLOCK_STORED:
                data = await storage.read_block(ref.store_index)
                if len(data) > self.block_size:
                    raise Exception('Block %s of %s is larger than the block size' % (index, self.config['path']))
                # the last block of the file is stored shorter
                data += b'\x00' * (self.block_size - len(data))
            else:
                raise NotImplementedError('VBK blocks of type 0x%x are not supported' % ref.kind)
            self.__blocks[index] = data
        return data

    async def read(self, size:int = -1):
        if size is None or size < 0:
            size = self.size
        size = min(size, self.size - self.__offset)
        if size <= 0:
            return b''
        res = []
        end = self.__offset + size
        while self.__offset < end:
            start = self.__offset % self.block_size
            data = (await self.read_block(self.__offset // self.block_size))[start:start + end - self.__offset]
            res.append(data)
            self.__offset += len(data)
        return b''.join(res)

    async def seek(self, offset:int, whence:int = 0):
        if whence == 0:
            self.__offset = offset
        elif whence == 1:
            self.__offset += offset
        elif whence == 2:
            self.__offset = self.size + offset
        else:
            raise Exception('Invalid whence value')
        self.__offset = max(0, self.__offset)

    async def tell(self):
        return self.__offset

    async def close(self):
        pass

    def __str__(self):
        return 'VBK file %s' % self.config['path']


class VBMStorage:
    """Backup file listed in the vbm file"""
    def __init__(self):
        self.id = None
        self.link_id = None # the backup file this one is the increment of
        self.filename = None
        self.creation_time = None

    def is_full(self):
        return self.filename.lower().endswith('.vbk')

    def __str__(self):
        return '%s %s %s' % (self.creation_time, 'full       ' if self.is_full() is True else 'incremental', self.filename)


class VBM:
    """The vbm file of a backup: the list of the backup files of the chain, which are stored next to it"""
    def __init__(self):
        self.job_name = None
        self.storages = [] # VBMStorage, the oldest first

    @staticmethod
    async def is_vbm(ds):
        pos = await ds.tell()
        await ds.seek(0, 0)
        data = await ds.read(64)
        await ds.seek(pos, 0)
        if data[:3] == b'\xef\xbb\xbf':
            data = data[3:]
        return data.lstrip().startswith(b'<BackupMeta')

    @staticmethod
    async def from_datasource(ds):
        await ds.seek(0, 0)
        data = b''
        while len(data) <= VBM_MAX_SIZE:
            chunk = await ds.read(1024 * 1024)
            if len(chunk) == 0:
                break
            data += chunk
        return VBM.from_bytes(data)

    @staticmethod
    def from_bytes(data:bytes):
        if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
            raise Exception('Unexpected declarations in the VBM file')
        vbm = VBM()
        root = ET.fromstring(data)
        backup = root.find('Backup')
        if backup is not None:
            vbm.job_name = backup.get('JobName')
        for element in root.iter('Storage'):
            path = element.get('FilePath')
            if path is None or element.get('Id') is None:
                continue
            storage = VBMStorage()
            storage.id = element.get('Id')
            storage.link_id = element.get('LinkId')
            storage.filename = ntpath.basename(path.replace('/', '\\'))
            try:
                storage.creation_time = datetime.datetime.strptime(element.get('CreationTimeUtc', ''), '%m/%d/%Y %H:%M:%S')
            except ValueError:
                storage.creation_time = datetime.datetime.min
            vbm.storages.append(storage)
        vbm.storages.sort(key = lambda storage: storage.creation_time)
        return vbm

    def get_chain(self, storage:VBMStorage):
        """Returns the backup files needed for reading the given one: the full backup first, then the increments up to it"""
        by_id = {other.id : other for other in self.storages}
        chain = [storage]
        while chain[0].is_full() is False:
            previous = by_id.get(chain[0].link_id)
            if previous is None or previous in chain:
                raise Exception('The VBM file does not list the backup file %s is based on' % chain[0].filename)
            chain.insert(0, previous)
        return chain

    def __str__(self):
        t = ['Backup job   : %s' % self.job_name]
        for index, storage in enumerate(self.storages):
            t.append('[%s] %s' % (index, storage))
        return '\n'.join(t)


class VBK:
    """
    A restore point of a Veeam backup: a full backup (vbk) and the incremental backups (vib) made after it.
    The files stored in it are listed by list_files and opened by open, the disks by list_disks and open_disk.
    """
    def __init__(self):
        self.storages = [] # VBKStorage, the oldest first
        self.vbm:VBM = None
        self.__owned = []
        self.__disks = {}

    @staticmethod
    async def from_datasource(ds, point:int = -1, verify:bool = None):
        """
        Opens a backup file.
        vbk: the full backup stored in the file.
        vbm: a restore point of the chain, point is the index of its backup file in the storages of the VBM (the last one by default).
        The backup files it needs are opened from the directory of the vbm file.
        An incremental backup (vib) can be listed alone, reading the disks needs the vbm or from_datasources.
        """
        if await VBM.is_vbm(ds) is False:
            return await VBK.from_datasources([ds], verify)
        vbk = VBK()
        vbk.vbm = await VBM.from_datasource(ds)
        if len(vbk.vbm.storages) == 0:
            raise Exception('The VBM file lists no backup files')
        try:
            for storage in vbk.vbm.get_chain(vbk.vbm.storages[point]):
                sibling = await ds.open_sibling(storage.filename)
                vbk.__owned.append(sibling)
                vbk.storages.append(await VBKStorage.from_datasource(sibling, verify))
        except Exception:
            await vbk.close()
            raise
        return vbk

    @staticmethod
    async def from_datasources(datasources:list, verify:bool = None):
        """Opens a chain of backup files: the full backup and the incremental ones made after it, the oldest first"""
        vbk = VBK()
        for ds in datasources:
            vbk.storages.append(await VBKStorage.from_datasource(ds, verify))
        return vbk

    @staticmethod
    async def is_vbk(ds):
        return await VBKStorage.is_vbk(ds) is True or await VBM.is_vbm(ds) is True

    @property
    def encrypted(self):
        return any(storage.encrypted for storage in self.storages)

    def check_encryption(self):
        if self.encrypted is True:
            raise VBKEncryptedError('The backup is encrypted, reading it is not supported')

    async def get_password_verifier(self):
        """Returns the VBKPasswordVerifier of an encrypted backup, None if the backup is not encrypted"""
        for storage in reversed(self.storages):
            verifier = await storage.get_password_verifier()
            if verifier is not None:
                return verifier
        return None

    def list_files(self):
        """Returns the paths of the files of the restore point"""
        self.check_encryption()
        return [entry.get_path() for entry in self.storages[-1].walk() if entry.is_directory() is False]

    def list_disks(self):
        """Returns the paths of the files which are disks of the backed up machines"""
        return [path for path in self.list_files() if path.lower().endswith(DISK_EXTENSIONS)]

    def get_entry(self, path:str):
        """Returns the entry of the path in the newest backup file, None if it does not exist"""
        self.check_encryption()
        return self.storages[-1].get_entry(path)

    async def open(self, path:str):
        """Opens a file of the restore point"""
        self.check_encryption()
        layers = []
        for storage in reversed(self.storages):
            entry = storage.get_entry(path)
            if entry is None:
                if len(layers) == 0:
                    raise FileNotFoundError(path)
                break
            if entry.is_directory() is True:
                raise IsADirectoryError(path)
            layers.append(entry)
            if entry.is_increment() is False:
                break
        vbkfile = VBKFile(layers[0].get_path(), layers)
        await vbkfile.setup()
        return vbkfile

    async def open_disk(self, path:str):
        """Opens a disk of the restore point, the disks are stored with their raw contents whatever their name is"""
        from adiskreader.disks import detect_disk_type
        from adiskreader.disks.raw import RAWDisk

        entry = self.get_entry(path)
        if entry is None:
            raise FileNotFoundError(path)
        path = entry.get_path()
        if path not in self.__disks:
            vbkfile = await self.open(path)
            disktype = await detect_disk_type(vbkfile)
            if disktype is None:
                disktype = RAWDisk
            self.__disks[path] = await disktype.from_datasource(vbkfile)
        return self.__disks[path]

    async def list_partitions(self):
        """Returns the partitions of all the disks of the restore point"""
        partitions = []
        for path in self.list_disks():
            try:
                disk = await self.open_disk(path)
                partitions += await disk.list_partitions()
            except Exception as e:
                logger.warning('The partitions of %s could not be listed: %s' % (path, e))
        return partitions

    async def close(self):
        """Closes the backup files opened by this object, the datasources it was opened from are to be closed by the caller"""
        for ds in reversed(self.__owned):
            await ds.close()
        self.__owned = []
        self.__disks = {}

    def __str__(self):
        t = []
        t.append('Type         : Veeam backup')
        if self.vbm is not None:
            t.append(str(self.vbm))
        for storage in self.storages:
            t.append(str(storage))
        if self.encrypted is False:
            for path in self.list_files():
                t.append('File         : %s (%s bytes)' % (path, self.get_entry(path).size))
        return '\n'.join(t)
