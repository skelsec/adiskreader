import io
from typing import Dict, List
from adiskreader.disks.vmdk.structures.extentdescriptor import ExtentDescriptor

MAX_SIZE = 20*1024

class DescriptorFile:
    def __init__(self):
        self.entries:Dict[str, str] = {}
        self.extentdescritpors:List[ExtentDescriptor] = []

    @staticmethod
    def from_bytes(data:bytes):
        df = DescriptorFile()
        print(data[:0x50])
        if data.startswith(b'# Disk DescriptorFile') is False:
            raise Exception('Invalid descriptor file')
        for rawline in data.split(b'\n'):
            if rawline.startswith(b'\x00'):
                break
            line=rawline.decode('utf-8').strip()
            if line.startswith('#') or line == '':
                continue
            
            if line.startswith("RW") or line.startswith("RDONLY") or line.startswith("NOACCESS"):
                df.extentdescritpors.append(ExtentDescriptor.from_line(line))
                continue

            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            value = value.replace('"', '')
            df.entries[key] = value

        return df

    
    @staticmethod
    def from_buffer(buffer:io.BytesIO):
        return DescriptorFile.from_bytes(buffer.read(MAX_SIZE))
    
    def __str__(self):
        t = []
        for k,v in self.entries.items():
            t.append(f"{k}={v}")
        for e in self.extentdescritpors:
            t.append(str(e))
        return '\n'.join(t)