# -*- coding: utf-8 -*-

"""
Python FAT filesystem module with :doc:`PyFilesystem2 <pyfilesystem2:index>` \
compatibility.

pyfatfs allows interaction with FAT12/16/32 filesystems, either via
:doc:`PyFilesystem2 <pyfilesystem2:index>` for file-level abstraction
or direct interaction with the filesystem for low-level access.
"""

#: Specifies default ("OEM") encoding
from adiskreader.filesystems.fat._exceptions import PyFATException

FAT_OEM_ENCODING = 'ibm437'
#: Specifies the long file name encoding, which is always UTF-16 (LE)
FAT_LFN_ENCODING = 'utf-16-le'


def _init_check(func):
    def _wrapper(*args, **kwargs):
        initialized = args[0].initialized

        if initialized is True:
            return func(*args, **kwargs)
        else:
            raise PyFATException("Class has not yet been fully initialized, "
                                 "please instantiate first.")

    return _wrapper


#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""FAT and BPB parsing for files."""

import datetime
import errno

import math
import struct
import threading
import time
import warnings

from contextlib import contextmanager
from io import FileIO, open, BytesIO, IOBase, SEEK_END
from os import PathLike
from typing import Union

from adiskreader.filesystems.fat.EightDotThree import EightDotThree
from adiskreader.filesystems.fat.FATDirectoryEntry import FATDirectoryEntry, FATLongDirectoryEntry
from adiskreader.filesystems.fat.FSInfo import FSInfo
from adiskreader.filesystems.fat._exceptions import PyFATException, NotAFatEntryException
from adiskreader.filesystems.fat.BootSectorHeader import BootSectorHeader, FAT12BootSectorHeader, \
                                     FAT32BootSectorHeader

from adiskreader.disks import Disk
from adiskreader.partitions import Partition
from adiskreader.filesystems import FileSystem


class FAT(FileSystem):
    """PyFAT base class, parses generic filesystem information."""

    #: Used as fat_type if unable to detect FAT type
    FAT_TYPE_UNKNOWN = 0
    #: Used as fat_type if FAT12 fs has been detected
    FAT_TYPE_FAT12 = 12
    #: Used as fat_type if FAT16 fs has been detected
    FAT_TYPE_FAT16 = 16
    #: Used as fat_type if FAT32 fs has been detected
    FAT_TYPE_FAT32 = 32

    #: Maps fat_type to BS_FilSysType from FS header information
    FS_TYPES = {FAT_TYPE_UNKNOWN: b"FAT     ",
                FAT_TYPE_FAT12: b"FAT12   ",
                FAT_TYPE_FAT16: b"FAT16   ",
                FAT_TYPE_FAT32: b"FAT32   "}

    #: Possible cluster values for FAT12 partitions
    FAT12_CLUSTER_VALUES = {'FREE_CLUSTER': 0x000,
                            'MIN_DATA_CLUSTER': 0x002,
                            'MAX_DATA_CLUSTER': 0xFEF,
                            'BAD_CLUSTER': 0xFF7,
                            'END_OF_CLUSTER_MIN': 0xFF8,
                            'END_OF_CLUSTER_MAX': 0xFFF}
    FAT12_SPECIAL_EOC = 0xFF0
    #: Possible cluster values for FAT16 partitions
    FAT16_CLUSTER_VALUES = {'FREE_CLUSTER': 0x0000,
                            'MIN_DATA_CLUSTER': 0x0002,
                            'MAX_DATA_CLUSTER': 0xFFEF,
                            'BAD_CLUSTER': 0xFFF7,
                            'END_OF_CLUSTER_MIN': 0xFFF8,
                            'END_OF_CLUSTER_MAX': 0xFFFF}
    #: Possible cluster values for FAT32 partitions
    FAT32_CLUSTER_VALUES = {'FREE_CLUSTER': 0x0000000,
                            'MIN_DATA_CLUSTER': 0x0000002,
                            'MAX_DATA_CLUSTER': 0x0FFFFFEF,
                            'BAD_CLUSTER': 0xFFFFFF7,
                            'END_OF_CLUSTER_MIN': 0xFFFFFF8,
                            'END_OF_CLUSTER_MAX': 0xFFFFFFF}
    #: Maps fat_type to possible cluster values
    FAT_CLUSTER_VALUES = {FAT_TYPE_FAT12: FAT12_CLUSTER_VALUES,
                          FAT_TYPE_FAT16: FAT16_CLUSTER_VALUES,
                          FAT_TYPE_FAT32: FAT32_CLUSTER_VALUES}

    #: FAT16 bit mask for clean shutdown bit
    FAT16_CLEAN_SHUTDOWN_BIT_MASK = 0x8000
    #: FAT16 bit mask for volume error bit
    FAT16_DRIVE_ERROR_BIT_MASK = 0x4000
    #: FAT32 bit mask for clean shutdown bit
    FAT32_CLEAN_SHUTDOWN_BIT_MASK = 0x8000000
    #: FAT32 bit mask for volume error bit
    FAT32_DRIVE_ERROR_BIT_MASK = 0x4000000

    #: Dirty bit in FAT header
    FAT_DIRTY_BIT_MASK = 0x01

    def __init__(self,
                 disk: Disk,
                 start_lba: int,
                 encoding: str = 'ibm437'):
        """Set up PyFat class instance.

        :param encoding: Define encoding to use for filenames
        :param offset: Offset of the FAT partition in the given file
        :type encoding: str
        :type offset: int
        """
        self.__disk = disk
        self.__start_lba = start_lba        
        self._fat_size = 0
        self.bpb_header: BootSectorHeader = None
        self.root_dir = None
        self.root_dir_sector = 0
        self.root_dir_sectors = 0
        self.bytes_per_cluster = 0
        self.first_data_sector = 0
        self.first_free_cluster = 0
        self.fat_type = self.FAT_TYPE_UNKNOWN
        self.fat = {}
        self.initialized = False
        self.encoding = encoding
        self.is_read_only = True
        self.lazy_load = True
    
    async def setup(self):
        await self.parse_header()
        await self._parse_fat()
        await self.parse_root_dir() 

    @_init_check
    async def read_cluster_contents(self, cluster: int) -> bytes:
        """Read contents of given cluster.

        :param cluster: Cluster number to read contents from
        :returns: Contents of cluster as `bytes`
        """
        sz = self.bytes_per_cluster
        cluster_address = self.get_data_cluster_address(cluster)
        start_lba = self.__start_lba + (cluster_address // self.bpb_header["BPB_BytsPerSec"])
        end_lba = start_lba + math.ceil(sz / self.bpb_header["BPB_BytsPerSec"])
        data = await self.__disk.read_LBAs(range(start_lba, end_lba+1))
        return data[:sz]

    def __get_clean_shutdown_bitmask(self):
        """Get clean shutdown bitmask for current FS.

        :raises: AttributeError
        """
        return getattr(self, f"FAT{self.fat_type}_CLEAN_SHUTDOWN_BIT_MASK")

    def _is_dirty(self) -> bool:
        """Check whether or not the partition currently is dirty."""
        try:
            clean_shutdown_bitmask = self.__get_clean_shutdown_bitmask()
        except AttributeError:
            # Bit not set on FAT12
            dos_dirty = False
        else:
            dos_dirty = (self.fat[1] &
                         clean_shutdown_bitmask) != clean_shutdown_bitmask

        nt_dirty = (self.bpb_header["BS_Reserved1"] &
                    self.FAT_DIRTY_BIT_MASK) == self.FAT_DIRTY_BIT_MASK

        return dos_dirty or nt_dirty

    def _get_total_sectors(self):
        """Get total number of sectors for all FAT sizes."""
        if self.bpb_header["BPB_TotSec16"] != 0:
            return self.bpb_header["BPB_TotSec16"]

        return self.bpb_header["BPB_TotSec32"]

    def _get_fat_size_count(self):
        """Get BPB_FATsz value."""
        if self.bpb_header["BPB_FATSz16"] != 0:
            return self.bpb_header["BPB_FATSz16"]

        try:
            return self.bpb_header["BPB_FATSz32"]
        except KeyError:
            raise PyFATException("Invalid FAT size of 0 detected in header, "
                                 "cannot continue")

    @_init_check
    async def _parse_fat(self):
        """Parse information in FAT."""
        # Read all FATs
        fat_size = self.bpb_header["BPB_BytsPerSec"]
        fat_size *= self._fat_size

        # Seek FAT entries
        first_fat_bytes = self.bpb_header["BPB_RsvdSecCnt"]
        first_fat_bytes *= self.bpb_header["BPB_BytsPerSec"]
        fats = []
        #for i in range(self.bpb_header["BPB_NumFATs"]):
        #    with self.__lock:
        #        self.__seek(first_fat_bytes + (i * fat_size))
        #        fats += [self.__fp.read(fat_size)]

        start_lba = self.__start_lba + self.bpb_header["BPB_RsvdSecCnt"]
        for i in range(self.bpb_header["BPB_NumFATs"]):
            end_lba = start_lba + self._fat_size
            fatdata = await self.__disk.read_LBAs(range(start_lba, end_lba+1))
            fats += [fatdata[:fat_size]]
            start_lba = end_lba
        
        if len(fats) < 1:
            raise PyFATException("Invalid number of FATs configured, "
                                 "cannot continue")
        elif len(set(fats)) > 1:
            warnings.warn("One or more FATs differ, filesystem most "
                          "likely corrupted. Using first FAT.")

        # Parse first FAT
        self.bytes_per_cluster = self.bpb_header["BPB_BytsPerSec"] * \
            self.bpb_header["BPB_SecPerClus"]

        if len(fats[0]) != self.bpb_header["BPB_BytsPerSec"] * self._fat_size:
            raise PyFATException("Invalid length of FAT")

        # FAT12: 12 bits (1.5 bytes) per FAT entry
        # FAT16: 16 bits (2 bytes) per FAT entry
        # FAT32: 32 bits (4 bytes) per FAT entry
        fat_entry_size = self.fat_type / 8
        total_entries = int(fat_size // fat_entry_size)
        self.fat = [None] * total_entries

        curr = 0
        cluster = 0
        incr = self.fat_type / 8
        while curr < fat_size:
            offset = curr + incr

            if self.fat_type == self.FAT_TYPE_FAT12:
                fat_nibble = fats[0][int(curr):math.ceil(offset)]
                fat_nibble = fat_nibble.ljust(2, b"\0")
                try:
                    self.fat[cluster] = struct.unpack("<H", fat_nibble)[0]
                except IndexError:
                    # Out of bounds, FAT size is not cleanly divisible by 3
                    # Do not touch last clusters
                    break

                if cluster % 2 == 0:
                    # Even: Keep low 12-bits of word
                    self.fat[cluster] &= 0x0FFF
                else:
                    # Odd: Keep high 12-bits of word
                    self.fat[cluster] >>= 4

                if math.ceil(offset) == (fat_size - 1):
                    # Sector boundary case for FAT12
                    del self.fat[-1]
                    break

            elif self.fat_type == self.FAT_TYPE_FAT16:
                self.fat[cluster] = struct.unpack("<H",
                                                  fats[0][int(curr):
                                                          int(offset)])[0]
            elif self.fat_type == self.FAT_TYPE_FAT32:
                self.fat[cluster] = struct.unpack("<L",
                                                  fats[0][int(curr):
                                                          int(offset)])[0]
                # Ignore first four bits, FAT32 clusters are
                # actually just 28bits long
                self.fat[cluster] &= 0x0FFFFFFF
            else:
                raise PyFATException("Unknown FAT type, cannot continue")

            curr += incr
            cluster += 1

        if None in self.fat:
            raise AssertionError("Unknown error during FAT parsing, please "
                                 "report this error.")

    @_init_check
    def __bytes__(self):
        """Represent current state of FAT as bytes.

        :returns: `bytes` representation of FAT.
        """
        b = b''
        if self.fat_type == self.FAT_TYPE_FAT12:
            for i, e in enumerate(self.fat):
                if i % 2 == 0:
                    b += struct.pack("<H", e)
                else:
                    nibble = b[-1:]
                    nibble = struct.unpack("<B", nibble)[0]
                    b = b[:-1]
                    b += struct.pack("<BB", ((e & 0xF) << 4) | nibble, e >> 4)

        else:
            if self.fat_type == self.FAT_TYPE_FAT16:
                fmt = "H"
            else:
                # FAT32
                fmt = "L"

            b = struct.pack(f"<{fmt * len(self.fat)}",
                            *self.fat)
        return b

    def calc_num_clusters(self, size: int = 0) -> int:
        """Calculate the number of required clusters.

        :param size: `int`: required bytes to allocate
        :returns: Number of required clusters
        """
        num_clusters = size / self.bytes_per_cluster
        num_clusters = math.ceil(num_clusters)

        return num_clusters

    async def _fat12_parse_root_dir(self):
        """Parse FAT12/16 root dir entries.

        FAT12/16 has a fixed location of root directory entries
        and is therefore size limited (BPB_RootEntCnt).
        """
        root_dir_byte = self.root_dir_sector * \
            self.bpb_header["BPB_BytsPerSec"]
        self.root_dir.set_cluster(self.root_dir_sector //
                                  self.bpb_header["BPB_SecPerClus"])
        max_bytes = self.bpb_header["BPB_RootEntCnt"] * \
            FATDirectoryEntry.FAT_DIRECTORY_HEADER_SIZE

        # Parse all directory entries in root directory
        subdirs, _ = await self.parse_dir_entries_in_address(root_dir_byte,
                                                       root_dir_byte +
                                                       max_bytes)
        for dir_entry in subdirs:
            await self.root_dir.add_subdirectory(dir_entry)

    async def _fat32_parse_root_dir(self):
        """Parse FAT32 root dir entries.

        FAT32 actually has its root directory entries distributed
        across a cluster chain that we need to follow
        """
        root_cluster = self.bpb_header["BPB_RootClus"]
        self.root_dir.set_cluster(root_cluster)

        # Follow root directory cluster chain
        async for dir_entry in self.parse_dir_entries_in_cluster_chain(root_cluster):
            await self.root_dir.add_subdirectory(dir_entry,
                                           recursive=not self.lazy_load)

    async def parse_root_dir(self):
        """Parse root directory entry."""
        root_dir_sfn = EightDotThree()
        root_dir_sfn.set_str_name("")
        dir_attr = FATDirectoryEntry.ATTR_DIRECTORY
        self.root_dir = FATDirectoryEntry(fs=self,
                                          DIR_Name=root_dir_sfn,
                                          DIR_Attr=dir_attr,
                                          DIR_NTRes=0,
                                          DIR_CrtTimeTenth=0,
                                          DIR_CrtTime=0,
                                          DIR_CrtDate=0,
                                          DIR_LstAccessDate=0,
                                          DIR_FstClusHI=0,
                                          DIR_WrtTime=0,
                                          DIR_WrtDate=0,
                                          DIR_FstClusLO=0,
                                          DIR_FileSize=0,
                                          encoding=self.encoding)

        if self.fat_type in [self.FAT_TYPE_FAT12, self.FAT_TYPE_FAT16]:
            await self._fat12_parse_root_dir()
        else:
            await self._fat32_parse_root_dir()

    async def parse_lfn_entry(self,
                        lfn_entry: FATLongDirectoryEntry,
                        lfn_dir_data: bytes):
        """Parse LFN entry at given address."""
        dir_hdr_sz = FATDirectoryEntry.FAT_DIRECTORY_HEADER_SIZE

        #with self.__lock:
        #    self.__seek(address)
        #    lfn_dir_data = self.__fp.read(dir_hdr_sz)

        lfn_hdr_layout = FATLongDirectoryEntry.FAT_LONG_DIRECTORY_LAYOUT
        lfn_dir_hdr = struct.unpack(lfn_hdr_layout, lfn_dir_data)
        lfn_dir_hdr = dict(zip(FATLongDirectoryEntry.FAT_LONG_DIRECTORY_VARS,
                               lfn_dir_hdr))

        lfn_entry.add_lfn_entry(**lfn_dir_hdr)

    def __parse_dir_entry(self, dir_data):
        """Parse directory entry at given address."""
        #with self.__lock:
        #    self.__seek(address)
        #    dir_hdr_size = FATDirectoryEntry.FAT_DIRECTORY_HEADER_SIZE
        #    dir_data = self.__fp.read(dir_hdr_size)

        dir_hdr = struct.unpack(FATDirectoryEntry.FAT_DIRECTORY_LAYOUT,
                                dir_data)
        dir_hdr = dict(zip(FATDirectoryEntry.FAT_DIRECTORY_VARS, dir_hdr))
        return dir_hdr

    async def parse_dir_entries_in_address(self,
                                     address: int = 0,
                                     max_address: int = 0,
                                     tmp_lfn_entry: FATLongDirectoryEntry =
                                     None):
        """Parse directory entries in address range."""
        if tmp_lfn_entry is None:
            tmp_lfn_entry = FATLongDirectoryEntry()

        dir_hdr_size = FATDirectoryEntry.FAT_DIRECTORY_HEADER_SIZE

        if max_address == 0:
            max_address = FATDirectoryEntry.FAT_DIRECTORY_HEADER_SIZE

        dir_entries = []

        start_lba = self.__start_lba + (address // self.bpb_header["BPB_BytsPerSec"]) #here we're taking it for granted that the start address is a multiple of the sector size
        end_lba = self.__start_lba + math.ceil((address + max_address) / self.bpb_header["BPB_BytsPerSec"])
        dir_datas = await self.__disk.read_LBAs(range(start_lba, end_lba+1))

        for i in range((max_address-address)//dir_hdr_size):
            dir_data = dir_datas[i*dir_hdr_size:(i+1)*dir_hdr_size]
            # Parse each entry
            dir_hdr = self.__parse_dir_entry(dir_data)
            dir_sn = EightDotThree(encoding=self.encoding)
            dir_first_byte = dir_hdr["DIR_Name"][0]
            try:
                dir_sn.set_byte_name(dir_hdr["DIR_Name"])
            except NotAFatEntryException as ex:
                # Not a directory of any kind, invalidate temporary LFN entries
                tmp_lfn_entry = FATLongDirectoryEntry()
                if ex.free_type == FATDirectoryEntry.FREE_DIR_ENTRY_MARK:
                    # Empty directory entry,
                    continue
                elif ex.free_type == FATDirectoryEntry.LAST_DIR_ENTRY_MARK:
                    # Last directory entry, do not parse any further
                    break
            else:
                dir_hdr["DIR_Name"] = dir_sn

            # Long File Names
            if FATLongDirectoryEntry.is_lfn_entry(dir_first_byte,
                                                  dir_hdr["DIR_Attr"]):
                await self.parse_lfn_entry(tmp_lfn_entry, dir_data) #TODO: check if this is correct
                continue

            # Normal directory entries
            if not tmp_lfn_entry.is_lfn_entry_complete():
                # Ignore incomplete LFN entries altogether
                tmp_lfn_entry = None

            dir_entry = FATDirectoryEntry(fs=self,
                                          encoding=self.encoding,
                                          lfn_entry=tmp_lfn_entry,
                                          lazy_load=self.lazy_load,
                                          **dir_hdr)
            dir_entries += [dir_entry]

            if not self.lazy_load:
                if dir_entry.is_directory() and not dir_entry.is_special():
                    # Iterate all subdirectories except for dot and dotdot
                    cluster = dir_entry.get_cluster()
                    subdirs = self.parse_dir_entries_in_cluster_chain(cluster)
                    for d in subdirs:
                        dir_entry.add_subdirectory(d)

            # Reset temporary LFN entry
            tmp_lfn_entry = FATLongDirectoryEntry()

        return dir_entries, tmp_lfn_entry

    async def parse_dir_entries_in_cluster_chain(self, cluster) -> list:
        """Parse directory entries while following given cluster chain."""
        #dir_entries = []
        tmp_lfn_entry = FATLongDirectoryEntry()
        max_bytes = (self.bpb_header["BPB_SecPerClus"] *
                     self.bpb_header["BPB_BytsPerSec"])
        for c in self.get_cluster_chain(cluster):
            # Parse all directory entries in chain
            b = self.get_data_cluster_address(c)
            ret = await self.parse_dir_entries_in_address(b, b+max_bytes,
                                                    tmp_lfn_entry)
            tmp_dir_entries, tmp_lfn_entry = ret
            for dir_entry in tmp_dir_entries:
                yield dir_entry
            #dir_entries += tmp_dir_entries

        #return dir_entries

    def get_data_cluster_address(self, cluster: int) -> int:
        """Get offset of given cluster in bytes.

        :param cluster: Cluster number as `int`
        :returns: Bytes address location of cluster
        """
        # First two cluster entries are reserved
        sector = (cluster - 2) * self.bpb_header["BPB_SecPerClus"] + \
            self.first_data_sector
        return sector * self.bpb_header["BPB_BytsPerSec"]

    @_init_check
    def get_cluster_chain(self, first_cluster):
        """Follow a cluster chain beginning with the first cluster address."""
        cluster_vals = self.FAT_CLUSTER_VALUES[self.fat_type]
        min_data_cluster = cluster_vals["MIN_DATA_CLUSTER"]
        max_data_cluster = cluster_vals["MAX_DATA_CLUSTER"]
        eoc_min = cluster_vals["END_OF_CLUSTER_MIN"]
        eoc_max = cluster_vals["END_OF_CLUSTER_MAX"]

        i = first_cluster
        while i <= len(self.fat):
            if min_data_cluster <= self.fat[i] <= max_data_cluster:
                # Normal data cluster, follow chain
                yield i
            elif self.fat_type == self.FAT_TYPE_FAT12 and \
                    self.fat[i] == self.FAT12_SPECIAL_EOC:
                # Special EOC
                yield i
                return
            elif eoc_min <= self.fat[i] <= eoc_max:
                # End of cluster, end chain
                yield i
                return
            elif self.fat[i] == cluster_vals["BAD_CLUSTER"]:
                # Bad cluster, cannot follow chain, file broken!
                raise PyFATException("Bad cluster found in FAT cluster "
                                     "chain, cannot access file")
            elif self.fat[i] == cluster_vals["FREE_CLUSTER"]:
                # FREE_CLUSTER mark when following a chain is treated an error
                raise PyFATException("FREE_CLUSTER mark found in FAT cluster "
                                     "chain, cannot access file")
            else:
                raise PyFATException("Invalid or unknown FAT cluster "
                                     "entry found with value "
                                     "\'{}\'".format(hex(self.fat[i])))

            i = self.fat[i]

    @_init_check
    def close(self):
        """Close session and free up all handles."""
        if not self.is_read_only:
            self._mark_clean()

        #self.__fp.close()
        self.initialized = False

    def __del__(self):
        """Try to close open handles."""
        try:
            self.close()
        except PyFATException:
            pass

    def __determine_fat_type(self) -> Union["FAT.FAT_TYPE_FAT12",
                                            "FAT.FAT_TYPE_FAT16",
                                            "FAT.FAT_TYPE_FAT32"]:
        """Determine FAT type.

        An internal method to determine whether this volume is FAT12,
        FAT16 or FAT32.

        returns: `str`: Any of PyFat.FAT_TYPE_FAT12, PyFat.FAT_TYPE_FAT16
                 or PyFat.FAT_TYPE_FAT32
        """
        total_sectors = self._get_total_sectors()
        rsvd_sectors = self.bpb_header["BPB_RsvdSecCnt"]
        fat_sz = self.bpb_header["BPB_NumFATs"] * self._fat_size
        root_dir_sectors = self.root_dir_sectors
        data_sec = total_sectors - (rsvd_sectors + fat_sz + root_dir_sectors)
        count_of_clusters = data_sec // self.bpb_header["BPB_SecPerClus"]

        if count_of_clusters < 4085:
            msft_fat_type = self.FAT_TYPE_FAT12
        elif count_of_clusters < 65525:
            msft_fat_type = self.FAT_TYPE_FAT16
        else:
            msft_fat_type = self.FAT_TYPE_FAT32

        if self.bpb_header["BPB_FATSz16"] == 0:
            if self.bpb_header["BPB_FATSz32"] != 0:
                linux_fat_type = self.FAT_TYPE_FAT32
            else:
                linux_fat_type = msft_fat_type
        elif count_of_clusters >= 4085:
            linux_fat_type = self.FAT_TYPE_FAT16
        else:
            linux_fat_type = self.FAT_TYPE_FAT12

        if msft_fat_type != linux_fat_type:
            warnings.warn(f"Unable to reliably determine FAT type, "
                          f"guessing either FAT{msft_fat_type} or "
                          f"FAT{linux_fat_type}. Opting for "
                          f"FAT{linux_fat_type}.")
        return linux_fat_type

    async def parse_header(self):
        """Parse BPB & FAT headers in opened file."""
        boot_sector = await self.__disk.read_LBA(self.__start_lba)

        self.bpb_header = BootSectorHeader()
        self.bpb_header.parse_header(boot_sector[:36])

        # Verify BPB headers
        self.__verify_bpb_header()

        # Parse FAT type specific header
        self.bpb_header = FAT12BootSectorHeader() \
            if self.bpb_header["BPB_FATSz16"] > 0 else FAT32BootSectorHeader()
        self.bpb_header.parse_header(boot_sector)

        # Determine FAT type
        self._fat_size = self._get_fat_size_count()
        self.fat_type = self.__determine_fat_type()

        # Calculate root directory sectors and starting point of root directory
        root_entries = self.bpb_header["BPB_RootEntCnt"]
        hdr_size = FATDirectoryEntry.FAT_DIRECTORY_HEADER_SIZE
        bytes_per_sec = self.bpb_header["BPB_BytsPerSec"]
        rsvd_secs = self.bpb_header["BPB_RsvdSecCnt"]
        num_fats = self.bpb_header["BPB_NumFATs"]

        self.root_dir_sectors = ((root_entries * hdr_size) +
                                 (bytes_per_sec - 1)) // bytes_per_sec
        self.root_dir_sector = rsvd_secs + (self._fat_size * num_fats)

        # Calculate first data sector
        self.first_data_sector = (rsvd_secs + (num_fats * self._fat_size) +
                                  self.root_dir_sectors)

        # Check signature
        signature = boot_sector[510:512]
        if signature != b'\x55\xAA':
            raise PyFATException(f"Invalid signature: \'{signature.hex()}\'.")

        # Initialization finished
        self.initialized = True

    def __verify_bpb_header(self):
        """Verify BPB header for correctness."""
        if self.bpb_header["BS_jmpBoot"][0] == 0xEB:
            if self.bpb_header["BS_jmpBoot"][2] != 0x90:
                raise PyFATException("Boot code must end with 0x90")
        elif self.bpb_header["BS_jmpBoot"][0] == 0xE9:
            pass
        else:
            raise PyFATException("Boot code must start with 0xEB or "
                                 "0xE9. Is this a FAT partition?")

        #: 512,1024,2048,4096: As per fatgen103.doc
        byts_per_sec_range = [2**x for x in range(9, 13)]
        if self.bpb_header["BPB_BytsPerSec"] not in byts_per_sec_range:
            raise PyFATException(f"Expected one of {byts_per_sec_range} "
                                 f"bytes per sector, got: "
                                 f"\'{self.bpb_header['BPB_BytsPerSec']}\'.")

        #: 1,2,4,8,16,32,64,128: As per fatgen103.doc
        sec_per_clus_range = [2**x for x in range(8)]
        if self.bpb_header["BPB_SecPerClus"] not in sec_per_clus_range:
            raise PyFATException(f"Expected one of {sec_per_clus_range} "
                                 f"sectors per cluster, got: "
                                 f"\'{self.bpb_header['BPB_SecPerClus']}\'.")

        bytes_per_cluster = self.bpb_header["BPB_BytsPerSec"]
        bytes_per_cluster *= self.bpb_header["BPB_SecPerClus"]
        if bytes_per_cluster > 32768:
            warnings.warn("Bytes per cluster should not be more than 32K, "
                          "but got: {}K. Trying to continue "
                          "anyway.".format(bytes_per_cluster // 1024), Warning)

        if self.bpb_header["BPB_RsvdSecCnt"] == 0:
            raise PyFATException("Number of reserved sectors must not be 0")

        if self.bpb_header["BPB_Media"] not in [0xf0, 0xf8, 0xf9, 0xfa, 0xfb,
                                                0xfc, 0xfd, 0xfe, 0xff]:
            raise PyFATException("Invalid media type")

        if self.bpb_header["BPB_NumFATs"] < 1:
            raise PyFATException("At least one FAT expected, None found.")

        root_entry_count = self.bpb_header["BPB_RootEntCnt"] * 32
        root_entry_count %= self.bpb_header["BPB_BytsPerSec"]
        if self.bpb_header["BPB_RootEntCnt"] != 0 and root_entry_count != 0:
            raise PyFATException("Root entry count does not cleanly align with"
                                 " bytes per sector!")

        if self.bpb_header["BPB_TotSec16"] == 0 and \
                self.bpb_header["BPB_TotSec32"] == 0:
            raise PyFATException("16-Bit and 32-Bit total sector count "
                                 "value empty.")
        

    async def getinfo(self, path: str, namespaces=None):
        """Generate PyFilesystem2's `Info` struct.

        :param path: Path to file or directory on filesystem
        :param namespaces: Info namespaces to query, `NotImplemented`
        :returns: `Info`
        """
        
        entry = await self.root_dir.get_entry(path)
        info = {"basic": {"name": repr(entry),
                          "is_dir": entry.is_directory()},
                "details": {"accessed": entry.get_atime().timestamp(),
                            "created": entry.get_ctime().timestamp(),
                            "metadata_changed": None,
                            "modified": entry.get_mtime().timestamp(),
                            "size": entry.filesize,
                            "type": self.gettype(path)}}
        return info
    
    @staticmethod
    async def from_disk(disk:Disk, start_lba:int, encoding=FAT_OEM_ENCODING):
        fs = FAT(disk, start_lba, encoding=encoding)
        await fs.setup()
        return fs
    
    @staticmethod
    async def from_partition(partition:Partition, encoding=FAT_OEM_ENCODING):
        return await FAT.from_disk(partition.disk, partition.start_LBA, encoding=encoding)
    
    async def open(self, path:str, mode:str = 'rb'):
        from adiskreader.filesystems.fat.FatIO import FatIO
        file = FatIO(self, path, mode)
        await file.setup()
        return file
    
    async def walk(self, path:str = "\\"):
        if path == "\\":
            async for root, dirs, files in self.root_dir.walk():
                if root == '/':
                    root = '\\'
                if root.startswith('\\') is False:
                    root = '\\' + root
                yield root, dirs, files
        else:
            entry = await self.root_dir.get_entry(path)
            if entry.is_file():
                raise NotADirectoryError
            async for root, dirs, files in entry.walk():
                if root == '/':
                    root = '\\'
                if root.startswith('\\') is False:
                    root = '\\' + root
                yield root, dirs, files
    
    async def stat(self, path:str):
        if path == "\\":
            return self.root_dir.stat()
        entry = await self.root_dir.get_entry(path)
        return await entry.stat()
    
    async def get_root(self):
        return self.root_dir
    
    async def get_record_by_path(self, path:str):
        return await self.root_dir.get_entry(path)