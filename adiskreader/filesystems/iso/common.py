import os
import bisect
import datetime

from adiskreader.filesystems import FileSystem

# Shared parts of the filesystems of the optical discs (ISO9660, UDF): both are read only trees of entries,
# the data of a file is a list of extents of the image.

MAX_DIRECTORY_SIZE = 256 * 1024 * 1024
EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

def split_path(path:str):
    return [part for part in path.replace('/', '\\').split('\\') if part != '']


class ExtentFile:
    """
    File object reading the extents of a file from the datasource.
    extents: list of (offset in the datasource, size in bytes), the offset is None for the extents which are not recorded (zeros).
    data: the contents, for the files stored in their own descriptor
    """
    def __init__(self, ds, extents:list, size:int, name:str = '', data:bytes = None):
        self.__ds = ds
        self.__extents = []
        self.__starts = []
        self.__data = data
        self.__size = size
        self.__pos = 0
        self.name = name
        total = 0
        for offset, length in extents:
            if length <= 0:
                continue
            self.__extents.append((offset, length))
            self.__starts.append(total)
            total += length
        self.__total = total

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

    @property
    def size(self):
        return self.__size

    async def tell(self):
        return self.__pos

    async def seek(self, pos:int, whence:int = 0):
        if whence == 0:
            newpos = pos
        elif whence == 1:
            newpos = self.__pos + pos
        elif whence == 2:
            newpos = self.__size + pos
        else:
            raise Exception('Invalid whence value')
        if newpos < 0:
            raise Exception('Invalid position')
        self.__pos = newpos
        return self.__pos

    async def read(self, size:int = -1):
        if size is None or size < 0:
            size = self.__size - self.__pos
        size = min(size, self.__size - self.__pos)
        if size <= 0:
            return b''
        if self.__data is not None:
            data = self.__data[self.__pos:self.__pos+size]
            data += b'\x00' * (size - len(data))
            self.__pos += size
            return data

        res = []
        pos = self.__pos
        remaining = size
        index = bisect.bisect_right(self.__starts, pos) - 1
        while remaining > 0:
            if index >= len(self.__extents) or pos >= self.__total:
                # the file is longer than its extents, the rest is not recorded
                res.append(b'\x00' * remaining)
                break
            offset, length = self.__extents[index]
            skip = pos - self.__starts[index]
            count = min(remaining, length - skip)
            if offset is None:
                res.append(b'\x00' * count)
            else:
                await self.__ds.seek(offset + skip, 0)
                data = await self.__ds.read(count)
                if len(data) != count:
                    raise Exception('The image is truncated, file %s can not be read' % self.name)
                res.append(data)
            pos += count
            remaining -= count
            index += 1
        self.__pos += size
        return b''.join(res)


class OpticalEntry:
    """Base of the directory entries of the filesystems of the optical discs"""
    def __init__(self, fs, parent, name:str):
        self.fs = fs
        self.parent = parent
        self.name = name
        self.size = 0
        self.mode = None # unix permissions, if the filesystem has them
        self.uid = None
        self.gid = None
        self.inode = 0
        self.atime:datetime.datetime = None
        self.mtime:datetime.datetime = None
        self.ctime:datetime.datetime = None
        self.hidden = False
        self.symlink_target:str = None
        self._children = None

    def is_directory(self):
        raise NotImplementedError()

    def is_file(self):
        return self.is_directory() is False

    def is_symlink(self):
        return self.symlink_target is not None

    def get_size(self):
        if self.symlink_target is not None:
            return len(self.symlink_target.encode())
        if self.is_directory() is True:
            return 0
        return self.size

    def same_directory(self, other):
        """True if the two entries are the same directory on the disc, used for finding the loops"""
        raise NotImplementedError()

    async def read_children(self):
        raise NotImplementedError()

    async def get_extents(self):
        """Returns the extents of the data as (offset, size) and the data itself if it is not stored in extents"""
        raise NotImplementedError()

    async def list_children(self):
        if self._children is None:
            if self.is_directory() is False:
                self._children = []
            else:
                # the directories must form a tree
                ancestor = self.parent
                while ancestor is not None:
                    if ancestor.same_directory(self) is True:
                        raise Exception('Directory loop at %s' % await self.resolve_full_path())
                    ancestor = ancestor.parent
                self._children = await self.read_children()
        return self._children

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

    async def stat(self):
        if self.is_directory() is True:
            st_mode = 0o40000 | (self.mode & 0o7777 if self.mode is not None else 0o555)
        elif self.is_symlink() is True:
            st_mode = 0o120000 | (self.mode & 0o7777 if self.mode is not None else 0o777)
        else:
            st_mode = 0o100000 | (self.mode & 0o7777 if self.mode is not None else 0o555)
        times = []
        for value in (self.atime, self.mtime, self.ctime):
            if value is None:
                value = self.mtime if self.mtime is not None else EPOCH
            times.append(int(value.timestamp()))
        uid = self.uid if self.uid is not None else 1000
        gid = self.gid if self.gid is not None else 1000
        return os.stat_result((st_mode, self.inode, 1, 1, uid, gid, self.get_size(), times[0], times[1], times[2]))

    async def get_console_output(self):
        for child in await self.list_children():
            st = await child.stat()
            perms = 'd' if child.is_directory() is True else ('l' if child.is_symlink() is True else ' ')
            for shift in (6, 3, 0):
                bits = (st.st_mode >> shift) & 7
                perms += ('r' if bits & 4 else '-') + ('w' if bits & 2 else '-') + ('x' if bits & 1 else '-')
            name = child.name
            if child.is_symlink() is True:
                name += ' -> %s' % child.symlink_target
            yield '%s\t%s\t%s\t%s' % (perms, child.get_size(), child.mtime if child.mtime is not None else '', name)

    def __str__(self):
        return '%s %s' % ('DIR ' if self.is_directory() is True else 'FILE', self.name)


class OpticalFileSystem(FileSystem):
    """Base of the filesystems of the optical discs, reading the image from a datasource"""
    def __init__(self, ds):
        self.ds = ds
        self.root:OpticalEntry = None
        self.volume_name = ''

    async def read_at(self, offset:int, size:int):
        await self.ds.seek(offset, 0)
        data = await self.ds.read(size)
        if len(data) != size:
            raise Exception('The image is truncated, %s bytes at %s can not be read' % (size, offset))
        return data

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
        if mode != 'rb':
            raise Exception('Only binary read mode is supported')
        entry = await self.get_record_by_path(path)
        if entry is None:
            raise FileNotFoundError(path)
        if entry.is_directory() is True:
            raise IsADirectoryError(path)
        if entry.is_symlink() is True:
            # as readlink does: the contents is the target
            target = entry.symlink_target.encode()
            return ExtentFile(self.ds, [], len(target), path, target)
        extents, data = await entry.get_extents()
        return ExtentFile(self.ds, extents, entry.get_size(), path, data)

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
