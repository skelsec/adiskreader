import datetime

from adiskreader import logger
from adiskreader.filesystems.iso.common import OpticalEntry, OpticalFileSystem, MAX_DIRECTORY_SIZE

# UDF (ECMA-167 / OSTA UDF 1.02 - 2.60), read only.
# Volume structure: anchor -> volume descriptor sequence -> partitions and the logical volume -> file set -> root.
# The addresses of the files are logical blocks of a partition of the logical volume, which can be
# a part of the disc (type 1), remapped by a table (virtual, sparable) or stored in a file (metadata).

BLOCK_SIZES = (2048, 512, 4096, 1024)
ANCHOR_SECTOR = 256
MAX_VDS_DESCRIPTORS = 256
MAX_AD_EXTENTS = 1024 * 1024
MAX_ICB_HOPS = 256
MAX_TABLE_SIZE = 64 * 1024 * 1024
VAT_SEARCH_BLOCKS = 512

TAG_PVD = 1
TAG_AVDP = 2
TAG_VDP = 3
TAG_PD = 5
TAG_LVD = 6
TAG_TD = 8
TAG_FSD = 256
TAG_FID = 257
TAG_AED = 258
TAG_IE = 259
TAG_TE = 260
TAG_FE = 261
TAG_EFE = 266

FT_UNSPECIFIED = 0
FT_DIRECTORY = 4
FT_FILE = 5
FT_SYMLINK = 12
FT_STREAMDIR = 13
FT_VAT = 248
FT_REALTIME = 249

AD_SHORT = 0
AD_LONG = 1
AD_EXTENDED = 2
AD_EMBEDDED = 3

EXTENT_RECORDED = 0
EXTENT_NOT_RECORDED = 1
EXTENT_NOT_ALLOCATED = 2
EXTENT_NEXT = 3

FID_HIDDEN = 0x01
FID_DIRECTORY = 0x02
FID_DELETED = 0x04
FID_PARENT = 0x08


def decode_cs0(data:bytes):
    """OSTA compressed unicode: the first byte tells if the characters are 8 or 16 bits"""
    if len(data) == 0:
        return ''
    if data[0] == 8:
        return data[1:].decode('latin-1')
    if data[0] == 16:
        return data[1:1 + ((len(data) - 1) & ~1)].decode('utf-16-be', errors='replace')
    return data[1:].decode('latin-1')

def decode_dstring(data:bytes):
    """Fixed size field, the last byte is the length of the used part"""
    if len(data) == 0:
        return ''
    length = min(data[-1], len(data) - 1)
    return decode_cs0(data[:length])

def parse_timestamp(data:bytes):
    """12 bytes: type and timezone, year, month, day, hour, minute, second, centiseconds, 100 microseconds, microseconds"""
    if len(data) < 12:
        return None
    try:
        typetz = int.from_bytes(data[0:2], 'little')
        year = int.from_bytes(data[2:4], 'little', signed=True)
        if year <= 0 or data[4] == 0:
            return None
        micro = data[9] * 10000 + data[10] * 100 + data[11]
        res = datetime.datetime(year, data[4], data[5], data[6], data[7], data[8], min(micro, 999999), tzinfo=datetime.timezone.utc)
        if (typetz >> 12) == 1:
            offset = typetz & 0xFFF
            if offset & 0x800:
                offset -= 0x1000
            if -1440 <= offset <= 1440:
                res -= datetime.timedelta(minutes=offset)
        return res
    except (ValueError, OverflowError):
        return None

def parse_tag(data:bytes, location:int = None):
    """Returns the identifier of the descriptor after checking its tag, None if it is not a valid one"""
    if len(data) < 16:
        return None
    checksum = (sum(data[0:4]) + sum(data[5:16])) & 0xFF
    if checksum != data[4]:
        return None
    ident = int.from_bytes(data[0:2], 'little')
    if ident == 0 and data[0:16] == b'\x00' * 16:
        return None
    if location is not None and int.from_bytes(data[12:16], 'little') != location:
        return None
    return ident


class LongAD:
    def __init__(self, length:int, block:int, partition:int):
        self.type = length >> 30
        self.length = length & 0x3FFFFFFF
        self.block = block
        self.partition = partition

    @staticmethod
    def from_bytes(data:bytes):
        return LongAD(int.from_bytes(data[0:4], 'little'), int.from_bytes(data[4:8], 'little'), int.from_bytes(data[8:10], 'little'))


class UDFPartition:
    """Part of the disc described by a partition descriptor, the logical blocks are continuous"""
    def __init__(self, fs, number:int, start:int, length:int):
        self.fs = fs
        self.number = number
        self.start = start # in blocks
        self.length = length

    async def setup(self):
        pass

    async def map(self, block:int, size:int):
        """Returns where the size bytes starting at the logical block are in the image as a list of (offset, size)"""
        return [((self.start + block) * self.fs.block_size, size)]

    def __str__(self):
        return 'physical partition %s at block %s' % (self.number, self.start)


class UDFSparablePartition(UDFPartition):
    """Rewritable discs: the defective packets are replaced using the sparing table"""
    def __init__(self, fs, physical:UDFPartition, packet_length:int, table_locations:list, table_size:int):
        super().__init__(fs, physical.number, physical.start, physical.length)
        self.packet_length = packet_length
        self.table_locations = table_locations
        self.table_size = table_size
        self.remap = {}

    async def setup(self):
        if self.packet_length == 0:
            raise Exception('Invalid packet length in the UDF sparable partition map')
        for location in self.table_locations:
            try:
                size = min(max(self.table_size, 56), MAX_TABLE_SIZE)
                data = await self.fs.read_at(location * self.fs.block_size, size)
                if parse_tag(data) != 0 or data[17:17+18] != b'*UDF Sparing Table':
                    raise Exception('not a sparing table')
                count = int.from_bytes(data[48:50], 'little')
                for i in range(count):
                    pos = 56 + i * 8
                    if pos + 8 > len(data):
                        break
                    original = int.from_bytes(data[pos:pos+4], 'little')
                    mapped = int.from_bytes(data[pos+4:pos+8], 'little')
                    if original < 0xFFFFFFF0:
                        self.remap[original] = mapped
                return
            except Exception as e:
                logger.debug('UDF: sparing table at %s is not usable: %s' % (location, e))

    async def map(self, block:int, size:int):
        if len(self.remap) == 0:
            return await super().map(block, size)
        res = []
        bs = self.fs.block_size
        while size > 0:
            packet = block - (block % self.packet_length)
            blocks = self.packet_length - (block - packet)
            count = min(size, blocks * bs)
            if packet in self.remap:
                # the table has the address of the replacement packet on the disc
                res.append(((self.remap[packet] + block - packet) * bs, count))
            else:
                res.append(((self.start + block) * bs, count))
            size -= count
            block += blocks
        return res

    def __str__(self):
        return 'sparable partition %s at block %s' % (self.number, self.start)


class UDFVirtualPartition(UDFPartition):
    """Write once discs: the virtual allocation table (the last file written to the disc) has the place of each block"""
    def __init__(self, fs, physical:UDFPartition):
        super().__init__(fs, physical.number, physical.start, physical.length)
        self.physical = physical
        self.table = []

    async def setup(self):
        bs = self.fs.block_size
        total = self.fs.image_size // bs
        if total == 0:
            raise Exception('The size of the image is not known, the virtual allocation table can not be found')
        # the ICB of the table is the last recorded block, images may have some padding after it
        first = max(total - VAT_SEARCH_BLOCKS, self.physical.start)
        tail = await self.fs.read_at(first * bs, (total - first) * bs)
        for index in range(total - first - 1, -1, -1):
            data = tail[index * bs:(index + 1) * bs]
            block = first + index - self.physical.start
            if parse_tag(data, block) not in (TAG_FE, TAG_EFE):
                continue
            try:
                icb = UDFFileEntry.from_bytes(data, self.physical, block)
                if icb.file_type not in (FT_VAT, FT_UNSPECIFIED):
                    continue
                content = await self.fs.read_icb_data(icb, MAX_TABLE_SIZE)
                if icb.file_type == FT_VAT:
                    header_length = int.from_bytes(content[0:2], 'little')
                    if header_length < 152 or header_length > len(content):
                        continue
                    entries = content[header_length:]
                else:
                    # UDF 1.50: the entries are followed by the identifier and the address of the previous table
                    if len(content) < 36 or content[-35:-13] != b'*UDF Virtual Alloc Tbl':
                        continue
                    entries = content[:-36]
                self.table = [int.from_bytes(entries[i:i+4], 'little') for i in range(0, len(entries) & ~3, 4)]
                return
            except Exception as e:
                logger.debug('UDF: virtual allocation table candidate at %s is not usable: %s' % (first + index, e))
        raise Exception('Virtual allocation table of the UDF volume not found')

    async def map(self, block:int, size:int):
        res = []
        bs = self.fs.block_size
        while size > 0:
            if block >= len(self.table) or self.table[block] == 0xFFFFFFFF:
                raise Exception('Block %s is not in the UDF virtual allocation table' % block)
            count = min(size, bs)
            offset = (self.physical.start + self.table[block]) * bs
            if len(res) > 0 and res[-1][0] + res[-1][1] == offset:
                res[-1] = (res[-1][0], res[-1][1] + count)
            else:
                res.append((offset, count))
            size -= count
            block += 1
        return res

    def __str__(self):
        return 'virtual partition on partition %s (%s blocks)' % (self.number, len(self.table))


class UDFMetadataPartition(UDFPartition):
    """UDF 2.50+: the directories and the file entries are in a file of the partition (with a mirror)"""
    def __init__(self, fs, physical:UDFPartition, file_location:int, mirror_location:int):
        super().__init__(fs, physical.number, physical.start, physical.length)
        self.physical = physical
        self.file_location = file_location
        self.mirror_location = mirror_location
        self.extents = [] # (first block of the metadata, offset in the image or None, size)

    async def setup(self):
        errors = []
        for location in (self.file_location, self.mirror_location):
            try:
                icb = await self.fs.read_file_entry(self.physical, location)
                extents, data = await self.fs.read_extents(icb)
                if data is not None:
                    raise Exception('metadata file has no extents')
                pos = 0
                self.extents = []
                for offset, size in extents:
                    self.extents.append((pos, offset, size))
                    pos += size
                return
            except Exception as e:
                errors.append(str(e))
        raise Exception('Metadata file of the UDF volume can not be read: %s' % ', '.join(errors))

    async def map(self, block:int, size:int):
        res = []
        pos = block * self.fs.block_size
        for start, offset, length in self.extents:
            if size <= 0:
                break
            if pos >= start + length or pos < start:
                continue
            count = min(size, start + length - pos)
            if offset is None:
                raise Exception('Block %s of the UDF metadata partition is not recorded' % block)
            res.append((offset + pos - start, count))
            pos += count
            size -= count
        if size > 0:
            raise Exception('Block %s is not in the UDF metadata partition' % block)
        return res

    def __str__(self):
        return 'metadata partition on partition %s' % self.number


class UDFFileEntry:
    """File entry or extended file entry: the attributes and the allocation descriptors of a file"""
    def __init__(self):
        self.partition:UDFPartition = None
        self.block = 0
        self.strategy = 0
        self.file_type = 0
        self.flags = 0
        self.uid = 0
        self.gid = 0
        self.permissions = 0
        self.link_count = 0
        self.information_length = 0
        self.atime = None
        self.mtime = None
        self.ctime = None
        self.unique_id = 0
        self.ad_data = b''
        self.ad_offset = 0

    @property
    def ad_type(self):
        return self.flags & 7

    @staticmethod
    def from_bytes(data:bytes, partition:UDFPartition, block:int):
        ident = int.from_bytes(data[0:2], 'little')
        if ident not in (TAG_FE, TAG_EFE):
            raise Exception('Not an UDF file entry (tag %s)' % ident)
        fe = UDFFileEntry()
        fe.partition = partition
        fe.block = block
        fe.strategy = int.from_bytes(data[20:22], 'little')
        fe.file_type = data[27]
        fe.flags = int.from_bytes(data[34:36], 'little')
        fe.uid = int.from_bytes(data[36:40], 'little')
        fe.gid = int.from_bytes(data[40:44], 'little')
        fe.permissions = int.from_bytes(data[44:48], 'little')
        fe.link_count = int.from_bytes(data[48:50], 'little')
        fe.information_length = int.from_bytes(data[56:64], 'little')
        if ident == TAG_FE:
            fe.atime = parse_timestamp(data[72:84])
            fe.mtime = parse_timestamp(data[84:96])
            fe.ctime = parse_timestamp(data[96:108])
            fe.unique_id = int.from_bytes(data[160:168], 'little')
            lengths = 168
        else:
            fe.atime = parse_timestamp(data[80:92])
            fe.mtime = parse_timestamp(data[92:104])
            fe.ctime = parse_timestamp(data[116:128])
            fe.unique_id = int.from_bytes(data[200:208], 'little')
            lengths = 208
        ea_length = int.from_bytes(data[lengths:lengths+4], 'little')
        ad_length = int.from_bytes(data[lengths+4:lengths+8], 'little')
        fe.ad_offset = lengths + 8 + ea_length
        if fe.ad_offset + ad_length > len(data):
            raise Exception('Invalid UDF file entry, the allocation descriptors do not fit in the block')
        fe.ad_data = data[fe.ad_offset:fe.ad_offset + ad_length]
        return fe

    def get_mode(self):
        """The permissions as unix mode bits"""
        mode = 0
        for shift, base in ((0, 0), (3, 5), (6, 10)):
            bits = (self.permissions >> base) & 7 # execute, write, read
            mode |= bits << shift
        if self.flags & 0x40:
            mode |= 0o4000
        if self.flags & 0x80:
            mode |= 0o2000
        if self.flags & 0x100:
            mode |= 0o1000
        return mode


class UDFEntry(OpticalEntry):
    def __init__(self, fs, parent, name:str, icb:UDFFileEntry):
        super().__init__(fs, parent, name)
        self.icb = icb
        self.size = icb.information_length
        self.mode = icb.get_mode()
        self.uid = icb.uid if icb.uid != 0xFFFFFFFF else None
        self.gid = icb.gid if icb.gid != 0xFFFFFFFF else None
        self.inode = icb.unique_id
        self.atime = icb.atime
        self.mtime = icb.mtime
        self.ctime = icb.ctime

    def is_directory(self):
        return self.icb.file_type == FT_DIRECTORY

    def same_directory(self, other):
        return self.icb.partition is other.icb.partition and self.icb.block == other.icb.block

    async def get_extents(self):
        return await self.fs.read_extents(self.icb)

    async def read_children(self):
        return await self.fs.read_directory(self)


class UDF(OpticalFileSystem):
    def __init__(self, ds):
        super().__init__(ds)
        self.block_size = 0
        self.image_size = 0
        self.partitions = [] # of the logical volume, in the order of the partition maps
        self.physical = {} # partition number -> UDFPartition
        self.logical_volume_name = ''
        self.fileset_location:LongAD = None
        self.revision = 0

    @staticmethod
    async def from_datasource(ds):
        fs = UDF(ds)
        await fs.setup()
        return fs

    async def get_image_size(self):
        try:
            pos = await self.ds.tell()
            size = await self.ds.seek(0, 2)
            if size is None:
                size = await self.ds.tell()
            await self.ds.seek(pos, 0)
            return size
        except Exception as e:
            logger.debug('UDF: the size of the image is not known: %s' % e)
            return 0

    async def find_anchor(self):
        """Returns the main and the reserve volume descriptor sequence as (block, length in bytes), sets the block size"""
        for tail in (False, True):
            for bs in BLOCK_SIZES:
                locations = [ANCHOR_SECTOR]
                if tail is True:
                    total = self.image_size // bs
                    locations = [total - 257, total - 1]
                for location in locations:
                    if location < 0:
                        continue
                    try:
                        data = await self.read_at(location * bs, 512)
                    except Exception:
                        continue
                    if parse_tag(data, location) != TAG_AVDP:
                        continue
                    self.block_size = bs
                    main = (int.from_bytes(data[20:24], 'little'), int.from_bytes(data[16:20], 'little'))
                    reserve = (int.from_bytes(data[28:32], 'little'), int.from_bytes(data[24:28], 'little'))
                    return main, reserve
        raise Exception('UDF anchor volume descriptor not found')

    async def read_vds(self, location:int, length:int):
        """Reads the volume descriptor sequence, the descriptor with the highest sequence number is the valid one"""
        bs = self.block_size
        descriptors = {}
        hops = 0
        index = 0
        count = min(length // bs, MAX_VDS_DESCRIPTORS)
        while index < count:
            data = await self.read_at((location + index) * bs, bs)
            index += 1
            ident = parse_tag(data, location + index - 1)
            if ident is None or ident == TAG_TD:
                break
            seq = int.from_bytes(data[16:20], 'little')
            if ident == TAG_VDP:
                # the sequence continues in another extent
                next_length = int.from_bytes(data[20:24], 'little')
                next_location = int.from_bytes(data[24:28], 'little')
                hops += 1
                if next_length == 0 or hops > MAX_ICB_HOPS:
                    break
                location, index, count = next_location, 0, min(next_length // bs, MAX_VDS_DESCRIPTORS)
                continue
            if ident == TAG_PD:
                key = (ident, int.from_bytes(data[22:24], 'little'))
            elif ident == TAG_LVD:
                key = (ident, data[84:212])
            else:
                key = (ident, None)
            if key not in descriptors or descriptors[key][0] <= seq:
                descriptors[key] = (seq, data)
        return [(key[0], data) for key, (seq, data) in descriptors.items()]

    async def setup(self):
        self.image_size = await self.get_image_size()
        main, reserve = await self.find_anchor()
        errors = []
        for location, length in (main, reserve):
            try:
                descriptors = await self.read_vds(location, length)
                await self.setup_volume(descriptors)
                break
            except Exception as e:
                errors.append(str(e))
                self.partitions = []
                self.physical = {}
        else:
            raise Exception('UDF volume descriptors can not be used: %s' % ', '.join(errors))
        await self.setup_fileset()

    async def setup_volume(self, descriptors:list):
        lvd = None
        for ident, data in descriptors:
            if ident == TAG_PD:
                number = int.from_bytes(data[22:24], 'little')
                start = int.from_bytes(data[188:192], 'little')
                length = int.from_bytes(data[192:196], 'little')
                self.physical[number] = UDFPartition(self, number, start, length)
            elif ident == TAG_LVD:
                lvd = data
            elif ident == TAG_PVD:
                self.volume_name = decode_dstring(data[24:56])
        if lvd is None:
            raise Exception('No logical volume descriptor')
        if len(self.physical) == 0:
            raise Exception('No partition descriptor')

        self.logical_volume_name = decode_dstring(lvd[84:212])
        if self.logical_volume_name != '':
            self.volume_name = self.logical_volume_name
        block_size = int.from_bytes(lvd[212:216], 'little')
        if block_size != self.block_size:
            raise Exception('Logical block size (%s) is not the same as the sector size (%s)' % (block_size, self.block_size))
        self.revision = int.from_bytes(lvd[240:242], 'little')
        self.fileset_location = LongAD.from_bytes(lvd[248:264])
        table_length = int.from_bytes(lvd[264:268], 'little')
        map_count = int.from_bytes(lvd[268:272], 'little')
        table = lvd[440:440+table_length]

        pos = 0
        for _ in range(map_count):
            if pos + 2 > len(table):
                raise Exception('Invalid partition map table')
            map_type = table[pos]
            map_length = table[pos+1]
            pmap = table[pos:pos+map_length]
            if map_length < 6 or len(pmap) != map_length:
                raise Exception('Invalid partition map')
            pos += map_length
            if map_type == 1:
                self.partitions.append(self.get_physical(int.from_bytes(pmap[4:6], 'little')))
                continue
            if map_type != 2 or map_length < 64:
                raise NotImplementedError('Unknown UDF partition map type: %s' % map_type)
            ident = pmap[5:28].rstrip(b'\x00')
            physical = self.get_physical(int.from_bytes(pmap[38:40], 'little'))
            if ident == b'*UDF Virtual Partition':
                partition = UDFVirtualPartition(self, physical)
            elif ident == b'*UDF Sparable Partition':
                count = min(pmap[42], 4)
                locations = [int.from_bytes(pmap[48+i*4:52+i*4], 'little') for i in range(count)]
                partition = UDFSparablePartition(self, physical, int.from_bytes(pmap[40:42], 'little'), locations, int.from_bytes(pmap[44:48], 'little'))
            elif ident == b'*UDF Metadata Partition':
                partition = UDFMetadataPartition(self, physical, int.from_bytes(pmap[40:44], 'little'), int.from_bytes(pmap[44:48], 'little'))
            else:
                raise NotImplementedError('Unknown UDF partition type: %s' % ident)
            self.partitions.append(partition)

        for partition in self.partitions:
            await partition.setup()

    def get_physical(self, number:int):
        if number not in self.physical:
            raise Exception('UDF partition %s has no descriptor' % number)
        return self.physical[number]

    def get_partition(self, reference:int):
        if reference >= len(self.partitions):
            raise Exception('Invalid UDF partition reference: %s' % reference)
        return self.partitions[reference]

    async def read_blocks(self, partition:UDFPartition, block:int, size:int):
        res = []
        for offset, length in await partition.map(block, size):
            res.append(await self.read_at(offset, length))
        return b''.join(res)

    async def setup_fileset(self):
        location = self.fileset_location
        root = None
        for _ in range(MAX_ICB_HOPS):
            partition = self.get_partition(location.partition)
            data = await self.read_blocks(partition, location.block, self.block_size)
            if parse_tag(data, location.block) != TAG_FSD:
                break
            root = LongAD.from_bytes(data[400:416])
            # a newer file set descriptor may follow
            location = LongAD.from_bytes(data[448:464])
            if location.length == 0:
                break
        if root is None:
            raise Exception('UDF file set descriptor not found')
        icb = await self.read_file_entry(self.get_partition(root.partition), root.block)
        if icb.file_type != FT_DIRECTORY:
            raise Exception('The root of the UDF volume is not a directory')
        self.root = UDFEntry(self, None, '', icb)

    async def read_file_entry(self, partition:UDFPartition, block:int):
        last = None
        for _ in range(MAX_ICB_HOPS):
            try:
                data = await self.read_blocks(partition, block, self.block_size)
                # some writers use the address on the disc in the tags of the virtual partition
                ident = parse_tag(data, None if isinstance(partition, UDFVirtualPartition) else block)
            except Exception:
                if last is None:
                    raise
                return last
            if ident == TAG_IE:
                # indirect entry: the file entry is somewhere else
                target = LongAD.from_bytes(data[36:52])
                partition = self.get_partition(target.partition)
                block = target.block
                continue
            if ident not in (TAG_FE, TAG_EFE):
                if last is not None:
                    return last
                raise Exception('No UDF file entry at block %s (tag %s)' % (block, ident))
            icb = UDFFileEntry.from_bytes(data, partition, block)
            if icb.strategy == 4096:
                # write once discs without virtual partition: the next block has the address of the newer
                # version of the entry, which is not recorded yet for the latest one
                last = icb
                block += 1
                continue
            if icb.strategy != 4:
                raise NotImplementedError('UDF ICB strategy %s is not supported' % icb.strategy)
            return icb
        if last is not None:
            return last
        raise Exception('Too many indirect UDF file entries')

    async def read_extents(self, icb:UDFFileEntry):
        """Returns the extents of the data of the file as (offset in the image or None, size) and the data if it is in the entry"""
        if icb.ad_type == AD_EMBEDDED:
            return [], icb.ad_data[:icb.information_length]
        if icb.ad_type not in (AD_SHORT, AD_LONG, AD_EXTENDED):
            raise Exception('Invalid UDF allocation descriptor type: %s' % icb.ad_type)
        ad_size = {AD_SHORT: 8, AD_LONG: 16, AD_EXTENDED: 20}[icb.ad_type]

        res = []
        data = icb.ad_data
        remaining = icb.information_length
        count = 0
        pos = 0
        while pos + ad_size <= len(data):
            raw = int.from_bytes(data[pos:pos+4], 'little')
            extent_type = raw >> 30
            length = raw & 0x3FFFFFFF
            if icb.ad_type == AD_SHORT:
                block = int.from_bytes(data[pos+4:pos+8], 'little')
                partition = icb.partition
            elif icb.ad_type == AD_LONG:
                block = int.from_bytes(data[pos+4:pos+8], 'little')
                partition = self.get_partition(int.from_bytes(data[pos+8:pos+10], 'little'))
            else:
                block = int.from_bytes(data[pos+12:pos+16], 'little')
                partition = self.get_partition(int.from_bytes(data[pos+16:pos+18], 'little'))
            pos += ad_size
            if length == 0:
                break
            count += 1
            if count > MAX_AD_EXTENTS:
                raise Exception('Too many extents in the UDF file entry')

            if extent_type == EXTENT_NEXT:
                # the descriptors continue in an allocation extent descriptor
                aed = await self.read_blocks(partition, block, length)
                if parse_tag(aed, block) != TAG_AED:
                    raise Exception('Invalid UDF allocation extent descriptor at block %s' % block)
                ad_length = int.from_bytes(aed[20:24], 'little')
                data = aed[24:24+ad_length]
                pos = 0
                continue
            if remaining <= 0:
                # allocated after the end of the file
                continue
            size = min(length, remaining)
            if extent_type == EXTENT_RECORDED:
                res += await partition.map(block, size)
            else:
                res.append((None, size))
            remaining -= size
        return res, None

    async def read_icb_data(self, icb:UDFFileEntry, max_size:int):
        if icb.information_length > max_size:
            raise Exception('UDF file is too large to be read at once: %s' % icb.information_length)
        extents, data = await self.read_extents(icb)
        if data is not None:
            return data
        res = []
        remaining = icb.information_length
        for offset, size in extents:
            size = min(size, remaining)
            res.append(b'\x00' * size if offset is None else await self.read_at(offset, size))
            remaining -= size
        res.append(b'\x00' * remaining)
        return b''.join(res)

    async def read_directory(self, directory:UDFEntry):
        data = await self.read_icb_data(directory.icb, MAX_DIRECTORY_SIZE)
        res = []
        pos = 0
        while pos + 38 <= len(data):
            if parse_tag(data[pos:pos+16]) != TAG_FID:
                # the rest of the block may be padding
                next_block = (pos // self.block_size + 1) * self.block_size
                if data[pos:next_block].strip(b'\x00') != b'':
                    logger.debug('UDF: invalid file identifier at %s of %s' % (pos, await directory.resolve_full_path()))
                    break
                pos = next_block
                continue
            characteristics = data[pos+18]
            name_length = data[pos+19]
            icb = LongAD.from_bytes(data[pos+20:pos+36])
            iu_length = int.from_bytes(data[pos+36:pos+38], 'little')
            name = data[pos+38+iu_length:pos+38+iu_length+name_length]
            pos += (38 + iu_length + name_length + 3) & ~3
            if characteristics & (FID_DELETED | FID_PARENT):
                continue
            try:
                fe = await self.read_file_entry(self.get_partition(icb.partition), icb.block)
            except NotImplementedError:
                raise
            except Exception as e:
                logger.debug('UDF: file entry of %s can not be read: %s' % (decode_cs0(name), e))
                continue
            entry = UDFEntry(self, directory, decode_cs0(name), fe)
            entry.hidden = bool(characteristics & FID_HIDDEN)
            if fe.file_type == FT_SYMLINK:
                try:
                    entry.symlink_target = self.parse_symlink(await self.read_icb_data(fe, 1024 * 1024))
                except Exception as e:
                    logger.debug('UDF: symlink %s can not be read: %s' % (entry.name, e))
            res.append(entry)
        return res

    @staticmethod
    def parse_symlink(data:bytes):
        """The target of a symlink is stored as a list of path components"""
        parts = []
        absolute = False
        pos = 0
        while pos + 4 <= len(data):
            ctype = data[pos]
            length = data[pos+1]
            ident = data[pos+4:pos+4+length]
            pos += 4 + length
            if ctype in (1, 2):
                absolute = True
                parts = []
            elif ctype == 3:
                parts.append('..')
            elif ctype == 4:
                parts.append('.')
            elif ctype == 5:
                parts.append(decode_cs0(ident))
        return ('/' if absolute is True else '') + '/'.join(parts)

    def __str__(self):
        return 'UDF %x.%02x volume %s' % (self.revision >> 8, self.revision & 0xFF, self.volume_name)
