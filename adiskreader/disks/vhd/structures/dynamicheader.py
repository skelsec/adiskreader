import io
from adiskreader.utils import round_up, ceil_d

class VHDDynamicHeader:
    # if the disk is dynamic, then the footer header's dataoffset points to this header
    def __init__(self):
        self.Cookie = None
        self.DataOffset = None #not user
        self.TableOffset = None
        self.HeaderVersion = None
        self.MaxTableEntries = None
        self.BlockSize = None
        self.Checksum = None
        self.ParentUniqueId = None
        self.ParentTimeStamp = None
        self.Reserved = None
        self.ParentUnicodeName = None
        self.ParentLocatorEntries = []
        self.BlockBitmapSectorCount = None
        self.SectorsPerBlock = None
    
    @staticmethod
    def from_bytes(data:bytes):
        return VHDDynamicHeader.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buffer:io.BytesIO):
        header = VHDDynamicHeader()
        header.Cookie = buffer.read(8)
        if header.Cookie != b'cxsparse':
            raise Exception('Invalid cookie')
        header.DataOffset = int.from_bytes(buffer.read(8), byteorder='big')
        header.TableOffset = int.from_bytes(buffer.read(8), byteorder='big')
        header.HeaderVersion = int.from_bytes(buffer.read(4), byteorder='big')
        header.MaxTableEntries = int.from_bytes(buffer.read(4), byteorder='big')
        header.BlockSize = int.from_bytes(buffer.read(4), byteorder='big')
        header.Checksum = int.from_bytes(buffer.read(4), byteorder='big')
        header.ParentUniqueId = buffer.read(16)
        header.ParentTimeStamp = int.from_bytes(buffer.read(4), byteorder='big')
        header.Reserved = buffer.read(4)
        header.ParentUnicodeName = buffer.read(512)
        for _ in range(8):
            header.ParentLocatorEntries.append(ParentLocatorEntry.from_buffer(buffer))
        header.BlockBitmapSectorCount = round_up(ceil_d(header.BlockSize, 512*8) , 512) // 512
        header.SectorsPerBlock = header.BlockSize // 512
        return header
    
    def __str__(self):
        t = []
        t.append(f"Cookie: {self.Cookie}")
        t.append(f"DataOffset: {self.DataOffset}")
        t.append(f"TableOffset: {self.TableOffset}")
        t.append(f"HeaderVersion: {self.HeaderVersion}")
        t.append(f"MaxTableEntries: {self.MaxTableEntries}")
        t.append(f"BlockSize: {self.BlockSize}")
        t.append(f"Checksum: {self.Checksum}")
        t.append(f"ParentUniqueId: {self.ParentUniqueId}")
        t.append(f"ParentTimeStamp: {self.ParentTimeStamp}")
        t.append(f"Reserved: {self.Reserved}")
        t.append(f"ParentUnicodeName: {self.ParentUnicodeName}")
        t.append(f"ParentLocatorEntries: {self.ParentLocatorEntries}")
        return '\n'.join(t)

class ParentLocatorEntry:
    def __init__(self):
        self.PlatformCode = None
        self.PlatformDataSpace = None
        self.PlatformDataLength = None
        self.Reserved = None
        self.PlatformDataOffset = None
    
    @staticmethod
    def from_bytes(data:bytes):
        return ParentLocatorEntry.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buffer:io.BytesIO):
        entry = ParentLocatorEntry()
        entry.PlatformCode = buffer.read(4)
        entry.PlatformDataSpace = int.from_bytes(buffer.read(4), byteorder='big')
        entry.PlatformDataLength = int.from_bytes(buffer.read(4), byteorder='big')
        entry.Reserved = buffer.read(4)
        entry.PlatformDataOffset = int.from_bytes(buffer.read(8), byteorder='big')
        return entry
    
    def __str__(self):
        t = []
        t.append(f"PlatformCode: {self.PlatformCode}")
        t.append(f"PlatformDataSpace: {self.PlatformDataSpace}")
        t.append(f"PlatformDataLength: {self.PlatformDataLength}")
        t.append(f"Reserved: {self.Reserved}")
        t.append(f"PlatformDataOffset: {self.PlatformDataOffset}")
        return '\n'.join(t)
