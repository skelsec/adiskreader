import io
import uuid
from typing import List, Dict, Tuple

# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-vhdx/340e64a4-ae2a-4dc1-b19b-3dd9d57a3359

class Headers:
    def __init__(self):
        self.TFI = None
        self.Header1 = None
        self.Header2 = None
        self.RegionTable = None
        self.RegionTable2 = None
        self.Reserved = None
    
    @staticmethod
    def from_bytes(data:bytes):
        return Headers.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        data = io.BytesIO(buff.read(1*1024*1024)) # 1MB
        headers = Headers()
        headers.TFI = TFI.from_buffer(data)
        headers.Header1 = Header.from_buffer(data)
        headers.Header2 = Header.from_buffer(data)
        headers.RegionTable = RegionTable.from_buffer(data)
        headers.RegionTable2 = RegionTable.from_buffer(data)
        headers.Reserved = buff.read()
        return headers

    def __str__(self) -> str:
        t = '== Headers ==\n'
        t += f'TFI:\n{self.TFI}\n'
        t += f'Header1:\n{self.Header1}\n'
        t += f'Header2:\n{self.Header2}\n'
        t += f'RegionTable:\n{self.RegionTable}\n'
        t += f'RegionTable2:\n{self.RegionTable2}\n'
        t += f'Reserved: {self.Reserved.hex()}'
        return t


# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-vhdx/23ba288b-4eda-47bf-bd48-6386489d12af

class TFI:
    def __init__(self):
        self.Signature = None
        self.Creator = None
    
    @staticmethod
    def from_bytes(data:bytes):
        return TFI.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        data = io.BytesIO(buff.read(64*1024)) # 64KB
        tfi = TFI()
        tfi.Signature = data.read(8)
        tfi.Creator = data.read(512)
        return tfi
    
    def __str__(self) -> str:
        t = '== TFI ==\n'
        t += f'Signature: {self.Signature.hex()}\n'
        t += f'Creator: {self.Creator.decode()}'
        return t
    
# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-vhdx/39d641c2-093c-4d4a-8c9d-bd4b9fc2ff31
class Header:
    def __init__(self):
        self.Signature = None
        self.Checksum = None
        self.SequenceNumber = None
        self.FileWriteGuid = None
        self.DataWriteGuid = None
        self.LogGuid = None
        self.LogVersion = None
        self.Version = None
        self.LogLength = None
        self.LogOffset = None
        self.LogSequenceNumber = None
        self.LogChecksum = None
        self.Reserved = None
    
    @staticmethod
    def from_bytes(data:bytes):
        return Header.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        data = io.BytesIO(buff.read(64*1024)) # 64KB
        header = Header()
        header.Signature = data.read(8)
        header.Checksum = data.read(4)
        header.SequenceNumber = int.from_bytes(data.read(4), byteorder='little', signed=False)
        header.FileWriteGuid = uuid.UUID(bytes_le=data.read(16))
        header.DataWriteGuid = uuid.UUID(bytes_le=data.read(16))
        header.LogGuid = uuid.UUID(bytes_le=data.read(16))
        header.LogVersion = int.from_bytes(data.read(4), byteorder='little', signed=False)
        header.Version = int.from_bytes(data.read(4), byteorder='little', signed=False)
        header.LogLength = int.from_bytes(data.read(4), byteorder='little', signed=False)
        header.LogOffset = int.from_bytes(data.read(4), byteorder='little', signed=False)
        header.LogSequenceNumber = int.from_bytes(data.read(8), byteorder='little', signed=False)
        header.LogChecksum = data.read(4)
        header.Reserved = data.read(416)
        return header

    def __str__(self) -> str:
        t = '== Header ==\n'
        t += f'Signature: {self.Signature.hex()}\n'
        t += f'Checksum: {self.Checksum.hex()}\n'
        t += f'SequenceNumber: {self.SequenceNumber}\n'
        t += f'FileWriteGuid: {self.FileWriteGuid}\n'
        t += f'DataWriteGuid: {self.DataWriteGuid}\n'
        t += f'LogGuid: {self.LogGuid}\n'
        t += f'LogVersion: {self.LogVersion}\n'
        t += f'Version: {self.Version}\n'
        t += f'LogLength: {self.LogLength}\n'
        t += f'LogOffset: {self.LogOffset}\n'
        t += f'LogSequenceNumber: {self.LogSequenceNumber}\n'
        t += f'LogChecksum: {self.LogChecksum.hex()}\n'
        t += f'Reserved: {self.Reserved.hex()}'
        return t
    
# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-vhdx/c562ffcd-6c63-421f-a28d-2ee812c94b08
class RegionTable:
    def __init__(self):
        self.header = None
        self.entries = []
    
    @staticmethod
    def from_bytes(data:bytes):
        return RegionTable.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        data = io.BytesIO(buff.read(64*1024)) # 64KB
        region_table = RegionTable()
        region_table.header = RegionTableHeader.from_buffer(data)
        for i in range(region_table.header.EntryCount):
            region_table.entries.append(RegionTableEntry.from_buffer(data))
        return region_table

    def __str__(self) -> str:
        t = '== RegionTable ==\n'
        t += f'Header:\n{self.header}\n'
        for i, entry in enumerate(self.entries):
            t += f'Entry {i}:\n{entry}\n'
        return t
    
# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-vhdx/538e059e-17bc-46ee-a451-fae4f21669c6
class RegionTableHeader:
    def __init__(self):
        self.Signature = None
        self.Checksum = None
        self.EntryCount = None
        self.Reserved = None
    
    @staticmethod
    def from_bytes(data:bytes):
        return RegionTableHeader.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        header = RegionTableHeader()
        header.Signature = buff.read(4)
        header.Checksum = buff.read(4)
        header.EntryCount = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        header.Reserved = buff.read(4)
        return header

    def __str__(self) -> str:
        t = '== RegionTableHeader ==\n'
        t += f'Signature: {self.Signature.hex()}\n'
        t += f'Checksum: {self.Checksum.hex()}\n'
        t += f'EntryCount: {self.EntryCount}\n'
        t += f'Reserved: {self.Reserved.hex()}'
        return t

# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-vhdx/194899ee-6504-44d5-a9aa-a2b566c2c419
class RegionTableEntry:
    def __init__(self):
        self.Guid = None
        self.FileOffset = None
        self.Length = None
        self.Required = None

    async def get_region(self, buffer:io.BytesIO):
        await buffer.seek(self.FileOffset)
        data = await buffer.read(self.Length)
        if str(self.Guid).upper() in VHDX_KNOWN_REGIONS:
            return (str(self.Guid).upper(), VHDX_KNOWN_REGIONS[str(self.Guid).upper()].from_bytes(data))
        return ('UNKNOWN', data)
    
    @staticmethod
    def from_bytes(data:bytes):
        return RegionTableEntry.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        entry = RegionTableEntry()
        entry.Guid = uuid.UUID(bytes_le=buff.read(16))
        entry.FileOffset = int.from_bytes(buff.read(8), byteorder='little', signed=False)
        entry.Length = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        entry.Required = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        return entry
    
    def __str__(self) -> str:
        t = '== RegionTableEntry ==\n'
        t += f'Guid: {self.Guid}\n'
        t += f'FileOffset: {self.FileOffset}\n'
        t += f'Length: {self.Length}\n'
        t += f'Required: {self.Required}\n'
        return t

# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-vhdx/c865b61e-3cb8-4fe8-b81f-5474384c6fc2

class MetaDataRegion:
    def __init__(self):
        self.table:MetaDataTable = None
        self.items:Tuple[str, object] = []

        self.BlockSize = None
        self.VirtualDiskSize = None
        self.LogicalSectorSize = None
        self.PhysicalSectorSize = None
        self.VirtualDiskId = None
        self.ChunkRatio = None
        self.LeaveBlockAllocated = None
        self.HasParent = None
        self.lba_per_block = None


    @staticmethod
    def from_bytes(data:bytes):
        return MetaDataRegion.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        data = io.BytesIO(buff.read(64*1024)) # 64KB
        region = MetaDataRegion()
        region.table = MetaDataTable.from_buffer(data)
        for tableentry in region.table.entries:
            buff.seek(tableentry.ItemOffset)
            idata = buff.read(tableentry.ItemLength)
            if str(tableentry.ItemId).upper() in VHDX_KNOWN_METADATA_ITEMS:
                region.items.append(
                    (
                        str(tableentry.ItemId).upper(),
                        VHDX_KNOWN_METADATA_ITEMS[str(tableentry.ItemId).upper()].from_bytes(idata))
                    )
            else:
                region.items.append('UNKNOWN', idata)
        
        for itype, item in region.items:
            if isinstance(item, FileParameters):
                region.BlockSize = item.BlockSize
                region.LeaveBlockAllocated = item.LeaveBlockAllocated
                region.HasParent = item.HasParent
            elif isinstance(item, VirtualDiskSize):
                region.VirtualDiskSize = item.VirtualDiskSize
            elif isinstance(item, LogicalSectorSize):
                region.LogicalSectorSize = item.LogicalSectorSize
            elif isinstance(item, PhysicalSectorSize):
                region.PhysicalSectorSize = item.PhysicalSectorSize
            elif isinstance(item, VirtualDiskId):
                region.VirtualDiskId = item.VirtualDiskId
        
        VHDX_MAX_SECTORS_PER_BLOCK = 2**23
        region.ChunkRatio = (VHDX_MAX_SECTORS_PER_BLOCK * region.LogicalSectorSize) // region.BlockSize
        region.lba_per_block = region.BlockSize // region.LogicalSectorSize
        return region
    
    def __str__(self) -> str:
        t = '== MetaDataRegion ==\n'
        #t += f'Table:\n{self.table}\n'
        t += f'BlockSize: {self.BlockSize}\n'
        t += f'VirtualDiskSize: {self.VirtualDiskSize}\n'
        t += f'LogicalSectorSize: {self.LogicalSectorSize}\n'
        t += f'PhysicalSectorSize: {self.PhysicalSectorSize}\n'
        t += f'VirtualDiskId: {self.VirtualDiskId}\n'

        #for itype, item in self.items:
        #    t += f'Item {itype}:\n{item}\n'
        return t

# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-vhdx/c865b61e-3cb8-4fe8-b81f-5474384c6fc2
class MetaDataTable:
    def __init__(self):
        self.header = None
        self.entries:List[MetaDataTableEntry] = []
    
    @staticmethod
    def from_bytes(data:bytes):
        return MetaDataTable.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        table = MetaDataTable()
        table.header = MetaDataTableHeader.from_buffer(buff)
        for i in range(table.header.EntryCount):
            table.entries.append(MetaDataTableEntry.from_buffer(buff))
        return table

    def __str__(self) -> str:
        t = '== MetaDataTable ==\n'
        t += f'Header:\n{self.header}\n'
        for i, entry in enumerate(self.entries):
            t += f'Entry {i}:\n{entry}\n'
        return t
    
class MetaDataTableHeader:
    def __init__(self):
        self.Signature = None
        self.Reserved = None
        self.EntryCount = None
        self.Reserved2 = None
    
    @staticmethod
    def from_bytes(data:bytes):
        return MetaDataTableHeader.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        header = MetaDataTableHeader()
        header.Signature = buff.read(8)
        header.Reserved = buff.read(2)
        header.EntryCount = int.from_bytes(buff.read(2), byteorder='little', signed=False)
        header.Reserved2 = buff.read(20)
        return header

    def __str__(self) -> str:
        t = '== MetaDataTableHeader ==\n'
        t += f'Signature: {self.Signature.hex()}\n'
        t += f'Reserved: {self.Reserved.hex()}\n'
        t += f'EntryCount: {self.EntryCount}\n'
        t += f'Reserved2: {self.Reserved2.hex()}'
        return t

# https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-vhdx/2b46bf83-0887-4025-a9b0-d18b29f1fa9b
class MetaDataTableEntry:
    def __init__(self):
        self.ItemId = None
        self.ItemOffset = None
        self.ItemLength = None
        self.isUser = None
        self.isVirtualDisk = None
        self.isRequired = None
        self.Reserved = None
        self.Reserved2 = None

    @staticmethod
    def from_bytes(data:bytes):
        return MetaDataTableEntry.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        entry = MetaDataTableEntry()
        entry.ItemId = uuid.UUID(bytes_le=buff.read(16))
        entry.ItemOffset = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        entry.ItemLength = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        
        entry.Reserved = buff.read(4)
        flagfield = entry.Reserved[0]
        entry.isUser = bool(flagfield >> 3) #msb of reserved
        entry.isVirtualDisk = bool( (flagfield >> 2) & 1) #2nd msb of reserved
        entry.isRequired = bool( (flagfield >> 1) & 1)
        entry.Reserved2 = buff.read(4)
        return entry
    
    def __str__(self) -> str:
        t = '== MetaDataTableEntry ==\n'
        t += f'ItemId: {self.ItemId}\n'
        t += f'ItemOffset: {self.ItemOffset}\n'
        t += f'ItemLength: {self.ItemLength}\n'
        t += f'isUser: {self.isUser}\n'
        t += f'isVirtualDisk: {self.isVirtualDisk}\n'
        t += f'isRequired: {self.isRequired}\n'
        t += f'Reserved: {self.Reserved.hex()}\n'
        t += f'Reserved -start-: {bin(self.Reserved[0])}\n'
        t += f'Reserved2: {self.Reserved2.hex()}'
        return t

class FileParameters:
    def __init__(self):
        self.BlockSize = None
        self.LeaveBlockAllocated = None
        self.HasParent = None
        self.Reserved = None
    
    @staticmethod
    def from_bytes(data:bytes):
        return FileParameters.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        file_parameters = FileParameters()
        file_parameters.BlockSize = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        file_parameters.Reserved = buff.read(4)
        flagfield = file_parameters.Reserved[0]
        file_parameters.LeaveBlockAllocated = bool(flagfield >> 7)
        file_parameters.HasParent = bool((flagfield >> 6) & 1)
        return file_parameters

    def __str__(self) -> str:
        t = '== FileParameters ==\n'
        t += f'BlockSize: {self.BlockSize}\n'
        t += f'Reserved: {self.Reserved.hex()}\n'
        t += f'LeaveBlockAllocated: {self.LeaveBlockAllocated}\n'
        t += f'HasParent: {self.HasParent}'
        return t

class VirtualDiskSize:
    def __init__(self):
        self.VirtualDiskSize = None

    @staticmethod
    def from_bytes(data:bytes):
        return VirtualDiskSize.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        virtual_disk_size = VirtualDiskSize()
        virtual_disk_size.VirtualDiskSize = int.from_bytes(buff.read(8), byteorder='little', signed=False)
        return virtual_disk_size

    def __str__(self) -> str:
        t = '== VirtualDiskSize ==\n'
        t += f'VirtualDiskSize: {self.VirtualDiskSize}'
        return t

class VirtualDiskId:
    def __init__(self):
        self.VirtualDiskId = None

    @staticmethod
    def from_bytes(data:bytes):
        return VirtualDiskId.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        virtual_disk_id = VirtualDiskId()
        virtual_disk_id.VirtualDiskId = uuid.UUID(bytes_le=buff.read(16))
        return virtual_disk_id

    def __str__(self) -> str:
        t = '== VirtualDiskId ==\n'
        t += f'VirtualDiskId: {self.VirtualDiskId}'
        return t
    
class LogicalSectorSize:
    def __init__(self):
        self.LogicalSectorSize = None

    @staticmethod
    def from_bytes(data:bytes):
        return LogicalSectorSize.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        logical_sector_size = LogicalSectorSize()
        logical_sector_size.LogicalSectorSize = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        return logical_sector_size

    def __str__(self) -> str:
        t = '== LogicalSectorSize ==\n'
        t += f'LogicalSectorSize: {self.LogicalSectorSize}'
        return t
    
class PhysicalSectorSize:
    def __init__(self):
        self.PhysicalSectorSize = None

    @staticmethod
    def from_bytes(data:bytes):
        return PhysicalSectorSize.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        physical_sector_size = PhysicalSectorSize()
        physical_sector_size.PhysicalSectorSize = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        return physical_sector_size

    def __str__(self) -> str:
        t = '== PhysicalSectorSize ==\n'
        t += f'PhysicalSectorSize: {self.PhysicalSectorSize}'
        return t

class ParentLocator:
    def __init__(self):
        self.LocatorType = None
        self.Reserved = None
        self.KeyValueCount = None
        self.Entries = []

        self.kv = {}

    @staticmethod
    def from_bytes(data:bytes):
        return ParentLocator.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        parent_locator = ParentLocator()
        parent_locator.LocatorType = uuid.UUID(bytes_le=buff.read(16))
        parent_locator.Reserved = buff.read(2)
        parent_locator.KeyValueCount = int.from_bytes(buff.read(2), byteorder='little', signed=False)
        for i in range(parent_locator.KeyValueCount):
            parent_locator.entries.append(ParentLocatorEntry.from_buffer(buff))
        for entry in parent_locator.entries:
            buff.seek(entry.KeyOffset)
            key = buff.read(entry.KeyLength).decode('utf-16-le')
            buff.seek(entry.ValueOffset)
            value = buff.read(entry.ValueLength).decode('utf-16-le')
            parent_locator.kv[key] = value
        return parent_locator

    def __str__(self) -> str:
        t = '== ParentLocator ==\n'
        t += f'PlatformCode: {self.PlatformCode.hex()}\n'
        t += f'PlatformDataSpace: {self.PlatformDataSpace}\n'
        t += f'PlatformDataLength: {self.PlatformDataLength}\n'
        t += f'Reserved: {self.Reserved.hex()}\n'
        t += f'PlatformData: {self.PlatformData.hex()}'
        for key in self.kv:
            t += f'  {key}: {self.kv[key]}\n'
        return t
    
class ParentLocatorEntry:
    def __init__(self):
        self.KeyOffset = None
        self.ValueOffset = None
        self.KeyLength = None
        self.ValueLength = None

    @staticmethod
    def from_bytes(data:bytes):
        return ParentLocatorEntry.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        entry = ParentLocatorEntry()
        entry.KeyOffset = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        entry.ValueOffset = int.from_bytes(buff.read(4), byteorder='little', signed=False)
        entry.KeyLength = int.from_bytes(buff.read(2), byteorder='little', signed=False)
        entry.ValueLength = int.from_bytes(buff.read(2), byteorder='little', signed=False)
        return entry
    
    def __str__(self) -> str:
        t = '== ParentLocatorEntry ==\n'
        t += f'KeyOffset: {self.KeyOffset}\n'
        t += f'ValueOffset: {self.ValueOffset}\n'
        t += f'KeyLength: {self.KeyLength}\n'
        t += f'ValueLength: {self.ValueLength}'
        return t

class BAT:
    def __init__(self):
        self.entries = []
        self.state_stats = {} # this is just for debugging purposes
    
    @staticmethod
    def from_bytes(data:bytes):
        return BAT.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        bat = BAT()
        while True:
            data = buff.read(8)
            if data == b'':
                break
            temp = int.from_bytes(data, byteorder='little', signed=False)
            state = temp & 0b111
            offset = temp >> 20
            offset *= 1024*1024 # not sure about the multiplier
            bat.entries.append((state, offset))
            bat.state_stats[state] = bat.state_stats.get(state, 0) + 1
        return bat

    def __str__(self) -> str:
        t = '== BAT ==\n'
        for i, entry in enumerate(self.entries):
            t += f'Entry {i}: {entry}\n'
        return t


VHDX_KNOWN_REGIONS = {
    '2DC27766-F623-4200-9D64-115E9BFD4A08' : BAT,
    '8B7CA206-4790-4B9A-B8FE-575F050F886E' : MetaDataRegion,
}

VHDX_KNOWN_METADATA_ITEMS = {
    'CAA16737-FA36-4D43-B3B6-33F0AA44E76B' : FileParameters,
    '2FA54224-CD1B-4876-B211-5DBED83BF4B8' : VirtualDiskSize,
    'BECA12AB-B2E6-4523-93EF-C309E000C746' : VirtualDiskId,
    '8141BF1D-A96F-4709-BA47-F233A8FAAB5F' : LogicalSectorSize,
    'CDA348C7-445D-4471-9CC9-E9885251C556' : PhysicalSectorSize,
    'A8D35F2D-B30B-454D-ABF7-D3D84834AB0C' : ParentLocator,
}