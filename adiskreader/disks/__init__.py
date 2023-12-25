

class Disk:
    def __init__(self):
        pass

    async def read_LBA(self, lba:int):
        raise NotImplementedError()

    async def read_LBAs(self, lbas:list):
        raise NotImplementedError()