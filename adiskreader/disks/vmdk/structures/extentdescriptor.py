import shlex
from adiskreader.disks.vmdk.structures.hostedsparseextent import HostedSparseExtent

class ExtentDescriptor:
    def __init__(self):
        self.access = None
        self.size_in_sector = None
        self.type = None
        self.filename = None
        self.offset = 0

    @staticmethod
    def from_line(line):
        extent = ExtentDescriptor()
        elems = shlex.split(line)
        if len(elems) < 4:
            raise Exception("Invalid extent descriptor")
        extent.access = elems[0].upper()
        extent.size_in_sector = int(elems[1])
        extent.type = elems[2].upper()
        extent.filename = elems[3]
        if len(elems) > 4:
            extent.offset = int(elems[4])
        return extent
    
    def __str__(self):
        return f"ExtentDescriptor({self.access}, {self.size_in_sector}, {self.type}, {self.filename}, {self.offset})"
    
    async def get_extent(self, stream):
        if self.type in ['FLAT', 'VMFS', 'VMFSRDM', 'VMFSRAW']:
            #simple extents
            raise NotImplementedError("FLAT extent type not supported")
        elif self.type == 'ZERO':
            # extents that are all zeroes
            raise NotImplementedError("ZERO extent type not supported")
        elif self.type == 'SPARSE':
            return await HostedSparseExtent.from_descriptor(stream, self)
        elif self.type == 'VMFSSPARSE':
            raise NotImplementedError("VMFSSPARSE extent type not supported")
        else:
            raise Exception("Invalid extent type")