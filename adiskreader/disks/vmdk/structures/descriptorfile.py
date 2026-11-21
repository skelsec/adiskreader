import io
from typing import Dict, List
from adiskreader.disks.vmdk.structures.extentdescriptor import ExtentDescriptor

MAX_SIZE = 64*1024

class DescriptorFile:
    """The text descriptor of the disk, either a file on its own or embedded in a sparse extent"""
    def __init__(self):
        self.entries:Dict[str, str] = {}
        self.extentdescriptors:List[ExtentDescriptor] = []

    @property
    def create_type(self):
        return self.entries.get('createType')

    @property
    def parent_filename(self):
        """File name of the parent disk if this disk is a snapshot (delta) of another one"""
        if self.entries.get('parentCID', 'ffffffff').lower() == 'ffffffff':
            return None
        return self.entries.get('parentFileNameHint')

    @staticmethod
    def from_bytes(data:bytes):
        df = DescriptorFile()
        # embedded descriptors are padded with zeroes
        data = data.split(b'\x00', 1)[0]
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('latin-1')
        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('#') or line == '':
                continue
            
            access = line.split(' ', 1)[0].upper()
            if access in ['RW', 'RDONLY', 'NOACCESS']:
                df.extentdescriptors.append(ExtentDescriptor.from_line(line))
                continue

            if line.find('=') == -1:
                raise Exception('Invalid descriptor file')
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            value = value.replace('"', '')
            df.entries[key] = value

        if 'createType' not in df.entries and len(df.extentdescriptors) == 0:
            raise Exception('Invalid descriptor file')
        return df

    
    @staticmethod
    def from_buffer(buffer:io.BytesIO):
        return DescriptorFile.from_bytes(buffer.read(MAX_SIZE))
    
    def __str__(self):
        t = []
        for k,v in self.entries.items():
            t.append(f"{k}={v}")
        for e in self.extentdescriptors:
            t.append(str(e))
        return '\n'.join(t)
