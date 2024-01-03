import io
import uuid

# https://en.wikipedia.org/wiki/GUID_Partition_Table
class GPT:
    def __init__(self):
        self.Signature = None
        self.Revision = None
        self.HeaderSize = None
        self.HeaderCRC32 = None
        self.Reserved = None
        self.CurrentLBA = None
        self.BackupLBA = None
        self.FirstUsableLBA = None
        self.LastUsableLBA = None
        self.DiskGUID = None
        self.PartitionEntriesStart = None
        self.NumberOfPartitionEntries = None
        self.SizeOfPartitionEntry = None
        self.PartitionEntryArrayCRC32 = None
        self.PartitionEntries = []
    
    @staticmethod
    async def from_disk(disk):
        hdrdata = await disk.read_LBA(1)
        gpt = GPT.from_bytes(hdrdata)
        ptdatasize = gpt.NumberOfPartitionEntries * gpt.SizeOfPartitionEntry
        ptdata = b''
        i = gpt.PartitionEntriesStart
        while len(ptdata) < ptdatasize:
            ptdata += await disk.read_LBA(i)
            i += 1
        ptdata = io.BytesIO(ptdata)
        for _ in range(gpt.NumberOfPartitionEntries):
            entry = GPTPartitionEntry.from_buffer(ptdata)
            if entry.PartitionTypeGUID == uuid.UUID('00000000-0000-0000-0000-000000000000'):
                continue
            gpt.PartitionEntries.append(entry)
        return gpt

    
    @staticmethod
    def from_bytes(data):
        return GPT.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        gpt = GPT()
        gpt.Signature = buff.read(8)
        if gpt.Signature != b'\x45\x46\x49\x20\x50\x41\x52\x54':
            raise Exception('Invalid GPT signature')
        gpt.Revision = buff.read(4)
        gpt.HeaderSize = int.from_bytes(buff.read(4), 'little')
        if gpt.HeaderSize != 92:
            raise Exception('Invalid GPT header size')
        gpt.HeaderCRC32 = int.from_bytes(buff.read(4), 'little')
        gpt.Reserved = buff.read(4)
        gpt.CurrentLBA = int.from_bytes(buff.read(8), 'little')
        gpt.BackupLBA = int.from_bytes(buff.read(8), 'little')
        gpt.FirstUsableLBA = int.from_bytes(buff.read(8), 'little')
        gpt.LastUsableLBA = int.from_bytes(buff.read(8), 'little')
        gpt.DiskGUID = uuid.UUID(bytes_le= buff.read(16))
        gpt.PartitionEntriesStart = int.from_bytes(buff.read(8), 'little')
        gpt.NumberOfPartitionEntries = int.from_bytes(buff.read(4), 'little')
        gpt.SizeOfPartitionEntry = int.from_bytes(buff.read(4), 'little')
        gpt.PartitionEntryArrayCRC32 = int.from_bytes(buff.read(4), 'little')
        return gpt
    
    def __str__(self):
        res = []
        res.append('GPT')
        res.append('Signature: {}'.format(self.Signature.hex()))
        res.append('Revision: {}'.format(self.Revision.hex()))
        res.append('HeaderSize: {}'.format(self.HeaderSize))
        res.append('HeaderCRC32: {}'.format(self.HeaderCRC32))
        res.append('Reserved: {}'.format(self.Reserved.hex()))
        res.append('CurrentLBA: {}'.format(self.CurrentLBA))
        res.append('BackupLBA: {}'.format(self.BackupLBA))
        res.append('FirstUsableLBA: {}'.format(self.FirstUsableLBA))
        res.append('LastUsableLBA: {}'.format(self.LastUsableLBA))
        res.append('DiskGUID: {}'.format(self.DiskGUID))
        res.append('PartitionEntriesStart: {}'.format(self.PartitionEntriesStart))
        res.append('NumberOfPartitionEntries: {}'.format(self.NumberOfPartitionEntries))
        res.append('SizeOfPartitionEntry: {}'.format(self.SizeOfPartitionEntry))
        res.append('PartitionEntryArrayCRC32: {}'.format(self.PartitionEntryArrayCRC32))
        res.append('PartitionEntries:')
        for pe in self.PartitionEntries:
            res.append('  {}'.format(pe))
        return '\n'.join(res)
    
class GPTPartitionEntry:
    def __init__(self):
        self.PartitionTypeGUID = None
        self.UniquePartitionGUID = None
        self.FirstLBA = None
        self.LastLBA = None
        self.Attributes = None
        self.PartitionName = None
        self.PartitionType = None
    
    @staticmethod
    def from_bytes(data):
        return GPTPartitionEntry.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        entry = GPTPartitionEntry()
        entry.PartitionTypeGUID = uuid.UUID(bytes_le=buff.read(16))
        entry.UniquePartitionGUID = uuid.UUID(bytes_le=buff.read(16))
        entry.FirstLBA = int.from_bytes(buff.read(8), 'little')
        entry.LastLBA = int.from_bytes(buff.read(8), 'little')
        entry.Attributes = int.from_bytes(buff.read(8), 'little')
        entry.PartitionName = buff.read(72).decode('utf-16-le')
        entry.PartitionType = WELL_KNOWN_GPT_GUIDS.get(str(entry.PartitionTypeGUID).upper(), str(entry.PartitionTypeGUID).upper())
        return entry
    
    def __str__(self):
        res = []
        res.append('GPTPartitionEntry')
        res.append('PartitionTypeGUID: {}'.format(self.PartitionTypeGUID))
        res.append('UniquePartitionGUID: {}'.format(self.UniquePartitionGUID))
        res.append('FirstLBA: {}'.format(self.FirstLBA))
        res.append('LastLBA: {}'.format(self.LastLBA))
        res.append('Attributes: {}'.format(self.Attributes))
        res.append('PartitionName: {}'.format(self.PartitionName))
        res.append('PartitionType: {}'.format(self.PartitionType))
        return '\n'.join(res)


WELL_KNOWN_GPT_GUIDS = {
    '00000000-0000-0000-0000-000000000000' : 'Unused entry',
    '024DEE41-33E7-11D3-9D69-0008C781F39F' : 'MBR partition scheme',
    'C12A7328-F81F-11D2-BA4B-00A0C93EC93B' : 'EFI System partition',
    '21686148-6449-6E6F-744E-656564454649' : 'BIOS boot partition[f]',
    'D3BFE2DE-3DAF-11DF-BA40-E3A556D89593' : 'Intel Fast Flash (iFFS) partition (for Intel Rapid Start technology)[41][42]',
    'F4019732-066E-4E12-8273-346C5641494F' : 'Sony boot partition[g]',
    'BFBFAFE7-A34F-448A-9A5B-6213EB736C22' : 'Lenovo boot partition[g]',
    'E3C9E316-0B5C-4DB8-817D-F92DF00215AE' : 'Microsoft Reserved Partition (MSR)[44]',
    'EBD0A0A2-B9E5-4433-87C0-68B6B72699C7' : 'Basic data partition[44][h]',
    '5808C8AA-7E8F-42E0-85D2-E1E90434CFB3' : 'Logical Disk Manager (LDM) metadata partition[44]',
    'AF9B60A0-1431-4F62-BC68-3311714A69AD' : 'Logical Disk Manager data partition[44]',
    'DE94BBA4-06D1-4D40-A16A-BFD50179D6AC' : 'Windows Recovery Environment[44]',
    '37AFFC90-EF7D-4E96-91C3-2D7AE055B174' : 'IBM General Parallel File System (GPFS) partition',
    'E75CAF8F-F680-4CEE-AFA3-B001E56EFC2D' : 'Storage Spaces partition[46]',
    '558D43C5-A1AC-43C0-AAC8-D1472B2923D1' : 'Storage Replica partition[47]',
    '75894C1E-3AEB-11D3-B7C1-7B03A0000000' : 'Data partition',
    'E2A1E728-32E3-11D6-A682-7B03A0000000' : 'Service partition',
    '0FC63DAF-8483-4772-8E79-3D69D8477DE4' : 'Linux filesystem data[h]',
    'A19D880F-05FC-4D3B-A006-743F0F84911E' : 'RAID partition',
    '6523F8AE-3EB1-4E2A-A05A-18B695AE656F' : 'Root partition (Alpha)[48]',
    'D27F46ED-2919-4CB8-BD25-9531F3C16534' : 'Root partition (ARC)[48]',
    '69DAD710-2CE4-4E3C-B16C-21A1D49ABED3' : 'Root partition (ARM 32‐bit)[48]',
    'B921B045-1DF0-41C3-AF44-4C6F280D3FAE' : 'Root partition (AArch64)[48]',
    '993D8D3D-F80E-4225-855A-9DAF8ED7EA97' : 'Root partition (IA-64)[48]',
    '77055800-792C-4F94-B39A-98C91B762BB6' : 'Root partition (LoongArch 64‐bit)[48]',
    '37C58C8A-D913-4156-A25F-48B1B64E07F0' : 'Root partition (mipsel: 32‐bit MIPS little‐endian)[48]',
    '700BDA43-7A34-4507-B179-EEB93D7A7CA3' : 'Root partition (mips64el: 64‐bit MIPS little‐endian)[48]',
    '1AACDB3B-5444-4138-BD9E-E5C2239B2346' : 'Root partition (PA-RISC)[48]',
    '1DE3F1EF-FA98-47B5-8DCD-4A860A654D78' : 'Root partition (32‐bit PowerPC)[48]',
    '912ADE1D-A839-4913-8964-A10EEE08FBD2' : 'Root partition (64‐bit PowerPC big‐endian)[48]',
    'C31C45E6-3F39-412E-80FB-4809C4980599' : 'Root partition (64‐bit PowerPC little‐endian)[48]',
    '60D5A7FE-8E7D-435C-B714-3DD8162144E1' : 'Root partition (RISC-V 32‐bit)[48]',
    '72EC70A6-CF74-40E6-BD49-4BDA08E8F224' : 'Root partition (RISC-V 64‐bit)[48]',
    '08A7ACEA-624C-4A20-91E8-6E0FA67D23F9' : 'Root partition (s390)[48]',
    '5EEAD9A9-FE09-4A1E-A1D7-520D00531306' : 'Root partition (s390x)[48]',
    'C50CDD70-3862-4CC3-90E1-809A8C93EE2C' : 'Root partition (TILE-Gx)[48]',
    '44479540-F297-41B2-9AF7-D131D5F0458A' : 'Root partition (x86)[48]',
    '4F68BCE3-E8CD-4DB1-96E7-FBCAF984B709' : 'Root partition (x86-64)[48]',
    'E18CF08C-33EC-4C0D-8246-C6C6FB3DA024' : '/usr partition (Alpha)[48]',
    '7978A683-6316-4922-BBEE-38BFF5A2FECC' : '/usr partition (ARC)[48]',
    '7D0359A3-02B3-4F0A-865C-654403E70625' : '/usr partition (ARM 32‐bit)[48]',
    'B0E01050-EE5F-4390-949A-9101B17104E9' : '/usr partition (AArch64)[48]',
    '4301D2A6-4E3B-4B2A-BB94-9E0B2C4225EA' : '/usr partition (IA-64)[48]',
    'E611C702-575C-4CBE-9A46-434FA0BF7E3F' : '/usr partition (LoongArch 64‐bit)[48]',
    '0F4868E9-9952-4706-979F-3ED3A473E947' : '/usr partition (mipsel: 32‐bit MIPS little‐endian)[48]',
    'C97C1F32-BA06-40B4-9F22-236061B08AA8' : '/usr partition (mips64el: 64‐bit MIPS little‐endian)[48]',
    'DC4A4480-6917-4262-A4EC-DB9384949F25' : '/usr partition (PA-RISC)[48]',
    '7D14FEC5-CC71-415D-9D6C-06BF0B3C3EAF' : '/usr partition (32‐bit PowerPC)[48]',
    '2C9739E2-F068-46B3-9FD0-01C5A9AFBCCA' : '/usr partition (64‐bit PowerPC big‐endian)[48]',
    '15BB03AF-77E7-4D4A-B12B-C0D084F7491C' : '/usr partition (64‐bit PowerPC little‐endian)[48]',
    'B933FB22-5C3F-4F91-AF90-E2BB0FA50702' : '/usr partition (RISC-V 32‐bit)[48]',
    'BEAEC34B-8442-439B-A40B-984381ED097D' : '/usr partition (RISC-V 64‐bit)[48]',
    'CD0F869B-D0FB-4CA0-B141-9EA87CC78D66' : '/usr partition (s390)[48]',
    '8A4F5770-50AA-4ED3-874A-99B710DB6FEA' : '/usr partition (s390x)[48]',
    '55497029-C7C1-44CC-AA39-815ED1558630' : '/usr partition (TILE-Gx)[48]',
    '75250D76-8CC6-458E-BD66-BD47CC81A812' : '/usr partition (x86)[48]',
    '8484680C-9521-48C6-9C11-B0720656F69E' : '/usr partition (x86-64)[48]',
    'FC56D9E9-E6E5-4C06-BE32-E74407CE09A5' : 'Root verity partition for dm-verity (Alpha)[48]',
    '24B2D975-0F97-4521-AFA1-CD531E421B8D' : 'Root verity partition for dm-verity (ARC) [48]',
    '7386CDF2-203C-47A9-A498-F2ECCE45A2D6' : 'Root verity partition for dm-verity (ARM 32‐bit) [48]',
    'DF3300CE-D69F-4C92-978C-9BFB0F38D820' : 'Root verity partition for dm-verity (AArch64) [48]',
    '86ED10D5-B607-45BB-8957-D350F23D0571' : 'Root verity partition for dm-verity (IA-64) [48]',
    'F3393B22-E9AF-4613-A948-9D3BFBD0C535' : 'Root verity partition for dm-verity (LoongArch 64‐bit) [48]',
    'D7D150D2-2A04-4A33-8F12-16651205FF7B' : 'Root verity partition for dm-verity (mipsel: 32‐bit MIPS little‐endian) [48]',
    '16B417F8-3E06-4F57-8DD2-9B5232F41AA6' : 'Root verity partition for dm-verity (mips64el: 64‐bit MIPS little‐endian) [48]',
    'D212A430-FBC5-49F9-A983-A7FEEF2B8D0E' : 'Root verity partition for dm-verity (PA-RISC) [48]',
    '906BD944-4589-4AAE-A4E4-DD983917446A' : 'Root verity partition for dm-verity (64‐bit PowerPC little‐endian) [48]',
    '9225A9A3-3C19-4D89-B4F6-EEFF88F17631' : 'Root verity partition for dm-verity (64‐bit PowerPC big‐endian) [48]',
    '98CFE649-1588-46DC-B2F0-ADD147424925' : 'Root verity partition for dm-verity (32‐bit PowerPC) [48]',
    'AE0253BE-1167-4007-AC68-43926C14C5DE' : 'Root verity partition for dm-verity (RISC-V 32‐bit) [48]',
    'B6ED5582-440B-4209-B8DA-5FF7C419EA3D' : 'Root verity partition for dm-verity (RISC-V 64‐bit) [48]',
    '7AC63B47-B25C-463B-8DF8-B4A94E6C90E1' : 'Root verity partition for dm-verity (s390) [48]',
    'B325BFBE-C7BE-4AB8-8357-139E652D2F6B' : 'Root verity partition for dm-verity (s390x) [48]',
    '966061EC-28E4-4B2E-B4A5-1F0A825A1D84' : 'Root verity partition for dm-verity (TILE-Gx) [48]',
    '2C7357ED-EBD2-46D9-AEC1-23D437EC2BF5' : 'Root verity partition for dm-verity (x86-64) [48]',
    'D13C5D3B-B5D1-422A-B29F-9454FDC89D76' : 'Root verity partition for dm-verity (x86) [48]',
    '8CCE0D25-C0D0-4A44-BD87-46331BF1DF67' : '/usr verity partition for dm-verity (Alpha) [48]',
    'FCA0598C-D880-4591-8C16-4EDA05C7347C' : '/usr verity partition for dm-verity (ARC) [48]',
    'C215D751-7BCD-4649-BE90-6627490A4C05' : '/usr verity partition for dm-verity (ARM 32‐bit) [48]',
    '6E11A4E7-FBCA-4DED-B9E9-E1A512BB664E' : '/usr verity partition for dm-verity (AArch64) [48]',
    '6A491E03-3BE7-4545-8E38-83320E0EA880' : '/usr verity partition for dm-verity (IA-64) [48]',
    'F46B2C26-59AE-48F0-9106-C50ED47F673D' : '/usr verity partition for dm-verity (LoongArch 64‐bit) [48]',
    '46B98D8D-B55C-4E8F-AAB3-37FCA7F80752' : '/usr verity partition for dm-verity (mipsel: 32‐bit MIPS little‐endian) [48]',
    '3C3D61FE-B5F3-414D-BB71-8739A694A4EF' : '/usr verity partition for dm-verity (mips64el: 64‐bit MIPS little‐endian) [48]',
    '5843D618-EC37-48D7-9F12-CEA8E08768B2' : '/usr verity partition for dm-verity (PA-RISC) [48]',
    'EE2B9983-21E8-4153-86D9-B6901A54D1CE' : '/usr verity partition for dm-verity (64‐bit PowerPC little‐endian) [48]',
    'BDB528A5-A259-475F-A87D-DA53FA736A07' : '/usr verity partition for dm-verity (64‐bit PowerPC big‐endian) [48]',
    'DF765D00-270E-49E5-BC75-F47BB2118B09' : '/usr verity partition for dm-verity (32‐bit PowerPC) [48]',
    'CB1EE4E3-8CD0-4136-A0A4-AA61A32E8730' : '/usr verity partition for dm-verity (RISC-V 32‐bit) [48]',
    '8F1056BE-9B05-47C4-81D6-BE53128E5B54' : '/usr verity partition for dm-verity (RISC-V 64‐bit) [48]',
    'B663C618-E7BC-4D6D-90AA-11B756BB1797' : '/usr verity partition for dm-verity (s390) [48]',
    '31741CC4-1A2A-4111-A581-E00B447D2D06' : '/usr verity partition for dm-verity (s390x) [48]',
    '2FB4BF56-07FA-42DA-8132-6B139F2026AE' : '/usr verity partition for dm-verity (TILE-Gx) [48]',
    '77FF5F63-E7B6-4633-ACF4-1565B864C0E6' : '/usr verity partition for dm-verity (x86-64) [48]',
    '8F461B0D-14EE-4E81-9AA9-049B6FB97ABD' : '/usr verity partition for dm-verity (x86) [48]',
    'D46495B7-A053-414F-80F7-700C99921EF8' : 'Root verity signature partition for dm-verity (Alpha)[48]',
    '143A70BA-CBD3-4F06-919F-6C05683A78BC' : 'Root verity signature partition for dm-verity (ARC)}[48]',
    '42B0455F-EB11-491D-98D3-56145BA9D037' : 'Root verity signature partition for dm-verity (ARM 32‐bit)[48]',
    '6DB69DE6-29F4-4758-A7A5-962190F00CE3' : 'Root verity signature partition for dm-verity (AArch64)[48]',
    'E98B36EE-32BA-4882-9B12-0CE14655F46A' : 'Root verity signature partition for dm-verity (IA-64)[48]',
    '5AFB67EB-ECC8-4F85-AE8E-AC1E7C50E7D0' : 'Root verity signature partition for dm-verity (LoongArch 64‐bit)[48]',
    'C919CC1F-4456-4EFF-918C-F75E94525CA5' : 'Root verity signature partition for dm-verity (mipsel: 32‐bit MIPS little‐endian)[48]',
    '904E58EF-5C65-4A31-9C57-6AF5FC7C5DE7' : 'Root verity signature partition for dm-verity (mips64el: 64‐bit MIPS little‐endian)[48]',
    '15DE6170-65D3-431C-916E-B0DCD8393F25' : 'Root verity signature partition for dm-verity (PA-RISC)[48]',
    'D4A236E7-E873-4C07-BF1D-BF6CF7F1C3C6' : 'Root verity signature partition for dm-verity (64‐bit PowerPC little‐endian)[48]',
    'F5E2C20C-45B2-4FFA-BCE9-2A60737E1AAF' : 'Root verity signature partition for dm-verity (64‐bit PowerPC big‐endian)[48]',
    '1B31B5AA-ADD9-463A-B2ED-BD467FC857E7' : 'Root verity signature partition for dm-verity (32‐bit PowerPC)[48]',
    '3A112A75-8729-4380-B4CF-764D79934448' : 'Root verity signature partition for dm-verity (RISC-V 32‐bit)[48]',
    'EFE0F087-EA8D-4469-821A-4C2A96A8386A' : 'Root verity signature partition for dm-verity (RISC-V 64‐bit)[48]',
    '3482388E-4254-435A-A241-766A065F9960' : 'Root verity signature partition for dm-verity (s390)[48]',
    'C80187A5-73A3-491A-901A-017C3FA953E9' : 'Root verity signature partition for dm-verity (s390x)[48]',
    'B3671439-97B0-4A53-90F7-2D5A8F3AD47B' : 'Root verity signature partition for dm-verity (TILE-Gx)[48]',
    '41092B05-9FC8-4523-994F-2DEF0408B176' : 'Root verity signature partition for dm-verity (x86-64)[48]',
    '5996FC05-109C-48DE-808B-23FA0830B676' : 'Root verity signature partition for dm-verity (x86)[48]',
    '5C6E1C76-076A-457A-A0FE-F3B4CD21CE6E' : '/usr verity signature partition for dm-verity (Alpha)[48]',
    '94F9A9A1-9971-427A-A400-50CB297F0F35' : '/usr verity signature partition for dm-verity (ARC)[48]',
    'D7FF812F-37D1-4902-A810-D76BA57B975A' : '/usr verity signature partition for dm-verity (ARM 32‐bit)[48]',
    'C23CE4FF-44BD-4B00-B2D4-B41B3419E02A' : '/usr verity signature partition for dm-verity (AArch64)[48]',
    '8DE58BC2-2A43-460D-B14E-A76E4A17B47F' : '/usr verity signature partition for dm-verity (IA-64)[48]',
    'B024F315-D330-444C-8461-44BBDE524E99' : '/usr verity signature partition for dm-verity (LoongArch 64‐bit)[48]',
    '3E23CA0B-A4BC-4B4E-8087-5AB6A26AA8A9' : '/usr verity signature partition for dm-verity (mipsel: 32‐bit MIPS little‐endian)[48]',
    'F2C2C7EE-ADCC-4351-B5C6-EE9816B66E16' : '/usr verity signature partition for dm-verity (mips64el: 64‐bit MIPS little‐endian)[48]',
    '450DD7D1-3224-45EC-9CF2-A43A346D71EE' : '/usr verity signature partition for dm-verity (PA-RISC)[48]',
    'C8BFBD1E-268E-4521-8BBA-BF314C399557' : '/usr verity signature partition for dm-verity (64‐bit PowerPC little‐endian)[48]',
    '0B888863-D7F8-4D9E-9766-239FCE4D58AF' : '/usr verity signature partition for dm-verity (64‐bit PowerPC big‐endian)[48]',
    '7007891D-D371-4A80-86A4-5CB875B9302E' : '/usr verity signature partition for dm-verity (32‐bit PowerPC)[48]',
    'C3836A13-3137-45BA-B583-B16C50FE5EB4' : '/usr verity signature partition for dm-verity (RISC-V 32‐bit)[48]',
    'D2F9000A-7A18-453F-B5CD-4D32F77A7B32' : '/usr verity signature partition for dm-verity (RISC-V 64‐bit)[48]',
    '17440E4F-A8D0-467F-A46E-3912AE6EF2C5' : '/usr verity signature partition for dm-verity (s390)[48]',
    '3F324816-667B-46AE-86EE-9B0C0C6C11B4' : '/usr verity signature partition for dm-verity (s390x)[48]',
    '4EDE75E2-6CCC-4CC8-B9C7-70334B087510' : '/usr verity signature partition for dm-verity (TILE-Gx)[48]',
    'E7BB33FB-06CF-4E81-8273-E543B413E2E2' : '/usr verity signature partition for dm-verity (x86-64)[48]',
    '974A71C0-DE41-43C3-BE5D-5C5CCD1AD2C0' : '/usr verity signature partition for dm-verity (x86)[48]',
    'BC13C2FF-59E6-4262-A352-B275FD6F7172' : '/boot, as an Extended Boot Loader (XBOOTLDR) partition[48][49]',
    '0657FD6D-A4AB-43C4-84E5-0933C84B4F4F' : 'Swap partition[48][49]',
    'E6D6D379-F507-44C2-A23C-238F2A3DF928' : 'Logical Volume Manager (LVM) partition',
    '933AC7E1-2EB4-4F13-B844-0E14E2AEF915' : '/home partition[48][49]',
    '3B8F8425-20E0-4F3B-907F-1A25A76F98E8' : '/srv (server data) partition[48][49]',
    '773F91EF-66D4-49B5-BD83-D683BF40AD16' : 'Per‐user home partition[48]',
    '7FFEC5C9-2D00-49B7-8941-3EA10A5586B7' : 'Plain dm-crypt partition[52][53][54]',
    'CA7D7CCB-63ED-4C53-861C-1742536059CC' : 'LUKS partition[52][53][54][55]',
    '8DA63339-0007-60C0-C436-083AC8230908' : 'Reserved',
    '0FC63DAF-8483-4772-8E79-3D69D8477DE4' : 'Linux filesystem data[57]',
    '0657FD6D-A4AB-43C4-84E5-0933C84B4F4F' : 'Linux Swap partition[58]',
    '83BD6B9D-7F41-11DC-BE0B-001560B84F0F' : 'Boot partition[59]',
    '516E7CB4-6ECF-11D6-8FF8-00022D09712B' : 'BSD disklabel partition[59]',
    '516E7CB5-6ECF-11D6-8FF8-00022D09712B' : 'Swap partition[59]',
    '516E7CB6-6ECF-11D6-8FF8-00022D09712B' : 'Unix File System (UFS) partition[59]',
    '516E7CB8-6ECF-11D6-8FF8-00022D09712B' : 'Vinum volume manager partition[59]',
    '516E7CBA-6ECF-11D6-8FF8-00022D09712B' : 'ZFS partition[59]',
    '74BA7DD9-A689-11E1-BD04-00E081286ACF' : 'nandfs partition[60]',
    '48465300-0000-11AA-AA11-00306543ECAC' : 'Hierarchical File System Plus (HFS+) partition',
    '7C3457EF-0000-11AA-AA11-00306543ECAC' : '"Apple APFS container',
    '55465300-0000-11AA-AA11-00306543ECAC' : 'Apple UFS container',
    '6A898CC3-1DD2-11B2-99A6-080020736631' : 'ZFS[i]',
    '52414944-0000-11AA-AA11-00306543ECAC' : 'Apple RAID partition',
    '52414944-5F4F-11AA-AA11-00306543ECAC' : 'Apple RAID partition, offline',
    '426F6F74-0000-11AA-AA11-00306543ECAC' : 'Apple Boot partition (Recovery HD)',
    '4C616265-6C00-11AA-AA11-00306543ECAC' : 'Apple Label',
    '5265636F-7665-11AA-AA11-00306543ECAC' : 'Apple TV Recovery partition',
    '53746F72-6167-11AA-AA11-00306543ECAC' : '"Apple Core Storage Container',
    '69646961-6700-11AA-AA11-00306543ECAC' : 'Apple APFS Preboot partition',
    '52637672-7900-11AA-AA11-00306543ECAC' : 'Apple APFS Recovery partition',
    '6A82CB45-1DD2-11B2-99A6-080020736631' : 'Boot partition',
    '6A85CF4D-1DD2-11B2-99A6-080020736631' : 'Root partition',
    '6A87C46F-1DD2-11B2-99A6-080020736631' : 'Swap partition',
    '6A8B642B-1DD2-11B2-99A6-080020736631' : 'Backup partition',
    '6A898CC3-1DD2-11B2-99A6-080020736631' : '/usr partition[i]',
    '6A8EF2E9-1DD2-11B2-99A6-080020736631' : '/var partition',
    '6A90BA39-1DD2-11B2-99A6-080020736631' : '/home partition',
    '6A9283A5-1DD2-11B2-99A6-080020736631' : 'Alternate sector',
    '6A945A3B-1DD2-11B2-99A6-080020736631' : 'Reserved partition',
    '6A9630D1-1DD2-11B2-99A6-080020736631' : 'Reserved partition',
    '6A980767-1DD2-11B2-99A6-080020736631' : 'Reserved partition',
    '6A96237F-1DD2-11B2-99A6-080020736631' : 'Reserved partition',
    '6A8D2AC7-1DD2-11B2-99A6-080020736631' : 'Reserved partition',
    '49F48D32-B10E-11DC-B99B-0019D1879648' : 'Swap partition',
    '49F48D5A-B10E-11DC-B99B-0019D1879648' : 'FFS partition',
    '49F48D82-B10E-11DC-B99B-0019D1879648' : 'LFS partition',
    '49F48DAA-B10E-11DC-B99B-0019D1879648' : 'RAID partition',
    '2DB519C4-B10F-11DC-B99B-0019D1879648' : 'Concatenated partition',
    '2DB519EC-B10F-11DC-B99B-0019D1879648' : 'Encrypted partition',
    'FE3A2A5D-4F32-41A7-B725-ACCC3285A309' : 'ChromeOS kernel',
    '3CB8E202-3B7E-47DD-8A3C-7FF2A13CFCEC' : 'ChromeOS rootfs',
    'CAB6E88E-ABF3-4102-A07A-D4BB9BE3C1D3' : 'ChromeOS firmware',
    '2E0A753D-9E48-43B0-8337-B15192CB1B5E' : 'ChromeOS future use',
    '09845860-705F-4BB5-B16C-8A8A099CAF52' : 'ChromeOS miniOS',
    '3F0F8318-F146-4E6B-8222-C28C8F02E0D5' : 'ChromeOS hibernate',
    '5DFBF5F4-2848-4BAC-AA5E-0D9A20B745A6' : '/usr partition (coreos-usr)',
    '3884DD41-8582-4404-B9A8-E9B84F2DF50E' : 'Resizable rootfs (coreos-resize)',
    'C95DC21A-DF0E-4340-8D7B-26CBFA9A03E0' : 'OEM customizations (coreos-reserved)',
    'BE9067B9-EA49-4F15-B4F6-F36F8C9E1818' : 'Root filesystem on RAID (coreos-root-raid)',
    '42465331-3BA3-10F1-802A-4861696B7521' : 'Haiku BFS',
    '85D5E45E-237C-11E1-B4B3-E89A8F7FC3A7' : 'Boot partition',
    '85D5E45A-237C-11E1-B4B3-E89A8F7FC3A7' : 'Data partition',
    '85D5E45B-237C-11E1-B4B3-E89A8F7FC3A7' : 'Swap partition',
    '0394EF8B-237E-11E1-B4B3-E89A8F7FC3A7' : 'Unix File System (UFS) partition',
    '85D5E45C-237C-11E1-B4B3-E89A8F7FC3A7' : 'Vinum volume manager partition',
    '85D5E45D-237C-11E1-B4B3-E89A8F7FC3A7' : 'ZFS partition',
    '45B0969E-9B03-4F30-B4C6-B4B80CEFF106' : 'Journal',
    '45B0969E-9B03-4F30-B4C6-5EC00CEFF106' : 'dm-crypt journal',
    '4FBD7E29-9D25-41B8-AFD0-062C0CEFF05D' : 'OSD',
    '4FBD7E29-9D25-41B8-AFD0-5EC00CEFF05D' : 'dm-crypt OSD',
    '89C57F98-2FE5-4DC0-89C1-F3AD0CEFF2BE' : 'Disk in creation',
    '89C57F98-2FE5-4DC0-89C1-5EC00CEFF2BE' : 'dm-crypt disk in creation',
    'CAFECAFE-9B03-4F30-B4C6-B4B80CEFF106' : 'Block',
    '30CD0809-C2B2-499C-8879-2D6B78529876' : 'Block DB',
    '5CE17FCE-4087-4169-B7FF-056CC58473F9' : 'Block write-ahead log',
    'FB3AABF9-D25F-47CC-BF5E-721D1816496B' : 'Lockbox for dm-crypt keys',
    '4FBD7E29-8AE0-4982-BF9D-5A8D867AF560' : 'Multipath OSD',
    '45B0969E-8AE0-4982-BF9D-5A8D867AF560' : 'Multipath journal',
    'CAFECAFE-8AE0-4982-BF9D-5A8D867AF560' : 'Multipath block',
    '7F4A666A-16F3-47A2-8445-152EF4D03F6C' : 'Multipath block',
    'EC6D6385-E346-45DC-BE91-DA2A7C8B3261' : 'Multipath block DB',
    '01B41E1B-002A-453C-9F17-88793989FF8F' : 'Multipath block write-ahead log',
    'CAFECAFE-9B03-4F30-B4C6-5EC00CEFF106' : 'dm-crypt block',
    '93B0052D-02D9-4D8A-A43B-33A3EE4DFBC3' : 'dm-crypt block DB',
    '306E8683-4FE2-4330-B7C0-00A917C16966' : 'dm-crypt block write-ahead log',
    '45B0969E-9B03-4F30-B4C6-35865CEFF106' : 'dm-crypt LUKS journal',
    'CAFECAFE-9B03-4F30-B4C6-35865CEFF106' : 'dm-crypt LUKS block',
    '166418DA-C469-4022-ADF4-B30AFD37F176' : 'dm-crypt LUKS block DB',
    '86A32090-3647-40B9-BBBD-38D8C573AA86' : 'dm-crypt LUKS block write-ahead log',
    '4FBD7E29-9D25-41B8-AFD0-35865CEFF05D' : 'dm-crypt LUKS OSD',
    '824CC7A0-36A8-11E3-890A-952519AD3F61' : 'Data partition',
    'CEF5A9AD-73BC-4601-89F3-CDEEEEE321A1' : 'Power-safe (QNX6) file system[68]',
    'C91818F9-8025-47AF-89D2-F030D7000C2C' : 'Plan 9 partition',
    '9D275380-40AD-11DB-BF97-000C2911D1B8' : 'vmkcore (coredump partition)',
    'AA31E02A-400F-11DB-9590-000C2911D1B8' : 'VMFS filesystem partition',
    '9198EFFC-31C0-11DB-8F78-000C2911D1B8' : 'VMware Reserved',
    '2568845D-2332-4675-BC39-8FA5A4748D15' : 'Bootloader',
    '114EAFFE-1552-4022-B26E-9B053604CF84' : 'Bootloader2',
    '49A4D17F-93A3-45C1-A0DE-F50B2EBE2599' : 'Boot',
    '4177C722-9E92-4AAB-8644-43502BFD5506' : 'Recovery',
    'EF32A33B-A409-486C-9141-9FFB711F6266' : 'Misc',
    '20AC26BE-20B7-11E3-84C5-6CFDB94711E9' : 'Metadata',
    '38F428E6-D326-425D-9140-6E0EA133647C' : 'System',
    'A893EF21-E428-470A-9E55-0668FD91A2D9' : 'Cache',
    'DC76DDA9-5AC1-491C-AF42-A82591580C0D' : 'Data',
    'EBC597D0-2053-4B15-8B64-E0AAC75F4DB1' : 'Persistent',
    'C5A0AEEC-13EA-11E5-A1B1-001E67CA0C3C' : 'Vendor',
    'BD59408B-4514-490D-BF12-9878D963F378' : 'Config',
    '8F68CC74-C5E5-48DA-BE91-A0C8C15E9C80' : 'Factory',
    '9FDAA6EF-4B3F-40D2-BA8D-BFF16BFB887B' : 'Factory (alt)[73]',
    '767941D0-2085-11E3-AD3B-6CFDB94711E9' : 'Fastboot / Tertiary[74][75]',
    'AC6D7924-EB71-4DF8-B48D-E267B27148FF' : 'OEM',
    '19A710A2-B3CA-11E4-B026-10604B889DCF' : 'Android Meta',
    '193D1EA4-B3CA-11E4-B075-10604B889DCF' : 'Android EXT',
    '7412F7D5-A156-4B13-81DC-867174929325' : 'Boot',
    'D4E6E2CD-4469-46F3-B5CB-1BFF57AFC149' : 'Config',
    '9E1A2D38-C612-4316-AA26-8B49521E5A8B' : 'PReP boot',
    'BC13C2FF-59E6-4262-A352-B275FD6F7172' : 'Shared boot loader configuration[76]',
    '734E5AFE-F61A-11E6-BC64-92361F002671' : 'Basic data partition (GEM, BGM, F32)',
    '8C8F8EFF-AC95-4770-814A-21994F2DBC8F' : 'Encrypted data partition',
    '90B6FF38-B98F-4358-A21F-48F35B4A8AD3' : 'ArcaOS Type 1',
    '7C5222BD-8F5D-4087-9C00-BF9843C7B58C' : 'SPDK block device[77]',
    '4778ED65-BF42-45FA-9C5B-287A1DC4AAB1' : 'barebox-state[78]',
    '3DE21764-95BD-54BD-A5C3-4ABE786F38A8' : 'U-Boot environment[79][80]',
    'B6FA30DA-92D2-4A9A-96F1-871EC6486200' : 'SoftRAID_Status',
    '2E313465-19B9-463F-8126-8A7993773801' : 'SoftRAID_Scratch',
    'FA709C7E-65B1-4593-BFD5-E71D61DE9B02' : 'SoftRAID_Volume',
    'BBBA6DF5-F46F-4A89-8F59-8765B2727503' : 'SoftRAID_Cache',
    'FE8A2634-5E2E-46BA-99E3-3A192091A350' : 'Bootloader (slot A/B/R)',
    'D9FD4535-106C-4CEC-8D37-DFC020CA87CB' : 'Durable mutable encrypted system data',
    'A409E16B-78AA-4ACC-995C-302352621A41' : 'Durable mutable bootloader data (including A/B/R metadata)',
    'F95D940E-CABA-4578-9B93-BB6C90F29D3E' : 'Factory-provisioned read-only system data',
    '10B8DBAA-D2BF-42A9-98C6-A7C5DB3701E7' : 'Factory-provisioned read-only bootloader data',
    '49FD7CB8-DF15-4E73-B9D9-992070127F0F' : 'Fuchsia Volume Manager',
    '421A8BFC-85D9-4D85-ACDA-B64EEC0133E9' : 'Verified boot metadata (slot A/B/R)',
    '9B37FFF6-2E58-466A-983A-F7926D0B04E0' : 'Zircon boot image (slot A/B/R)',
    #'C12A7328-F81F-11D2-BA4B-00A0C93EC93B' : 'fuchsia-esp', # collides with EFI System Partition???
    '606B000B-B7C7-4653-A7D5-B737332C899D' : 'fuchsia-system',
    '08185F0C-892D-428A-A789-DBEEC8F55E6A' : 'fuchsia-data',
    '48435546-4953-2041-494E-5354414C4C52' : 'fuchsia-install',
    '2967380E-134C-4CBB-B6DA-17E7CE1CA45D' : 'fuchsia-blob',
    '41D0E340-57E3-954E-8C1E-17ECAC44CFF5' : 'fuchsia-fvm',
    'DE30CC86-1F4A-4A31-93C4-66F147D33E05' : 'Zircon boot image (slot A)',
    '23CC04DF-C278-4CE7-8471-897D1A4BCDF7' : 'Zircon boot image (slot B)',
    'A0E5CF57-2DEF-46BE-A80C-A2067C37CD49' : 'Zircon boot image (slot R)',
    '4E5E989E-4C86-11E8-A15B-480FCF35F8E6' : 'sys-config',
    '5A3A90BE-4C86-11E8-A15B-480FCF35F8E6' : 'factory-config',
    '5ECE94FE-4C86-11E8-A15B-480FCF35F8E6' : 'bootloader',
    '8B94D043-30BE-4871-9DFA-D69556E8C1F3' : 'guid-test',
    'A13B4D9A-EC5F-11E8-97D8-6C3BE52705BF' : 'Verified boot metadata (slot A)',
    'A288ABF2-EC5F-11E8-97D8-6C3BE52705BF' : 'Verified boot metadata (slot B)',
    '6A2460C3-CD11-4E8B-80A8-12CCE268ED0A' : 'Verified boot metadata (slot R)',
    '1D75395D-F2C6-476B-A8B7-45CC1C97B476' : 'misc',
    '900B0FC5-90CD-4D4F-84F9-9F8ED579DB88' : 'emmc-boot1',
    'B2B2E8D1-7C10-4EBC-A2D0-4614568260AD' : 'emmc-boot2',
}