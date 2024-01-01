import io
import enum
import datetime
from adiskreader.filesystems.ntfs.structures.attributes import Attribute
from adiskreader.utils import filetime_to_dt


class SIFlag(enum.IntFlag):
    READ_ONLY = 0x00000001
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
    INTEGRITY_STREAM = 0x00008000
    VIRTUAL = 0x00010000
    NO_SCRUB_DATA = 0x00020000
    RECALL_ON_OPEN = 0x00040000
    RECALL_ON_DATA_ACCESS = 0x00400000
    TXF = 0x80000000

class STANDARD_INFORMATION(Attribute):
    def __init__(self):
        super().__init__()
        self.time_created:datetime.datetime = None
        self.time_modified:datetime.datetime = None
        self.time_mft_modified:datetime.datetime = None
        self.time_accessed:datetime.datetime = None
        self.flags:SIFlag = None
        self.maximum_versions = None
        self.version = None
        self.classid = None
        self.owner_id = None
        self.security_id = None
        self.quota_charged = None
        self.usn = None

    @staticmethod
    def from_header(header):
        si = STANDARD_INFORMATION.from_bytes(header.data)
        si.header = header
        return si
    
    @staticmethod
    def from_bytes(data):
        return STANDARD_INFORMATION.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = STANDARD_INFORMATION()
        si.time_created  = filetime_to_dt(int.from_bytes(buff.read(8), 'little'))
        si.time_modified = filetime_to_dt(int.from_bytes(buff.read(8), 'little'))
        si.time_mft_modified = filetime_to_dt(int.from_bytes(buff.read(8), 'little'))
        si.time_accessed = filetime_to_dt(int.from_bytes(buff.read(8), 'little'))
        si.flags = SIFlag(int.from_bytes(buff.read(4), 'little'))
        si.maximum_versions = int.from_bytes(buff.read(4), 'little')
        si.version = int.from_bytes(buff.read(4), 'little')
        si.classid = int.from_bytes(buff.read(4), 'little')
        si.owner_id = int.from_bytes(buff.read(4), 'little')
        si.security_id = int.from_bytes(buff.read(4), 'little')
        si.quota_charged = int.from_bytes(buff.read(8), 'little')
        si.usn = int.from_bytes(buff.read(8), 'little')
        return si
    
    def __str__(self):
        res = []
        res.append('Standard Information')
        res.append('Time Created: {}'.format(self.time_created))
        res.append('Time Modified: {}'.format(self.time_modified))
        res.append('Time MFT Modified: {}'.format(self.time_mft_modified))
        res.append('Time Accessed: {}'.format(self.time_accessed))
        res.append('Flags: {}'.format(str(self.flags)))
        res.append('Maximum Versions: {}'.format(self.maximum_versions))
        res.append('Version: {}'.format(self.version))
        res.append('Class ID: {}'.format(self.classid))
        res.append('Owner ID: {}'.format(self.owner_id))
        res.append('Security ID: {}'.format(self.security_id))
        res.append('Quota Charged: {}'.format(self.quota_charged))
        res.append('USN: {}'.format(self.usn))
        return '\n'.join(res)

