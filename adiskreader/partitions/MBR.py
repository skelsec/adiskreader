import io

# https://en.wikipedia.org/wiki/Master_boot_record
class MBR:
    def __init__(self):
        self.boot_code_1 = None
        self.boot_code_2 = None
        self.timestamp = None
        self.disk_signature = None
        self.partition_table = []
        self.boot_signature = None

    @staticmethod
    def from_bytes(data):
        return MBR.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        mbr = MBR()
        mbr.boot_code_1 = buff.read(218)
        mbr.timestamp = buff.read(6)
        mbr.boot_code_2 = buff.read(222)
        mbr.timestamp = buff.read(4)
        mbr.disk_signature = buff.read(6)
        buff.seek(0x01BE,0)
        for i in range(4):
            pt = MBRPartitionEntry.from_buffer(buff)
            if pt.partition_type == b'\x00':
                continue
            mbr.partition_table.append(pt)
        mbr.boot_signature = buff.read(2)
        if mbr.boot_signature != b'\x55\xAA':
            raise Exception('Invalid boot signature')
        return mbr
    
    def __str__(self):
        res = []
        res.append('MBR')
        res.append('Boot Code 1: {}'.format(self.boot_code_1.hex()))
        res.append('Timestamp: {}'.format(self.timestamp.hex()))
        res.append('Boot Code 2: {}'.format(self.boot_code_2.hex()))
        res.append('Disk Signature: {}'.format(self.disk_signature.hex())) #disk_signature
        res.append('Partition Table:')
        for pt in self.partition_table:
            res.append('  {}'.format(pt))
        res.append('Boot Signature: {}'.format(self.boot_signature.hex()))
        return '\n'.join(res)
    
class MBRPartitionEntry:
    def __init__(self):
        self.status = None
        self.start_chs = None
        self.partition_type = None
        self.end_chs = None
        self.FirstLBA = None
        self.size = None
        self.PartitionName = '' #for compat with GPTPartitionEntry

    @staticmethod
    def from_bytes(data):
        return MBRPartitionEntry.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        entry = MBRPartitionEntry()
        entry.status = buff.read(1)
        entry.start_chs = buff.read(3)
        entry.partition_type = buff.read(1)
        entry.end_chs = buff.read(3)
        entry.FirstLBA = int.from_bytes(buff.read(4), 'little', signed=False)
        entry.size = int.from_bytes(buff.read(4), 'little', signed=False)
        return entry
    
    def __str__(self):
        res = []
        res.append('MBR Partition Entry')
        res.append('Status: {}'.format(self.status.hex()))
        res.append('Start CHS: {}'.format(self.start_chs.hex()))
        res.append('Partition Type: {}'.format(self.partition_type.hex()))
        res.append('End CHS: {}'.format(self.end_chs.hex()))
        res.append('FirstLBA: {}'.format(self.FirstLBA))
        res.append('Size: {}'.format(self.size))
        return '\n'.join(res)