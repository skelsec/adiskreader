import math
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
        self.first_lba = None
        self.FileSytemType = None
    
    async def setup(self):
        self.first_lba = await self.disk.read_LBA(self.start_LBA)
        hint = self.first_lba[3:11]
        if hint == b'NTFS    ':
            self.FileSytemType = 'NTFS'
        elif hint.startswith(b'MSDOS') is True:
            self.FileSytemType = 'FAT'
    
    async def mount(self):
        if self.FileSytemType == 'NTFS':
            from adiskreader.filesystems.ntfs import NTFS
            fs = NTFS(self.disk, self.start_LBA)
            await fs.setup()
            return fs
        elif self.FileSytemType == 'FAT':
            from adiskreader.filesystems.fat import FAT
            fs = FAT(self.disk, self.start_LBA)
            await fs.setup()
            return fs
        else:
            try:
                from adiskreader.filesystems.ntfs import NTFS
                fs = NTFS(self.disk, self.start_LBA)
                await fs.setup()
                return fs
            except:
                pass
            try:
                from adiskreader.filesystems.fat import FAT
                fs = FAT(self.disk, self.start_LBA)
                await fs.setup()
                return fs
            except:
                pass
            raise Exception('Unknown partition type!')
    
    @staticmethod
    async def create_empty(disk):
        part = Partition()
        part.disk = disk
        part.start_LBA = 0
        part.end_LBA = 0
        part.size = 0
        part.PartitionName = ''
        await part.setup()
        return part
    
    @staticmethod
    async def from_disk(disk, start_LBA, end_LBA = math.inf, name = 'OFFSET'):
        part = Partition()
        part.disk = disk
        part.start_LBA = start_LBA
        part.end_LBA = end_LBA
        part.size = end_LBA - start_LBA
        part.PartitionName = name
        await part.setup()
        return part

    @staticmethod
    async def from_raw_partition(disk, raw_partition):
        if isinstance(raw_partition, MBRPartitionEntry):
            part = Partition()
            part.disk = disk
            part.start_LBA = raw_partition.FirstLBA
            part.end_LBA = raw_partition.FirstLBA + raw_partition.size
            part.size = raw_partition.size
            part.PartitionName = raw_partition.PartitionName
            await part.setup()
            return part
        
        elif isinstance(raw_partition, GPTPartitionEntry):
            part = Partition()
            part.disk = disk
            part.start_LBA = raw_partition.FirstLBA
            part.end_LBA = raw_partition.LastLBA
            part.size = raw_partition.LastLBA - raw_partition.FirstLBA
            part.PartitionName = raw_partition.PartitionName
            part.PartitionTypeName = raw_partition.PartitionType
            await part.setup()
            return part
        else:
            raise Exception('Unknown partition type')
    
    def __str__(self):
        return 'Partition: {} - {} ({} sectors) {} {}'.format(self.start_LBA, self.end_LBA, self.size, self.PartitionName, self.PartitionTypeName)

class PartitionFinder:
    def __init__(self, disk):
        self.disk = disk
        self.boot_record = None #MBR or GPT
        self.partitions = []
    
    @staticmethod
    async def from_disk(disk):
        pf = PartitionFinder(disk)
        await pf.read_boot_record()
        return pf
    
    async def read_boot_record(self):
        try:
            self.boot_record = await MBR.from_disk(self.disk)
        except:
            self.boot_record = await GPT.from_disk(self.disk)
        else:
            if len(self.boot_record.PartitionEntries) == 1 and self.boot_record.PartitionEntries[0].partition_type == b'\xEE':
                # Fake (but valid) MBR, read GPT instead
                self.boot_record = await GPT.from_disk(self.disk)
        
    
    async def find_partitions(self):
        if len(self.partitions) > 0:
            return self.partitions
        
        if self.boot_record is None:
            await self.read_boot_record()
        if len(self.boot_record.PartitionEntries) == 0:
            # most likely the disk immedialtey starts with a filesystem
            # so there is no MBR/GPT
            part = await Partition.create_empty(self.disk)
            self.partitions.append(part)
        
        for pt in self.boot_record.PartitionEntries:
            part = await Partition.from_raw_partition(self.disk, pt)
            self.partitions.append(part)
        
        
        return self.partitions
    
    def __str__(self):
        t = 'Partitions:\n'
        for p in self.partitions:
            t += '  {}\n'.format(p)
        return t
