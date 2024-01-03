import io

from cachetools import LRUCache
from adiskreader.filesystems.ntfs.structures.filerecord import FileRecord
from adiskreader.filesystems.ntfs.structures.file import NTFSFile


# MFT is a special file record that contains the list of all other file records on the filesystem
class MFT:
    def __init__(self, fs, start_cluster, inode = 0):
        self.__fs = fs
        self.__inode = inode
        self.__start_cluster = start_cluster
        self.__record_size = None
        self.__root_ref = 5 #this should be constant
        self.__inode_cache = LRUCache(maxsize=1000)
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
        self.record = FileRecord.from_buffer(recdata, self.__fs, self.__inode)
        self.file = NTFSFile(self.__fs, self.record)
        await self.file.setup()

        #async for dataattr in self.record.get_attribute_by_type(0x80, self):
        #    pbar = tqdm(total=dataattr.header.real_size, unit='B', unit_scale=True)
        #    async for chunk in dataattr.header.read_attribute_data(self.__fs):
        #        self.mftdata.write(chunk)
        #        pbar.update(len(chunk))
        #        
        #    pbar.close()
        #    break
        #
        #self.mftdata.seek(0, 0)

    async def get_inode(self, ref):
        # retrieve the inode from the MFT with all attributes resolved, except the data attribute
        #self.mftdata.seek(ref * self.__record_size, 0)
        #frdata = self.mftdata.read(self.__record_size)
        #if frdata == b'\x00' * self.__record_size:
        #    return None
        #input(frdata)
        #fr = FileRecord.from_bytes(frdata)
        #await fr.reparse(self.__fs)
        #return fr
        if ref in self.__inode_cache:
            return self.__inode_cache[ref]
        await self.file.seek(ref * self.__record_size, 0)
        frdata = await self.file.read(self.__record_size)
        if frdata == b'\x00' * self.__record_size:
            return None
        fr = FileRecord.from_bytes(frdata, self.__fs, ref)
        await fr.reparse(self.__fs)
        self.__inode_cache[ref] = fr
        return fr
    
    async def find_path(self, path):
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
    
    async def list_directory(self, path):
        dir_inode = await self.find_path(path)
        if dir_inode is None:
            raise Exception('Path not found')
        async for idx, fn in dir_inode.list_directory():
            yield fn.name
    
    async def resolve_full_path(self, file_record):
        paths = []
        refs_seen = {}
        
        fn = file_record.get_main_filename_attr()
        current_ref = fn.parent_ref
        seq = fn.parent_seq
        paths.append(fn.name)

        while current_ref != self.__root_ref:
            if current_ref in refs_seen:
                print('Loop detected')
                break
            refs_seen[current_ref] = True
            try:
                #print('Current ref: %s' % current_ref)
                inode = await self.get_inode(current_ref)
                current_file_attr = inode.get_main_filename_attr()
            except IndexError:
                print('Ref %s not found in INODE table' % current_ref)
                break
            
            if seq != current_file_attr.parent_seq:
                #print('Sequence mismatch: %s' % ('\\'.join(reversed(paths))))
                return None
            current_ref = current_file_attr.parent_ref
            seq = current_file_attr.parent_seq
            paths.append(current_file_attr.name)
        
        full_path = '\\'.join(reversed(paths))
        #input(full_path)
        #input(self.inodes[ref].get_attribute_by_type(0x30)[0].name)

    @staticmethod
    async def from_filesystem(fs, start_cluster):
        mft = MFT(fs, start_cluster)
        await mft.setup()
        return mft
