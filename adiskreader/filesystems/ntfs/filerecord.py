import io
import enum
from adiskreader.filesystems.ntfs.attributes import Attribute

# https://flatcap.github.io/linux-ntfs/ntfs/concepts/file_record.html

class FileRecordFlags(enum.IntFlag):
    IN_USE = 0x01 # File record is in use
    DIRECTORY = 0x02 # File record is a directory
    EXTENSION = 0x04 # Record is an exension (Set for records in the $Extend directory)
    SPECIAL_INDEX_PRESENT = 0x08 # Special index present (Set for non-directory records containing an index: $Secure, $ObjID, $Quota, $Reparse)


class FileRecord:
    def __init__(self):
        self.signature = None
        self.usa_offset = None
        self.usa_count = None
        self.lsn = None
        self.sequence_number = None
        self.link_count = None
        self.attr_offset = None
        self.flags = None
        self.bytes_in_use = None
        self.bytes_allocated = None
        self.base_record = None
        self.next_attr_id = None
        self.record_number = None
        self.attributes = []
    
    def get_attribute_by_name(self, name:str):
        # there could be multiple attributes of the same type
        res = []
        for attr in self.attributes:
            if attr.header.name == name:
                res.append(attr)
        return res
    
    def get_attribute_by_type(self, atype:int):
        # there could be multiple attributes of the same type
        res = []
        for attr in self.attributes:
            if attr.header.type == atype:
                res.append(attr)
        return res
    
    def add_attribute(self, attr:Attribute):
        self.attributes.append(attr)
    
    def get_main_filename_attr(self):
        '''Returns the main filename attribute of the entry.

        As an entry can have multiple FILENAME attributes, this function allows
        to return the main one, i.e., the one with the lowest attribute id and
        the "biggest" namespace.
        '''
        fn_attrs = self.get_attribute_by_type(0x30)
        high_attr_id = 0xFFFFFFFF
        main_fn = None

        if fn_attrs is not None:
            #search for the lowest id, that will give the first FILE_NAME
            for fn_attr in fn_attrs:
                if fn_attr.header.id < high_attr_id:
                    main_fn = fn_attr
                    high_attr_id = fn_attr.header.id

            ##TODO is this necessary? Maybe the first name is always the with with the biggest namespace
            ## after we have the lowest, search for same name, but the biggest namespace
            #for fn_attr in fn_attrs:
            #    if main_fn.content.parent_ref == fn_attr.content.parent_ref and \
            #        main_fn.content.parent_seq == fn_attr.content.parent_seq and \
            #        fn_attr.content.name_type.value < main_fn.content.name_type.value:
            #            main_fn = fn_attr

        return main_fn

    
    @staticmethod
    def from_bytes(data:bytes):
        return FileRecord.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff:io.BytesIO):
        start_pos = buff.tell()
        fr = FileRecord()
        fr.signature = buff.read(4)
        if fr.signature != b'FILE':
            raise Exception('Invalid file record signature. Expected FILE, got {}'.format(fr.signature))
        fr.usa_offset = int.from_bytes(buff.read(2), 'little')
        fr.usa_count = int.from_bytes(buff.read(2), 'little')
        fr.lsn = int.from_bytes(buff.read(8), 'little')
        fr.sequence_number = int.from_bytes(buff.read(2), 'little')
        fr.link_count = int.from_bytes(buff.read(2), 'little')
        fr.attr_offset = int.from_bytes(buff.read(2), 'little')
        fr.flags = FileRecordFlags(int.from_bytes(buff.read(2), 'little'))
        fr.bytes_in_use = int.from_bytes(buff.read(4), 'little')
        fr.bytes_allocated = int.from_bytes(buff.read(4), 'little')
        fr.base_record = int.from_bytes(buff.read(8), 'little')
        fr.next_attr_id = int.from_bytes(buff.read(2), 'little')
        fr.record_number = int.from_bytes(buff.read(2), 'little')
        
        buff.seek(fr.attr_offset + start_pos, 0)
        while (buff.tell() - start_pos) < fr.bytes_in_use:
            attr = Attribute.from_buffer(buff)
            fr.add_attribute(attr)
            if attr.header.type == 0xFFFFFFFF:
                break
        
        # Seek to the end of the file record to allow for the next file record to be read
        buff.seek(start_pos, 0)
        buff.seek(fr.bytes_allocated, 1)
        return fr

    @staticmethod
    async def from_reader(reader):
        pos = reader.tell()
        await reader.seek(0x1C, 1)
        bytes_allocated = int.from_bytes(await reader.read(4), 'little')
        await reader.seek(pos, 0)
        data = await reader.read(bytes_allocated)
        return FileRecord.from_bytes(data)

    
    @staticmethod
    async def from_filesystem(fs, start_cluster):
        reader = fs.get_cluster_reader(start_cluster)
        return await FileRecord.from_reader(reader)
    
    def __str__(self):
        res = []
        res.append('FileRecord')
        res.append('Signature: {}'.format(self.signature.hex()))
        res.append('USA Offset: {}'.format(self.usa_offset))
        res.append('USA Count: {}'.format(self.usa_count))
        res.append('LSN: {}'.format(self.lsn))
        res.append('Sequence Number: {}'.format(self.sequence_number))
        res.append('Link Count: {}'.format(self.link_count))
        res.append('Attribute Offset: {}'.format(self.attr_offset))
        res.append('Flags: {}'.format(self.flags))
        res.append('Bytes In Use: {}'.format(self.bytes_in_use))
        res.append('Bytes Allocated: {}'.format(self.bytes_allocated))
        res.append('Base Record: {}'.format(self.base_record))
        res.append('Next Attribute ID: {}'.format(self.next_attr_id))
        res.append('Record Number: {}'.format(self.record_number))
        res.append('Attributes:')
        for attr in self.attributes:
            res.append(str(attr))
        return '\n'.join(res)
