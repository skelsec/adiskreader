
# VDI (VirtualBox) disk reader

import enum
import struct
import uuid
from typing import List
from adiskreader import logger
from adiskreader.disks import Disk
from adiskreader.datasource import DataSource
from cachetools import LRUCache

SECTOR_SIZE = 512
VDI_SIGNATURE = 0xBEDA107F
PREHEADER_SIZE = 72
HEADER0_SIZE = 348
MAX_BLOCKS = 0x20000000 # as VirtualBox has it

BLOCK_FREE = 0xFFFFFFFF # not stored in this file
BLOCK_ZERO = 0xFFFFFFFE
NULL_UUID = uuid.UUID(int=0)

class VDIType(enum.IntEnum):
    NORMAL = 1 # dynamically allocated
    FIXED = 2
    UNDO = 3
    DIFF = 4


class VDIHeader:
    def __init__(self):
        self.text:str = None
        self.version = (0, 0)
        self.type:VDIType = None
        self.flags = 0
        self.comment:str = None
        self.blocks_offset = 0 # of the block map
        self.data_offset = 0
        self.disk_size = 0
        self.block_size = 0
        self.block_extra_size = 0 # bytes before the data of each block
        self.block_count = 0
        self.blocks_allocated = 0
        self.uuid:uuid.UUID = None
        self.uuid_modify:uuid.UUID = None
        self.uuid_parent:uuid.UUID = None
        self.uuid_parent_modify:uuid.UUID = None

    @staticmethod
    def from_bytes(data:bytes):
        if len(data) < PREHEADER_SIZE + HEADER0_SIZE or struct.unpack_from('<I', data, 64)[0] != VDI_SIGNATURE:
            raise Exception('Not a VDI file')
        header = VDIHeader()
        header.text = data[:64].split(b'\x00')[0].decode('latin-1').strip()
        version = struct.unpack_from('<I', data, 68)[0]
        header.version = (version >> 16, version & 0xFFFF)
        pos = PREHEADER_SIZE
        if header.version[0] == 1:
            # size of the header, not needed for the fields used here
            pos += 4
        elif header.version[0] != 0:
            raise NotImplementedError('VDI version %s.%s is not supported' % header.version)

        disktype, header.flags = struct.unpack_from('<II', data, pos)
        header.type = VDIType(disktype)
        header.comment = data[pos+8:pos+264].split(b'\x00')[0].decode('utf-8', errors='replace')
        pos += 264
        if header.version[0] == 1:
            header.blocks_offset, header.data_offset = struct.unpack_from('<II', data, pos)
            pos += 8
        # legacy geometry: cylinders, heads, sectors, sector size
        pos += 16
        if header.version[0] == 1:
            pos += 4
        header.disk_size, header.block_size = struct.unpack_from('<QI', data, pos)
        pos += 12
        if header.version[0] == 1:
            header.block_extra_size = struct.unpack_from('<I', data, pos)[0]
            pos += 4
        header.block_count, header.blocks_allocated = struct.unpack_from('<II', data, pos)
        pos += 8
        header.uuid = uuid.UUID(bytes_le=data[pos:pos+16])
        header.uuid_modify = uuid.UUID(bytes_le=data[pos+16:pos+32])
        header.uuid_parent = uuid.UUID(bytes_le=data[pos+32:pos+48])
        if header.version[0] == 1:
            header.uuid_parent_modify = uuid.UUID(bytes_le=data[pos+48:pos+64])
        else:
            # the block map and the data follow the header
            header.blocks_offset = PREHEADER_SIZE + HEADER0_SIZE
            header.data_offset = header.blocks_offset + header.block_count * 4

        if header.block_size == 0 or header.block_size % SECTOR_SIZE != 0 or header.block_count > MAX_BLOCKS:
            raise Exception('Invalid VDI header')
        if header.block_count * header.block_size < header.disk_size:
            raise Exception('Invalid VDI header, the disk is larger than its blocks')
        return header


class VDIDisk(Disk):
    """
    VDI disk (dynamic, fixed or differencing).
    The path of the parent is not stored in the differencing disks (VirtualBox keeps it in the settings of the machine),
    so the parent is to be given: either with set_parent, or by opening all the files of the chain with from_chain.
    """
    def __init__(self):
        self.__stream = None
        self.header:VDIHeader = None
        self.parent:Disk = None
        self.capacity = 0 # in sectors
        self.block_map = []
        self.__warned = False
        self.__lba_cache = LRUCache(maxsize=1000)

    async def setup(self, ds:DataSource):
        self.__stream = ds
        await self.__stream.seek(0, 0)
        self.header = VDIHeader.from_bytes(await self.__stream.read(1024))
        self.capacity = self.header.disk_size // SECTOR_SIZE
        await self.__stream.seek(self.header.blocks_offset, 0)
        data = await self.__stream.read(self.header.block_count * 4)
        if len(data) != self.header.block_count * 4:
            raise Exception('The VDI file is truncated')
        self.block_map = struct.unpack('<%dI' % self.header.block_count, data)

    @staticmethod
    async def from_datasource(ds:DataSource, parent:Disk = None):
        disk = VDIDisk()
        await disk.setup(ds)
        if parent is not None:
            disk.set_parent(parent)
        return disk

    @staticmethod
    async def from_chain(datasources:List[DataSource]):
        """
        Opens a differencing disk together with its parents, the datasources of the files can be in any order.
        Returns the disk which is not the parent of any other one.
        """
        disks = [await VDIDisk.from_datasource(ds) for ds in datasources]
        by_uuid = {disk.header.uuid: disk for disk in disks}
        parents = []
        for disk in disks:
            if disk.needs_parent() is True:
                parent = by_uuid.get(disk.header.uuid_parent)
                if parent is None or parent is disk:
                    raise Exception('The parent of the VDI disk %s is not found: %s' % (disk.header.uuid, disk.header.uuid_parent))
                disk.set_parent(parent)
                parents.append(parent)
        leaves = [disk for disk in disks if not any(disk is parent for parent in parents)]
        if len(leaves) != 1:
            raise Exception('The VDI disks are not one chain')
        return leaves[0]

    def needs_parent(self):
        return self.header.type in (VDIType.DIFF, VDIType.UNDO) and self.header.uuid_parent != NULL_UUID

    def set_parent(self, parent:Disk):
        """Sets the disk this one stores the changes of, it can be of any disk type. The parent is not closed by this disk."""
        if isinstance(parent, VDIDisk) and parent.header.uuid != self.header.uuid_parent:
            logger.warning('The parent disk %s is not the one the disk was created from, data might be inconsistent' % parent.header.uuid)
        self.parent = parent

    async def close(self):
        """The datasources of the disk and its parents are to be closed by the caller"""
        self.parent = None

    async def read_sectors(self, sector:int, count:int):
        """Reads consecutive sectors, the data is shorter if the end of the disk is reached"""
        count = min(count, self.capacity - sector)
        if count <= 0:
            return b''
        if self.parent is None and self.__warned is False and self.needs_parent() is True:
            self.__warned = True
            logger.warning('The VDI disk is a differencing disk without its parent, only the changed blocks are read')

        header = self.header
        res = []
        offset = sector * SECTOR_SIZE
        end = offset + count * SECTOR_SIZE
        while offset < end:
            index = offset // header.block_size
            start = offset % header.block_size
            size = min(end - offset, header.block_size - start)
            block = self.block_map[index]
            if block == BLOCK_FREE and self.parent is not None:
                # the parent can be smaller than the disk
                data = await self.parent.read_sectors(offset // SECTOR_SIZE, size // SECTOR_SIZE)
            elif block in (BLOCK_FREE, BLOCK_ZERO):
                data = b''
            else:
                # the blocks next to each other in the file are read together
                while offset + size < end and self.block_map[index + 1] == self.block_map[index] + 1 and header.block_extra_size == 0:
                    index += 1
                    size += min(end - offset - size, header.block_size)
                await self.__stream.seek(header.data_offset + block * (header.block_size + header.block_extra_size) + header.block_extra_size + start, 0)
                data = await self.__stream.read(size)
            res.append(data + b'\x00' * (size - len(data)))
            offset += size
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
        t.append('Type       : VDI Disk')
        t.append('Datasource : %s' % self.__stream)
        t.append('Disk type  : %s' % self.header.type.name)
        t.append('Capacity   : %d' % (self.capacity * SECTOR_SIZE))
        if self.needs_parent() is True:
            t.append('Parent     : %s' % self.header.uuid_parent)
        return '\n'.join(t)
