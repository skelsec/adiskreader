import io
import enum

class VHDFeatures(enum.IntFlag):
    NONE = 0x00000000
    Temporary = 0x00000001
    Reserved1 = 0x00000002

class VHDType(enum.Enum):
    NONE = 0
    RESERVED = 1
    FIXED = 2
    DYNAMIC = 3
    DIFFERENCING = 4
    RESERVED2 = 5
    RESERVED3 = 6

class HardDiskFooter:
    def __init__(self):
        self.Cookie = None
        self.Features = None
        self.FileFormatVersion = None
        self.DataOffset = None
        self.TimeStamp = None
        self.CreatorApplication = None
        self.CreatorVersion = None
        self.CreatorHostOS = None
        self.OriginalSize = None
        self.CurrentSize = None
        self.DiskGeometry = None
        self.DiskType = None
        self.Checksum = None
        self.UniqueId = None
        self.SavedState = None
        self.Padding = None
    
    @staticmethod
    def from_bytes(data:bytes):
        return HardDiskFooter.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buffer:io.BytesIO):
        footer = HardDiskFooter()
        footer.Cookie = buffer.read(8)
        if footer.Cookie != b'conectix':
            raise Exception('Invalid cookie')
        footer.Features = VHDFeatures(int.from_bytes(buffer.read(4), byteorder='big'))
        footer.FileFormatVersion = int.from_bytes(buffer.read(4), byteorder='big')
        footer.DataOffset = int.from_bytes(buffer.read(8), byteorder='big')
        footer.TimeStamp = int.from_bytes(buffer.read(4), byteorder='big')
        footer.CreatorApplication = buffer.read(4)
        footer.CreatorVersion = int.from_bytes(buffer.read(4), byteorder='big')
        footer.CreatorHostOS = int.from_bytes(buffer.read(4), byteorder='big')
        footer.OriginalSize = int.from_bytes(buffer.read(8), byteorder='big')
        footer.CurrentSize = int.from_bytes(buffer.read(8), byteorder='big')
        footer.DiskGeometry = int.from_bytes(buffer.read(4), byteorder='big')
        footer.DiskType = VHDType(int.from_bytes(buffer.read(4), byteorder='big'))
        footer.Checksum = int.from_bytes(buffer.read(4), byteorder='big')
        footer.UniqueId = buffer.read(16)
        footer.SavedState = int.from_bytes(buffer.read(1), byteorder='big')
        footer.Padding = buffer.read(427)
        return footer
    
    def __str__(self):
        t = []
        t.append(f"Cookie: {self.Cookie}")
        t.append(f"Features: {self.Features}")
        t.append(f"FileFormatVersion: {self.FileFormatVersion}")
        t.append(f"DataOffset: {hex(self.DataOffset)}")
        t.append(f"TimeStamp: {self.TimeStamp}")
        t.append(f"CreatorApplication: {self.CreatorApplication}")
        t.append(f"CreatorVersion: {self.CreatorVersion}")
        t.append(f"CreatorHostOS: {self.CreatorHostOS}")
        t.append(f"OriginalSize: {self.OriginalSize}")
        t.append(f"CurrentSize: {self.CurrentSize}")
        t.append(f"DiskGeometry: {self.DiskGeometry}")
        t.append(f"DiskType: {self.DiskType}")
        t.append(f"Checksum: {self.Checksum}")
        t.append(f"UniqueId: {self.UniqueId}")
        t.append(f"SavedState: {self.SavedState}")
        return '\n'.join(t)
    
