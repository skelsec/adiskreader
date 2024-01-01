import io
import enum
import uuid
from adiskreader.utils import filetime_to_dt
from adiskreader.filesystems.ntfs.structures.attributes import Attribute
import datetime


class FILE_NAME(Attribute):
    def __init__(self):
        super().__init__()
        self.parent_ref:int = None
        self.parent_seq:int = None
        self.time_created:datetime.datetime = None
        self.time_modified:datetime.datetime = None
        self.time_mft_modified:datetime.datetime = None
        self.time_accessed:datetime.datetime = None
        self.allocated_size:int = None
        self.real_size:int = None
        self.flags:FileNameFlag = None
        self.reparse_value:int = None
        self.name_length:int = None
        self.namespace = None
        self.name:str = None

    @staticmethod
    def from_header(header):
        si = FILE_NAME.from_bytes(header.data)
        si.header = header
        return si
    
    @staticmethod
    def from_bytes(data):
        return FILE_NAME.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = FILE_NAME()
        si.parent_ref = int.from_bytes(buff.read(6), 'little')
        si.parent_seq = int.from_bytes(buff.read(2), 'little')
        si.time_created   = filetime_to_dt(int.from_bytes(buff.read(8), 'little'))
        si.time_modified  = filetime_to_dt(int.from_bytes(buff.read(8), 'little'))
        si.time_mft_modified = filetime_to_dt(int.from_bytes(buff.read(8), 'little'))
        si.time_accessed  = filetime_to_dt(int.from_bytes(buff.read(8), 'little'))
        si.allocated_size = int.from_bytes(buff.read(8), 'little')
        si.real_size = int.from_bytes(buff.read(8), 'little')
        si.flags = FileNameFlag(int.from_bytes(buff.read(4), 'little'))
        si.reparse_value = int.from_bytes(buff.read(4), 'little')
        si.name_length = int.from_bytes(buff.read(1), 'little')
        si.namespace = int.from_bytes(buff.read(1), 'little')
        si.name = buff.read(si.name_length*2).decode('utf-16-le')
        return si
    
    def __str__(self):
        res = []
        res.append('File Name')
        res.append('Header: {}'.format(self.header))
        res.append('Parent Ref: {}'.format(self.parent_ref))
        res.append('Parent Seq: {}'.format(self.parent_seq))
        res.append('Time Created: {}'.format(self.time_created))
        res.append('Time Modified: {}'.format(self.time_modified))
        res.append('Time MFT Modified: {}'.format(self.time_mft_modified))
        res.append('Time Accessed: {}'.format(self.time_accessed))
        res.append('Allocated Size: {}'.format(self.allocated_size))
        res.append('Real Size: {}'.format(self.real_size))
        res.append('Flags: {}'.format(str(self.flags)))
        res.append('Reparse Value: {}'.format(self.reparse_value))
        res.append('Name Length: {}'.format(self.name_length))
        res.append('Namespace: {}'.format(self.namespace))
        res.append('Name: {}'.format(self.name))
        return '\n'.join(res)

class FileNameFlag(enum.IntFlag):
    READ_ONLY = 0x0001
    HIDDEN = 0x0002
    SYSTEM = 0x0004
    ARCHIVE = 0x0020
    DEVICE = 0x0040
    NORMAL = 0x0080
    TEMPORARY = 0x0100
    SPARSE_FILE = 0x0200
    REPARSE_POINT = 0x0400
    COMPRESSED = 0x0800
    OFFLINE = 0x1000
    NOT_CONTENT_INDEXED = 0x2000
    ENCRYPTED = 0x4000
    DIRECTORY = 0x10000000
    INDEX_VIEW = 0x20000000