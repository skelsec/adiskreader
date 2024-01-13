import io
import math

class NTFSFile:
    def __init__(self, fs, fr, datastream = ''):
        self.__fs = fs
        self.__fr = fr
        self.__dataattr = None
        self.__dataruns = []
        self.__buffer = io.BytesIO()
        self.__buffer_total_len = 0
        self.__real_size = None # the attribute header might not have the correct size
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
        
        if stop_idx is None:
            stop_idx = len(self.__dataruns) - 1
            stop_offset_cluster = 0
            stop_offset_pos = target_range.stop - (stop_offset_cluster * self.__fs.cluster_size)
            if stop_offset_pos == 0:
                stop_offset_pos = None
        #print('runs: %s' % self.__dataattr.header.data_runs)
        #print('start_offset_pos: %s' % start_offset_pos)
        #print('start_offset_cluster: %s' % start_offset_cluster)
        #print('stop_offset_pos: %s' % stop_offset_pos)
        #print('stop_offset_cluster: %s' % stop_offset_cluster)
        #print('start_idx: %s' % start_idx)
        #print('stop_idx: %s' % stop_idx)
        #input()

        if start_idx is None or stop_idx is None:
            print('start_idx: %s' % start_idx)
            print('stop_idx: %s' % stop_idx)
            print('size: %s' % size)
            print('total size: %s' % self.__dataattr.header.real_size)
            print('data runs: %s' % self.__dataattr.header.data_runs)
            print('internal real size: %s' % self.__real_size)
            input('Error: start_idx or stop_idx is None')
            raise FileNotFoundError

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

        #print('StartIdx: %s' % start_idx)
        #print('StopIdx: %s' % stop_idx)
        #print('Data run raw: %s' % [x.hex() for x in self.__dataattr.header.data_run_raw])
        #print('Original runmap: %s' % self.__dataattr.header.data_runs)
        #input('RunMap: %s' % runmap)
        
        for run_offset, run_length in runmap:
            if run_offset == 0:
                data = b'\x00' * (run_length * self.__fs.cluster_size)
                buff.write(data)
                if buff.tell() + start_offset_pos >= size:
                    break
            
            else:
                #print('Reading at offset: %s length: %s' % (run_offset, run_length))
                async for data in self.__fs.read_sequential_clusters(run_offset, run_length): #debug = True
                    buff.write(data)
                    if buff.tell() + start_offset_pos >= size:
                        break
        
        buff.seek(start_offset_pos,0)
        data = buff.read(size)
        return data

    async def setup(self):
        # parse the data attribute
        fsize = await self.__fr.get_attribute(0x30)
        fsize = fsize[0]
        #input(fsize)
        #input(self.__fr)
        self.__dataattr = await self.__fr.get_attribute(0x80, name = self.datastream)
        self.__dataattr = self.__dataattr[0]
        if self.__dataattr.header.non_resident is False:
            self.__buffer.write(self.__dataattr.header.data)
            self.__buffer_total_len = len(self.__dataattr.header.data)
            self.__real_size = self.__buffer_total_len
            self.__buffer.seek(0, 0)
        else:
            # creating mapping of data runs
            self.__real_size = 0
            prevstart = 0
            for i, x in enumerate(self.__dataattr.header.data_runs):
                run_offset, run_length = x
                self.__dataruns.append((range(prevstart, prevstart + run_length*self.__fs.cluster_size), i))
                prevstart += run_length*self.__fs.cluster_size
                self.__real_size += run_length*self.__fs.cluster_size
            #input(self.__dataruns)

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
                newpos = self.__real_size - pos
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
            data = await self.read_runs(size)
            self.__pos += len(data)
            return data
