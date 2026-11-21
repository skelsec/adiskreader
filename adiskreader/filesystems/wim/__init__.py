import os
import ntpath
import posixpath
from urllib.parse import urlparse, unquote
import xml.etree.ElementTree as ET

from adiskreader import logger
from adiskreader.filesystems import FileSystem
from adiskreader.filesystems.wim.structures import *
from adiskreader.filesystems.wim.resource import WIMResource, WIMSolidBlob
from adiskreader.filesystems.wim.file import WIMFile

# WIM (Windows Imaging) files: archives holding one or more directory trees (images), the contents of the files
# are stored once per WIM file and are looked up by their SHA1 hash.
# https://wimlib.net/ has the best description of the format

METADATA_CACHE_CHUNKS = 256
METADATA_READAHEAD = 256 * 1024
MAX_DIRECTORY_ENTRIES = 10000000
MAX_SECURITY_DESCRIPTORS = 1000000

def align8(value:int):
    return (value + 7) & ~7

def split_path(path:str):
    return [part for part in path.replace('/', '\\').split('\\') if part != '']

def get_datasource_filename(ds):
    """Name of the file behind the datasource, None if not known"""
    if 'path' in ds.config:
        return ntpath.basename(str(ds.config['path']).replace('/', '\\'))
    if 'url' in ds.config:
        return unquote(posixpath.basename(urlparse(ds.config['url']).path))
    return None


class WIMDentry:
    """File or directory of a WIM image"""
    def __init__(self, image, header:WIMDentryHeader, streams:list, parent):
        self.image = image
        self.header = header
        self.streams = streams
        self.parent = parent
        self.name = header.name
        self.__children = None

    def is_directory(self):
        return WIMFileAttribute.DIRECTORY in self.header.attributes

    def is_file(self):
        return self.is_directory() is False

    def get_stream_names(self):
        """Names of the named data streams"""
        return [stream.name for stream in self.streams if stream.name != '']

    def get_stream_hash(self, name:str = ''):
        """Returns the SHA1 hash of the data stream, all zeroes if the stream is empty, None if there is no such stream"""
        if name == '':
            unnamed = [stream.hash for stream in self.streams if stream.name == '']
            for streamhash in unnamed:
                if streamhash != ZERO_HASH:
                    return streamhash
            if len(unnamed) > 0 and self.header.hash == ZERO_HASH:
                return ZERO_HASH
            return self.header.hash
        for stream in self.streams:
            if stream.name == name:
                return stream.hash
        for stream in self.streams:
            if stream.name != '' and stream.name.lower() == name.lower():
                return stream.hash
        return None

    def get_size(self, name:str = ''):
        streamhash = self.get_stream_hash(name)
        if self.is_directory() is True or streamhash is None or streamhash == ZERO_HASH:
            return 0
        entry = self.image.wim.lookup.get(streamhash)
        if entry is None:
            return 0
        return entry[0].data_size

    async def list_children(self):
        if self.__children is None:
            if self.is_directory() is False or self.header.subdir_offset == 0:
                self.__children = []
            else:
                # the directories must form a tree
                ancestor = self.parent
                while ancestor is not None:
                    if ancestor.header.subdir_offset == self.header.subdir_offset:
                        raise Exception('Directory loop in the WIM image at %s' % await self.resolve_full_path())
                    ancestor = ancestor.parent
                self.__children = await self.image.read_dentries(self.header.subdir_offset, self)
        return self.__children

    async def get_child(self, name:str):
        children = await self.list_children()
        for child in children:
            if child.name == name:
                return child
        lname = name.lower()
        for child in children:
            if child.name.lower() == lname:
                return child
        return None

    async def get_children(self):
        for child in await self.list_children():
            yield 'dir' if child.is_directory() is True else 'file', child.name, child

    async def get_parent(self):
        if self.parent is None:
            return self
        return self.parent

    async def resolve_full_path(self):
        parts = []
        entry = self
        while entry.parent is not None:
            parts.append(entry.name)
            entry = entry.parent
        return '\\' + '\\'.join(reversed(parts))

    async def get_security_descriptor(self):
        """Returns the security descriptor as bytes, None if the entry does not have one"""
        return await self.image.get_security_descriptor(self.header.security_id)

    async def stat(self):
        st_mode = 0o40555 if self.is_directory() is True else 33261
        st_ino = self.header.hard_link_group_id if self.header.hard_link_group_id is not None else 0
        st_atime = int(filetime_to_dt(self.header.last_access_time).timestamp())
        st_mtime = int(filetime_to_dt(self.header.last_write_time).timestamp())
        st_ctime = int(filetime_to_dt(self.header.creation_time).timestamp())
        return os.stat_result((st_mode, st_ino, 1, 1, 1000, 1000, self.get_size(), st_atime, st_mtime, st_ctime))

    async def get_console_output(self):
        for child in await self.list_children():
            perms = 'dr-xr-xr-x' if child.is_directory() is True else ' r--r--r--'
            yield '%s\t%s\t%s\t%s' % (perms, child.get_size(), filetime_to_dt(child.header.last_write_time), child.name)

    def __str__(self):
        return '%s %s' % ('DIR ' if self.is_directory() is True else 'FILE', self.name)


class WIMImage(FileSystem):
    """One image (directory tree) of a WIM file"""
    def __init__(self, wim, index:int, resource:WIMResource):
        self.wim = wim
        self.index = index
        self.__resource = resource
        self.__buffer = b''
        self.__buffer_offset = 0
        self.__security_sizes = []
        self.__security_offset = 0
        self.root:WIMDentry = None

    async def setup(self):
        # the metadata starts with the security descriptors, the root directory follows them
        hdr = await self.read_metadata(0, 8)
        if len(hdr) < 8:
            raise Exception('WIM metadata resource is truncated')
        total_length = int.from_bytes(hdr[0:4], 'little')
        count = int.from_bytes(hdr[4:8], 'little')
        if total_length > self.__resource.size or count > MAX_SECURITY_DESCRIPTORS or 8 + count * 8 > max(total_length, 8):
            raise Exception('Invalid security data in the WIM metadata resource')
        sizes = await self.read_metadata(8, count * 8)
        self.__security_sizes = [int.from_bytes(sizes[i:i+8], 'little') for i in range(0, len(sizes), 8)]
        self.__security_offset = 8 + count * 8

        entries = await self.read_dentries(align8(max(total_length, 8)), None, 1)
        if len(entries) == 0:
            # image without files
            header = WIMDentryHeader()
            header.attributes = WIMFileAttribute.DIRECTORY
            header.subdir_offset = 0
            header.security_id = -1
            header.creation_time = header.last_access_time = header.last_write_time = 0
            header.hash = ZERO_HASH
            self.root = WIMDentry(self, header, [], None)
        else:
            self.root = entries[0]
            self.root.name = ''

    async def read_metadata(self, offset:int, size:int):
        # the entries are small and follow each other
        start = offset - self.__buffer_offset
        if start < 0 or start + size > len(self.__buffer):
            self.__buffer = await self.__resource.read(offset, max(size, METADATA_READAHEAD))
            self.__buffer_offset = offset
            start = 0
        return self.__buffer[start:start+size]

    async def read_dentries(self, offset:int, parent:WIMDentry, maxcount:int = MAX_DIRECTORY_ENTRIES):
        """Reads the directory entries stored after each other at the offset of the metadata"""
        entries = []
        while len(entries) < maxcount:
            data = await self.read_metadata(offset, 8)
            length = int.from_bytes(data, 'little')
            if len(data) < 8 or length <= 8:
                # end of the directory
                break
            data = await self.read_metadata(offset, length)
            if len(data) != length:
                raise Exception('WIM directory entry is truncated')
            header = WIMDentryHeader.from_bytes(data)
            offset += align8(length)

            streams = []
            for _ in range(header.num_extra_streams):
                data = await self.read_metadata(offset, 8)
                length = int.from_bytes(data, 'little')
                data = await self.read_metadata(offset, length)
                if length < WIMStreamEntry.FIXED_SIZE or len(data) != length:
                    raise Exception('WIM stream entry is truncated')
                streams.append(WIMStreamEntry.from_bytes(data))
                offset += align8(length)
            entries.append(WIMDentry(self, header, streams, parent))
        return entries

    async def get_security_descriptor(self, security_id:int):
        if security_id is None or security_id < 0 or security_id >= len(self.__security_sizes):
            return None
        offset = self.__security_offset + sum(self.__security_sizes[:security_id])
        return await self.__resource.read(offset, self.__security_sizes[security_id])

    async def get_root(self):
        return self.root

    async def get_record_by_path(self, path:str):
        """Returns the entry of the path, None if it does not exist"""
        entry = self.root
        for part in split_path(path):
            if part == '.':
                continue
            if part == '..':
                entry = await entry.get_parent()
                continue
            entry = await entry.get_child(part)
            if entry is None:
                return None
        return entry

    async def stat(self, path:str):
        entry = await self.get_record_by_path(path)
        if entry is None:
            raise FileNotFoundError(path)
        return await entry.stat()

    async def open(self, path:str, mode:str = 'rb'):
        """Opens a file of the image, a named data stream is opened with path:streamname"""
        if mode != 'rb':
            raise Exception('Only binary read mode is supported')
        parts = split_path(path)
        if len(parts) == 0:
            raise IsADirectoryError(path)
        datastream = ''
        m = parts[-1].find(':')
        if m != -1:
            datastream = parts[-1][m+1:]
            parts[-1] = parts[-1][:m]

        entry = await self.get_record_by_path('\\'.join(parts))
        if entry is None:
            raise FileNotFoundError(path)
        if entry.is_directory() is True and datastream == '':
            raise IsADirectoryError(path)
        streamhash = entry.get_stream_hash(datastream)
        if streamhash is None:
            raise FileNotFoundError(path)
        if streamhash == ZERO_HASH:
            return WIMFile(None, path)
        return WIMFile(self.wim.get_resource(streamhash), path)

    async def walk(self, path:str = '\\'):
        top = await self.get_record_by_path(path)
        if top is None:
            raise FileNotFoundError(path)
        if top.is_directory() is False:
            raise NotADirectoryError(path)
        todo = [top]
        while len(todo) > 0:
            entry = todo.pop()
            dirs = []
            files = []
            subdirs = []
            for child in await entry.list_children():
                if child.is_directory() is True:
                    dirs.append(child.name)
                    subdirs.append(child)
                else:
                    files.append(child.name)
            yield await entry.resolve_full_path(), dirs, files
            todo += reversed(subdirs)

    def __str__(self):
        return 'WIM image %s' % self.index


class WIMImageInfo:
    """Description of an image of the WIM file, mount() opens it"""
    def __init__(self, wim, index:int):
        self.wim = wim
        self.index = index # starts with 1
        self.name = ''
        self.description = ''
        self.properties = {} # the simple elements of the XML data

    async def mount(self):
        return await self.wim.mount(self.index)

    def __str__(self):
        t = 'Image %s: %s' % (self.index, self.name)
        if self.description not in ['', self.name]:
            t += ' (%s)' % self.description
        for key in ['DIRCOUNT', 'FILECOUNT', 'TOTALBYTES']:
            if key in self.properties:
                t += ' %s: %s' % (key.lower(), self.properties[key])
        return t


class WIM:
    """
    WIM file. Each image of the file is a filesystem, opened with mount().
    The other parts of a split WIM (swm) are opened from the directory of the first part.
    """
    def __init__(self):
        self.__stream = None
        self.__siblings = []
        self.header:WIMHeader = None
        self.lookup = {} # SHA1 hash -> (WIMLookupEntry, datasource of the part, header of the part)
        self.metadata = [] # WIMLookupEntry of the metadata of each image
        self.images = [] # WIMImageInfo
        self.xml = None

    @staticmethod
    async def from_datasource(ds):
        wim = WIM()
        await wim.setup(ds)
        return wim

    @staticmethod
    async def is_wim(ds):
        """Checks the magic of the file, restores the position of the datasource"""
        pos = await ds.tell()
        await ds.seek(0, 0)
        magic = await ds.read(8)
        await ds.seek(pos, 0)
        return magic == WIM_MAGIC

    async def setup(self, ds):
        self.__stream = ds
        self.header = await self.read_header(ds)
        await self.read_lookup_table(ds, self.header, True)
        if len(self.metadata) != self.header.image_count:
            logger.warning('The WIM file has %s images but %s metadata resources' % (self.header.image_count, len(self.metadata)))
        if self.header.total_parts > 1:
            await self.open_parts()
        await self.read_xml()

    async def read_header(self, ds):
        await ds.seek(0, 0)
        data = await ds.read(WIM_HEADER_SIZE)
        header = WIMHeader.from_bytes(data)
        if len(data) < WIM_HEADER_SIZE:
            raise Exception('WIM header is truncated')
        if header.version not in [WIM_VERSION_DEFAULT, WIM_VERSION_SOLID]:
            raise NotImplementedError('WIM version 0x%x is not supported' % header.version)
        return header

    def open_resource(self, ds, header:WIMHeader, resource:WIMResourceHeader, cache_chunks:int = 8):
        return WIMResource(ds, resource, header.compression, header.chunk_size, cache_chunks)

    async def read_lookup_table(self, ds, header:WIMHeader, with_metadata:bool):
        if header.lookup_table.offset == 0:
            return
        data = await self.open_resource(ds, header, header.lookup_table).read_all()
        entries = []
        for i in range(0, len(data) - WIMLookupEntry.SIZE + 1, WIMLookupEntry.SIZE):
            entry = WIMLookupEntry.from_bytes(data[i:i+WIMLookupEntry.SIZE])
            if WIMResourceFlag.FREE not in entry.resource.flags:
                entries.append(entry)

        # Solid resources (ESD): the entries of the resources and of the data stored in them follow each other in any order,
        # the offsets of the data are counted from the beginning of the first resource of such a run of entries.
        solid_resources = None
        for index, entry in enumerate(entries):
            if WIMResourceFlag.SOLID in entry.resource.flags:
                if solid_resources is None:
                    solid_resources = []
                    for other in entries[index:]:
                        if WIMResourceFlag.SOLID not in other.resource.flags:
                            break
                        if other.resource.original_size == SOLID_RESOURCE_MAGIC and other.part_number == header.part_number:
                            # opened once, the chunks decompressed are shared by the files
                            solid_resources.append(self.open_resource(ds, header, other.resource))
                if entry.resource.original_size == SOLID_RESOURCE_MAGIC or entry.part_number != header.part_number:
                    continue
                if len(solid_resources) == 0:
                    logger.warning('Data without solid resource in the lookup table of the WIM file: %s' % entry.hash.hex())
                    continue
                entry.solid_resources = solid_resources
                if entry.hash not in self.lookup:
                    self.lookup[entry.hash] = (entry, ds, header)
                continue
            solid_resources = None
            if WIMResourceFlag.METADATA in entry.resource.flags:
                # the metadata of the images are in the first part, in the order of the images
                if with_metadata is True and entry.part_number == header.part_number:
                    self.metadata.append(entry)
                continue
            if entry.part_number != header.part_number or entry.hash in self.lookup:
                continue
            self.lookup[entry.hash] = (entry, ds, header)

    async def open_parts(self):
        # the parts are named name.swm, name2.swm, name3.swm ...
        filename = get_datasource_filename(self.__stream)
        if filename is None:
            logger.warning('The WIM file is split and the name of the file is not known, the other parts are not opened')
            return
        stem, ext = ntpath.splitext(filename)
        stem = stem.rstrip('0123456789') if self.header.part_number != 1 else stem
        for part_number in range(1, self.header.total_parts + 1):
            if part_number == self.header.part_number:
                continue
            name = '%s%s%s' % (stem, part_number if part_number > 1 else '', ext)
            try:
                ds = await self.__stream.open_sibling(name)
            except Exception as e:
                logger.warning('Part %s of the WIM file (%s) could not be opened: %s' % (part_number, name, e))
                continue
            self.__siblings.append(ds)
            header = await self.read_header(ds)
            if header.guid != self.header.guid or header.part_number != part_number:
                logger.warning('%s is not part %s of the WIM file' % (name, part_number))
                continue
            await self.read_lookup_table(ds, header, len(self.metadata) == 0)

    async def read_xml(self):
        self.images = [WIMImageInfo(self, index + 1) for index in range(len(self.metadata))]
        if self.header.xml_data.offset == 0 or self.header.xml_data.original_size == 0:
            return
        try:
            data = await self.open_resource(self.__stream, self.header, self.header.xml_data).read_all()
            self.xml = data.decode('utf-16')
            if '<!DOCTYPE' in self.xml or '<!ENTITY' in self.xml:
                raise Exception('Unexpected declarations')
            root = ET.fromstring(self.xml)
            for element in root.findall('IMAGE'):
                index = int(element.get('INDEX', '0'))
                if index < 1 or index > len(self.images):
                    continue
                info = self.images[index - 1]
                for child in element:
                    if len(child) == 0 and child.text is not None:
                        info.properties[child.tag] = child.text
                info.name = info.properties.get('NAME', '')
                info.description = info.properties.get('DESCRIPTION', '')
        except Exception as e:
            logger.warning('Failed to parse the XML data of the WIM file: %s' % e)

    def get_resource(self, streamhash:bytes):
        """Returns the reader of the data with the given SHA1 hash"""
        entry = self.lookup.get(streamhash)
        if entry is None:
            if self.header.total_parts > 1:
                raise FileNotFoundError('The data of the file is not in the opened parts of the WIM file')
            raise FileNotFoundError('The data of the file is not in the WIM file')
        if entry[0].solid_resources is not None:
            return WIMSolidBlob(entry[0].solid_resources, entry[0].resource.offset, entry[0].resource.size)
        return self.open_resource(entry[1], entry[2], entry[0].resource)

    async def mount(self, index:int = 1):
        """Opens an image, index starts with 1"""
        if index < 1 or index > len(self.metadata):
            raise Exception('No such image in the WIM file: %s' % index)
        resource = self.open_resource(self.__stream, self.header, self.metadata[index - 1].resource, METADATA_CACHE_CHUNKS)
        image = WIMImage(self, index, resource)
        await image.setup()
        return image

    async def list_partitions(self):
        # the images take the place of the partitions of the disks
        return self.images

    async def close(self):
        """Closes the other parts of a split WIM, the datasource of the WIM is to be closed by the caller"""
        for ds in self.__siblings:
            await ds.close()
        self.__siblings = []

    def __str__(self):
        t = []
        t.append('Type         : WIM')
        t.append('Datasource   : %s' % self.__stream)
        t.append(str(self.header))
        for image in self.images:
            t.append(str(image))
        return '\n'.join(t)
