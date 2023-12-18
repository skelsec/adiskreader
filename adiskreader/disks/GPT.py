import io
import uuid

# https://en.wikipedia.org/wiki/GUID_Partition_Table
class GPT:
    def __init__(self):
        self.Signature = None
        self.Revision = None
        self.HeaderSize = None
        self.HeaderCRC32 = None
        self.Reserved = None
        self.CurrentLBA = None
        self.BackupLBA = None
        self.FirstUsableLBA = None
        self.LastUsableLBA = None
        self.DiskGUID = None
        self.PartitionEntriesStart = None
        self.NumberOfPartitionEntries = None
        self.SizeOfPartitionEntry = None
        self.PartitionEntryArrayCRC32 = None
        self.PartitionEntries = []
    
    @staticmethod
    async def from_disk(disk):
        hdrdata = await disk.read_LBA(1)
        gpt = GPT.from_bytes(hdrdata)
        ptdatasize = gpt.NumberOfPartitionEntries * gpt.SizeOfPartitionEntry
        ptdata = b''
        i = gpt.PartitionEntriesStart
        while len(ptdata) < ptdatasize:
            ptdata += await disk.read_LBA(i)
            i += 1
        ptdata = io.BytesIO(ptdata)
        for _ in range(gpt.NumberOfPartitionEntries):
            entry = GPTPartitionEntry.from_buffer(ptdata)
            if entry.PartitionTypeGUID == uuid.UUID('00000000-0000-0000-0000-000000000000'):
                continue
            gpt.PartitionEntries.append(entry)
        return gpt

    
    @staticmethod
    def from_bytes(data):
        return GPT.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        gpt = GPT()
        gpt.Signature = buff.read(8)
        if gpt.Signature != b'\x45\x46\x49\x20\x50\x41\x52\x54':
            raise Exception('Invalid GPT signature')
        gpt.Revision = buff.read(4)
        gpt.HeaderSize = int.from_bytes(buff.read(4), 'little')
        if gpt.HeaderSize != 92:
            raise Exception('Invalid GPT header size')
        gpt.HeaderCRC32 = int.from_bytes(buff.read(4), 'little')
        gpt.Reserved = buff.read(4)
        gpt.CurrentLBA = int.from_bytes(buff.read(8), 'little')
        gpt.BackupLBA = int.from_bytes(buff.read(8), 'little')
        gpt.FirstUsableLBA = int.from_bytes(buff.read(8), 'little')
        gpt.LastUsableLBA = int.from_bytes(buff.read(8), 'little')
        gpt.DiskGUID = uuid.UUID(bytes_le= buff.read(16))
        gpt.PartitionEntriesStart = int.from_bytes(buff.read(8), 'little')
        gpt.NumberOfPartitionEntries = int.from_bytes(buff.read(4), 'little')
        gpt.SizeOfPartitionEntry = int.from_bytes(buff.read(4), 'little')
        gpt.PartitionEntryArrayCRC32 = int.from_bytes(buff.read(4), 'little')
        return gpt
    
    def __str__(self):
        res = []
        res.append('GPT')
        res.append('Signature: {}'.format(self.Signature.hex()))
        res.append('Revision: {}'.format(self.Revision.hex()))
        res.append('HeaderSize: {}'.format(self.HeaderSize))
        res.append('HeaderCRC32: {}'.format(self.HeaderCRC32))
        res.append('Reserved: {}'.format(self.Reserved.hex()))
        res.append('CurrentLBA: {}'.format(self.CurrentLBA))
        res.append('BackupLBA: {}'.format(self.BackupLBA))
        res.append('FirstUsableLBA: {}'.format(self.FirstUsableLBA))
        res.append('LastUsableLBA: {}'.format(self.LastUsableLBA))
        res.append('DiskGUID: {}'.format(self.DiskGUID))
        res.append('PartitionEntriesStart: {}'.format(self.PartitionEntriesStart))
        res.append('NumberOfPartitionEntries: {}'.format(self.NumberOfPartitionEntries))
        res.append('SizeOfPartitionEntry: {}'.format(self.SizeOfPartitionEntry))
        res.append('PartitionEntryArrayCRC32: {}'.format(self.PartitionEntryArrayCRC32))
        res.append('PartitionEntries:')
        for pe in self.PartitionEntries:
            res.append('  {}'.format(pe))
        return '\n'.join(res)
    
class GPTPartitionEntry:
    def __init__(self):
        self.PartitionTypeGUID = None
        self.UniquePartitionGUID = None
        self.FirstLBA = None
        self.LastLBA = None
        self.Attributes = None
        self.PartitionName = None
    
    @staticmethod
    def from_bytes(data):
        return GPTPartitionEntry.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        entry = GPTPartitionEntry()
        entry.PartitionTypeGUID = uuid.UUID(bytes_le=buff.read(16))
        entry.UniquePartitionGUID = uuid.UUID(bytes_le=buff.read(16))
        entry.FirstLBA = int.from_bytes(buff.read(8), 'little')
        entry.LastLBA = int.from_bytes(buff.read(8), 'little')
        entry.Attributes = int.from_bytes(buff.read(8), 'little')
        entry.PartitionName = buff.read(72).decode('utf-16-le')
        return entry
    
    def __str__(self):
        res = []
        res.append('GPTPartitionEntry')
        res.append('PartitionTypeGUID: {}'.format(self.PartitionTypeGUID))
        res.append('UniquePartitionGUID: {}'.format(self.UniquePartitionGUID))
        res.append('FirstLBA: {}'.format(self.FirstLBA))
        res.append('LastLBA: {}'.format(self.LastLBA))
        res.append('Attributes: {}'.format(self.Attributes))
        res.append('PartitionName: {}'.format(self.PartitionName))
        return '\n'.join(res)