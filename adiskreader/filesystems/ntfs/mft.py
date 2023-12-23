import io
import traceback
from adiskreader.filesystems.ntfs.attributes import Attribute, FileNameFlag
from adiskreader.filesystems.ntfs.filerecord import FileRecord, FileRecordFlags
from tqdm import tqdm

class MFT:
    def __init__(self, fs, start_cluster):
        self.__fs = fs
        self.__start_cluster = start_cluster
        self.__record_size = None
        self.record:FileRecord = None
        self.inodes = []
        self.mftdata = io.BytesIO()
    
    def add_inode(self, file_record):
        self.inodes.append(file_record)

    async def get_inode(self, ref):
        self.mftdata.seek(ref * self.__record_size, 0)
        fr = FileRecord.from_buffer(self.mftdata)
        return fr

    async def parse(self, only_root=False):
        print('MFT')
        print('Attributes:')
        for attr in self.record.attributes:
            print(attr)
        
        data = b''
        bytes_allocated = -1
        for dataattr in self.record.get_attribute_by_type(0x80):
            data = b''
            datasize = await dataattr.header.get_data_size(self.__fs)
            pbar = tqdm(total=datasize, unit='B', unit_scale=True)
            async for chunk in dataattr.header.read_attribute_data(self.__fs):
                self.mftdata.write(chunk)
                pbar.update(len(chunk))
                #while len(data) >= 1024:
                #    fdata = data[:1024]
                #    data = data[1024:]
                #    if fdata == b'\x00' * 1024:
                #        # This is a sparse file, skip it
                #        print('Skipping sparse file')
                #        continue
                #    try:
                #        fr = FileRecord.from_bytes(fdata)
                #        self.add_inode(fr)
                #        if len(self.inodes) > 500 and only_root is True:
                #            break
                #        #for name in fr.get_attribute_by_type(0x30):
                #        #    input(name.name)
                #        
                #    except:
                #        print('Failed to parse file record')
                #        print(fdata)
                #        print(fdata.count(b'FILE'))
                #        input()
                #        raise
            pbar.close()
            print('Data size -attr-: %s' % dataattr.header.real_size)
            input('Data size -actu-: %s' % self.mftdata.tell())
            break
            #if len(data) > 0:
            #    print('Leftover data')
            #    input(data)

        #buff = io.BytesIO(data)
        #while buff.tell() < len(data):
        #    pos = buff.tell()
        #    tdata = buff.read(1024)
        #    if tdata == b'\x00' * 1024:
        #        # This is a sparse file, skip it
        #        print('Skipping sparse file')
        #        continue
        #    buff.seek(pos, 0)
        #    try:
        #        print(buff.tell() / len(data))
        #        fr = FileRecord.from_buffer(buff)
        #        self.add_inode(fr)
        #    except:
        #        print('Failed to parse file record')
        #        print(tdata)
        #        print(tdata.count(b'FILE'))
        #        traceback.print_exc()
        #        input()
        #        break
        #    #if len(self.inodes) > 500 and only_root is True:
        #    #    break   
        ##input(len(data))
        ##input('INODES: %s' % len(self.inodes))

        # reading one MFT record to determint the size of the MFT
        self.mftdata.seek(0, 0)
        fr = FileRecord.from_buffer(self.mftdata)
        self.__record_size = fr.bytes_allocated
        input(self.__record_size)

        for i in range(1000, 100000000, 1):
            inode = await self.get_inode(i)
            fname = inode.get_main_filename_attr()
            if fname is None:
                continue
            await self.resolve_full_path(inode)
            
        #root = self.inodes[5]
        #print('++++++++++++++++++++++++++++++++++++')
        #input(root)
        #await self.resolve_full_path(None)

    @staticmethod
    async def from_filesystem(fs, start_cluster):
        mft = MFT(fs, start_cluster)
        mft.record = await FileRecord.from_filesystem(fs, start_cluster)
        print()
        print()
        print('MFT record')
        input(str(mft.record))
        await mft.parse(only_root=False)
        
        
    async def resolve_full_path(self, file_record):
        paths = []
        refs_seen = {}
        root_ref = 5
        
        fn = file_record.get_main_filename_attr()
        current_ref = fn.parent_ref
        seq = fn.parent_seq
        paths.append(fn.name)

        while current_ref != root_ref:
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
        input(full_path)
        #input(self.inodes[ref].get_attribute_by_type(0x30)[0].name)
