import io
import enum
import uuid
from adiskreader.filesystems.ntfs.structures.attributes import Attribute

class PROPERTY_SET(Attribute):
    def __init__(self):
        super().__init__()
        self.data = None
    
    @staticmethod
    def from_header(header):
        si = PROPERTY_SET.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return PROPERTY_SET.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = PROPERTY_SET()
        si.data = buff.read()
        return si
    
    def __str__(self):
        res = []
        res.append('PROPERTY_SET')
        res.append('Data: {}'.format(self.data))
        return '\n'.join(res)