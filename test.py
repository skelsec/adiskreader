import asyncio
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.partitions.ntfs import NTFSPartition

async def amain():
    fname = 'a.vhdx'
    vhdx = await VHDXDisk.from_file(fname)
    test = await vhdx.read_LBA(262184)
    
    partition = await NTFSPartition.from_disk(vhdx, 262184)
    print(vhdx.active_meta)
    print(partition)
    #print(test.hex())


def main():
    asyncio.run(amain())

if __name__ == '__main__':
    main()