import asyncio
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.partitions import Partitions

from adiskreader.filesystems.ntfs.ntfs import NTFS

async def amain():
    fname = '/mnt/hgfs/vhdxtest/WinDev2311Eval.vhdx'
    vhdx = await VHDXDisk.from_file(fname)
    partitions = Partitions(vhdx)
    await partitions.find_partitions()

    print(partitions)

    part = partitions.partitions[3]

    
    partition = await NTFS.from_partition(part)
    print(vhdx.active_meta)
    print(partition)
    #print(test.hex())


def main():
    asyncio.run(amain())

if __name__ == '__main__':
    main()