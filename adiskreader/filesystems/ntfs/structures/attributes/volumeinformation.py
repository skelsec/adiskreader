import io
import enum
from adiskreader.filesystems.ntfs.structures.attributes import Attribute

class VOLUME_INFORMATION(Attribute):
    def __init__(self):
        super().__init__()
        self.unknown = None
        self.major_version = None
        self.minor_version = None
        self.flags = None
        self.unknown2 = None
    
    @staticmethod
    def from_header(header):
        si = VOLUME_INFORMATION.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return VOLUME_INFORMATION.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = VOLUME_INFORMATION()
        si.unknown = int.from_bytes(buff.read(8), 'little')
        si.major_version = int.from_bytes(buff.read(1), 'little')
        si.minor_version = int.from_bytes(buff.read(1), 'little')
        si.flags = VOLUME_INFORMATION_FLAG(int.from_bytes(buff.read(2), 'little'))
        si.unknown2 = int.from_bytes(buff.read(4), 'little')
        return si
    
    def __str__(self):
        res = []
        res.append('Volume Information')
        res.append('Unknown: {}'.format(self.unknown))
        res.append('Major Version: {}'.format(self.major_version))
        res.append('Minor Version: {}'.format(self.minor_version))
        res.append('Flags: {}'.format(self.flags))
        res.append('Unknown2: {}'.format(self.unknown2))
        return '\n'.join(res)

class VOLUME_INFORMATION_FLAG(enum.IntFlag):
    Dirty = 0x0001
    ResizeLogFile = 0x0002
    UpgradeOnMount = 0x0004
    MountedOnNT4 = 0x0008
    DeleteUSNUnderway = 0x0010
    RepairObjectId = 0x0020
    ModifiedByChkdsk = 0x8000

