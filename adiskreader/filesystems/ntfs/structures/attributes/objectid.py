import io
import enum
import uuid
from adiskreader.filesystems.ntfs.structures.attributes import Attribute

class OBJECT_ID(Attribute):
    def __init__(self):
        super().__init__()
        self.object_id = None
        self.birth_volume_id = None
        self.birth_object_id = None
        self.domain_id = None
    
    @staticmethod
    def from_header(header):
        si = OBJECT_ID.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        data = data + b'\x00' * (0x40 - len(data)) #yes, this is actually per documentation
        return OBJECT_ID.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = OBJECT_ID()
        si.object_id = uuid.UUID(bytes_le = buff.read(16))
        si.birth_volume_id = uuid.UUID(bytes_le = buff.read(16))
        si.birth_object_id = uuid.UUID(bytes_le = buff.read(16))
        si.domain_id = uuid.UUID(bytes_le = buff.read(16))
        return si
    
    def __str__(self):
        res = []
        res.append('OBJECT_ID')
        res.append('Object ID: {}'.format(self.object_id))
        res.append('Birth Volume ID: {}'.format(self.birth_volume_id))
        res.append('Birth Object ID: {}'.format(self.birth_object_id))
        res.append('Domain ID: {}'.format(self.domain_id))
        return '\n'.join(res)