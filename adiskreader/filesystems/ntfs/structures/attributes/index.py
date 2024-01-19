import io
import enum
from adiskreader.filesystems.ntfs.structures.attributes import Attribute
from adiskreader.filesystems.ntfs.structures.utils import apply_fixups

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
        si.clusters_per_index_record = int.from_bytes(buff.read(1), 'little', signed=True)
        if si.clusters_per_index_record < 0:
            si.clusters_per_index_record = 1 << abs(si.clusters_per_index_record)
        else:
            si.clusters_per_index_record *= si.bytes_per_record
        si.padding = int.from_bytes(buff.read(3), 'little')
        hdrpos = buff.tell()
        si.index_header = IndexHeader.from_buffer(buff)
        # Seek to the first index entry
        buff.seek(hdrpos + si.index_header.first_entry_offset, 0)
        
        # Read all index entries
        # Added multiple safeguards to prevent infinite loops
        pos_safegueard = buff.tell()
        while buff.tell() < hdrpos + si.index_header.index_length:
            entry = IndexEntry.from_buffer(buff)
            if buff.tell() == pos_safegueard:
                # no progress made, break out of loop
                break

            si.index_entries.append(entry)
            # correct padding
            if (buff.tell() % si.bytes_per_record) != 0:
                buff.seek(si.bytes_per_record - (buff.tell() % si.bytes_per_record), 1)

            if IndexEntryFlag.LAST_ENTRY in entry.flags:
                break
        return si

    def __str__(self):
        res = []
        res.append('Index Root')
        res.append('Header: {}'.format(self.header))
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
        si = INDEX_ALLOCATION()
        si.header = header
        # not parsing data here because we need the index record size
        # this must be obtained from the INDEX_ROOT attribute
        return si
    
    #def read_indicies(self, index_record_size, fs):
    #    buffer = io.BytesIO(self.header.data)
    #    buffer.seek(0, 0)
    #    while buffer.tell() < self.header.real_size:
    #        recdata = buffer.read(index_record_size)
    #        if recdata == b'\x00' * index_record_size:
    #            # empty index record
    #            continue
    #        if recdata == b'':
    #            # end of data
    #            break
    #        ir = IndexRecord.from_bytes(recdata, fs)
    #        yield ir
    
    async def read_indicies(self, index_record_size, fs):
        # this record is ALWAYS non-resident
        # therefore, we need to read the data from the data runs
        # and parse the index records from there
        data = b''
        for cluster, size in self.header.data_runs:
            for i in range(size):
                data += await fs.read_cluster(cluster+i)
                while len(data) >= index_record_size:
                    recdata = data[0:index_record_size]
                    data = data[index_record_size:]
                    if recdata == b'\x00' * index_record_size:
                        # empty index record
                        continue
                    if recdata == b'':
                        # end of data
                        break
                    ir = IndexRecord.from_bytes(recdata, fs)
                    yield ir
    
    def __str__(self):
        res = []
        res.append('INDEX_ALLOCATION')
        res.append('Header: {}'.format(self.header))
        for index in self.index_records:
            res.append(str(index))
        return '\n'.join(res)


class IndexRecord:
    def __init__(self):
        self.magic = None
        self.usa_offset = None
        self.usa_count = None # in WORDs
        self.logfile_seq = None
        self.vcn = None
        self.index_header = None
        self.update_seq = []
        self.entries = []

    @staticmethod
    def from_bytes(data, fs):
        return IndexRecord.from_buffer(io.BytesIO(data), fs)

    @staticmethod
    def from_buffer(buff, fs):
        pos = buff.tell()
        ir = IndexRecord()
        ir.magic = buff.read(4)
        if ir.magic != b'INDX':
            ##### DEBUGGING #####
            print('------ ERRR (IndexRecord) -------')
            buff.seek(-16, 1)
            print(buff.read()[0:400])
            print(buff.tell())
            input()
            raise Exception('Invalid magic for Index Record! Expected INDX, got {}'.format(ir.magic))
        ir.usa_offset = int.from_bytes(buff.read(2), 'little')
        ir.usa_count = int.from_bytes(buff.read(2), 'little')
        ir.logfile_seq = int.from_bytes(buff.read(8), 'little')
        ir.vcn = int.from_bytes(buff.read(8), 'little')
        hdrpos = buff.tell()
        ir.index_header = IndexHeader.from_buffer(buff)
        
        ir.update_seq = apply_fixups(buff, ir.usa_offset, ir.usa_count, start_pos=pos, bytes_per_sector=fs.pbs.bytes_per_sector, validate_checksum=False)

        
        buff.seek(hdrpos + ir.index_header.first_entry_offset, 0)
        while buff.tell() < hdrpos + ir.index_header.first_entry_offset + ir.index_header.index_length:
        #while True:
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
            ie.sub_node_ref = int.from_bytes(buff.read(6), 'little')
            ie.sub_node_seq = int.from_bytes(buff.read(2), 'little')
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