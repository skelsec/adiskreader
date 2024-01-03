from adiskreader.partitions.MBR import MBR, MBRPartitionEntry
from adiskreader.partitions.GPT import GPT, GPTPartitionEntry

class Partition:
    def __init__(self):
        self.disk = None
        self.start_LBA = None
        self.end_LBA = None
        self.size = None
        self.PartitionName = None
        self.PartitionTypeName = ''
    
    async def guess_filesystem(self, disk):
        first_lba = await disk.read_LBA(self.start_LBA)
        print(first_lba)


    @staticmethod
    def from_raw_partition(disk, raw_partition):
        if isinstance(raw_partition, MBRPartitionEntry):
            part = Partition()
            part.disk = disk
            part.start_LBA = raw_partition.FirstLBA
            part.end_LBA = raw_partition.FirstLBA + raw_partition.size
            part.size = raw_partition.size
            part.PartitionName = raw_partition.PartitionName
            return part
        
        elif isinstance(raw_partition, GPTPartitionEntry):
            part = Partition()
            part.disk = disk
            part.start_LBA = raw_partition.FirstLBA
            part.end_LBA = raw_partition.LastLBA
            part.size = raw_partition.LastLBA - raw_partition.FirstLBA
            part.PartitionName = raw_partition.PartitionName
            part.PartitionTypeName = raw_partition.PartitionType
            return part
        else:
            raise Exception('Unknown partition type')
    
    def __str__(self):
        return 'Partition: {} - {} ({} sectors) {} {}'.format(self.start_LBA, self.end_LBA, self.size, self.PartitionName, self.PartitionTypeName)

class Partitions:
    def __init__(self, disk):
        self.disk = disk
        self.boot_record = None #MBR or GPT
        self.partitions = []
    
    async def read_boot_record(self):
        data = await self.disk.read_LBA(0)
        try:
            self.boot_record = MBR.from_bytes(data)
        except:
            self.boot_record = await GPT.from_disk(self.disk)
        else:
            if len(self.boot_record.partition_table) == 1 and self.boot_record.partition_table[0].partition_type == b'\xEE':
                # Fake (but valid) MBR, read GPT instead
                self.boot_record = await GPT.from_disk(self.disk)
        
    
    async def find_partitions(self):
        if len(self.partitions) > 0:
            return self.partitions
        
        if self.boot_record is None:
            await self.read_boot_record()
        if isinstance(self.boot_record, MBR):
            for pt in self.boot_record.partition_table:
                self.partitions.append(Partition.from_raw_partition(self.disk, pt))
        elif isinstance(self.boot_record, GPT):
            for pt in self.boot_record.PartitionEntries:
                self.partitions.append(Partition.from_raw_partition(self.disk, pt))
        else:
            raise Exception('Unknown boot record type')
        
        return self.partitions
    
    def __str__(self):
        t = 'Partitions:\n'
        for p in self.partitions:
            t += '  {}\n'.format(p)
        return t
