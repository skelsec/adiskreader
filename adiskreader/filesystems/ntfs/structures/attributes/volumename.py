import io
import enum
import uuid
from adiskreader.filesystems.ntfs.structures.attributes import Attribute
from winacl.dtyp.security_descriptor import SECURITY_DESCRIPTOR as SD

class VOLUME_NAME(Attribute):
    def __init__(self):
        super().__init__()
        self.name = None

    @staticmethod
    def from_header(header):
        si = VOLUME_NAME.from_bytes(header.data)
        si.header = header
        return si
    
    @staticmethod
    def from_bytes(data):
        return VOLUME_NAME.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = VOLUME_NAME()
        si.name = buff.read().decode('utf-16-le')
        return si
    
    def __str__(self):
        res = []
        res.append('Volume Name')
        res.append('Name: {}'.format(self.name))
        return '\n'.join(res)
