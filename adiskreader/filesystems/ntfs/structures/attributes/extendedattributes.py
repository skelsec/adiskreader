import io
import enum
import uuid
from adiskreader.filesystems.ntfs.structures.attributes import Attribute

class EA(Attribute):
    def __init__(self):
        super().__init__()
        self.next_offset = None
        self.flags = None
        self.name_length = None
        self.value_length = None
        self.name = None
        self.value = None
    
    @staticmethod
    def from_header(header):
        si = EA.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return EA.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = EA()
        si.next_offset = int.from_bytes(buff.read(4), 'little')
        si.flags = int.from_bytes(buff.read(1), 'little')
        si.name_length = int.from_bytes(buff.read(1), 'little')
        si.value_length = int.from_bytes(buff.read(2), 'little')
        si.name = buff.read(si.name_length) #maybe utf-16-le?
        si.value = buff.read(si.value_length)
        return si
    
    def __str__(self):
        res = []
        res.append('EA')
        res.append('Next Offset: {}'.format(self.next_offset))
        res.append('Flags: {}'.format(self.flags))
        res.append('Name Length: {}'.format(self.name_length))
        res.append('Value Length: {}'.format(self.value_length))
        res.append('Name: {}'.format(self.name))
        res.append('Value: {}'.format(self.value))
        return '\n'.join(res)

class EA_INFORMATION(Attribute):
    def __init__(self):
        super().__init__()
        self.packed_size = None
        self.need_ea_cnt = None
        self.unpacket_size = None
    
    @staticmethod
    def from_header(header):
        si = EA_INFORMATION.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return EA_INFORMATION.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = EA_INFORMATION()
        si.packed_size = int.from_bytes(buff.read(2), 'little')
        si.need_ea_cnt = int.from_bytes(buff.read(2), 'little')
        si.unpacket_size = int.from_bytes(buff.read(4), 'little')
        return si
    
    def __str__(self):
        res = []
        res.append('EA_INFORMATION')
        res.append('Packed Size: {}'.format(self.packed_size))
        res.append('Need EA Cnt: {}'.format(self.need_ea_cnt))
        res.append('Unpacket Size: {}'.format(self.unpacket_size))
        return '\n'.join(res)