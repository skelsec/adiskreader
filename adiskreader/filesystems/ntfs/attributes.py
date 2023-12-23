import io
import enum
import uuid
from winacl.dtyp.security_descriptor import SECURITY_DESCRIPTOR as SD

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

    #async def reparse(self, fs):
    #    # Reparse the attribute by reading data run references and re-reading the attribute
    #    # Do not use this on DATA attributes, as it will read the entire file into memory
    #    buff = io.BytesIO()
    #    async for chunk in self.read_attribute_data(fs):
    #        buff.write(chunk)
    #    return type(self).from_header(header, buff)
    
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

        print(self.real_size)
        print(self.alloc_size)
        print(self.init_size)
        print(await self.get_data_size(fs))
        input(self.data_runs)
        
        rem_len = self.real_size
        for run_offset, run_length in self.data_runs:
            if run_offset == 0:
                data = b'\x00' * (run_length * fs.cluster_size)
                rem_len -= len(data)
                if rem_len <= 0:
                    data = data[:rem_len]
                    yield data
                    break
                yield data
                continue
            
            async for data in fs.read_sequential_clusters(run_offset, run_length):
                rem_len -= len(data)
                print('Rem len: %s' % rem_len)
                if rem_len <= 0:
                    data = data[:rem_len]
                    yield data
                    break
                yield data

class STANDARD_INFORMATION(Attribute):
    def __init__(self):
        super().__init__()
        self.time_created = None
        self.time_modified = None
        self.time_mft_modified = None
        self.time_accessed = None
        self.flags = None
        self.maximum_versions = None
        self.version = None
        self.classid = None
        self.owner_id = None
        self.security_id = None
        self.quota_charged = None
        self.usn = None

    @staticmethod
    def from_header(header):
        si = STANDARD_INFORMATION.from_bytes(header.data)
        si.header = header
        return si
    
    @staticmethod
    def from_bytes(data):
        return STANDARD_INFORMATION.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = STANDARD_INFORMATION()
        si.time_created = int.from_bytes(buff.read(8), 'little')
        si.time_modified = int.from_bytes(buff.read(8), 'little')
        si.time_mft_modified = int.from_bytes(buff.read(8), 'little')
        si.time_accessed = int.from_bytes(buff.read(8), 'little')
        si.flags = int.from_bytes(buff.read(4), 'little')
        si.maximum_versions = int.from_bytes(buff.read(4), 'little')
        si.version = int.from_bytes(buff.read(4), 'little')
        si.classid = int.from_bytes(buff.read(4), 'little')
        si.owner_id = int.from_bytes(buff.read(4), 'little')
        si.security_id = int.from_bytes(buff.read(4), 'little')
        si.quota_charged = int.from_bytes(buff.read(8), 'little')
        si.usn = int.from_bytes(buff.read(8), 'little')
        return si
    
    def __str__(self):
        res = []
        res.append('Standard Information')
        res.append('Time Created: {}'.format(self.time_created))
        res.append('Time Modified: {}'.format(self.time_modified))
        res.append('Time MFT Modified: {}'.format(self.time_mft_modified))
        res.append('Time Accessed: {}'.format(self.time_accessed))
        res.append('Flags: {}'.format(str(self.flags)))
        res.append('Maximum Versions: {}'.format(self.maximum_versions))
        res.append('Version: {}'.format(self.version))
        res.append('Class ID: {}'.format(self.classid))
        res.append('Owner ID: {}'.format(self.owner_id))
        res.append('Security ID: {}'.format(self.security_id))
        res.append('Quota Charged: {}'.format(self.quota_charged))
        res.append('USN: {}'.format(self.usn))
        return '\n'.join(res)


class FILE_NAME(Attribute):
    def __init__(self):
        super().__init__()
        self.parent_ref = None
        self.parent_seq = None
        self.time_created = None
        self.time_modified = None
        self.time_mft_modified = None
        self.time_accessed = None
        self.allocated_size = None
        self.real_size = None
        self.flags = None
        self.reparse_value = None
        self.name_length = None
        self.namespace = None
        self.name = None

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
        si.time_created = int.from_bytes(buff.read(8), 'little')
        si.time_modified = int.from_bytes(buff.read(8), 'little')
        si.time_mft_modified = int.from_bytes(buff.read(8), 'little')
        si.time_accessed = int.from_bytes(buff.read(8), 'little')
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

# INCOMPLETE
class INDEX_ROOT(Attribute):
    def __init__(self):
        super().__init__()
        self.attribute_type = None
        self.collation_rule = None
        self.bytes_per_record = None
        self.clusters_per_index_record = None
        self.padding = None
        self.index_header = None
        self.index_entries = []

    @staticmethod
    def from_header(header):
        si = INDEX_ROOT.from_bytes(header.data)
        si.header = header
        return si
    
    @staticmethod
    def from_bytes(data):
        return INDEX_ROOT.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff):
        si = INDEX_ROOT()
        si.attribute_type = int.from_bytes(buff.read(4), 'little')
        si.collation_rule = int.from_bytes(buff.read(4), 'little')
        si.bytes_per_record = int.from_bytes(buff.read(4), 'little')
        si.clusters_per_index_record = int.from_bytes(buff.read(1), 'little')
        si.padding = int.from_bytes(buff.read(3), 'little')
        si.index_header = IndexHeader.from_buffer(buff)
        while buff.tell() < len(buff.getvalue()):
            si.index_entries.append(IndexEntry.from_buffer(buff))
        return si

class IndexEntry:
    def __init__(self):
        self.file_reference = None
        self.entry_length = None
        self.stream_length = None
        self.flags = None
        self.TODO = None # TODO: finish this
    
    @staticmethod
    def from_bytes(data):
        return IndexEntry.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff):
        ie = IndexEntry()
        ie.file_reference = int.from_bytes(buff.read(8), 'little')
        ie.entry_length = int.from_bytes(buff.read(2), 'little')
        ie.stream_length = int.from_bytes(buff.read(2), 'little')
        ie.flags = int.from_bytes(buff.read(1), 'little')
        ie.TODO = buff.read(ie.entry_length - 0x0D)
        return ie

class IndexHeader:
    def __init__(self):
        self.first_entry_offset = None
        self.index_length = None
        self.allocated_size = None
        self.flags = None
        self.unused = None

    @staticmethod
    def from_bytes(data):
        return IndexHeader.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff):
        ih = IndexHeader()
        ih.first_entry_offset = int.from_bytes(buff.read(4), 'little')
        ih.index_length = int.from_bytes(buff.read(4), 'little')
        ih.allocated_size = int.from_bytes(buff.read(4), 'little')
        ih.flags = int.from_bytes(buff.read(1), 'little')
        ih.unused = int.from_bytes(buff.read(3), 'little')
        return ih

class VOLUME_NAME(Attribute):
    def __init__(self):
        super().__init__()
        self.name = None

    @staticmethod
    def from_header(header):
        si = VOLUME_NAME.from_bytes(header.data)
        si.header = header
        return si
    
    @staticmethod
    def from_bytes(data):
        return VOLUME_NAME.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = VOLUME_NAME()
        si.name = buff.read().decode('utf-16-le')
        return si
    
    def __str__(self):
        res = []
        res.append('Volume Name')
        res.append('Name: {}'.format(self.name))
        return '\n'.join(res)

class VOLUME_INFORMATION(Attribute):
    def __init__(self):
        super().__init__()
        self.unknown = None
        self.major_version = None
        self.minor_version = None
        self.flags = None
        self.unknown2 = None
    
    @staticmethod
    def from_header(header):
        si = VOLUME_INFORMATION.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return VOLUME_INFORMATION.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = VOLUME_INFORMATION()
        si.unknown = int.from_bytes(buff.read(8), 'little')
        si.major_version = int.from_bytes(buff.read(1), 'little')
        si.minor_version = int.from_bytes(buff.read(1), 'little')
        si.flags = VOLUME_INFORMATION_FLAG(int.from_bytes(buff.read(2), 'little'))
        si.unknown2 = int.from_bytes(buff.read(4), 'little')
        return si
    
    def __str__(self):
        res = []
        res.append('Volume Information')
        res.append('Unknown: {}'.format(self.unknown))
        res.append('Major Version: {}'.format(self.major_version))
        res.append('Minor Version: {}'.format(self.minor_version))
        res.append('Flags: {}'.format(self.flags))
        res.append('Unknown2: {}'.format(self.unknown2))
        return '\n'.join(res)

class VOLUME_INFORMATION_FLAG(enum.IntFlag):
    Dirty = 0x0001
    ResizeLogFile = 0x0002
    UpgradeOnMount = 0x0004
    MountedOnNT4 = 0x0008
    DeleteUSNUnderway = 0x0010
    RepairObjectId = 0x0020
    ModifiedByChkdsk = 0x8000


class SECURITY_DESCRIPTOR(Attribute):
    def __init__(self):
        super().__init__()
        self.sid = None
    
    @staticmethod
    def from_header(header):
        si = SECURITY_DESCRIPTOR.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return SECURITY_DESCRIPTOR.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = SECURITY_DESCRIPTOR()
        si.sid = SD.from_buffer(buff)
        return si
    
    def __str__(self):
        res = []
        res.append('SECURITY_DESCRIPTOR')
        res.append('SID: {}'.format(self.sid))
        return '\n'.join(res)

class INDEX_ALLOCATION(Attribute):
    def __init__(self):
        super().__init__()
        self.data = None
    
    @staticmethod
    def from_header(header):
        si = INDEX_ALLOCATION.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        return INDEX_ALLOCATION.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = INDEX_ALLOCATION()
        si.data = buff.read()
        return si
    
    def __str__(self):
        res = []
        res.append('INDEX_ALLOCATION')
        res.append('Data: {}'.format(self.data))
        return '\n'.join(res)

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

class OBJECT_ID(Attribute):
    def __init__(self):
        super().__init__()
        self.object_id = None
        self.birth_volume_id = None
        self.birth_object_id = None
        self.domain_id = None
    
    @staticmethod
    def from_header(header):
        si = OBJECT_ID.from_bytes(header.data)
        si.header = header
        return si

    @staticmethod
    def from_bytes(data):
        data = data + b'\x00' * (0x40 - len(data)) #yes, this is actually per documentation
        return OBJECT_ID.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = OBJECT_ID()
        si.object_id = uuid.UUID(bytes_le = buff.read(16))
        si.birth_volume_id = uuid.UUID(bytes_le = buff.read(16))
        si.birth_object_id = uuid.UUID(bytes_le = buff.read(16))
        si.domain_id = uuid.UUID(bytes_le = buff.read(16))
        return si
    
    def __str__(self):
        res = []
        res.append('OBJECT_ID')
        res.append('Object ID: {}'.format(self.object_id))
        res.append('Birth Volume ID: {}'.format(self.birth_volume_id))
        res.append('Birth Object ID: {}'.format(self.birth_object_id))
        res.append('Domain ID: {}'.format(self.domain_id))
        return '\n'.join(res)

class ATTRIBUTE_LIST:
    def __init__(self):
        self.data = None
    
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
        si.data = buff.read()
        return si
    
    def __str__(self):
        res = []
        res.append('ATTRIBUTE_LIST')
        res.append('Data: {}'.format(self.data))
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

# placeholder do not use
class DummyAttribute(Attribute):
    def __init__(self):
        super().__init__()
        self.data = None
    
    @staticmethod
    def from_bytes(data):
        return DummyAttribute.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_header(header):
        attr = DummyAttribute()
        attr.header = header
        return attr
    
    def __str__(self):
        return 'Dummy Attribute'

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