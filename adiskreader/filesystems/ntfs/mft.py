import io
from adiskreader.filesystems.ntfs.attributes import Attribute
from adiskreader.filesystems.ntfs.filerecord import FileRecord, FileRecordFlags
from tqdm import tqdm

class MFT:
    def __init__(self, fs, start_cluster):
        self.__fs = fs
        self.__start_cluster = start_cluster
        self.record:FileRecord = None
        self.inodes = []
    
    def add_inode(self, file_record):
        self.inodes.append(file_record)

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
                data += chunk
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
            break
            #if len(data) > 0:
            #    print('Leftover data')
            #    input(data)

        buff = io.BytesIO(data)
        while buff.tell() < len(data):
            pos = buff.tell()
            tdata = buff.read(1024)
            if tdata == b'\x00' * 1024:
                # This is a sparse file, skip it
                print('Skipping sparse file')
                continue
            buff.seek(pos, 0)
            try:
                print(buff.tell() / len(data))
                fr = FileRecord.from_buffer(buff)
                self.add_inode(fr)
            except:
                print('Failed to parse file record')
                print(tdata)
                print(tdata.count(b'FILE'))
                input()
                break
            #if len(self.inodes) > 500 and only_root is True:
            #    break   
        input(len(data))
        input('INODES: %s' % len(self.inodes))
        for inode in self.inodes:
            input(inode)
            
        root = self.inodes[7]
        print('++++++++++++++++++++++++++++++++++++')
        input(root)
    
    @staticmethod
    async def from_filesystem(fs, start_cluster):
        mft = MFT(fs, start_cluster)
        mft.record = await FileRecord.from_filesystem(fs, start_cluster)
        print()
        print()
        print('MFT record')
        input(str(mft.record))
        await mft.parse(only_root=False)
        
        
