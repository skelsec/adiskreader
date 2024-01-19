import io
import enum
import math
import zlib
from cachetools import LRUCache

class HostedSparseExtentFlags(enum.IntFlag):
    NONE = 0x00000000
    ValidLineDetectionTest = 0x00000001
    RedundantGrainTable = 0x00000002
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

class HostedSparseExtent:
    def __init__(self, stream, descriptor):
        self.__stream = stream
        self.__descriptor = descriptor
        self.header:HostedSparseExtendHeader = None
        self.GD = []
        self.RGD = []
        self.grainTableCache = LRUCache(maxsize=1000)
        self.__current_grain_table_index = None
        self.__current_grain_table = None

    async def setup(self):
        await self.__stream.seek(self.__descriptor.offset * 512)
        headerdata = await self.__stream.read(0x200)
        self.header = HostedSparseExtendHeader.from_bytes(headerdata)

        test = self.header.descriptorOffset + self.header.descriptorSize + self.header.rgdOffset
        print('test: %s' % test)

        if self.header.gdOffset == 0xffffffffffffffff:
            input(self.header)
            #await self.load_global_directory(self.header.rgdOffset, self.GD)

            await self.load_global_directory(128, self.GD)

        else:
            await self.load_global_directory(self.header.gdOffset, self.GD)
            if HostedSparseExtentFlags.RedundantGrainTable in self.header.flags:
                await self.load_global_directory(self.header.rgdOffset, self.RGD)


    async def load_global_directory(self, start_sector, dst):
        numGTs = math.ceil((self.header.capacity * 512) / self.header._gtCoverage)
        print('numGTs: %s' % numGTs)
        print('start_sector: %s' % start_sector)
        await self.__stream.seek(start_sector * 512)
        gtdata = await self.__stream.read(4*numGTs)
        input('GD: %s' % gtdata[:100])
        gtbuff = io.BytesIO(gtdata)
        for _ in range(numGTs):
            dst.append(int.from_bytes(gtbuff.read(4), byteorder='little'))

    async def load_grain_table(self, index):
        if self.__current_grain_table_index == index:
            return
        if index >= len(self.GD):
            raise Exception("Invalid grain table index")
        if index in self.grainTableCache:
            self.__current_grain_table = self.grainTableCache[index]
            self.__current_grain_table_index = index
            return
        
        print(index)
        print(self.GD[:10])
        print(self.GD[index])
        await self.__stream.seek(self.GD[index] * 512)
        gtdata = await self.__stream.read(self.header.numGTEsPerGT * 4)
        print(gtdata[:100])
        graintable = []
        for i in range(0,self.header.numGTEsPerGT, 4):
            graintable.append(int.from_bytes(gtdata[i:i+4], byteorder='little'))
        self.grainTableCache[index] = graintable
        self.__current_grain_table = graintable
        self.__current_grain_table_index = index
        return
    
    async def read_grain(self, index):
        if index >= self.header.capacity:
            raise Exception("Invalid grain index")
        
        print(index)
        print('index // self.header.numGTEsPerGT: %s' % (index // self.header.numGTEsPerGT))
        await self.load_grain_table(index // self.header.numGTEsPerGT)
        print('Current GT: %s' % self.__current_grain_table[:100])
        offset = index % self.header.numGTEsPerGT
        print('offset: %s' % offset)
        grain_offset = self.__current_grain_table[offset]
        input('Grain offset: %s' % grain_offset)
        await self.__stream.seek(grain_offset)
        rawdata = await self.__stream.read(self.header.grainSize * 512)
        if HostedSparseExtentFlags.CompressedGrains in self.header.flags:
            rawdata = zlib.decompress(rawdata, wbits=-15)
        return rawdata
    
    @staticmethod
    async def from_descriptor(stream, descriptor):
        hse = HostedSparseExtent(stream, descriptor)
        await hse.setup()
        return hse