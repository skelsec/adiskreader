# -*- coding: utf-8 -*-

"""Implementation of `FatIO` for basic I/O."""
import errno
import io
import threading
from typing import Union, Optional

#from fs.mode import Mode

from adiskreader.filesystems.fat import FAT


class FatIO(io.RawIOBase):
    """Wrap basic I/O operations for FAT."""

    def __init__(self, fs: FAT,
                 path: str,
                 mode ) -> None: #Mode = Mode('r')
        """Wrap basic I/O operations for FAT. **Currently read-only**.

        :param fs: `FAT`: Instance of opened filesystem
        :param path: `str`: Path to file. If `mode` is *r*,
                            the file must exist.
        :param mode: `Mode`: Mode to open file in.
        """
        super(FatIO, self).__init__()
        self.mode = mode
        self.fs = fs
        self.name = str(path)
        # TODO: File locking
        self._lock = threading.Lock()
        self.dir_entry = None
        

        #: Position in bytes from beginning of file
        self.__bpos = 0
        #: Current cluster chain number
        self.__cpos = None
        #: Current cluster chain index
        self.__cindex = 0
        #: Current cluster chain offset (in bytes)
        self.__coffpos = 0
    
    async def setup(self):
        self.dir_entry = await self.fs.root_dir.get_entry(self.name)
        print(type(self.dir_entry))
        input(self.dir_entry )
        if self.dir_entry.is_directory() or self.dir_entry.is_special():
            raise IsADirectoryError(errno.EISDIR, self.name)
        elif self.dir_entry.is_volume_id():
            raise FileNotFoundError(errno.ENOENT, self.name)

        self.__cpos = self.dir_entry.get_cluster()

    def __repr__(self) -> str:
        """Readable representation of class instance.

        ex: <FatFile fs=<PyFat object> path="/README.txt" mode="r">
        """
        return str(f'<{self.__class__.__name__} '
                   f'fs={self.fs} '
                   f'path="{self.name}" '
                   f'mode="{self.mode}"')

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to a given offset in the file.

        :param offset: ``int``: offset in bytes in the file
        :param whence: ``int``: offset position:
                       - ``0``: absolute
                       - ``1``: relative to current position
                       - ``2``: relative to file end
        :returns: New position in bytes in the file
        """
        if whence == 1:
            offset += self.__bpos
        elif whence == 2:
            offset += self.dir_entry.get_size()
        elif whence != 0:
            raise ValueError(f"Invalid whence {whence}, should be 0, 1 or 2")

        offset = min(offset, self.dir_entry.filesize)
        prev_index = self.__cindex

        self.__cindex = offset // self.fs.bytes_per_cluster
        self.__coffpos = offset % self.fs.bytes_per_cluster
        self.__bpos = offset

        if self.__bpos == self.dir_entry.filesize and \
                self.__bpos > 0 and self.__coffpos == 0:
            # We are currently at the end of the last cluster, there is no
            # next cluster so go back to the end of the previous cluster
            self.__coffpos = self.fs.bytes_per_cluster
            self.__cindex -= 1

        # If we go back, we have to start from the beginning of the file
        if self.__cindex < prev_index:
            self.__cpos = self.dir_entry.get_cluster()
            prev_index = 0

        if self.__cindex > prev_index:
            fp = self.fs.get_cluster_chain(self.__cpos)
            for _ in range(0, self.__cindex - prev_index + 1):
                self.__cpos = next(fp)

        return self.__bpos

    def seekable(self) -> bool:
        """FAT I/O driver is able to seek in files.

        :returns: `True`
        """
        return True

    def close(self) -> None:
        """Close open file handles assuming lock handle."""
        self.seek(0)
        super().close()

    def readable(self) -> bool:
        """Determine whether or not the file is readable."""
        return self.mode.reading

    async def read(self, size: int = -1) -> Union[bytes, None]:
        """Read given bytes from file."""

        # Set size boundary
        print('Size: %s' % size)
        print('__bpos: %s' % self.__bpos)
        print('Fsize: %s' % self.dir_entry.filesize)
        if size + self.__bpos > self.dir_entry.filesize or size < 0:
            size = self.dir_entry.filesize - self.__bpos

        if size == 0:
            return b""

        chunks = []
        read_bytes = 0
        cluster_offset = self.__coffpos
        for c in self.fs.get_cluster_chain(self.__cpos):
            chunk_size = self.fs.bytes_per_cluster - cluster_offset
            # Do not read past EOF
            if read_bytes + chunk_size > size:
                chunk_size = size - read_bytes

            chunk = await self.fs.read_cluster_contents(c)
            chunk = chunk[cluster_offset:][:chunk_size]
            cluster_offset = 0
            chunks.append(chunk)
            read_bytes += chunk_size
            if read_bytes == size:
                break

        self.seek(read_bytes, 1)

        chunks = b"".join(chunks)
        if len(chunks) != size:
            raise RuntimeError("Read a different amount of data "
                               "than was requested.")
        return chunks

    async def readinto(self, __buffer: bytearray) -> Optional[int]:
        """Read data "directly" into bytearray."""
        data = await self.read(len(__buffer))
        bytes_read = len(data)
        __buffer[:bytes_read] = data
        return bytes_read
