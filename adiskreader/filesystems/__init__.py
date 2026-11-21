from adiskreader.disks import Disk
from adiskreader.partitions import Partition

class FileSystem:
    def __init__(self):
        pass

    @staticmethod
    async def from_disk(disk:Disk, start_lba:int):
        raise NotImplementedError()
    
    @staticmethod
    async def from_partition(partition:Partition):
        raise NotImplementedError()

    async def get_root(self):
        raise NotImplementedError()
    
    async def open(self, path:str, mode:str = 'rb'):
        raise NotImplementedError()
    
    async def stat(self, path:str):
        raise NotImplementedError()
    
    async def walk(self, path:str = "\\"):
        raise NotImplementedError()

    async def open_datasource(self, path:str):
        """Opens a file of the filesystem as a datasource, for reading the disk images stored in it"""
        from adiskreader.datasource.fsfile import FSFileSource
        return await FSFileSource.from_filesystem(self, path)
