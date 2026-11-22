from urllib.parse import urlparse
from adiskreader.partitions import Partition, PartitionFinder

class Disk:
    def __init__(self):
        pass

    async def read_LBA(self, lba:int):
        raise NotImplementedError()

    async def read_LBAs(self, lbas:list):
        raise NotImplementedError()
    
    async def get_boot_record(self):
        pf = await PartitionFinder.from_disk(self)
        return await pf.get_boot_record()

    async def list_partitions(self):
        pf = await PartitionFinder.from_disk(self)
        await pf.find_partitions()
        return pf.partitions
    
    @staticmethod
    async def from_datasource(ds):
        from adiskreader.disks.raw import RAWDisk

        if ds.config is None:
            raise Exception('Datasource config is None')
        if 'schemes' in ds.config:
            schemes = ds.config['schemes']
            if 'RAW' in schemes:
                return await RAWDisk.from_datasource(ds)
            if 'VHDX' in schemes:
                from adiskreader.disks.vhdx import VHDXDisk
                return await VHDXDisk.from_datasource(ds)
            if 'VMDK' in schemes:
                from adiskreader.disks.vmdk import VMDKDisk
                return await VMDKDisk.from_datasource(ds)
            if 'VHD' in schemes:
                from adiskreader.disks.vhd import VHDDisk
                return await VHDDisk.from_datasource(ds)
            if 'QCOW2' in schemes:
                from adiskreader.disks.qcow2 import QCOW2Disk
                return await QCOW2Disk.from_datasource(ds)
            if 'VDI' in schemes:
                from adiskreader.disks.vdi import VDIDisk
                return await VDIDisk.from_datasource(ds)
        
        if 'path' in ds.config:
            disktype = get_disk_for_extension(ds.config['path'])
            if disktype is not None:
                return await disktype.from_datasource(ds)
        
        if 'url' in ds.config:
            # the parameters of the url are not part of the file name
            disktype = get_disk_for_extension(urlparse(ds.config['url']).path)
            if disktype is not None:
                return await disktype.from_datasource(ds)
                
        # the extension did not help, the sparse formats have a signature at the start of the file
        disktype = await detect_disk_type(ds)
        if disktype is not None:
            return await disktype.from_datasource(ds)

        # give up and try RAW
        return await RAWDisk.from_datasource(ds)

async def detect_disk_type(ds):
    """Returns the disk type of the signature at the start of the file, None if there is no known one"""
    await ds.seek(0, 0)
    data = await ds.read(72)
    if data[:4] == b'QFI\xfb':
        from adiskreader.disks.qcow2 import QCOW2Disk
        return QCOW2Disk
    if data[:8] == b'vhdxfile':
        from adiskreader.disks.vhdx import VHDXDisk
        return VHDXDisk
    if data[:4] in (b'KDMV', b'COWD'):
        from adiskreader.disks.vmdk import VMDKDisk
        return VMDKDisk
    if data[64:68] == b'\x7f\x10\xda\xbe':
        from adiskreader.disks.vdi import VDIDisk
        return VDIDisk
    if data[:8] == b'conectix':
        from adiskreader.disks.vhd import VHDDisk
        return VHDDisk
    return None

def get_disk_for_extension(extension:str):
    extension = extension.upper()
    if extension.endswith('VHDX') is True:
        from adiskreader.disks.vhdx import VHDXDisk
        return VHDXDisk
    if extension.endswith('VHD') is True:
        from adiskreader.disks.vhd import VHDDisk
        return VHDDisk
    if extension.endswith('VMDK') is True:
        from adiskreader.disks.vmdk import VMDKDisk
        return VMDKDisk
    if extension.endswith('QCOW2') is True:
        from adiskreader.disks.qcow2 import QCOW2Disk
        return QCOW2Disk
    if extension.endswith('VDI') is True:
        from adiskreader.disks.vdi import VDIDisk
        return VDIDisk
    if extension.endswith('RAW') is True:
        from adiskreader.disks.raw import RAWDisk
        return RAWDisk
    return None