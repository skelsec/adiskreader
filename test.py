import asyncio
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.partitions import Partitions

from adiskreader.filesystems.ntfs import NTFS

async def amain():
    fname = '/mnt/hgfs/vhdxtest/WinDev2311Eval.vhdx'
    vhdx = await VHDXDisk.from_file(fname)
    partitions = Partitions(vhdx)
    await partitions.find_partitions()
    print(partitions)
    partition = partitions.partitions[3]
    
    fs = await NTFS.from_partition(partition)
    print(vhdx.active_meta)
    print(fs)
    async for entry in fs.ls('Windows\\System32'):
        print(entry)


def main():
    asyncio.run(amain())

if __name__ == '__main__':
    main()