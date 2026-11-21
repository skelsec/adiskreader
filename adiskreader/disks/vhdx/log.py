
# MS-VHDX 2.3 Log
#
# The log is a circular buffer of entries. Each entry describes writes to the VHDX file
# which might not have reached their final location. An image with a non-zero LogGuid
# must have its log replayed before the BAT and the metadata can be trusted.
# As the datasource is read-only, the replay is done by an overlay on top of the datasource.

import io
import uuid
from typing import List
from adiskreader.datasource import DataSource
from adiskreader.utils import crc32c, ceil_d

LOG_SECTOR_SIZE = 4096
LOG_ENTRY_HEADER_SIZE = 64
LOG_DESCRIPTOR_SIZE = 32


def read_circular(log:bytes, offset:int, length:int):
    offset = offset % len(log)
    data = log[offset:offset+length]
    if len(data) < length:
        data += log[:length-len(data)]
    return data


class LogEntryHeader:
    def __init__(self):
        self.Signature = None
        self.Checksum = None
        self.EntryLength = None
        self.Tail = None
        self.SequenceNumber = None
        self.DescriptorCount = None
        self.Reserved = None
        self.LogGuid = None
        self.FlushedFileOffset = None
        self.LastFileOffset = None

    @staticmethod
    def from_bytes(data:bytes):
        return LogEntryHeader.from_buffer(io.BytesIO(data))

    @staticmethod
    def from_buffer(buff):
        header = LogEntryHeader()
        header.Signature = buff.read(4)
        header.Checksum = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        header.EntryLength = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        header.Tail = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        header.SequenceNumber = int.from_bytes(buff.read(8), byteorder='little', signed=False)
        header.DescriptorCount = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        header.Reserved = buff.read(4)
        header.LogGuid = uuid.UUID(bytes_le=buff.read(16))
        header.FlushedFileOffset = int.from_bytes(buff.read(8), byteorder='little', signed=False)
        header.LastFileOffset = int.from_bytes(buff.read(8), byteorder='little', signed=False)
        return header

    def __str__(self) -> str:
        t = '== LogEntryHeader ==\n'
        t += f'EntryLength: {self.EntryLength}\n'
        t += f'Tail: {self.Tail}\n'
        t += f'SequenceNumber: {self.SequenceNumber}\n'
        t += f'DescriptorCount: {self.DescriptorCount}\n'
        t += f'LogGuid: {self.LogGuid}\n'
        t += f'FlushedFileOffset: {self.FlushedFileOffset}\n'
        t += f'LastFileOffset: {self.LastFileOffset}\n'
        return t


class LogEntry:
    def __init__(self):
        self.offset = None # offset of the entry in the log
        self.header:LogEntryHeader = None
        self.writes = [] # (FileOffset, length, data), data is None for zeroed ranges

    @staticmethod
    def from_log(log:bytes, offset:int, logguid:uuid.UUID):
        """Parses the entry at the given offset of the log. Returns None if there is no valid entry."""
        header = LogEntryHeader.from_bytes(read_circular(log, offset, LOG_ENTRY_HEADER_SIZE))
        if header.Signature != b'loge' or header.LogGuid != logguid:
            return None
        if header.EntryLength == 0 or header.EntryLength % LOG_SECTOR_SIZE != 0 or header.EntryLength > len(log):
            return None

        raw = read_circular(log, offset, header.EntryLength)
        # the checksum covers the entire entry with the checksum field zeroed
        if crc32c(raw[:4] + b'\x00'*4 + raw[8:]) != header.Checksum:
            return None

        # the descriptors follow the header, the data sectors start on the next sector boundary
        data_offset = ceil_d(LOG_ENTRY_HEADER_SIZE + header.DescriptorCount * LOG_DESCRIPTOR_SIZE, LOG_SECTOR_SIZE) * LOG_SECTOR_SIZE
        if data_offset > header.EntryLength:
            return None

        entry = LogEntry()
        entry.offset = offset
        entry.header = header
        for i in range(header.DescriptorCount):
            pos = LOG_ENTRY_HEADER_SIZE + i * LOG_DESCRIPTOR_SIZE
            desc = raw[pos:pos+LOG_DESCRIPTOR_SIZE]
            signature = desc[:4]
            fileoffset = int.from_bytes(desc[16:24], byteorder='little', signed=False)
            if int.from_bytes(desc[24:32], byteorder='little', signed=False) != header.SequenceNumber:
                return None

            if signature == b'zero':
                zerolength = int.from_bytes(desc[8:16], byteorder='little', signed=False)
                entry.writes.append((fileoffset, zerolength, None))
            elif signature == b'desc':
                # the first 8 and the last 4 bytes of the sector are stored in the descriptor
                # because the data sector has its signature and sequence number there
                trailing = desc[4:8]
                leading = desc[8:16]
                sector = raw[data_offset:data_offset+LOG_SECTOR_SIZE]
                data_offset += LOG_SECTOR_SIZE
                if len(sector) != LOG_SECTOR_SIZE or sector[:4] != b'data':
                    return None
                sequence = sector[4:8] + sector[-4:] # high + low
                if int.from_bytes(sequence[4:] + sequence[:4], byteorder='little', signed=False) != header.SequenceNumber:
                    return None
                entry.writes.append((fileoffset, LOG_SECTOR_SIZE, leading + sector[8:-4] + trailing))
            else:
                return None
        return entry


def find_active_sequence(log:bytes, logguid:uuid.UUID) -> List[LogEntry]:
    """Returns the entries which have to be replayed, in order. Empty if the log has no valid sequence."""
    entries = {}
    def get_entry(offset):
        if offset not in entries:
            entries[offset] = LogEntry.from_log(log, offset, logguid)
        return entries[offset]

    active = []
    for tail in range(0, len(log), LOG_SECTOR_SIZE):
        if log[tail:tail+4] != b'loge':
            continue

        sequence = []
        offset = tail
        length = 0
        while True:
            entry = get_entry(offset)
            if entry is None:
                break
            if len(sequence) > 0 and entry.header.SequenceNumber != sequence[-1].header.SequenceNumber + 1:
                break
            length += entry.header.EntryLength
            if length > len(log):
                break
            sequence.append(entry)
            offset = (offset + entry.header.EntryLength) % len(log)

        if len(sequence) == 0:
            continue
        # the last entry (head) stores where its sequence starts
        head = sequence[-1]
        if head.header.Tail != tail:
            continue
        if len(active) == 0 or head.header.SequenceNumber > active[-1].header.SequenceNumber:
            active = sequence
    return active


class LogReplaySource(DataSource):
    """ A DataSource that applies the writes of the VHDX log on top of another DataSource """
    def __init__(self, ds:DataSource, entries:List[LogEntry]):
        super().__init__(ds.config)
        self.__stream = ds
        self.__offset = 0
        self.__writes = []
        for entry in entries:
            self.__writes.extend(entry.writes)
        self.__start = min([w[0] for w in self.__writes], default=0)
        self.__end = max([w[0] + w[1] for w in self.__writes], default=0)

    async def read(self, size:int):
        await self.__stream.seek(self.__offset, 0)
        data = await self.__stream.read(size)
        if self.__offset < self.__end and self.__offset + size > self.__start:
            data = bytearray(data)
            for fileoffset, length, wdata in self.__writes:
                start = max(fileoffset, self.__offset)
                end = min(fileoffset + length, self.__offset + size)
                if start >= end:
                    continue
                if wdata is None:
                    wdata = b'\x00' * (end - start)
                else:
                    wdata = wdata[start-fileoffset:end-fileoffset]
                # the log might write after the end of the file
                if len(data) < start - self.__offset:
                    data += b'\x00' * (start - self.__offset - len(data))
                data[start-self.__offset:end-self.__offset] = wdata
            data = bytes(data)
        self.__offset += len(data)
        return data

    async def seek(self, offset:int, whence:int = 0):
        await self.__stream.seek(offset, whence)
        self.__offset = await self.__stream.tell()

    async def close(self):
        await self.__stream.close()

    async def tell(self):
        return self.__offset
