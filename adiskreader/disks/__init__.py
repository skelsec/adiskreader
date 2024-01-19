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
        
        if 'path' in ds.config:
            disktype = get_disk_for_extension(ds.config['path'])
            if disktype is not None:
                return await disktype.from_datasource(ds)
        
        if 'url' in ds.config:
            disktype = get_disk_for_extension(ds.config['url'])
            if disktype is not None:
                return await disktype.from_datasource(ds)
                
        # give up and try RAW
        return await RAWDisk.from_datasource(ds)

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
    if extension.endswith('RAW') is True:
        from adiskreader.disks.raw import RAWDisk
        return RAWDisk
    return None