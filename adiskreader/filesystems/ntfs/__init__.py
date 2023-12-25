
import io
from adiskreader.disks import Disk
from adiskreader.partitions import Partition
from adiskreader.filesystems import FileSystem
from adiskreader.filesystems.ntfs.structures.attributes import Attribute
from adiskreader.filesystems.ntfs.structures.filerecord import FileRecord, FileRecordFlags
from adiskreader.filesystems.ntfs.structures.mft import MFT
from adiskreader.filesystems.ntfs.structures.pbs import PBS

# https://flatcap.github.io/linux-ntfs/ntfs/concepts/attribute_header.html

class NTFSFile:
    def __init__(self, fs, fr:FileRecord):
        self.__fs = fs
        self.__fr = fr
        self.__dataattr = None
        self.__dataruns = []
        self.__buffer = io.BytesIO()

        self.__pos = 0

    def runs_to_read(self, size):
        runs = []
        total_size = 0
        pos = 0
        for run_offset, run_length in self.__dataruns:
            len_in_bytes = run_length * self.__fs.cluster_size
            if pos + len_in_bytes < self.__pos:
                pos += len_in_bytes
                continue
            
            if pos + len_in_bytes == self.__pos:
                pos += len_in_bytes
                continue
            
            pos_offset = self.__pos - pos

            if total_size + len_in_bytes < size:
                # we can read the whole run
                runs.append((run_offset, run_length))
                total_size += len_in_bytes
                continue
            
            elif total_size + len_in_bytes == size:
                # we can read the whole run
                runs.append((run_offset, run_length))
                total_size += len_in_bytes
                break

            else:
                # we can read only part of the run
                remaining = size - total_size
                sectors = remaining // self.__fs.cluster_size
                runs.append((run_offset, sectors + 1))
                break

        return runs

    async def setup(self):
        # parse the data attribute
        self.__dataattr = self.__fr.get_attribute_by_type(0x80)[0]
        if self.__dataattr.header.non_resident is False:
            self.__buffer.write(self.__dataattr.header.data)
        else:
            self.__dataruns = self.__dataattr.header.dataruns
    
    async def read(self, size=-1):
        if self.__dataattr.header.non_resident is False:
            return self.__buffer.read(size)
        else:
            if size == -1:
                size = self.__dataattr.header.real_size


            while size == -1 or self.__pos < size:
                self.__pos += 1
                
            for run_offset, run_length in self.data_runs:


    


class NTFS(FileSystem):
    def __init__(self, disk, start_lba):
        self.__start_lba = start_lba
        self.__disk = disk
        self.cluster_size = None
        self.pbs:PBS = None
        self.mft = None
        self.mftmirr = None
    
    async def setup(self):
        self.cluster_size = self.pbs.sectors_per_cluster * self.pbs.bytes_per_sector

    async def read_sector(self, sector_idx):
        lba_idx = self.__start_lba + sector_idx
        data = b''
        while len(data) < self.pbs.bytes_per_sector:
            data += await self.__disk.read_LBA(lba_idx)
        return data[:self.pbs.bytes_per_sector]

    async def read_cluster(self, cluster_idx):
        start_sector_idx = cluster_idx * self.pbs.sectors_per_cluster
        data = b''
        lba_indices = []
        for i in range(self.pbs.sectors_per_cluster):
            lba_indices.append(self.__start_lba + start_sector_idx + i)
        
        data = await self.__disk.read_LBAs(lba_indices)
        return data[:self.cluster_size]
    
    async def read_sequential_clusters(self, cluster_idx, cnt, batch_size=10*1024*1024):
        lba_indices = []
        total_sectors = self.pbs.sectors_per_cluster * cnt

        requested_size = 0
        for i in range(total_sectors):
            requested_size += self.pbs.sectors_per_cluster * self.pbs.bytes_per_sector
            lba = self.__start_lba + (cluster_idx * self.pbs.sectors_per_cluster) + i
            lba_indices.append(lba)

            if requested_size >= batch_size:
                yield await self.__disk.read_LBAs(lba_indices)
                requested_size = 0
                lba_indices = []

        if lba_indices:
            yield await self.__disk.read_LBAs(lba_indices)
    

    async def ls(self, path:str):
        async for entry in self.mft.list_directory(path):
            yield entry

    
    @staticmethod
    async def from_disk(disk:Disk, start_lba:int):
        fs = NTFS(disk, start_lba)
        pbsdata = await disk.read_LBA(start_lba)
        fs.pbs = PBS.from_bytes(pbsdata)
        await fs.setup()
        fs.mft = await MFT.from_filesystem(fs, fs.pbs.mft_cluster)
        
        return fs
    
    @staticmethod
    async def from_partition(partition:Partition):
        return await NTFS.from_disk(partition.disk, partition.start_LBA)
    
    @staticmethod
    def from_bytes(data):
        return NTFS.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        part = NTFS()
        part.pbs = PBS.from_buffer(buff)
        #part.mft = MFT.from_buffer(buff)
        #part.mftmirr = MFT.from_buffer(buff)
        return part

    def __str__(self):
        res = []
        res.append('NTFS Partition')
        res.append('PBS:')
        res.append('  {}'.format(self.pbs))
        res.append('MFT:')
        res.append('  {}'.format(self.mft))
        res.append('MFT Mirror:')
        res.append('  {}'.format(self.mftmirr))
        return '\n'.join(res)

