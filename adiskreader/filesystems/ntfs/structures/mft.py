import io
import ntpath
from collections import OrderedDict
from cachetools import LRUCache
from adiskreader.filesystems.ntfs.structures.filerecord import FileRecord
from adiskreader.filesystems.ntfs.structures.file import NTFSFile

# MFT is a special file record that contains the list of all other file records on the filesystem
class MFT:
    def __init__(self, fs, start_cluster):
        self.__fs = fs
        self.__start_cluster = start_cluster
        self.__record_size = None
        self.__inode_cache = LRUCache(maxsize=10000)
        self.record:FileRecord = None
        self.mftdata = io.BytesIO()

    async def setup(self):
        # file record allocation size is unknown at this point
        self.__record_size = self.__fs.pbs.get_filerecord_size_bytes()

        # file record size can be smaller than the cluster size
        # reding the MFT file record here
        recdata = io.BytesIO()
        while recdata.tell() < self.__record_size:
            temp = await self.__fs.read_cluster(self.__start_cluster)
            recdata.write(temp)
        recdata.seek(0, 0)

        # parse the MFT file record
        self.record = FileRecord.from_buffer(recdata, self.__fs)
        self.file = NTFSFile(self.__fs, self.record)
        await self.file.setup()

    async def get_inode(self, ref):
        if ref in self.__inode_cache:
            return self.__inode_cache[ref]
        await self.file.seek(ref * self.__record_size, 0)
        frdata = await self.file.read(self.__record_size)
        if frdata == b'\x00' * self.__record_size:
            return None
        fr = FileRecord.from_bytes(frdata, self.__fs)
        await fr.reparse(self.__fs)
        self.__inode_cache[ref] = fr
        return fr
    
    async def find_path(self, path):
        if path == '\\':
            return await self.get_inode(5)
        
        # split path into parts
        parts = path.split('\\')
        if parts[0] == '':
            parts = parts[1:]
        if parts[-1] == '':
            parts = parts[:-1]
        
        # removing datastream name
        m = parts[-1].find(':')
        if m != -1:
            parts[-1] = parts[-1][:m]
        
        # start at root
        inode = await self.get_inode(5)
        temppart = []
        for part in parts:
            async for idx, fn in inode.list_directory():
                if fn.name == part:
                    temppart.append(part)
                    inode = await self.get_inode(idx.file_ref)
                    break
            else:
                #print('Path not found')
                return None
        return inode

    @staticmethod
    async def from_filesystem(fs, start_cluster):
        mft = MFT(fs, start_cluster)
        await mft.setup()
        return mft
    
    async def walk(self, path:str = "\\"):
        inode = await self.find_path(path)
        if inode is None:
            #raise Exception('Path not found')
            yield [], [], []
        if inode.is_directory() is False:
            raise Exception('Path is not a directory')
        
        subdirs = OrderedDict()

        async for root, dirs, files, dindices in inode.walk(path):
            if root.startswith('\\') is False:
                root = '\\' + root
            yield root, dirs, files
            if len(dindices) == 0:
                continue
            if root not in subdirs:
                subdirs[root] = dindices
            else:
                subdirs[root].extend(dindices)
        

        ref_seen = {}
        while True:
            try:
                x = subdirs.popitem(last=False)
                root, subdir_indices = x
            except KeyError:
                break
            
            while subdir_indices:
                file_ref, fname = subdir_indices.pop()
                if file_ref in ref_seen:
                    continue
                dirpath = ntpath.join(root,fname)
                inode = await self.get_inode(file_ref)
                ref_seen[file_ref] = True
                if inode is None:
                    continue
                    
                async for subroot, dirs, files, dindices in inode.walk():
                    if subroot.startswith('\\') is False:
                        subroot = '\\' + subroot
                    yield subroot, dirs, files

                    if len(dindices) == 0:
                        continue

                    subdirs[dirpath] = dindices
        