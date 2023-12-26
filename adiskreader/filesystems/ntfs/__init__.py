
import io
import math
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

    async def read_runs(self, size):
        input('Size: %s' % size)
        buff = io.BytesIO()
        runs = []
        runmap = []
        target_range = range(self.__pos, self.__pos + size)
        start_offset_cluster = None
        start_offset_pos = None
        stop_offset_cluster = None
        stop_offset_pos = None
        start_idx = None
        stop_idx = None
        total_len_ctr = 0
        for r, index in self.__dataruns:
            if start_idx is not None and stop_idx is not None:
                break

            if target_range.start >= r.stop:
                total_len_ctr += len(r)
                continue
            
            if target_range.start in r:
                start_offset_cluster = math.floor((target_range.start - r.start) // self.__fs.cluster_size)
                start_offset_pos = target_range.start - (r.start + (start_offset_cluster * self.__fs.cluster_size))
                start_idx = index
            
            if target_range.stop in r:
                stop_offset_cluster = math.ceil((r.stop - target_range.stop) // self.__fs.cluster_size)
                stop_offset_pos = target_range.stop - (stop_offset_cluster * self.__fs.cluster_size)
                if stop_offset_pos == 0:
                    stop_offset_pos = None
                stop_idx = index
        
        if self.__dataruns[-1][0].stop == target_range.stop:
            stop_offset_cluster = 0
            stop_offset_pos = target_range.stop - (stop_offset_cluster * self.__fs.cluster_size)
            if stop_offset_pos == 0:
                stop_offset_pos = None
            stop_idx = len(self.__dataruns) - 1

        print('runs: %s' % self.__dataattr.header.data_runs)
        print('start_offset_pos: %s' % start_offset_pos)
        print('start_offset_cluster: %s' % start_offset_cluster)
        print('stop_offset_pos: %s' % stop_offset_pos)
        print('stop_offset_cluster: %s' % stop_offset_cluster)
        print('start_idx: %s' % start_idx)
        print('stop_idx: %s' % stop_idx)
        input()

        if start_idx is None or stop_idx is None:
            raise Exception('Invalid data run')

        if start_idx == stop_idx:
            run_offset, run_len = self.__dataattr.header.data_runs[start_idx]
            runmap.append((run_offset + start_offset_cluster, run_len - start_offset_cluster))
        
        else:
            # start run needs to be offset
            start_run_offset, start_run_len = self.__dataattr.header.data_runs[start_idx]
            if start_run_offset == 0:
                runmap.append((0, start_run_len-(start_offset_pos)))
            else:
                runmap.append((start_run_offset + start_offset_cluster, start_run_len-start_offset_cluster))
            
            # middle runs don't need to be offset
            for i in range(start_idx+1, stop_idx):
                run_offset, run_length = self.__dataattr.header.data_runs[i]
                runmap.append((run_offset, run_length))

            # end run needs to be offset
            end_run_offset, end_run_len = self.__dataattr.header.data_runs[stop_idx]
            if end_run_offset == 0:
                runmap.append((0, stop_offset_pos))
            else:
                runmap.append((end_run_offset, end_run_len - stop_offset_cluster))

        print('StartIdx: %s' % start_idx)
        print('StopIdx: %s' % stop_idx)
        input('RunMap: %s' % runmap)
        
        for run_offset, run_length in runmap:
            if run_offset == 0:
                data = b'\x00' * (run_length * self.__fs.cluster_size)
                buff.write(data)
                if buff.tell() + start_offset_pos >= size:
                    break
            
            else:
                async for data in self.__fs.read_sequential_clusters(run_offset, run_length):
                    buff.write(data)
                    if buff.tell() + start_offset_pos >= size:
                        break
        
        buff.seek(start_offset_pos,0)
        data = buff.read(size)
        return data

    async def setup(self):
        # parse the data attribute
        self.__dataattr = self.__fr.get_attribute_by_type(0x80)[0]
        if self.__dataattr.header.non_resident is False:
            self.__buffer.write(self.__dataattr.header.data)
        else:
            # creating mapping of data runs
            prevstart = 0
            for i, x in enumerate(self.__dataattr.header.data_runs):
                run_offset, run_length = x
                self.__dataruns.append((range(prevstart, prevstart + run_length*self.__fs.cluster_size), i))
                prevstart += run_length*self.__fs.cluster_size
            input(self.__dataruns)

    async def tell(self):
        if self.__dataattr.header.non_resident is False:
            return self.__buffer.tell()
        
        return self.__pos
    
    async def seek(self, pos, whence=0):
        if self.__dataattr.header.non_resident is False:
            return self.__buffer.seek(pos, whence)
        else:
            newpos = 0
            if whence == 0:
                newpos = pos
            elif whence == 1:
                newpos = pos + self.__pos
            elif whence == 2:
                newpos = self.__dataattr.header.real_size - pos
            else:
                raise Exception('Invalid whence value')
            
            if newpos < 0 or newpos > self.__dataattr.header.real_size:
                raise Exception('Invalid position')
            self.__pos = newpos

    async def read(self, size=-1):
        if self.__dataattr.header.non_resident is False:
            return self.__buffer.read(size)
        else:
            if size == -1:
                size = self.__dataattr.header.real_size - self.__pos

            data = await self.read_runs(size)
            self.__pos += len(data)
            return data

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

    async def open(self, path, mode = 'rb'):
        if mode != 'rb':
            raise Exception('Only binary read mode is supported')
        inode = await self.mft.find_path(path)
        if inode is None:
            raise Exception('File not found')
        file = NTFSFile(self, inode)
        await file.setup()
        return file

    
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

