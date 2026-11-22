import bisect
import io
import math

from adiskreader.compression.lznt1 import lznt1_decompress
from adiskreader.filesystems.ntfs.structures.attributes import AttributeHeaderFlag

class NTFSFile:
    def __init__(self, fs, fr, datastream = ''):
        self.__fs = fs
        self.__fr = fr
        self.__dataattr = None
        self.__dataruns = []
        self.__buffer = io.BytesIO()
        self.__buffer_total_len = 0
        self.__real_size = None
        self.__init_size = None
        self.__data_runs = [] # (first cluster, cluster count) of all the runs of the file, sparse runs have 0 as their first cluster
        self.__run_vcns = [] # first cluster of the file (VCN) in each of the runs
        self.__cu_clusters = 0 # clusters in a compression unit, 0 when the file is not compressed
        self.__cu_cache = (None, b'') # index and data of the compression unit read last
        self.datastream = datastream

        self.__pos = 0
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def close(self):
        pass

    async def seekable(self):
        return True
    
    async def writable(self):
        return False
    
    async def readable(self):
        return True
    
    def get_dataruns(self):
        return self.__dataruns
    
    def get_dataattr(self):
        return self.__dataattr
    
    def get_record(self):
        return self.__fr

    async def read_runs(self, size):
        """Reads size bytes from the current position, the caller keeps it inside of the file"""
        start = self.__pos
        end = start + size
        cluster_size = self.__fs.cluster_size
        buff = io.BytesIO()
        for r, index in self.__dataruns:
            if r.stop <= start:
                continue
            if r.start >= end:
                break
            run_offset, _ = self.__data_runs[index]
            # the part of the run which is needed, and which has data: the file is zeroes after its initialized size
            first = max(start, r.start)
            last = min(end, r.stop)
            data_last = min(last, self.__init_size)
            if run_offset == 0 or data_last <= first:
                # sparse run
                buff.write(b'\x00' * (last - first))
                continue
            first_cluster = (first - r.start) // cluster_size
            skip = first - r.start - first_cluster * cluster_size
            needed = data_last - first
            cluster_count = (skip + needed + cluster_size - 1) // cluster_size
            got = 0
            async for data in self.__fs.read_sequential_clusters(run_offset + first_cluster, cluster_count):
                if skip > 0:
                    data = data[skip:]
                    skip = 0
                data = data[:needed - got]
                buff.write(data)
                got += len(data)
                if got >= needed:
                    break
            if got != needed:
                raise Exception('Could not read the clusters of the file')
            buff.write(b'\x00' * (last - data_last))

        return buff.getvalue()

    def get_clusters(self, vcn, count):
        """Returns the (first cluster, cluster count) runs of count clusters of the file from vcn, the sparse ones have 0 as their first cluster"""
        res = []
        index = bisect.bisect_right(self.__run_vcns, vcn) - 1
        while count > 0 and index < len(self.__data_runs):
            run_offset, run_length = self.__data_runs[index]
            skip = vcn - self.__run_vcns[index]
            take = min(run_length - skip, count)
            if take > 0:
                res.append((run_offset + skip if run_offset != 0 else 0, take))
                vcn += take
                count -= take
            index += 1
        if count > 0:
            # after the end of the runs
            res.append((0, count))
        return res

    async def read_compression_unit(self, index):
        """Returns the data of a compression unit of a compressed file"""
        if self.__cu_cache[0] == index:
            return self.__cu_cache[1]
        cluster_size = self.__fs.cluster_size
        cu_size = self.__cu_clusters * cluster_size
        runs = [run for run in self.get_clusters(index * self.__cu_clusters, self.__cu_clusters) if run[0] != 0]
        stored = sum(run_length for _, run_length in runs)
        if stored == 0:
            # sparse unit
            data = b'\x00' * cu_size
        else:
            buff = io.BytesIO()
            for run_offset, run_length in runs:
                async for part in self.__fs.read_sequential_clusters(run_offset, run_length):
                    buff.write(part)
            data = buff.getvalue()
            if len(data) != stored * cluster_size:
                raise Exception('Could not read the clusters of the file')
            if stored < self.__cu_clusters:
                # a unit which takes fewer clusters than its size is compressed, the clusters it spared are sparse
                data = lznt1_decompress(data, cu_size)
        # the file is zeroes after its initialized size
        valid = self.__init_size - index * cu_size
        if valid < cu_size:
            valid = max(valid, 0)
            data = data[:valid] + b'\x00' * (cu_size - valid)
        self.__cu_cache = (index, data)
        return data

    async def read_compressed(self, size):
        """Reads size bytes of a compressed file from the current position, the caller keeps it inside of the file"""
        start = self.__pos
        end = start + size
        cu_size = self.__cu_clusters * self.__fs.cluster_size
        buff = io.BytesIO()
        for index in range(start // cu_size, (end + cu_size - 1) // cu_size):
            data = await self.read_compression_unit(index)
            cu_start = index * cu_size
            buff.write(data[max(start - cu_start, 0):end - cu_start])
        return buff.getvalue()

    async def setup(self):
        # parse the data attribute
        dataattrs = await self.__fr.get_attribute(0x80, name = self.datastream)
        self.__dataattr = dataattrs[0]
        if self.__dataattr.header.non_resident is False:
            self.__buffer.write(self.__dataattr.header.data)
            self.__buffer_total_len = len(self.__dataattr.header.data)
            self.__real_size = self.__buffer_total_len
            self.__buffer.seek(0, 0)
        else:
            # creating mapping of data runs
            # a fragmented file has its runs in more attributes (listed by the attribute list), the first one has the sizes
            parts = {}
            for attr in dataattrs:
                if attr.header.non_resident is True:
                    parts.setdefault(attr.header.start_vcn, attr.header)
            first = parts.get(0, self.__dataattr.header)
            allocated = 0
            for start_vcn in sorted(parts):
                for run_offset, run_length in parts[start_vcn].data_runs:
                    self.__dataruns.append((range(allocated, allocated + run_length*self.__fs.cluster_size), len(self.__data_runs)))
                    self.__data_runs.append((run_offset, run_length))
                    self.__run_vcns.append(allocated // self.__fs.cluster_size)
                    allocated += run_length*self.__fs.cluster_size
            if 0 in parts:
                self.__real_size = first.real_size
                self.__init_size = min(first.init_size, first.real_size)
            else:
                # the sizes are not known
                self.__real_size = allocated
                self.__init_size = allocated
            if first.flags & AttributeHeaderFlag.COMPRESSED and first.compression_unit != 0:
                # LZNT1 compressed file, it is compressed in units of this many clusters
                self.__cu_clusters = 1 << first.compression_unit
            elif self.__real_size > allocated:
                raise Exception('The data runs of the file are shorter than the file')

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
                newpos = self.__real_size + pos
            else:
                raise Exception('Invalid whence value')
            
            if newpos < 0 or newpos > self.__real_size:
                raise Exception('Invalid position')
            self.__pos = newpos

    async def read(self, size=-1):
        if self.__pos >= self.__real_size:
            return b''
        if self.__dataattr.header.non_resident is False:
            if size == -1:
                size = self.__buffer_total_len - self.__buffer.tell()
            if size < 1:
                return b''
            data = self.__buffer.read(size)
            return data
        else:
            if size == -1:
                size = self.__real_size - self.__pos
            if size < 1:
                return b''
            if self.__cu_clusters != 0:
                data = await self.read_compressed(size)
            else:
                data = await self.read_runs(size)
            self.__pos += len(data)
            return data
