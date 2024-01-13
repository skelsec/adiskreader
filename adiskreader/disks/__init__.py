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
        
        if 'path' in ds.config:
            if ds.config['path'].upper().endswith('.VHDX'):
                from adiskreader.disks.vhdx import VHDXDisk
                return await VHDXDisk.from_datasource(ds)
        
        if 'url' in ds.config:
            if ds.config['url'].upper().endswith('.VHDX'):
                from adiskreader.disks.vhdx import VHDXDisk
                return await VHDXDisk.from_datasource(ds)
        
        # give up and try RAW
        return await RAWDisk.from_datasource(ds)
            