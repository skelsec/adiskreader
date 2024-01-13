
import io
import logging

logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter(
        '%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


from cachetools import LRUCache
from adiskreader.disks import Disk
from adiskreader.partitions import Partition
from adiskreader.filesystems import FileSystem
from adiskreader.filesystems.ntfs.structures.mft import MFT
from adiskreader.filesystems.ntfs.structures.pbs import PBS

# https://flatcap.github.io/linux-ntfs/ntfs/concepts/attribute_header.html


class NTFS(FileSystem):
    def __init__(self, disk:Disk, start_lba:int):
        self.__start_lba = start_lba
        self.__disk = disk
        self.cluster_size = None
        self.pbs:PBS = None
        self.mft = None
        self.mftmirr = None
        self.__clustercache = LRUCache(maxsize=100)
    
    async def setup(self):
        pbsdata = await self.__disk.read_LBA(self.__start_lba)
        self.pbs = PBS.from_bytes(pbsdata)
        self.cluster_size = self.pbs.sectors_per_cluster * self.pbs.bytes_per_sector
        self.mft = await MFT.from_filesystem(self, self.pbs.mft_cluster)
        

    async def read_sector(self, sector_idx):
        lba_idx = self.__start_lba + sector_idx
        data = b''
        while len(data) < self.pbs.bytes_per_sector:
            data += await self.__disk.read_LBA(lba_idx)
        data = data[:self.pbs.bytes_per_sector]
        return data

    async def read_cluster(self, cluster_idx):
        if cluster_idx in self.__clustercache:
            return self.__clustercache[cluster_idx]
        start_sector_idx = cluster_idx * self.pbs.sectors_per_cluster
        data = b''
        lba_indices = []
        for i in range(self.pbs.sectors_per_cluster):
            lba_indices.append(self.__start_lba + start_sector_idx + i)
        
        data = await self.__disk.read_LBAs(lba_indices)
        data = data[:self.cluster_size]
        self.__clustercache[cluster_idx] = data
        return data
      
    async def read_sequential_clusters(self, cluster_idx, cnt, batch_size=10*1024*1024):
        lba_indices = []
        total_sectors = self.pbs.sectors_per_cluster * cnt

        test_data = b''
        requested_size = 0
        for i in range(total_sectors):
            requested_size += self.pbs.sectors_per_cluster * self.pbs.bytes_per_sector
            lba = self.__start_lba + (cluster_idx * self.pbs.sectors_per_cluster) + i
            lba_indices.append(lba)
    
            if requested_size >= batch_size:
                data = await self.__disk.read_LBAs(lba_indices)
                yield data
                requested_size = 0
                lba_indices = []
    
        if lba_indices:
            yield await self.__disk.read_LBAs(lba_indices)
    

    async def ls(self, path:str):
        async for entry in self.mft.list_directory(path):
            yield entry

    async def stat(self, path:str):
        inode = await self.mft.find_path(path)
        stat = await inode.stat()
        return stat
    
    async def get_record_by_path(self, path):
        inode = await self.mft.find_path(path)
        return inode
    
    async def open(self, path, mode = 'rb'):
        if mode != 'rb':
            raise Exception('Only binary read mode is supported')
        
        # split path into parts
        parts = path.split('\\')
        if parts[0] == '':
            parts = parts[1:]
        if parts[-1] == '':
            parts = parts[:-1]
        
        # removing datastream name
        datastream = ''
        m = parts[-1].find(':')
        if m != -1:
            parts[-1] = parts[-1][:m]
            datastream = parts[-1][m+1:]
        
        inode = await self.mft.find_path('\\'.join(parts))
        if inode is None:
            raise FileNotFoundError
        file = await inode.get_file(datastream)
        return file
    
    async def walk(self, path: str = "\\"):
        async for x in self.mft.walk(path):
            yield x

    
    @staticmethod
    async def from_disk(disk:Disk, start_lba:int):
        fs = NTFS(disk, start_lba)
        await fs.setup()
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

    async def get_root(self):
        return await self.mft.get_inode(5)
