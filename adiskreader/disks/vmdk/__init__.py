import bisect
import ntpath
from typing import List
from adiskreader.disks import Disk
from adiskreader.datasource import DataSource
from adiskreader.disks.vmdk.structures.descriptorfile import DescriptorFile, MAX_SIZE
from adiskreader.disks.vmdk.structures.extentdescriptor import ExtentDescriptor
from adiskreader.disks.vmdk.structures.extent import SECTOR_SIZE, ZeroExtent, FlatExtent
from adiskreader.disks.vmdk.structures.hostedsparseextent import HostedSparseExtent, HOSTED_SPARSE_MAGIC
from adiskreader.disks.vmdk.structures.cowdextent import COWDExtent, COWD_MAGIC
from adiskreader.disks.vmdk.structures.sesparseextent import SESparseExtent, SESPARSE_CONST_MAGIC
from cachetools import LRUCache

EXTENT_TYPES = {
    'FLAT' : FlatExtent,
    'VMFS' : FlatExtent,
    'ZERO' : ZeroExtent,
    'SPARSE' : HostedSparseExtent,
    'VMFSSPARSE' : COWDExtent,
    'SESPARSE' : SESparseExtent,
}

class VMDKDisk(Disk):
    """
    VMDK disk, opened either by its descriptor file or by the sparse extent which has the descriptor embedded.
    The other files of the disk (extents, parent disks of snapshots) are opened from the directory of the first file.
    """
    def __init__(self):
        self.__stream = None
        self.descriptorfile:DescriptorFile = None
        self.parent:VMDKDisk = None
        self.capacity = 0 # in sectors
        self.__extent_starts = []
        self.__extents = {} # index of the extent descriptor -> extent
        self.__siblings = [] # datasources opened by this disk
        self.__lba_cache = LRUCache(maxsize=1000)
    
    async def setup(self, ds:DataSource):
        self.__stream = ds
        await self.__stream.seek(0, 0)
        header = await self.__stream.read(SECTOR_SIZE)
        if header[:4] == HOSTED_SPARSE_MAGIC:
            await self.setup_extent_file(HostedSparseExtent, 'SPARSE')
        elif header[:4] == COWD_MAGIC:
            await self.setup_extent_file(COWDExtent, 'VMFSSPARSE')
        elif int.from_bytes(header[:8], 'little') == SESPARSE_CONST_MAGIC:
            await self.setup_extent_file(SESparseExtent, 'SESPARSE')
        else:
            await self.__stream.seek(0, 0)
            self.descriptorfile = DescriptorFile.from_bytes(await self.__stream.read(MAX_SIZE))

        if len(self.descriptorfile.extentdescriptors) == 0:
            raise Exception("No extent descriptors found")
        for extentdescriptor in self.descriptorfile.extentdescriptors:
            self.__extent_starts.append(self.capacity)
            self.capacity += extentdescriptor.size_in_sector

        if self.descriptorfile.parent_filename is not None:
            self.parent = await VMDKDisk.from_datasource(await self.open_file(self.descriptorfile.parent_filename))

    async def setup_extent_file(self, extenttype, typename):
        # the file is an extent, the descriptor is either embedded in it or the extent is opened on its own
        extentdescriptor = ExtentDescriptor()
        extentdescriptor.type = typename
        extent = extenttype(self.__stream, extentdescriptor)
        await extent.setup()
        self.__extents[0] = extent

        data = None
        if extenttype is HostedSparseExtent:
            data = await extent.read_descriptor()
        if data is None:
            extentdescriptor.access = 'RW'
            extentdescriptor.size_in_sector = extent.capacity
            self.descriptorfile = DescriptorFile()
            self.descriptorfile.extentdescriptors.append(extentdescriptor)
            return

        self.descriptorfile = DescriptorFile.from_bytes(data)
        if len(self.descriptorfile.extentdescriptors) == 1 and self.descriptorfile.extentdescriptors[0].type == typename:
            # the only extent is this file
            extent.descriptor = self.descriptorfile.extentdescriptors[0]
        else:
            del self.__extents[0]

    async def open_file(self, filename:str):
        """Opens another file of the disk, absolute paths are searched in the directory of the descriptor"""
        try:
            ds = await self.__stream.open_sibling(filename)
        except Exception as e:
            basename = ntpath.basename(filename.replace('/', '\\'))
            if basename == filename:
                raise e
            ds = await self.__stream.open_sibling(basename)
        self.__siblings.append(ds)
        return ds

    async def get_extent(self, index:int):
        if index in self.__extents:
            return self.__extents[index]
        extentdescriptor = self.descriptorfile.extentdescriptors[index]
        if extentdescriptor.type not in EXTENT_TYPES:
            raise NotImplementedError('%s extent type not supported' % extentdescriptor.type)
        stream = None
        if extentdescriptor.filename is not None:
            stream = await self.open_file(extentdescriptor.filename)
        extent = EXTENT_TYPES[extentdescriptor.type](stream, extentdescriptor)
        await extent.setup()
        self.__extents[index] = extent
        return extent

    async def close(self):
        """Closes the files opened by the disk, the datasource of the disk is to be closed by the caller"""
        if self.parent is not None:
            await self.parent.close()
            self.parent = None
        for ds in self.__siblings:
            await ds.close()
        self.__siblings = []
        self.__extents = {}
        
    @staticmethod
    async def from_datasource(ds:DataSource):
        disk = VMDKDisk()
        await disk.setup(ds)
        return disk

    async def read_sectors(self, sector:int, count:int):
        """Reads consecutive sectors, the data is shorter if the end of the disk is reached"""
        count = min(count, self.capacity - sector)
        res = []
        while count > 0:
            index = bisect.bisect_right(self.__extent_starts, sector) - 1
            extent_sector = sector - self.__extent_starts[index]
            extent_count = min(count, self.descriptorfile.extentdescriptors[index].size_in_sector - extent_sector)
            extent = await self.get_extent(index)
            for size, data in await extent.read(extent_sector, extent_count):
                if data is None:
                    # not stored in this disk
                    data = b''
                    if self.parent is not None:
                        data = await self.parent.read_sectors(sector, size)
                    data += b'\x00' * (size * SECTOR_SIZE - len(data))
                res.append(data)
                sector += size
                count -= size
        return b''.join(res)

    async def read_LBAs(self, lbas:List[int]):
        res = []
        start = 0
        for i in range(1, len(lbas) + 1):
            if i == len(lbas) or lbas[i] != lbas[i-1] + 1:
                res.append(await self.read_sectors(lbas[start], i - start))
                start = i
        return b''.join(res)
    
    async def read_LBA(self, lba:int):
        if lba in self.__lba_cache:
            return self.__lba_cache[lba]
        data = await self.read_sectors(lba, 1)
        self.__lba_cache[lba] = data
        return data

    def __str__(self):
        t = []
        t.append('Type       : VMDK Disk')
        t.append('Datasource : %s' % self.__stream)
        t.append('Create type: %s' % self.descriptorfile.create_type)
        t.append('Capacity   : %d' % (self.capacity * SECTOR_SIZE))
        t.append('Extents    : %d' % len(self.descriptorfile.extentdescriptors))
        if self.parent is not None:
            t.append('Parent     : %s' % self.descriptorfile.parent_filename)
        return '\n'.join(t)
