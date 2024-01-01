import io

class PBS:
    def __init__(self):
        self.jump_instruction = None
        self.oem_id = None
        self.bytes_per_sector = None
        self.sectors_per_cluster = None
        self.reserved_sectors = None
        self.unused = None #unused
        self.unused2 = None #unused
        self.media_descriptor = None
        self.unused3 = None
        self.sectors_per_track = None
        self.number_of_heads = None
        self.hidden_sectors = None
        self.unused4 = None
        self.unused5 = None
        self.total_sectors = None
        self.mft_cluster = None
        self.mft_cluster_mirror = None
        self.bytes_per_record = None
        self.unused6 = None
        self.bytes_per_index_buffer = None
        self.unused7 = None
        self.volume_serial_number = None
        self.unused8 = None
        self.boot_code = None
        self.boot_sector_signature = None

    def get_filerecord_size_bytes(self):
        if self.bytes_per_record < 0:
            return 1 << abs(self.bytes_per_record)
        return self.bytes_per_record * self.sectors_per_cluster * self.bytes_per_sector

    def get_index_buffer_size_bytes(self):
        if self.bytes_per_index_buffer < 0:
            return 1 << abs(self.bytes_per_index_buffer)
        return self.bytes_per_index_buffer * self.sectors_per_cluster * self.bytes_per_sector
    
    @staticmethod
    def from_bytes(data):
        return PBS.from_buffer(io.BytesIO(data))
    
    @staticmethod
    def from_buffer(buff):
        pbs = PBS()
        pbs.jump_instruction = buff.read(3)
        pbs.oem_id = buff.read(8)
        if pbs.oem_id != b'NTFS    ':
            raise Exception('Invalid NTFS oem id')
        
        pbs.bytes_per_sector = int.from_bytes(buff.read(2), 'little')
        pbs.sectors_per_cluster = int.from_bytes(buff.read(1), 'little')
        pbs.reserved_sectors = int.from_bytes(buff.read(2), 'little')
        pbs.unused = int.from_bytes(buff.read(3), 'little')
        pbs.unused2 = int.from_bytes(buff.read(2), 'little')
        pbs.media_descriptor = int.from_bytes(buff.read(1), 'little')
        pbs.unused3 = int.from_bytes(buff.read(2), 'little')
        pbs.sectors_per_track = int.from_bytes(buff.read(2), 'little')
        pbs.number_of_heads = int.from_bytes(buff.read(2), 'little')
        pbs.hidden_sectors = int.from_bytes(buff.read(4), 'little')
        pbs.unused4 = int.from_bytes(buff.read(4), 'little')
        pbs.unused5 = int.from_bytes(buff.read(4), 'little')
        pbs.total_sectors = int.from_bytes(buff.read(8), 'little')
        pbs.mft_cluster = int.from_bytes(buff.read(8), 'little')
        pbs.mft_cluster_mirror = int.from_bytes(buff.read(8), 'little')
        pbs.bytes_per_record = int.from_bytes(buff.read(1), 'little', signed=True)
        pbs.unused6 = int.from_bytes(buff.read(3), 'little')
        pbs.bytes_per_index_buffer = int.from_bytes(buff.read(1), 'little', signed=True)
        pbs.unused7 = int.from_bytes(buff.read(3), 'little')
        pbs.volume_serial_number = int.from_bytes(buff.read(8), 'little')
        pbs.unused8 = buff.read(4)
        pbs.boot_code = buff.read(426)
        pbs.boot_sector_signature = buff.read(2)
        if pbs.boot_sector_signature != b'\x55\xAA':
            raise Exception('Invalid boot sector signature')
        return pbs
    
    def __str__(self):
        res = []
        res.append('Jump Instruction: {}'.format(self.jump_instruction.hex()))
        res.append('OEM ID: {}'.format(self.oem_id.decode()))
        res.append('Bytes Per Sector: {}'.format(self.bytes_per_sector))
        res.append('Sectors Per Cluster: {}'.format(self.sectors_per_cluster))
        res.append('Reserved Sectors: {}'.format(self.reserved_sectors))
        res.append('Unused: {}'.format(self.unused))
        res.append('Unused2: {}'.format(self.unused2))
        res.append('Media Descriptor: {}'.format(self.media_descriptor))
        res.append('Unused3: {}'.format(self.unused3))
        res.append('Sectors Per Track: {}'.format(self.sectors_per_track))
        res.append('Number Of Heads: {}'.format(self.number_of_heads))
        res.append('Hidden Sectors: {}'.format(self.hidden_sectors))
        res.append('Unused4: {}'.format(self.unused4))
        res.append('Unused5: {}'.format(self.unused5))
        res.append('Total Sectors: {}'.format(self.total_sectors))
        res.append('MFT Cluster: {}'.format(self.mft_cluster))
        res.append('MFT Cluster Mirror: {}'.format(self.mft_cluster_mirror))
        res.append('Bytes Per Record: {}'.format(self.bytes_per_record))
        res.append('Unused6: {}'.format(self.unused6))
        res.append('Bytes Per Index Buffer: {}'.format(self.bytes_per_index_buffer))
        res.append('Unused7: {}'.format(self.unused7))
        res.append('Volume Serial Number: {}'.format(self.volume_serial_number))
        res.append('Unused8: {}'.format(self.unused8.hex()))
        res.append('Boot Code: {}'.format(self.boot_code.hex()))
        res.append('Boot Sector Signature: {}'.format(self.boot_sector_signature.hex()))
        return '\n'.join(res)
