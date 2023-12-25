import io

class Attribute:
    def __init__(self):
        self.header = None
    
    @staticmethod
    async def from_reader(reader, header = None):
        header = await AttributeHeader.from_reader(reader)
        return NTFS_ATTR_TYPE_MAP[header.type].from_header(header)
    
    @staticmethod
    def from_bytes(data:bytes):
        return Attribute.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff:io.BytesIO):
        pos = buff.tell()
        htype = buff.read(4)
        if htype == b'\xFF\xFF\xFF\xFF':
            length = 4
        else:
            length = int.from_bytes(buff.read(4), 'little')
        buff.seek(pos, 0)
        hdata = buff.read(length)
        header = AttributeHeader.from_bytes(hdata)
        try:
            return NTFS_ATTR_TYPE_MAP[header.type].from_header(header)
        except:
            print(header)
            buff.seek(pos, 0)
            print(buff.read(length))
            raise

class AttributeHeader:
    def __init__(self):
        self.type = None
        self.length = None
        self.non_resident = None
        self.name_length = None
        self.name_offset = None
        self.flags = None
        self.id = None
    
    @staticmethod
    async def from_reader(reader):
        temp = await reader.read(8)
        header = AttributeHeader()
        header.type = int.from_bytes(temp[0:4], 'little')
        header.length = int.from_bytes(temp[4:8], 'little')
        data = await reader.read(header.length - 8)
        buff = io.BytesIO(temp + data)
        buff.seek(8,0)
        header.non_resident = bool(int.from_bytes(buff.read(1), 'little'))
        header.name_length = int.from_bytes(buff.read(1), 'little')
        header.name_offset = int.from_bytes(buff.read(2), 'little')
        header.flags = int.from_bytes(buff.read(2), 'little')
        header.id = int.from_bytes(buff.read(2), 'little')
        if header.non_resident is False:
            header = ResidentAttribute.from_header(header, buff)
        else:
            header = NonResidentAttribute.from_header(header, buff)
        
        return header

    async def reparse(self, fs, include_data = False):
        # Reparse the attribute by reading data run references and re-reading the attribute
        # Do not use this on DATA attributes, as it will read the entire file into memory
        if include_data is False and self.type == 0x80:
            return NTFS_ATTR_TYPE_MAP[self.type].from_header(self)
        
        buff = io.BytesIO()
        async for chunk in self.read_attribute_data(fs):
            buff.write(chunk)
        
        buff.seek(0,0)
        self.data = buff.read()
        print('Reparse: %s' % hex(self.type))
        input(self.data)
        return NTFS_ATTR_TYPE_MAP[self.type].from_header(self)
    
    @staticmethod
    def from_bytes(data:bytes):
        return AttributeHeader.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff:io.BytesIO):
        temp = buff.read(8)
        header = AttributeHeader()
        header.type = int.from_bytes(temp[0:4], 'little')
        header.length = int.from_bytes(temp[4:8], 'little')
        data = buff.read(header.length - 8)
        buff = io.BytesIO(temp + data)
        buff.seek(8,0)
        header.non_resident = bool(int.from_bytes(buff.read(1), 'little'))
        header.name_length = int.from_bytes(buff.read(1), 'little')
        header.name_offset = int.from_bytes(buff.read(2), 'little')
        header.flags = int.from_bytes(buff.read(2), 'little')
        header.id = int.from_bytes(buff.read(2), 'little')
        if header.non_resident is False:
            header = ResidentAttribute.from_header(header, buff)
        else:
            header = NonResidentAttribute.from_header(header, buff)
        
        return header

class ResidentAttribute(AttributeHeader):
    def __init__(self):
        super().__init__()
        self.attr_length = None
        self.attr_offset = None
        self.indexed_flag = None
        self.padding = None
        self.name = None
        self.data = None
    
    @staticmethod
    def from_header(header, buffer):
        attr = ResidentAttribute()
        attr.type = header.type
        attr.length = header.length
        attr.non_resident = header.non_resident
        attr.name_length = header.name_length
        attr.name_offset = header.name_offset
        attr.flags = header.flags
        attr.id = header.id
        attr.attr_length = int.from_bytes(buffer.read(4), 'little')
        attr.attr_offset = int.from_bytes(buffer.read(2), 'little')
        attr.indexed_flag = int.from_bytes(buffer.read(1), 'little')
        attr.padding = int.from_bytes(buffer.read(1), 'little')
        if attr.name_length > 0:
            buffer.seek(attr.name_offset, 0)
            attr.name = buffer.read(attr.name_length*2).decode('utf-16-le')
        
        buffer.seek(attr.attr_offset, 0)
        attr.data = buffer.read(attr.attr_length) # - attr.attr_offset
        return attr

    async def read_attribute_data(self, fs):
        # yield data in chunks
        chunk_size=1024*1024
        for i in range(0, len(self.data), chunk_size):
            yield self.data[i:i+chunk_size]
    
    def __str__(self):
        res = []
        res.append('Resident Attribute')
        res.append('Type: {}'.format(hex(self.type)))
        res.append('Length: {}'.format(self.length))
        res.append('Non Resident: {}'.format(self.non_resident))
        res.append('Name Length: {}'.format(self.name_length))
        res.append('Name Offset: {}'.format(self.name_offset))
        res.append('Flags: {}'.format(self.flags))
        res.append('ID: {}'.format(self.id))
        res.append('Attribute Length: {}'.format(self.attr_length))
        res.append('Attribute Offset: {}'.format(self.attr_offset))
        res.append('Indexed Flag: {}'.format(self.indexed_flag))
        res.append('Padding: {}'.format(self.padding))
        res.append('Name: {}'.format(self.name))
        res.append('Data: {}'.format(self.data))
        return '\n'.join(res)        

class NonResidentAttribute(AttributeHeader):
    def __init__(self):
        super().__init__()
        self.start_vcn = None
        self.last_vcn = None
        self.runlist_offset = None
        self.compression_unit = None
        self.padding = None
        self.alloc_size = None
        self.real_size = None
        self.init_size = None
        self.name = None
        self.data = None
        self.data_runs = []
    
    def __str__(self):
        res = []
        res.append('Non Resident Attribute')
        res.append('Type: {}'.format(hex(self.type)))
        res.append('Length: {}'.format(self.length))
        res.append('Non Resident: {}'.format(self.non_resident))
        res.append('Name Length: {}'.format(self.name_length))
        res.append('Name Offset: {}'.format(self.name_offset))
        res.append('Flags: {}'.format(self.flags))
        res.append('ID: {}'.format(self.id))
        res.append('Start VCN: {}'.format(self.start_vcn))
        res.append('Last VCN: {}'.format(self.last_vcn))
        res.append('Runlist Offset: {}'.format(self.runlist_offset))
        res.append('Compression Unit: {}'.format(self.compression_unit))
        res.append('Padding: {}'.format(self.padding))
        res.append('Alloc Size: {}'.format(self.alloc_size))
        res.append('Real Size: {}'.format(self.real_size))
        res.append('Init Size: {}'.format(self.init_size))
        res.append('Name: {}'.format(self.name))
        res.append('Data: {}'.format(self.data))
        res.append('Data Runs: {}'.format(self.data_runs))
        return '\n'.join(res)
    
    @staticmethod
    def from_header(header, buffer):
        attr = NonResidentAttribute()
        attr.type = header.type
        attr.length = header.length
        attr.non_resident = header.non_resident
        attr.name_length = header.name_length
        attr.name_offset = header.name_offset
        attr.flags = header.flags
        attr.id = header.id
        attr.start_vcn = int.from_bytes(buffer.read(8), 'little')
        attr.last_vcn = int.from_bytes(buffer.read(8), 'little')
        attr.runlist_offset = int.from_bytes(buffer.read(2), 'little')
        attr.compression_unit = int.from_bytes(buffer.read(2), 'little')
        attr.padding = int.from_bytes(buffer.read(4), 'little')
        attr.alloc_size = int.from_bytes(buffer.read(8), 'little')
        attr.real_size = int.from_bytes(buffer.read(8), 'little')
        attr.init_size = int.from_bytes(buffer.read(8), 'little')
        if attr.name_length > 0:
            buffer.seek(attr.name_offset, 0)
            attr.name = buffer.read(attr.name_length*2).decode('utf-16-le')

        # Processing runlist
        buffer.seek(attr.runlist_offset, 0)  # Relative seek to the runlist
        current_cluster = 0  # Start at the beginning of the file
        while True:
            first_byte = int.from_bytes(buffer.read(1), 'little')
            if first_byte == 0:
                break  # End of data run

            len_length = first_byte & 0x0F
            offset_length = (first_byte >> 4) & 0x0F

            run_length = int.from_bytes(buffer.read(len_length), 'little')
            if offset_length > 0:
                # Read the run offset (relative offset)
                relative_run_offset = int.from_bytes(buffer.read(offset_length), 'little', signed=True)
                # Convert to absolute offset
                current_cluster += relative_run_offset
            else:
                # Sparse run, don't update current_cluster
                current_cluster = 0  # Reset current cluster for sparse run

            attr.data_runs.append((current_cluster, run_length))
        
        return attr

    async def get_data_size(self, fs):
        """Calculates the size of the data using data run references and a fs."""
        size = 0
        for run_offset, run_length in self.data_runs:
            size += run_length * fs.cluster_size
        return size

    async def read_attribute_data(self, fs):
        """Reads the actual data using data run references and a fs."""
        """Yields data in chunks"""
        
        rem_len = self.real_size
        for run_offset, run_length in self.data_runs:
            if run_offset == 0:
                data = b'\x00' * (run_length * fs.cluster_size)
                rem_len -= len(data)
                if rem_len <= 0:
                    if rem_len < 0:
                        data = data[:rem_len]
                    yield data
                    break
                yield data
                continue
            
            async for data in fs.read_sequential_clusters(run_offset, run_length):
                rem_len -= len(data)
                if rem_len <= 0:
                    if rem_len < 0:
                        data = data[:rem_len]
                    yield data
                    break
                yield data



# do not use
class EndAttribute(Attribute):
    def __init__(self):
        super().__init__()
        self.data = None
    
    @staticmethod
    def from_bytes(data):
        return EndAttribute.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_header(header):
        attr = EndAttribute()
        attr.header = header
        return attr
    
    def __str__(self):
        return 'END Attribute'

from adiskreader.filesystems.ntfs.structures.attributes.standardinformation import STANDARD_INFORMATION
from adiskreader.filesystems.ntfs.structures.attributes.filename import FILE_NAME, FileNameFlag
from adiskreader.filesystems.ntfs.structures.attributes.bitmap import BITMAP
from adiskreader.filesystems.ntfs.structures.attributes.securitydescriptor import SECURITY_DESCRIPTOR
from adiskreader.filesystems.ntfs.structures.attributes.loggedutilitystream import LOGGED_UTILITY_STREAM
from adiskreader.filesystems.ntfs.structures.attributes.objectid import OBJECT_ID
from adiskreader.filesystems.ntfs.structures.attributes.attributelist import ATTRIBUTE_LIST
from adiskreader.filesystems.ntfs.structures.attributes.volumeinformation import VOLUME_INFORMATION
from adiskreader.filesystems.ntfs.structures.attributes.volumename import VOLUME_NAME
from adiskreader.filesystems.ntfs.structures.attributes.data import DATA
from adiskreader.filesystems.ntfs.structures.attributes.index import INDEX_ROOT, INDEX_ALLOCATION, IndexEntry, IndexEntryFlag
from adiskreader.filesystems.ntfs.structures.attributes.reparsepoint import REPARSE_POINT
from adiskreader.filesystems.ntfs.structures.attributes.extendedattributes import EA, EA_INFORMATION
from adiskreader.filesystems.ntfs.structures.attributes.propertyset import PROPERTY_SET

NTFS_ATTR_TYPE_MAP = {
    0x10: STANDARD_INFORMATION,
    0x20: ATTRIBUTE_LIST,
    0x30: FILE_NAME, 
    0x40: OBJECT_ID,
    0x50: SECURITY_DESCRIPTOR,
    0x60: VOLUME_NAME,
    0x70: VOLUME_INFORMATION,
    0x80: DATA,
    0x90: INDEX_ROOT,
    0xA0: INDEX_ALLOCATION,
    0xB0: BITMAP,
    0xC0: REPARSE_POINT,
    0xD0: EA_INFORMATION,
    0xE0: EA,
    0xF0: PROPERTY_SET,
    0x100: LOGGED_UTILITY_STREAM,
    0xFFFFFFFF: EndAttribute
}