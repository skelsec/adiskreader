import io
import enum
from adiskreader.filesystems.ntfs.structures.attributes import Attribute

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
        pos = buff.tell()
        si = INDEX_ROOT()
        si.attribute_type = int.from_bytes(buff.read(4), 'little')
        si.collation_rule = int.from_bytes(buff.read(4), 'little')
        si.bytes_per_record = int.from_bytes(buff.read(4), 'little')
        si.clusters_per_index_record = int.from_bytes(buff.read(1), 'little')
        si.padding = int.from_bytes(buff.read(3), 'little')
        hdrpos = buff.tell()
        si.index_header = IndexHeader.from_buffer(buff)
        # Seek to the first index entry
        buff.seek(hdrpos + si.index_header.first_entry_offset, 0)
        
        #while buff.tell() < (pos + si.index_header.first_entry_offset + si.index_header.index_length):
        while buff.tell() < hdrpos + si.index_header.index_length:
            entry = IndexEntry.from_buffer(buff)
            si.index_entries.append(entry)
            # correct padding
            if (buff.tell() % si.bytes_per_record) != 0:
                buff.seek(si.bytes_per_record - (buff.tell() % si.bytes_per_record), 1)

            #if IndexEntryFlag.LAST_ENTRY in entry.flags:
            #    break
        return si

    def __str__(self):
        res = []
        res.append('Index Root')
        res.append('Attribute Type: {}'.format(self.attribute_type))
        res.append('Collation Rule: {}'.format(self.collation_rule))
        res.append('Bytes Per Record: {}'.format(self.bytes_per_record))
        res.append('Clusters Per Index Record: {}'.format(self.clusters_per_index_record))
        res.append('Padding: {}'.format(self.padding))
        res.append('Index Header: {}'.format(self.index_header))
        for entry in self.index_entries:
            res.append('ENTRY')
            res.append(str(entry))
        return '\n'.join(res)


class INDEX_ALLOCATION(Attribute):
    def __init__(self):
        super().__init__()
        self.index_records = []
    
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
        pos = buff.tell()
        test_data = buff.read()
        size = buff.tell()
        buff.seek(pos, 0)

        # PROBLEM: The size of one index record is not always 4096 bytes
        # The actual size of the record is marked in a different attribute
        # The size of an Index Record is defined in the Index Root and is 4 kB by default.
        index_record_allocation_size = 4096
        si = INDEX_ALLOCATION()
        while buff.tell() < size:
            try:
                ir = IndexRecord.from_buffer(buff)
                si.index_records.append(ir)
                # pad to 4 kB
                if buff.tell() % index_record_allocation_size != 0:
                    buff.seek(index_record_allocation_size - (buff.tell() % index_record_allocation_size), 1)
            except:
                print('------ ERRR -------')
                print(test_data[0:200])
                print('------ last chunk -----')
                print(buff.read()[0:200])
                print(buff.tell())
                print(size)
                break
                #raise
        
        return si
    
    def __str__(self):
        res = []
        res.append('INDEX_ALLOCATION')
        for index in self.index_records:
            res.append(str(index))
        return '\n'.join(res)



class IndexRecord:
    def __init__(self):
        self.magic = None
        self.update_seq_offset = None
        self.update_seq_size = None # in WORDs
        self.logfile_seq = None
        self.vcn = None
        self.index_header = None
        self.update_seq = []
        self.entries = []

    @staticmethod
    def from_bytes(data):
        return IndexRecord.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff):
        pos = buff.tell()
        ir = IndexRecord()
        ir.magic = buff.read(4)
        if ir.magic != b'INDX':
            ##### DEBUGGING #####
            print('------ ERRR -------')
            buff.seek(-16, 1)
            print(buff.read()[0:400])
            print(buff.tell())
            input()
            raise Exception('Invalid magic for Index Record! Expected INDX, got {}'.format(ir.magic))
        ir.update_seq_offset = int.from_bytes(buff.read(2), 'little')
        ir.update_seq_size = int.from_bytes(buff.read(2), 'little')
        ir.logfile_seq = int.from_bytes(buff.read(8), 'little')
        ir.vcn = int.from_bytes(buff.read(8), 'little')
        hdrpos = buff.tell()
        ir.index_header = IndexHeader.from_buffer(buff)
        
        buff.seek(pos + ir.update_seq_offset, 0)
        for _ in range(ir.update_seq_size):
            ir.update_seq.append(buff.read(2))
        
        buff.seek(hdrpos + ir.index_header.first_entry_offset, 0)
        while buff.tell() < hdrpos + ir.index_header.first_entry_offset + ir.index_header.index_length:
            entry = IndexEntry.from_buffer(buff)
            ir.entries.append(entry)
            if IndexEntryFlag.LAST_ENTRY in entry.flags:
                break
        return ir
    
    def __str__(self):
        res = []
        res.append('Index Record')
        res.append('Magic: {}'.format(self.magic))
        res.append('Update Seq Offset: {}'.format(self.update_seq_offset))
        res.append('Update Seq Size: {}'.format(self.update_seq_size))
        res.append('Logfile Seq: {}'.format(self.logfile_seq))
        res.append('VCN: {}'.format(self.vcn))
        res.append('Index Header: {}'.format(self.index_header))
        res.append('Update Seq: {}'.format(self.update_seq))
        for entry in self.entries:
            res.append('ENTRY')
            res.append(str(entry))
        return '\n'.join(res)

class IndexEntry:
    def __init__(self):
        self.file_ref = None
        self.file_seq = None
        self.entry_length = None
        self.stream_length = None
        self.flags = None
        self.stream = None
        #self.padding = None documentation claims that padding is present, but it is not or in an unknown logic
        self.sub_node_ref = None
        self.sub_node_seq = None
    
    @staticmethod
    def from_bytes(data):
        return IndexEntry.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff):
        pos = buff.tell()
        ie = IndexEntry()
        ie.file_ref = int.from_bytes(buff.read(6), 'little')
        ie.file_seq = int.from_bytes(buff.read(2), 'little')
        ie.entry_length = int.from_bytes(buff.read(2), 'little')
        ie.stream_length = int.from_bytes(buff.read(2), 'little')
        ie.flags = IndexEntryFlag(int.from_bytes(buff.read(4), 'little'))
        streamstart = buff.tell()

        if IndexEntryFlag.SUB_NODE in ie.flags:
            buff.seek(pos + ie.entry_length - 8, 0)
            ie.sub_node_ref = int.from_bytes(buff.read(8), 'little')
            #ie.sub_node_seq = int.from_bytes(buff.read(2), 'little')
            buff.seek(streamstart, 0)

        if IndexEntryFlag.LAST_ENTRY not in ie.flags:
            stream_size_actual = (pos + ie.entry_length) - streamstart
            if IndexEntryFlag.SUB_NODE in ie.flags:
                stream_size_actual -= 8
            ie.stream = buff.read(stream_size_actual)
            
        buff.seek(pos + ie.entry_length, 0)
        return ie
    
    def __str__(self):
        res = []
        res.append('Index Entry')
        res.append('File Ref: {}'.format(self.file_ref))
        res.append('File Seq: {}'.format(self.file_seq))
        res.append('Entry Length: {}'.format(self.entry_length))
        res.append('Stream Length: {}'.format(self.stream_length))
        res.append('Flags: {}'.format(str(self.flags)))
        res.append('Stream: {}'.format(self.stream))
        res.append('Sub Node Ref: {}'.format(self.sub_node_ref))
        res.append('Sub Node Seq: {}'.format(self.sub_node_seq))
        return '\n'.join(res)

class IndexEntryFlag(enum.IntFlag):
    SUB_NODE = 0x01
    LAST_ENTRY = 0x02

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
    
    def __str__(self):
        res = []
        res.append('Index Header')
        res.append('First Entry Offset: {}'.format(self.first_entry_offset))
        res.append('Index Length: {}'.format(self.index_length))
        res.append('Allocated Size: {}'.format(self.allocated_size))
        res.append('Flags: {}'.format(self.flags))
        res.append('Unused: {}'.format(self.unused))
        return '\n'.join(res)