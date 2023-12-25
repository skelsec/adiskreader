import io
import enum
import uuid
from adiskreader.filesystems.ntfs.structures.attributes import Attribute

class REPARSE_POINT(Attribute):
    def __init__(self):
        super().__init__()
        self.reparse_type = None
        self.reparse_data_length = None
        self.unused = None
        self.reparse_data = None
    
    @staticmethod
    def from_header(header):
        si = REPARSE_POINT.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return REPARSE_POINT.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = REPARSE_POINT()
        si.reparse_type = int.from_bytes(buff.read(4), 'little')
        si.reparse_data_length = int.from_bytes(buff.read(2), 'little')
        si.unused = int.from_bytes(buff.read(2), 'little')
        si.reparse_data = buff.read(si.reparse_data_length)
        return si
    
    def __str__(self):
        res = []
        res.append('REPARSE_POINT')
        res.append('Reparse Type: {}'.format(self.reparse_type))
        res.append('Reparse Data Length: {}'.format(self.reparse_data_length))
        res.append('Unused: {}'.format(self.unused))
        res.append('Reparse Data: {}'.format(self.reparse_data))
        return '\n'.join(res)