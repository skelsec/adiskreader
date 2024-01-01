import io
import enum
import uuid
from adiskreader.filesystems.ntfs.structures.attributes import Attribute

class ATTRIBUTE_LIST(Attribute):
    def __init__(self):
        super().__init__()
        self.attributes = []
    
    @staticmethod
    def from_header(header):
        si = ATTRIBUTE_LIST.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return ATTRIBUTE_LIST.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = ATTRIBUTE_LIST()
        while buff.tell() < len(buff.getbuffer()):
            entry = AttributeEntry.from_buffer(buff)
            si.attributes.append(entry)
        return si
    
    def __str__(self):
        res = []
        res.append('ATTRIBUTE_LIST')
        for attr in self.attributes:
            res.append(str(attr))
        return '\n'.join(res)
    
class AttributeEntry:
    def __init__(self):
        self.type = None
        self.length = None
        self.name_length = None
        self.name_offset = None
        self.start_vcn = None
        self.file_ref = None
        self.file_ctr = None
        self.attribute_id = None
        self.name = None

    @staticmethod
    def from_bytes(data):
        return AttributeEntry.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff):
        pos = buff.tell()
        ae = AttributeEntry()
        ae.type = int.from_bytes(buff.read(4), 'little')
        ae.length = int.from_bytes(buff.read(2), 'little')
        ae.name_length = int.from_bytes(buff.read(1), 'little')
        ae.name_offset = int.from_bytes(buff.read(1), 'little')
        ae.start_vcn = int.from_bytes(buff.read(8), 'little')
        ae.file_ref = int.from_bytes(buff.read(6), 'little')
        ae.file_ctr = int.from_bytes(buff.read(2), 'little')
        ae.attribute_id = int.from_bytes(buff.read(2), 'little')
        if ae.name_length > 0:
            buff.seek(pos + ae.name_offset, 0)
            ae.name = buff.read(ae.name_length * 2).decode('utf-16-le')
        buff.seek(pos + ae.length, 0)
        return ae
    
    def __str__(self):
        res = []
        res.append('ATTRIBUTE_LIST_ENTRY')
        res.append('Type: {}'.format(self.type))
        res.append('Length: {}'.format(self.length))
        res.append('Name Length: {}'.format(self.name_length))
        res.append('Name Offset: {}'.format(self.name_offset))
        res.append('Start VCN: {}'.format(self.start_vcn))
        res.append('File Ref: {}'.format(self.file_ref))
        res.append('File Ctr: {}'.format(self.file_ctr))
        res.append('Attribute ID: {}'.format(self.attribute_id))
        res.append('Name: {}'.format(self.name))
        return '\n'.join(res)