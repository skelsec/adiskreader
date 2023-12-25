import io
import enum
import uuid
from adiskreader.filesystems.ntfs.structures.attributes import Attribute

class DATA(Attribute):
    def __init__(self):
        super().__init__()
        self.data = None

    @staticmethod
    def from_header(header):
        si = DATA.from_bytes(header.data)
        si.header = header
        return si
    
    @staticmethod
    def from_bytes(data):
        return DATA.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = DATA()
        si.data = buff.read()
        return si
    
    def __str__(self):
        res = []
        res.append('Data')
        res.append('Data: {}'.format(self.data))
        return '\n'.join(res)