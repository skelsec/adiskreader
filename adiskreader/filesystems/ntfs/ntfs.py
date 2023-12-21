
import io
from adiskreader.filesystems.ntfs.attributes import Attribute
from adiskreader.filesystems.ntfs.filerecord import FileRecord, FileRecordFlags
from adiskreader.filesystems.ntfs.mft import MFT

# https://flatcap.github.io/linux-ntfs/ntfs/concepts/attribute_header.html

class NTFSClusterReader:
    def __init__(self, ntfs, start_cluster):
        self.ntfs = ntfs
        self.start_cluster = start_cluster
        self.current_cluster = start_cluster
        self.current_offset = 0
        self.cluster_size = ntfs.pbs.sectors_per_cluster * ntfs.pbs.bytes_per_sector
        self.data = b''

    async def _load_cluster(self, cluster_number):
        # Load the specified cluster's data
        return await self.ntfs.read_cluster(cluster_number)

    async def read(self, size):
        while self.current_offset + size > len(self.data):
            # Load next cluster if needed
            self.data += await self._load_cluster(self.current_cluster)
            self.current_cluster += 1

        res = self.data[self.current_offset:self.current_offset + size]
        self.current_offset += size
        return res

    async def peek(self, size):
        if self.current_offset + size > len(self.data):
            self.data += await self._load_cluster(self.current_cluster)
        return self.data[self.current_offset:self.current_offset + size]

    async def seek(self, offset, whence=0):
        if whence == 0:
            # Absolute positioning
            self.current_cluster = self.start_cluster + (offset // self.cluster_size)
            self.current_offset = offset % self.cluster_size
            self.data = await self._load_cluster(self.current_cluster)
        elif whence == 1:
            # Relative positioning
            self.current_offset += offset
            while self.current_offset >= len(self.data):
                self.data += await self._load_cluster(self.current_cluster)
                self.current_cluster += 1
        elif whence == 2:
            raise Exception('whence=2 not implemented')
        else:
            raise Exception('Invalid whence')

    def tell(self):
        return self.current_offset

    

class NTFS:
    def __init__(self, disk, start_lba):
        self.__start_lba = start_lba
        self.__disk = disk
        self.cluster_size = None
        self.pbs:PBS = None
        self.mft = None
        self.mftmirr = None
        self.logfile = None
        self.volume = None
        self.attrdef = None
        self.root = None
        self.bitmap = None
        self.boot = None
        self.badclus = None
        self.secure = None
        self.upcase = None
        self.extend = None
    
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
        #input('read_sequential_clusters %s %s' % (cluster_idx, cnt))
        lba_indices = []
        total_sectors = self.pbs.sectors_per_cluster * cnt

        #print('Data to be read: %s sectors' % ((total_sectors * self.pbs.bytes_per_sector)/1024/1024))
        #input('total_sectors %s' % total_sectors)
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
            
    
    def get_cluster_reader(self, cluster):
        return NTFSClusterReader(self, cluster)
    
    @staticmethod
    async def from_disk(disk, start_lba):
        fs = NTFS(disk, start_lba)
        pbsdata = await disk.read_LBA(start_lba)
        fs.pbs = PBS.from_bytes(pbsdata)
        await fs.setup()
        fs.mft = await MFT.from_filesystem(fs, fs.pbs.mft_cluster)
        
        return fs
    
    @staticmethod
    async def from_partition(partition):
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
        #part.logfile = MFT.from_buffer(buff)
        #part.volume = MFT.from_buffer(buff)
        #part.attrdef = MFT.from_buffer(buff)
        #part.root = MFT.from_buffer(buff)
        #part.bitmap = MFT.from_buffer(buff)
        #part.boot = MFT.from_buffer(buff)
        #part.badclus = MFT.from_buffer(buff)
        #part.secure = MFT.from_buffer(buff)
        #part.upcase = MFT.from_buffer(buff)
        #part.extend = MFT.from_buffer(buff)
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
        res.append('Log File:')
        res.append('  {}'.format(self.logfile))
        res.append('Volume:')
        res.append('  {}'.format(self.volume))
        res.append('Attribute Definition:')
        res.append('  {}'.format(self.attrdef))
        res.append('Root:')
        res.append('  {}'.format(self.root))
        res.append('Bitmap:')
        res.append('  {}'.format(self.bitmap))
        res.append('Boot:')
        res.append('  {}'.format(self.boot))
        res.append('Bad Clusters:')
        res.append('  {}'.format(self.badclus))
        res.append('Secure:')
        res.append('  {}'.format(self.secure))
        res.append('Upcase:')
        res.append('  {}'.format(self.upcase))
        res.append('Extend:')
        res.append('  {}'.format(self.extend))
        return '\n'.join(res)


class PBS:
    def __init__(self):
        self.jump_instruction = None
        self.oem_id = None
        self.bytes_per_sector = None
        self.sectors_per_cluster = None
        self.reserved_sectors = None
        self.unused = None #unused
        self.unused2 = None #unused
        self.media_descriptor = None
        self.unused3 = None
        self.sectors_per_track = None
        self.number_of_heads = None
        self.hidden_sectors = None
        self.unused4 = None
        self.unused5 = None
        self.total_sectors = None
        self.mft_cluster = None
        self.mft_cluster_mirror = None
        self.bytes_per_record = None
        self.unused6 = None
        self.bytes_per_index_buffer = None
        self.unused7 = None
        self.volume_serial_number = None
        self.unused8 = None
        self.boot_code = None
        self.boot_sector_signature = None
    
    @staticmethod
    def from_bytes(data):
        return PBS.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        pbs = PBS()
        pbs.jump_instruction = buff.read(3)
        pbs.oem_id = buff.read(8)
        if pbs.oem_id != b'NTFS    ':
            raise Exception('Invalid NTFS oem id')
        
        pbs.bytes_per_sector = int.from_bytes(buff.read(2), 'little')
        pbs.sectors_per_cluster = int.from_bytes(buff.read(1), 'little')
        pbs.reserved_sectors = int.from_bytes(buff.read(2), 'little')
        pbs.unused = int.from_bytes(buff.read(3), 'little')
        pbs.unused2 = int.from_bytes(buff.read(2), 'little')
        pbs.media_descriptor = int.from_bytes(buff.read(1), 'little')
        pbs.unused3 = int.from_bytes(buff.read(2), 'little')
        pbs.sectors_per_track = int.from_bytes(buff.read(2), 'little')
        pbs.number_of_heads = int.from_bytes(buff.read(2), 'little')
        pbs.hidden_sectors = int.from_bytes(buff.read(4), 'little')
        pbs.unused4 = int.from_bytes(buff.read(4), 'little')
        pbs.unused5 = int.from_bytes(buff.read(4), 'little')
        pbs.total_sectors = int.from_bytes(buff.read(8), 'little')
        pbs.mft_cluster = int.from_bytes(buff.read(8), 'little')
        pbs.mft_cluster_mirror = int.from_bytes(buff.read(8), 'little')
        pbs.bytes_per_record = int.from_bytes(buff.read(1), 'little')
        pbs.unused6 = int.from_bytes(buff.read(3), 'little')
        pbs.bytes_per_index_buffer = int.from_bytes(buff.read(1), 'little')
        pbs.unused7 = int.from_bytes(buff.read(3), 'little')
        pbs.volume_serial_number = int.from_bytes(buff.read(8), 'little')
        pbs.unused8 = buff.read(4)
        pbs.boot_code = buff.read(426)
        pbs.boot_sector_signature = buff.read(2)
        if pbs.boot_sector_signature != b'\x55\xAA':
            raise Exception('Invalid boot sector signature')
        return pbs
    
    def __str__(self):
        res = []
        res.append('Jump Instruction: {}'.format(self.jump_instruction.hex()))
        res.append('OEM ID: {}'.format(self.oem_id.decode()))
        res.append('Bytes Per Sector: {}'.format(self.bytes_per_sector))
        res.append('Sectors Per Cluster: {}'.format(self.sectors_per_cluster))
        res.append('Reserved Sectors: {}'.format(self.reserved_sectors))
        res.append('Unused: {}'.format(self.unused))
        res.append('Unused2: {}'.format(self.unused2))
        res.append('Media Descriptor: {}'.format(self.media_descriptor))
        res.append('Unused3: {}'.format(self.unused3))
        res.append('Sectors Per Track: {}'.format(self.sectors_per_track))
        res.append('Number Of Heads: {}'.format(self.number_of_heads))
        res.append('Hidden Sectors: {}'.format(self.hidden_sectors))
        res.append('Unused4: {}'.format(self.unused4))
        res.append('Unused5: {}'.format(self.unused5))
        res.append('Total Sectors: {}'.format(self.total_sectors))
        res.append('MFT Cluster: {}'.format(self.mft_cluster))
        res.append('MFT Cluster Mirror: {}'.format(self.mft_cluster_mirror))
        res.append('Bytes Per Record: {}'.format(self.bytes_per_record))
        res.append('Unused6: {}'.format(self.unused6))
        res.append('Bytes Per Index Buffer: {}'.format(self.bytes_per_index_buffer))
        res.append('Unused7: {}'.format(self.unused7))
        res.append('Volume Serial Number: {}'.format(self.volume_serial_number))
        res.append('Unused8: {}'.format(self.unused8.hex()))
        res.append('Boot Code: {}'.format(self.boot_code.hex()))
        res.append('Boot Sector Signature: {}'.format(self.boot_sector_signature.hex()))
        return '\n'.join(res)
    