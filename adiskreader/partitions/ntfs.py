
import io
# https://flatcap.github.io/linux-ntfs/ntfs/concepts/attribute_header.html

class NTFSClusterReader:
    def __init__(self, partition, start_cluster):
        self.partition = partition
        self.start_cluster = start_cluster
        self.current_cluster = start_cluster
        self.current_offset = 0
        self.data = b''
    
    async def read(self, size):
        if self.current_offset + size > len(self.data):
            self.data += await self.partition.read_cluster(self.current_cluster)
        res = self.data[self.current_offset:self.current_offset+size]
        self.current_offset += size
        return res
    
    async def peek(self, size):
        if self.current_offset + size > len(self.data):
            self.data += await self.partition.read_cluster(self.current_cluster)
        res = self.data[self.current_offset:self.current_offset+size]
        return res

    async def seek(self, offset, whence=0):
        if whence == 0:
            self.current_cluster = self.start_cluster
            self.current_offset = offset
        elif whence == 1:
            self.current_offset += offset
        elif whence == 2:
            raise Exception('whence=2 not implemented')
        else:
            raise Exception('Invalid whence')
    
    def tell(self):
        return self.current_offset
    

class NTFSPartition:
    def __init__(self, disk, start_lba):
        self.__start_lba = start_lba
        self.__disk = disk
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
    
    async def read_cluster(self, cluster):
        OffsetInSectors = cluster * self.pbs.sectors_per_cluster
        lba_idx = self.__start_lba + OffsetInSectors
        return await self.__disk.read_LBA(lba_idx)
    
    def get_cluster_reader(self, cluster):
        return NTFSClusterReader(self, cluster)
    
    @staticmethod
    async def from_disk(disk, start_lba):
        part = NTFSPartition(disk, start_lba)
        pbsdata = await disk.read_LBA(start_lba)
        part.pbs = PBS.from_bytes(pbsdata)
        part.mft = await MFT.from_partition(part, part.pbs.mft_cluster)
        return part
    
    @staticmethod
    def from_bytes(data):
        return NTFSPartition.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        part = NTFSPartition()
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

class MFT:
    def __init__(self):
        self.signature = None
        self.usa_offset = None
        self.usa_count = None
        self.lsn = None
        self.sequence_number = None
        self.link_count = None
        self.attr_offset = None
        self.flags = None
        self.bytes_in_use = None
        self.bytes_allocated = None
        self.base_record = None
        self.next_attr_id = None
        self.record_number = None
        self.attributes = []
    
    @staticmethod
    async def from_partition(partition, start_cluster):
        reader = partition.get_cluster_reader(start_cluster)
        
        mft = MFT()
        mft.signature = await reader.read(4)
        if mft.signature != b'FILE':
            raise Exception('Invalid MFT signature')
        mft.usa_offset = int.from_bytes(await reader.read(2), 'little')
        mft.usa_count = int.from_bytes(await reader.read(2), 'little')
        mft.lsn = int.from_bytes(await reader.read(8), 'little')
        mft.sequence_number = int.from_bytes(await reader.read(2), 'little')
        mft.link_count = int.from_bytes(await reader.read(2), 'little')
        mft.attr_offset = int.from_bytes(await reader.read(2), 'little')
        mft.flags = int.from_bytes(await reader.read(2), 'little')
        mft.bytes_in_use = int.from_bytes(await reader.read(4), 'little')
        mft.bytes_allocated = int.from_bytes(await reader.read(4), 'little')
        mft.base_record = int.from_bytes(await reader.read(8), 'little')
        mft.next_attr_id = int.from_bytes(await reader.read(2), 'little')
        mft.record_number = int.from_bytes(await reader.read(2), 'little')

        await reader.seek(mft.attr_offset, 0)
        while True:
            attr = await Attribute.from_reader(reader)
            input('ATTR %s' % attr)
            print(await reader.peek(100))
            mft.attributes.append(attr)
            if attr.type == 0xFFFFFFFF:
                break
            await reader.seek(attr.length, 1)
        return mft
    
    def __str__(self):
        res = []
        res.append('MFT')
        res.append('Signature: {}'.format(self.signature.hex()))
        res.append('USA Offset: {}'.format(self.usa_offset))
        res.append('USA Count: {}'.format(self.usa_count))
        res.append('LSN: {}'.format(self.lsn))
        res.append('Sequence Number: {}'.format(self.sequence_number))
        res.append('Link Count: {}'.format(self.link_count))
        res.append('Attribute Offset: {}'.format(self.attr_offset))
        res.append('Flags: {}'.format(self.flags))
        res.append('Bytes In Use: {}'.format(self.bytes_in_use))
        res.append('Bytes Allocated: {}'.format(self.bytes_allocated))
        res.append('Base Record: {}'.format(self.base_record))
        res.append('Next Attribute ID: {}'.format(self.next_attr_id))
        res.append('Record Number: {}'.format(self.record_number))
        return '\n'.join(res)

class Attribute:
    def __init__(self):
        self.type = None
        self.length = None
        self.non_resident = None
        self.name_length = None
        self.name_offset = None
        self.flags = None
        self.id = None
        self.attr_length = None
        self.attr_offset = None
        self.indexed_flag = None
        self.padding = None
        self.content = None
        self.name = None
    
    #@staticmethod
    #def from_bytes(data):
    #    return Attribute.from_buffer(io.BytesIO(data))
    
    @staticmethod
    async def from_reader(buff):
        attr = Attribute()
        attr.type = int.from_bytes(await buff.read(4), 'little')
        attr.length = int.from_bytes(await buff.read(4), 'little')
        attr.non_resident = int.from_bytes(await buff.read(1), 'little')
        attr.name_length = int.from_bytes(await buff.read(1), 'little')
        attr.name_offset = int.from_bytes(await buff.read(2), 'little')
        attr.flags = int.from_bytes(await buff.read(2), 'little')
        attr.id = int.from_bytes(await buff.read(2), 'little')
        attr.attr_length = int.from_bytes(await buff.read(4), 'little')
        attr.attr_offset = int.from_bytes(await buff.read(2), 'little')
        attr.indexed_flag = int.from_bytes(await buff.read(1), 'little')
        attr.padding = int.from_bytes(await buff.read(1), 'little')

        if attr.non_resident == 0:
            if attr.name_length == 0:
                #await buff.seek(attr.attr_offset, 0)
                attr.content = STANDARD_INFORMATION.from_bytes(await buff.read(attr.attr_length))
                print(attr.content)
            else:
                await buff.seek(attr.name_offset, 0)
                attr.name = await buff.read(attr.name_length)
                await buff.seek(attr.attr_offset, 0)
                attr.content = await buff.read(attr.attr_length - attr.name_length)
        else:
            print(attr)
            raise Exception('Non resident attribute not implemented')
        return attr

    
    def __str__(self):
        res = []
        res.append('Attribute')
        res.append('Type: {}'.format(self.type))
        res.append('Length: {}'.format(self.length))
        res.append('Non Resident: {}'.format(self.non_resident))
        res.append('Name Length: {}'.format(self.name_length))
        res.append('Name Offset: {}'.format(self.name_offset))
        res.append('Flags: {}'.format(self.flags))
        res.append('ID: {}'.format(self.id))
        res.append('Attribute Length: {}'.format(self.attr_length))
        res.append('Attribute Offset: {}'.format(self.attr_offset))
        res.append('Indexed Flag: {}'.format(self.indexed_flag))
        res.append('Padding: {}'.format(self.padding))
        res.append('Content: {}'.format(self.content))
        res.append('Name: {}'.format(self.name))
        return '\n'.join(res)
    
class NonResidentAttribute:
    def __init__(self):
        self.type = None
        self.length = None
        self.non_resident = None
        self.name_length = None
        self.name_offset = None
        self.flags = None
        self.id = None
        self.reserved = None
        self.start_vcn = None
        self.end_vcn = None
        self.runlist_offset = None
        self.compression_unit_size = None
        self.unused = None
        self.alloc_size = None
        self.real_size = None
        self.initial_size = None
        self.attr_content = None
    
    @staticmethod
    def from_bytes(data):
        return NonResidentAttribute.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        attr = NonResidentAttribute()
        attr.type = int.from_bytes(buff.read(4), 'little')
        attr.length = int.from_bytes(buff.read(4), 'little')
        attr.non_resident = int.from_bytes(buff.read(1), 'little')
        attr.name_length = int.from_bytes(buff.read(1), 'little')
        attr.name_offset = int.from_bytes(buff.read(2), 'little')
        attr.flags = int.from_bytes(buff.read(2), 'little')
        attr.id = int.from_bytes(buff.read(2), 'little')
        attr.reserved = buff.read(2)
        attr.start_vcn = int.from_bytes(buff.read(8), 'little')
        attr.end_vcn = int.from_bytes(buff.read(8), 'little')
        attr.runlist_offset = int.from_bytes(buff.read(2), 'little')
        attr.compression_unit_size = int.from_bytes(buff.read(2), 'little')
        attr.unused = buff.read(4)
        attr.alloc_size = int.from_bytes(buff.read(8), 'little')
        attr.real_size = int.from_bytes(buff.read(8), 'little')
        attr.initial_size = int.from_bytes(buff.read(8), 'little')
        attr.attr_content = buff.read(attr.length - 48)
        return attr

class STANDARD_INFORMATION:
    def __init__(self):
        self.time_created = None
        self.time_modified = None
        self.time_mft_modified = None
        self.time_accessed = None
        self.flags = None
        self.maximum_versions = None
        self.version = None
        self.classid = None
        self.owner_id = None
        self.security_id = None
        self.quota_charged = None
        self.usn = None
    
    @staticmethod
    def from_bytes(data):
        return STANDARD_INFORMATION.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        si = STANDARD_INFORMATION()
        si.time_created = int.from_bytes(buff.read(8), 'little')
        si.time_modified = int.from_bytes(buff.read(8), 'little')
        si.time_mft_modified = int.from_bytes(buff.read(8), 'little')
        si.time_accessed = int.from_bytes(buff.read(8), 'little')
        si.flags = int.from_bytes(buff.read(4), 'little')
        si.maximum_versions = int.from_bytes(buff.read(4), 'little')
        si.version = int.from_bytes(buff.read(4), 'little')
        si.classid = int.from_bytes(buff.read(4), 'little')
        si.owner_id = int.from_bytes(buff.read(4), 'little')
        si.security_id = int.from_bytes(buff.read(4), 'little')
        si.quota_charged = int.from_bytes(buff.read(8), 'little')
        si.usn = int.from_bytes(buff.read(8), 'little')
        return si
    
    def __str__(self):
        res = []
        res.append('Standard Information')
        res.append('Time Created: {}'.format(self.time_created))
        res.append('Time Modified: {}'.format(self.time_modified))
        res.append('Time MFT Modified: {}'.format(self.time_mft_modified))
        res.append('Time Accessed: {}'.format(self.time_accessed))
        res.append('Flags: {}'.format(self.flags))
        res.append('Maximum Versions: {}'.format(self.maximum_versions))
        res.append('Version: {}'.format(self.version))
        res.append('Class ID: {}'.format(self.classid))
        res.append('Owner ID: {}'.format(self.owner_id))
        res.append('Security ID: {}'.format(self.security_id))
        res.append('Quota Charged: {}'.format(self.quota_charged))
        res.append('USN: {}'.format(self.usn))
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
    