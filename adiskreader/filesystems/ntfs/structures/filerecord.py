import io
import os
import enum
from adiskreader.filesystems.ntfs import logger
from adiskreader.filesystems.ntfs.structures.attributes import Attribute, FILE_NAME
from adiskreader.filesystems.ntfs.structures.file import NTFSFile
from adiskreader.filesystems.ntfs.structures.utils import apply_fixups

# In NTFS everything is a filerecord (even directories), not to be confused with actual files
# A filerecord is a header followed by a collection of attributes
# These attributes hold the actual data of the file, or the list of files of a directory inside indices
# https://flatcap.github.io/linux-ntfs/ntfs/concepts/file_record.html

class FileRecordFlags(enum.IntFlag):
    IN_USE = 0x01 # File record is in use
    DIRECTORY = 0x02 # File record is a directory
    EXTENSION = 0x04 # Record is an exension (Set for records in the $Extend directory)
    SPECIAL_INDEX_PRESENT = 0x08 # Special index present (Set for non-directory records containing an index: $Secure, $ObjID, $Quota, $Reparse)

class FileRecord:
    # Note: Parsing a file record will only read and parse the header and the resident attributes.
    #       Non-resident attributes must be parsed when they are needed using the `reparse` function.
    def __init__(self,fs = None):
        self.__fs = fs
        self.__current_fname = None
        self.__full_path = None
        self.signature = None
        self.usa_offset = None
        self.usa_count = None
        self.lsn = None
        self.sequence_number = None
        self.link_count = None
        self.attr_offset = None
        self.flags = None
        self.bytes_in_use = None
        self.bytes_allocated = None
        self.base_record = None
        self.next_attr_id = None
        self.record_number = None
        self.attributes = []
        self.update_seq = []
        self.__dir_entries_full = False
        self.__dir_entries = [] # this is a list of tuples (index, FILE_NAME)

    def is_directory(self):
        return FileRecordFlags.DIRECTORY in self.flags
    
    async def get_attribute_by_name(self, name:str):
        """Returns all the attributes with the given name."""
        # there could be multiple attributes of the same type
        attr_to_resolve = []
        for attr in self.attributes:
            if attr.header.type == 0x20:
                # this is a list of partial attributes which we'd need to resolve if the name matches.
                for tattr in attr.attributes:
                    if tattr.name == name:
                        attr_to_resolve.append(tattr)
            
            if attr.header.name == name:
                # this is a normal attribute
                yield attr
        
        # resolving the partial attributes from the attribute list
        for attr in attr_to_resolve:
            inode = await self.__fs.mft.get_inode(attr.file_ref)
            if inode is None:
                continue
            for attr in inode.attributes:
                if attr.header.name == name:
                    yield attr
    
    async def get_file(self, datastream):
        file = NTFSFile(self.__fs, self, datastream)
        await file.setup()
        return file

    
    async def get_attribute_by_type(self, atype:int):
        """Returns all the attributes with the given type."""
        # there could be multiple attributes of the same type
        attr_to_resolve = []
        for attr in self.attributes:
            if attr.header.type == 0x20:
                # this is a list of partial attributes which we'd need to resolve if the name matches.
                for tattr in attr.attributes:
                    if tattr.type == atype:
                        attr_to_resolve.append(tattr)
            if attr.header.type == atype:
                yield attr
        
        # resolving the partial attributes from the attribute list
        for attr in attr_to_resolve:
            inode = await self.__fs.mft.get_inode(attr.file_ref)
            if inode is None:
                continue
            for attr in inode.attributes:
                if attr.header.type == atype:
                    yield attr
    
    async def get_attribute(self, atype:int = None, name:str = None):
        res = []
        if atype is not None:
            async for attr in self.get_attribute_by_type(atype):
                if name is not None:
                    if attr.header.name == name:
                        res.append(attr)
                else:
                    res.append(attr)
        else:
            async for attr in self.get_attribute_by_name(name):
                res.append(attr)
        return res

    
    async def get_main_filename_attr(self):
        '''Returns the main filename attribute of the entry.

        As an entry can have multiple FILENAME attributes, this function allows
        to return the main one, i.e., the one with the lowest attribute id and
        the "biggest" namespace.
        '''
        if self.__current_fname is not None:
            return self.__current_fname
        
        fn_attrs = await self.get_attribute(0x30)
        high_attr_id = 0xFFFFFFFF
        main_fn = None

        if fn_attrs is not None:
            #search for the lowest id, that will give the first FILE_NAME
            for fn_attr in fn_attrs:
                if fn_attr.header.id < high_attr_id:
                    main_fn = fn_attr
                    high_attr_id = fn_attr.header.id

            ##TODO is this necessary? Maybe the first name is always the with with the biggest namespace
            ## after we have the lowest, search for same name, but the biggest namespace
            #for fn_attr in fn_attrs:
            #    if main_fn.content.parent_ref == fn_attr.content.parent_ref and \
            #        main_fn.content.parent_seq == fn_attr.content.parent_seq and \
            #        fn_attr.content.name_type.value < main_fn.content.name_type.value:
            #            main_fn = fn_attr

        self.__current_fname = main_fn
        return main_fn

    
    @staticmethod
    def from_bytes(data:bytes, fs):
        return FileRecord.from_buffer(io.BytesIO(data), fs)

    @staticmethod
    def from_buffer(buff:io.BytesIO, fs):
        start_pos = buff.tell()
        fr = FileRecord(fs)
        fr.signature = buff.read(4)
        if fr.signature != b'FILE':
            raise Exception('Invalid file record signature. Expected FILE, got {}'.format(fr.signature))
        fr.usa_offset = int.from_bytes(buff.read(2), 'little')
        fr.usa_count = int.from_bytes(buff.read(2), 'little')
        fr.lsn = int.from_bytes(buff.read(8), 'little')
        fr.sequence_number = int.from_bytes(buff.read(2), 'little')
        fr.link_count = int.from_bytes(buff.read(2), 'little')
        fr.attr_offset = int.from_bytes(buff.read(2), 'little')
        fr.flags = FileRecordFlags(int.from_bytes(buff.read(2), 'little'))
        fr.bytes_in_use = int.from_bytes(buff.read(4), 'little')
        fr.bytes_allocated = int.from_bytes(buff.read(4), 'little')
        fr.base_record = int.from_bytes(buff.read(8), 'little')
        fr.next_attr_id = int.from_bytes(buff.read(2), 'little')
        fr.record_number = int.from_bytes(buff.read(2), 'little')
        
        fr.update_seq = apply_fixups(buff, fr.usa_offset, fr.usa_count, start_pos, fs.pbs.bytes_per_sector)
        
        buff.seek(fr.attr_offset + start_pos, 0)
        while (buff.tell() - start_pos) < fr.bytes_in_use:
            attr = Attribute.from_buffer(buff)
            if attr.header.type == 0xFFFFFFFF:
                break
            fr.attributes.append(attr)
        
        # Seek to the end of the file record to allow for the next file record to be read
        buff.seek(start_pos, 0)
        buff.seek(fr.bytes_allocated, 1)
        return fr

    async def reparse(self, include_data = False):
        attrs = []
        for attr in self.attributes:
            tattr = await attr.header.reparse(self.__fs, include_data= include_data)
            attrs.append(tattr)
        self.attributes = attrs
    
    async def get_parent(self):
        fn = await self.get_main_filename_attr()
        return await self.__fs.mft.get_inode(fn.parent_ref)
    
    async def get_child(self, name):
        async for index, fn in self.list_directory():
            if fn.name == name:
                return await self.__fs.mft.get_inode(index.file_ref)
        return None
    
    async def list_directory(self):
        bitmap = None
        dir_indices = []
        index_record_size = None
        alloc_attrs = []
        subnodes = []

        if self.__dir_entries_full is True:
            for entry in self.__dir_entries:
                yield entry
        else:
            self.__dir_entries = []

            async for attr in self.get_attribute_by_name('$I30'):
                if attr.header.type == 0xB0:
                    bitmap = attr.bitfield
            
            # if there is a bitmap, we can use it to know which indices are allocated
            if bitmap is not None:
                for i in range(0, len(bitmap)):
                    for j in range(0, 8):
                        if (bitmap[i] >> j) & 1 == 1:
                            dir_indices.append((i * 8) + j)

            async for attr in self.get_attribute_by_name('$I30'):
                if attr.header.type == 0x90:
                    index_record_size = attr.bytes_per_record
                    for index in attr.index_entries:
                        if index.stream is not None:
                            fn = FILE_NAME.from_bytes(index.stream)
                            self.__dir_entries.append((index, fn))
                            yield index, fn
                        if index.sub_node_ref is not None:
                            subnodes.append(index.sub_node_ref)
                            #input('SUBNODE: %s ' % index.sub_node_ref)
                            #raise Exception('Sub node ref not implemented')
                elif attr.header.type == 0xA0:
                    alloc_attrs.append(attr)
                elif attr.header.type == 0xB0:
                    pass
                else:
                    raise Exception('Unknown attribute type: %s' % hex(attr.header.type))
            
            for attr in alloc_attrs:
                async for record in attr.read_indicies(index_record_size, self.__fs):
                    for index in record.entries:
                        if index.stream is not None:
                            fn = FILE_NAME.from_bytes(index.stream)
                            self.__dir_entries.append((index, fn))
                            yield index, fn
            
            #at this point we successfully parsed the directory entries, so we can mark it as full
            self.__dir_entries_full = True
    
    async def resolve_full_path(self):
        if self.__full_path is not None:
            return self.__full_path
        
        paths = []
        refs_seen = {}
        
        fn = await self.get_main_filename_attr()
        current_ref = fn.parent_ref
        seq = fn.parent_seq
        paths.append(fn.name)

        while current_ref != 5: # 5 is the root ref
            if current_ref in refs_seen:
                logger.debug('[FILERECORD][%s] Loop detected' % self.sequence_number)
                paths.append('__ORPHANED__')
                break
            refs_seen[current_ref] = True
            try:
                #print('Current ref: %s' % current_ref)
                inode = await self.__fs.mft.get_inode(current_ref)
                if inode is None:
                    logger.debug('[FILERECORD][%s] Ref %s not found in INODE table' % (self.sequence_number, current_ref))
                    paths.append('__ORPHANED__')
                    break
                current_file_attr = await inode.get_main_filename_attr()
            except Exception as e:
                logger.debug('[FILERECORD][%s] Error in parsing parent: %s' % (self.sequence_number, current_ref, e))
                paths.append('__ORPHANED__')
                break
            
            if seq != current_file_attr.parent_seq:
                logger.debug('[FILERECORD][%s] Sequence mismatch %s' % (self.sequence_number, current_ref))
            current_ref = current_file_attr.parent_ref
            seq = current_file_attr.parent_seq
            paths.append(current_file_attr.name)
        
        self.__full_path = '\\'.join(reversed(paths))
        return self.__full_path


    async def walk(self, fullpath = None):
        # TODO: filter namespaces. most files come with 2 namespaces at least, one is the dos 
        #       name, the other is the win32 name. We should only return the win32 name
        #       http://inform.pucp.edu.pe/~inf232/Ntfs/ntfs_doc_v0.5/concepts/filename_namespace.html
        
        if fullpath is None:
            fullpath = await self.resolve_full_path()
        
        #bitmap = await self.get_attribute(0xB0, '$I30')
        #bitmap = bitmap[0].bitfield # there should only be one bitmap

        index_root = await self.get_attribute(0x90, '$I30')
        if len(index_root) == 0:
            raise Exception('No index root found')
        index_root = index_root[0] # there must only be one index root
        index_record_size = index_root.bytes_per_record

        dirs = []
        dir_indices = []
        files = []
        for index in index_root.index_entries:
            if index.stream is not None:
                fn = FILE_NAME.from_bytes(index.stream)
                if index.file_ref < 24:
                    # these are reserved
                    continue
                if fn.is_directory():
                    dirs.append(fn.name)
                    dir_indices.append((index.file_ref, fn.name))
                else:
                    files.append(fn.name)

        yield fullpath, dirs, files, dir_indices
        dirs = []
        dir_indices = []
        files = []

        index_alloc = await self.get_attribute(0xA0, '$I30')
        if len(index_alloc) > 0:
            index_alloc = index_alloc[0] # there should only be one index alloc or none

            async for index in index_alloc.read_indicies(index_record_size, self.__fs):
                for index in index.entries:
                    if index.file_ref < 24:
                        # these are reserved
                        continue
                    if index.stream is not None:
                        fn = FILE_NAME.from_bytes(index.stream)
                        if fn.is_directory():
                            dirs.append(fn.name)
                            dir_indices.append((index.file_ref, fn.name))
                        else:
                            files.append(fn.name)
                    
                        if len(dirs) > 100 or len(files) > 100:
                            yield fullpath, dirs, files, dir_indices
                            dirs = []
                            dir_indices = []
                            files = []

        yield fullpath, dirs, files, dir_indices

    async def stat(self):
        # returns the stat of the file
        si = await self.get_attribute(0x10)
        si = si[0]
        fname = await self.get_main_filename_attr()
        dataattr = await self.get_attribute(0x80)
        dataattr = dataattr[0]
        st_uid=1000 #TODO
        st_gid=1000 #TODO
        st_nlink=self.link_count
        st_ino=self.sequence_number
        st_mode=33261 #TODO
        st_atime = int(si.time_accessed.timestamp())
        st_mtime = int(si.time_modified.timestamp())
        st_ctime = int(si.time_created.timestamp())
        if dataattr.header.non_resident is False:
            st_size = len(dataattr.header.data)
        else:
            st_size = dataattr.header.real_size
        st_dev = 1
        return os.stat_result((st_mode, st_ino, st_dev, st_nlink, st_uid, st_gid, st_size, st_atime, st_mtime, st_ctime))
    
    async def get_console_output(self):
        async for index, fn in self.list_directory():
            if fn.is_directory() is True:
                entry = '%s\t%s\t%s\t%s' % ('dr-xr-xr-x',  fn.allocated_size, fn.time_created, fn.name)
            else:
                entry = '%s\t%s\t%s\t%s' % (' r--r--r--',  fn.real_size, fn.time_created, fn.name)
            yield entry

    async def get_children(self):
        """Returns a list of directory entries in current directory"""
        async for _, fn in self.list_directory():
            if fn.is_directory() is True:
                yield 'dir', fn.name, fn
            else:
                yield 'file', fn.name, fn


    @staticmethod
    async def determine_allocation_size(fs, start_cluster):
        # When parsing the filesystem, there is no way to know the size of the file record
        # This function will read the start cluster, and determine the size of the file record
        recbuff = io.BytesIO()
        data = await fs.read_cluster(start_cluster)
        recbuff.write(data)
        recbuff.seek(0x1C, 0)
        return int.from_bytes(recbuff.read(4), 'little')
    
    def __str__(self):
        res = []
        res.append('FileRecord')
        res.append('Signature: {}'.format(self.signature.hex()))
        res.append('USA Offset: {}'.format(self.usa_offset))
        res.append('USA Count: {}'.format(self.usa_count))
        res.append('LSN: {}'.format(self.lsn))
        res.append('Sequence Number: {}'.format(self.sequence_number))
        res.append('Link Count: {}'.format(self.link_count))
        res.append('Attribute Offset: {}'.format(self.attr_offset))
        res.append('Flags: {}'.format(self.flags))
        res.append('Bytes In Use: {}'.format(self.bytes_in_use))
        res.append('Bytes Allocated: {}'.format(self.bytes_allocated))
        res.append('Base Record: {}'.format(self.base_record))
        res.append('Next Attribute ID: {}'.format(self.next_attr_id))
        res.append('Record Number: {}'.format(self.record_number))
        res.append('Update Sequence: {}'.format(self.update_seq))
        res.append('Attributes:')
        for attr in self.attributes:
            res.append('---- ATTR START ----')
            for line in str(attr).split('\n'):
                res.append('  {}'.format(line))
            res.append('---- ATTR END ----')
        return '\n'.join(res)
