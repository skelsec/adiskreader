import io
from adiskreader.filesystems.ntfs.structures.attributes import Attribute


class BITMAP(Attribute):
    # not much info on this one
    def __init__(self):
        super().__init__()
        self.bitfield = None


    @staticmethod
    def from_header(header):
        si = BITMAP.from_bytes(header.data)
        si.header = header
        return si
    
    @staticmethod
    def from_bytes(data):
        return BITMAP.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = BITMAP()
        si.bitfield = buff.read()
        return si
    
    def __str__(self):
        res = []
        res.append('Bitmap')
        res.append('BitField: {}'.format(self.bitfield))
        return '\n'.join(res)
