
# QCOW2 disk reader

import zlib
from typing import List
from adiskreader import logger
from adiskreader import compression
from adiskreader.disks import Disk
from adiskreader.disks.parent import open_parent_disk
from adiskreader.datasource import DataSource
from adiskreader.disks.qcow2.structures import *
from cachetools import LRUCache

SECTOR_SIZE = 512

# what a part of the disk is read from
RUN_DATA = 0         # the data file, at the given offset
RUN_ZERO = 1
RUN_UNALLOCATED = 2  # the backing file, zeroes if there is none
RUN_COMPRESSED = 3   # a compressed cluster, described by its L2 entry

def get_zstd_decompressor():
    """Returns a function decompressing one zstd frame"""
    return compression.get_zstd_decompressor()

def inflate(data:bytes, size:int):
    # raw deflate stream, the bytes after its end belong to other clusters
    return zlib.decompressobj(-15).decompress(data, size)


class BackingDisk:
    """Opens the backing file of a QCOW2 disk, which can be of any disk type"""
    def __init__(self, backing_format:str = None):
        self.backing_format = backing_format

    async def from_datasource(self, ds:DataSource):
        from adiskreader.disks import detect_disk_type
        if self.backing_format is None:
            disktype = await detect_disk_type(ds)
        elif self.backing_format == 'qcow2':
            disktype = QCOW2Disk
        elif self.backing_format == 'vmdk':
            from adiskreader.disks.vmdk import VMDKDisk as disktype
        elif self.backing_format == 'vpc':
            from adiskreader.disks.vhd import VHDDisk as disktype
        elif self.backing_format == 'vhdx':
            from adiskreader.disks.vhdx import VHDXDisk as disktype
        elif self.backing_format == 'vdi':
            from adiskreader.disks.vdi import VDIDisk as disktype
        elif self.backing_format in ('raw', 'file'):
            disktype = None
        else:
            raise NotImplementedError('Backing file format %s is not supported' % self.backing_format)
        if disktype is None:
            from adiskreader.disks.raw import RAWDisk as disktype
        return await disktype.from_datasource(ds)


class QCOW2Disk(Disk):
    """
    QCOW2 disk (version 2 and 3).
    The backing file and the external data file are opened from the directory of the file, using the names stored in it.
    The disk is read as it is now, the state saved in a snapshot is read from the disk returned by open_snapshot.
    """
    def __init__(self):
        self.__stream = None
        self.__data_stream = None # the clusters are stored here, the file itself unless there is an external data file
        self.header:QCOW2Header = None
        self.parent:Disk = None
        self.snapshots:List[QCOW2Snapshot] = []
        self.snapshot:QCOW2Snapshot = None # set if this disk is the state saved in a snapshot
        self.capacity = 0 # in sectors
        self.size = 0 # in bytes
        self.cluster_size = 0
        self.L1 = []
        self.__l2_entry_size = 8
        self.__l2_bits = 0 # number of bits of the L2 index
        self.__subclusters = 1
        self.__data_file_raw = False
        self.__decompress = inflate
        self.__owner = True # the disks of the snapshots use the files of the disk they were opened from
        self.__siblings = [] # datasources opened by this disk
        self.__l2_cache = LRUCache(maxsize=128)
        self.__cluster_cache = LRUCache(maxsize=16)
        self.__lba_cache = LRUCache(maxsize=1000)

    async def setup(self, ds:DataSource):
        self.__stream = ds
        self.__data_stream = ds
        await self.read_header()
        await self.read_snapshots()
        self.set_size(self.header.size)
        self.L1 = await self.read_l1(self.header.l1_table_offset, self.header.l1_size)
        if self.header.incompatible_features & INCOMPAT_DATA_FILE:
            await self.open_data_file()
        if self.header.backing_file is not None:
            await self.open_parent()

    @staticmethod
    async def from_datasource(ds:DataSource):
        disk = QCOW2Disk()
        await disk.setup(ds)
        return disk

    async def read_at(self, offset:int, size:int):
        await self.__stream.seek(offset, 0)
        data = await self.__stream.read(size)
        if len(data) != size:
            raise Exception('The QCOW2 file is truncated, %s bytes at %s can not be read' % (size, offset))
        return data

    async def read_header(self):
        await self.__stream.seek(0, 0)
        data = await self.__stream.read(MAX_HEADER_SIZE)
        self.header = QCOW2Header.from_bytes(data)
        header = self.header
        if header.crypt_method != 0:
            raise NotImplementedError('Encrypted QCOW2 disks are not supported')
        unknown = header.incompatible_features & ~INCOMPAT_SUPPORTED
        if unknown != 0:
            raise NotImplementedError('QCOW2 disk uses features which are not supported: 0x%x' % unknown)
        if header.incompatible_features & INCOMPAT_CORRUPT:
            logger.warning('The QCOW2 disk is marked as corrupt, data might be inconsistent')
        if header.compression_type == COMPRESSION_ZSTD:
            self.__decompress = get_zstd_decompressor()
        elif header.compression_type != COMPRESSION_DEFLATE:
            raise NotImplementedError('QCOW2 compression type %s is not supported' % header.compression_type)

        self.cluster_size = 1 << header.cluster_bits
        if header.incompatible_features & INCOMPAT_EXTENDED_L2:
            self.__l2_entry_size = 16
            self.__subclusters = 32
        self.__l2_bits = header.cluster_bits - (4 if self.__l2_entry_size == 16 else 3)
        self.__data_file_raw = header.autoclear_features & AUTOCLEAR_DATA_FILE_RAW != 0

        if header.backing_file_size > 0:
            if header.backing_file_size > MAX_NAME_SIZE:
                raise Exception('Invalid backing file name in the QCOW2 header')
            data = await self.read_at(header.backing_file_offset, header.backing_file_size)
            header.backing_file = data.decode('utf-8', errors='replace')

    async def read_snapshots(self):
        header = self.header
        if header.nb_snapshots == 0:
            return
        if header.nb_snapshots > MAX_SNAPSHOTS:
            raise Exception('Invalid snapshot count in the QCOW2 header')
        await self.__stream.seek(header.snapshots_offset, 0)
        data = await self.__stream.read(min(header.nb_snapshots * MAX_SNAPSHOT_SIZE, MAX_SNAPSHOT_TABLE_SIZE))
        pos = 0
        for _ in range(header.nb_snapshots):
            snapshot, pos = QCOW2Snapshot.from_bytes(data, pos)
            if snapshot.disk_size is None:
                snapshot.disk_size = header.size
            self.snapshots.append(snapshot)

    async def read_l1(self, offset:int, size:int):
        if size > MAX_L1_SIZE:
            raise Exception('Invalid L1 table size in the QCOW2 file')
        data = await self.read_at(offset, size * 8) if size > 0 else b''
        return [int.from_bytes(data[i:i+8], 'big') & OFFSET_MASK for i in range(0, len(data), 8)]

    def set_size(self, size:int):
        self.size = size
        self.capacity = (size + SECTOR_SIZE - 1) // SECTOR_SIZE

    async def open_data_file(self):
        if self.header.data_file is None:
            raise Exception('The name of the data file is not stored in the QCOW2 file')
        self.__data_stream = await self.__stream.open_sibling(self.header.data_file)
        self.__siblings.append(self.__data_stream)

    async def open_parent(self):
        name = self.header.backing_file
        if name.startswith('json:') is True or name.find('://') != -1:
            raise NotImplementedError('Backing file %s is not a file' % name)
        self.parent, ds = await open_parent_disk(
            self.__stream, [name], BackingDisk(self.header.backing_format),
            lambda disk: True
        )
        self.__siblings.append(ds)

    async def open_snapshot(self, name:str):
        """Returns the disk as it was when the snapshot was taken. name is either the name or the id of the snapshot."""
        for snapshot in self.snapshots:
            if snapshot.name == name:
                break
        else:
            for snapshot in self.snapshots:
                if snapshot.id == str(name):
                    break
            else:
                raise Exception('No such snapshot: %s' % name)

        disk = QCOW2Disk()
        disk.__stream = self.__stream
        disk.__data_stream = self.__data_stream
        disk.__owner = False
        disk.__l2_entry_size = self.__l2_entry_size
        disk.__l2_bits = self.__l2_bits
        disk.__subclusters = self.__subclusters
        disk.__data_file_raw = self.__data_file_raw
        disk.__decompress = self.__decompress
        disk.header = self.header
        disk.parent = self.parent
        disk.snapshots = self.snapshots
        disk.snapshot = snapshot
        disk.cluster_size = self.cluster_size
        disk.set_size(snapshot.disk_size)
        disk.L1 = await self.read_l1(snapshot.l1_table_offset, snapshot.l1_size)
        return disk

    async def close(self):
        """Closes the files opened by the disk, the datasource of the disk is to be closed by the caller"""
        if self.__owner is True:
            if self.parent is not None:
                await self.parent.close()
            for ds in self.__siblings:
                await ds.close()
        self.parent = None
        self.__siblings = []

    async def read_l2(self, offset:int):
        table = self.__l2_cache.get(offset)
        if table is None:
            table = await self.read_at(offset, self.cluster_size)
            self.__l2_cache[offset] = table
        return table

    async def get_cluster_runs(self, cluster:int, start:int, size:int):
        """Returns where size bytes from the start offset of the cluster are stored, as list of (size, run type, value)"""
        l1_index = cluster >> self.__l2_bits
        if l1_index >= len(self.L1) or self.L1[l1_index] == 0:
            return [(size, RUN_UNALLOCATED, None)]
        table = await self.read_l2(self.L1[l1_index])
        pos = (cluster & ((1 << self.__l2_bits) - 1)) * self.__l2_entry_size
        entry = int.from_bytes(table[pos:pos+8], 'big')
        if entry & L2E_COMPRESSED:
            return [(size, RUN_COMPRESSED, (entry, start))]

        offset = entry & OFFSET_MASK
        # with an external data file the first cluster of that file is in use as well
        allocated = offset != 0 or (self.__data_stream is not self.__stream and entry & L2E_COPIED != 0)
        if self.__subclusters == 1:
            if entry & L2E_ZERO:
                return [(size, RUN_ZERO, None)]
            if allocated is False:
                return [(size, RUN_UNALLOCATED, None)]
            return [(size, RUN_DATA, offset + start)]

        # extended L2 entry: the cluster is made of 32 parts, each has its own state
        bitmap = int.from_bytes(table[pos+8:pos+16], 'big')
        subcluster_size = self.cluster_size // self.__subclusters
        runs = []
        end = start + size
        while start < end:
            index = start // subcluster_size
            count = min(end, (index + 1) * subcluster_size) - start
            if (bitmap >> (32 + index)) & 1:
                run = (count, RUN_ZERO, None)
            elif (bitmap >> index) & 1:
                run = (count, RUN_DATA, offset + start)
            else:
                run = (count, RUN_UNALLOCATED, None)
            runs.append(run)
            start += count
        return runs

    async def read_compressed(self, entry:int):
        """Returns the decompressed cluster of the L2 entry"""
        offset_bits = 62 - (self.header.cluster_bits - 8)
        offset = entry & ((1 << offset_bits) - 1)
        cluster = self.__cluster_cache.get(offset)
        if cluster is None:
            sectors = ((entry & ((1 << 62) - 1)) >> offset_bits) + 1
            # the size is only known in sectors, the file can end before the end of the last one
            await self.__stream.seek(offset, 0)
            data = await self.__stream.read(sectors * SECTOR_SIZE - (offset & (SECTOR_SIZE - 1)))
            cluster = self.__decompress(data, self.cluster_size)
            if len(cluster) != self.cluster_size:
                raise Exception('Compressed cluster at %s can not be decompressed' % offset)
            self.__cluster_cache[offset] = cluster
        return cluster

    async def read_parent(self, sector:int, count:int):
        data = b''
        if self.parent is not None:
            # the backing file can be smaller than the disk
            data = await self.parent.read_sectors(sector, count)
        return data + b'\x00' * (count * SECTOR_SIZE - len(data))

    async def read_sectors(self, sector:int, count:int):
        """Reads consecutive sectors, the data is shorter if the end of the disk is reached"""
        count = min(count, self.capacity - sector)
        if count <= 0:
            return b''
        offset = sector * SECTOR_SIZE
        size = count * SECTOR_SIZE

        if self.__data_file_raw is True:
            # the data file is the raw disk
            runs = [(size, RUN_DATA, offset)]
        else:
            # collecting the runs first, so the clusters next to each other are read together
            runs = []
            pos = offset
            end = offset + size
            while pos < end:
                start = pos & (self.cluster_size - 1)
                length = min(end - pos, self.cluster_size - start)
                for run in await self.get_cluster_runs(pos >> self.header.cluster_bits, start, length):
                    if len(runs) > 0 and runs[-1][1] == run[1] and (
                            run[1] in (RUN_ZERO, RUN_UNALLOCATED) or
                            (run[1] == RUN_DATA and runs[-1][2] + runs[-1][0] == run[2])):
                        runs[-1] = (runs[-1][0] + run[0], run[1], runs[-1][2])
                    else:
                        runs.append(run)
                pos += length

        res = []
        pos = offset
        for length, runtype, value in runs:
            if runtype == RUN_DATA:
                await self.__data_stream.seek(value, 0)
                data = await self.__data_stream.read(length)
                # the last cluster of the file is not always complete
                data += b'\x00' * (length - len(data))
            elif runtype == RUN_COMPRESSED:
                cluster = await self.read_compressed(value[0])
                data = cluster[value[1]:value[1] + length]
            elif runtype == RUN_UNALLOCATED and self.parent is not None:
                data = await self.read_parent(pos // SECTOR_SIZE, length // SECTOR_SIZE)
            else:
                data = b'\x00' * length
            res.append(data)
            pos += length
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
        t.append('Type       : QCOW2 Disk')
        t.append('Datasource : %s' % self.__stream)
        t.append('Version    : %d' % self.header.version)
        t.append('Capacity   : %d' % self.size)
        t.append('Cluster    : %d' % self.cluster_size)
        if self.header.data_file is not None:
            t.append('Data file  : %s' % self.header.data_file)
        if self.header.backing_file is not None:
            t.append('Parent     : %s' % self.header.backing_file)
        if self.snapshot is not None:
            t.append('Snapshot   : %s' % self.snapshot.name)
        elif len(self.snapshots) > 0:
            t.append('Snapshots  : %s' % ', '.join(snapshot.name for snapshot in self.snapshots))
        return '\n'.join(t)
