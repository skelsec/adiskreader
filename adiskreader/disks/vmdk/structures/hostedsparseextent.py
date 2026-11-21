import io
import enum
import math
from adiskreader.disks.vmdk.structures.extent import SparseExtent, SECTOR_SIZE, GRAIN_UNALLOCATED, GRAIN_ZERO, GRAIN_DATA

HOSTED_SPARSE_MAGIC = b'KDMV'
# the grain directory offset is stored in the footer
GD_AT_END = 0xffffffffffffffff

class HostedSparseExtentFlags(enum.IntFlag):
    NONE = 0x00000000
    ValidLineDetectionTest = 0x00000001
    RedundantGrainTable = 0x00000002
    ZeroedGrainGTE = 0x00000004
    CompressedGrains = 0x00010000
    MarkersInUse = 0x00020000

class HostedSparseCompressAlgorithm(enum.Enum):
    UNKNOWN = -1
    NONE = 0
    DEFLATE = 1
    LZ4 = 2
    LZ4HC = 3
    ZLIB = 4
    ZSTD = 5

class HostedSparseExtendHeader:
    def __init__(self):
        self.magicNumber = None
        self.version = None
        self.flags = None
        self.capacity = None
        self.grainSize = None
        self.descriptorOffset = None
        self.descriptorSize = None
        self.numGTEsPerGT = None
        self.rgdOffset = None
        self.gdOffset = None
        self.overHead = None
        self.uncleanShutdown = None
        self.signleEndLineChar = None
        self.nonEndLineChar = None
        self.doubleEndLineChar1 = None
        self.doubleEndLineChar2 = None
        self.compressAlgorithm = None
        self._compressAlgorithmInt = None
        self.pad = None
        self._gtCoverage = None
    
    @staticmethod
    def from_bytes(data:bytes):

        return HostedSparseExtendHeader.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buffer:io.BytesIO):
        header = HostedSparseExtendHeader()
        header.magicNumber = int.from_bytes(buffer.read(4), byteorder='little')
        if header.magicNumber != 0x564d444b:
            raise Exception("Invalid magic number")
        header.version = int.from_bytes(buffer.read(4), byteorder='little')
        header.flags = HostedSparseExtentFlags(int.from_bytes(buffer.read(4), byteorder='little'))
        header.capacity = int.from_bytes(buffer.read(8), byteorder='little')
        header.grainSize = int.from_bytes(buffer.read(8), byteorder='little')
        header.descriptorOffset = int.from_bytes(buffer.read(8), byteorder='little')
        header.descriptorSize = int.from_bytes(buffer.read(8), byteorder='little')
        header.numGTEsPerGT = int.from_bytes(buffer.read(4), byteorder='little')
        header.rgdOffset = int.from_bytes(buffer.read(8), byteorder='little')
        header.gdOffset = int.from_bytes(buffer.read(8), byteorder='little')
        header.overHead = int.from_bytes(buffer.read(8), byteorder='little')
        header.uncleanShutdown = int.from_bytes(buffer.read(1), byteorder='little')
        header.signleEndLineChar = int.from_bytes(buffer.read(1), byteorder='little')
        header.nonEndLineChar = int.from_bytes(buffer.read(1), byteorder='little')
        header.doubleEndLineChar1 = int.from_bytes(buffer.read(1), byteorder='little')
        header.doubleEndLineChar2 = int.from_bytes(buffer.read(1), byteorder='little')
        header._compressAlgorithmInt = int.from_bytes(buffer.read(2), byteorder='little')
        try:
            header.compressAlgorithm = HostedSparseCompressAlgorithm(header._compressAlgorithmInt)
        except:
            header.compressAlgorithm = HostedSparseCompressAlgorithm.UNKNOWN
        header.pad = buffer.read(433)
        header._gtCoverage = header.numGTEsPerGT * header.grainSize * 512
        return header

    def __str__(self):
        t = []
        t.append(f"magicNumber: {self.magicNumber}")
        t.append(f"version: {self.version}")
        t.append(f"flags: {self.flags}")
        t.append(f"capacity: {self.capacity}")
        t.append(f"grainSize: {self.grainSize}")
        t.append(f"descriptorOffset: {self.descriptorOffset}")
        t.append(f"descriptorSize: {self.descriptorSize}")
        t.append(f"numGTEsPerGT: {self.numGTEsPerGT}")
        t.append(f"rgdOffset: {self.rgdOffset}")
        t.append(f"gdOffset: {self.gdOffset}")
        t.append(f"overHead: {self.overHead}")
        t.append(f"uncleanShutdown: {self.uncleanShutdown}")
        t.append(f"signleEndLineChar: {self.signleEndLineChar}")
        t.append(f"nonEndLineChar: {self.nonEndLineChar}")
        t.append(f"doubleEndLineChar1: {self.doubleEndLineChar1}")
        t.append(f"doubleEndLineChar2: {self.doubleEndLineChar2}")
        t.append(f"compressAlgorithm: {self.compressAlgorithm}")
        t.append(f"_gtCoverage: {self._gtCoverage}")
        return '\n'.join(t)

class HostedSparseExtent(SparseExtent):
    """SPARSE extent, used by the hosted products (Workstation, Fusion) and by the stream optimized disks of OVA files"""
    def __init__(self, stream, descriptor):
        super().__init__(stream, descriptor)
        self.header:HostedSparseExtendHeader = None

    async def setup(self):
        await self.stream.seek(0, 0)
        self.header = HostedSparseExtendHeader.from_bytes(await self.stream.read(SECTOR_SIZE))
        if self.header.gdOffset == GD_AT_END:
            # stream optimized: the header with the grain directory offset is in the footer,
            # which is followed by the end-of-stream marker
            await self.stream.seek(-2 * SECTOR_SIZE, 2)
            self.header = HostedSparseExtendHeader.from_bytes(await self.stream.read(SECTOR_SIZE))
        if self.header.grainSize == 0 or self.header.numGTEsPerGT == 0:
            raise Exception("Invalid sparse extent header")

        self.capacity = self.header.capacity
        self.grain_sectors = self.header.grainSize
        self.gt_entries = self.header.numGTEsPerGT
        self.compressed = HostedSparseExtentFlags.CompressedGrains in self.header.flags
        self.init_caches()
        numGTs = math.ceil(self.header.capacity / (self.header.numGTEsPerGT * self.header.grainSize))
        await self.read_directory(self.header.gdOffset * SECTOR_SIZE, numGTs)

    async def read_descriptor(self):
        """Returns the raw descriptor file embedded in the extent, None if there is none"""
        if self.header.descriptorOffset == 0 or self.header.descriptorSize == 0:
            return None
        await self.stream.seek(self.header.descriptorOffset * SECTOR_SIZE, 0)
        data = await self.stream.read(self.header.descriptorSize * SECTOR_SIZE)
        if data.strip(b'\x00') == b'':
            # extents of split disks have the space for it, but it is empty
            return None
        return data

    def get_table_offset(self, gde:int):
        if gde == 0:
            return None
        return gde * SECTOR_SIZE

    def get_grain(self, gte:int):
        if gte == 0:
            return GRAIN_UNALLOCATED, None
        if gte == 1 and HostedSparseExtentFlags.ZeroedGrainGTE in self.header.flags:
            return GRAIN_ZERO, None
        return GRAIN_DATA, gte * SECTOR_SIZE
    
    @staticmethod
    async def from_descriptor(stream, descriptor):
        hse = HostedSparseExtent(stream, descriptor)
        await hse.setup()
        return hse
