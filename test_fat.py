import asyncio
from adiskreader.disks.raw import RAWDisk
from adiskreader.partitions import Partitions

from adiskreader.filesystems.fat import FAT
import hashlib
import random
import glob
import os
from tqdm import tqdm

async def fs_read(file, offset, size):
    await file.seek(offset, 0)
    return await file.read(size)

def file_read(file, offset, size):
    file.seek(offset, 0)
    return file.read(size)

async def amain():
    fname = '/mnt/hgfs/vhdxtest/fulldisk_new.dd.raw' #'/dev/sdb' #'/mnt/hgfs/vhdxtest/fulldisk_new.dd.raw'
    vhdx = await RAWDisk.from_file(fname)
    partitions = Partitions(vhdx)
    await partitions.find_partitions()
    print(partitions)
    
    #for partition in partitions.partitions:
    #    await partition.guess_filesystem(vhdx)
    
    partition = partitions.partitions[0]

    fs = await FAT.from_partition(partition)
    print(fs)

    good_data = open('/mnt/rawtest/EFI/Microsoft/Recovery/BCD.LOG', 'rb').read()
    
    test_file = await fs.open('EFI\\Microsoft\\Recovery\\BCD.LOG')
    test_data = await test_file.read()

    print(test_data == good_data)

def main():
    asyncio.run(amain())

if __name__ == '__main__':
    main()