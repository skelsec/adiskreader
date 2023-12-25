import io
import enum
import uuid
from adiskreader.filesystems.ntfs.structures.attributes import Attribute

class LOGGED_UTILITY_STREAM(Attribute):
    # same as DATA
    def __init__(self):
        super().__init__()
        self.data = None
    
    @staticmethod
    def from_header(header):
        si = LOGGED_UTILITY_STREAM.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return LOGGED_UTILITY_STREAM.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = LOGGED_UTILITY_STREAM()
        si.data = buff.read()
        return si
    
    def __str__(self):
        res = []
        res.append('LOGGED_UTILITY_STREAM')
        res.append('Data: {}'.format(self.data))
        return '\n'.join(res)