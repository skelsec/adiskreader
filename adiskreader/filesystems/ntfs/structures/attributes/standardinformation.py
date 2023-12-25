import io
from adiskreader.filesystems.ntfs.structures.attributes import Attribute

class STANDARD_INFORMATION(Attribute):
    def __init__(self):
        super().__init__()
        self.time_created = None
        self.time_modified = None
        self.time_mft_modified = None
        self.time_accessed = None
        self.flags = None
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
        si.time_created = int.from_bytes(buff.read(8), 'little')
        si.time_modified = int.from_bytes(buff.read(8), 'little')
        si.time_mft_modified = int.from_bytes(buff.read(8), 'little')
        si.time_accessed = int.from_bytes(buff.read(8), 'little')
        si.flags = int.from_bytes(buff.read(4), 'little')
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

