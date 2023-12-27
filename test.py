import asyncio
from adiskreader.disks.vhdx import VHDXDisk
from adiskreader.partitions import Partitions

from adiskreader.filesystems.ntfs import NTFS

import random

async def fs_read(file, offset, size):
    await file.seek(offset, 0)
    return await file.read(size)

def file_read(file, offset, size):
    file.seek(offset, 0)
    return file.read(size)

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
    #async for entry in fs.ls('Windows\\System32'):
    #    print(entry)

    fs_file = await fs.open('Windows\\System32\\config\\SAM')
    file = open('/mnt/test/Windows/System32/config/SAM', 'rb')

    fs_fulldata = await fs_file.read()
    file_fulldata = file.read()
    if fs_fulldata != file_fulldata:
        print('Mismatch at full file')
        i = 0
        while i < len(fs_fulldata):
            if fs_fulldata[i] != file_fulldata[i]:
                print('Mismatch at offset %s' % i)
                print(fs_fulldata[i:i+0x100])
                print(file_fulldata[i:i+0x100])
                break
            i += 1
        #print(fs_fulldata)
        #print(file_fulldata)
        raise Exception('Mismatch')

    filesize = 65535

    test_runs = 0
    while True:
        offset = random.randint(0, filesize)
        size = random.randint(0, filesize-offset)
        data_fs = await fs_read(fs_file, offset, size)
        data_file = file_read(file, offset, size)
        if data_fs != data_file:
            print('Mismatch at offset %s size %s' % (offset, size))
            print(data_fs)
            print(data_file)
            print(test_runs)
            raise Exception('Mismatch')
        test_runs += 1
    
    #await file.seek(0x10)
    #data = await file.read(5)
    #print(data)


def main():
    asyncio.run(amain())

if __name__ == '__main__':
    main()