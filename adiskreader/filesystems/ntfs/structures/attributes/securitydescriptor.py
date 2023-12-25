import io
import enum
import uuid
from adiskreader.filesystems.ntfs.structures.attributes import Attribute
from winacl.dtyp.security_descriptor import SECURITY_DESCRIPTOR as SD

class SECURITY_DESCRIPTOR(Attribute):
    def __init__(self):
        super().__init__()
        self.sid = None
    
    @staticmethod
    def from_header(header):
        si = SECURITY_DESCRIPTOR.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return SECURITY_DESCRIPTOR.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = SECURITY_DESCRIPTOR()
        si.sid = SD.from_buffer(buff)
        return si
    
    def __str__(self):
        res = []
        res.append('SECURITY_DESCRIPTOR')
        res.append('SID: {}'.format(self.sid))
        return '\n'.join(res)
