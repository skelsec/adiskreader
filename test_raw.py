import asyncio
from adiskreader.disks.raw import RAWDisk
from adiskreader.partitions import Partitions

from adiskreader.filesystems.ntfs import NTFS
import hashlib
import random
import glob
import os

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
    partition = partitions.partitions[2]

    fs = await NTFS.from_partition(partition)
    print(fs)

    #print('Listing directory')
    #async for entry in fs.ls('Windows\\System32\\config'):
    #    print(entry)
    #
    #print('Reading file')
    #input()
    #fs_file = await fs.open('Windows\\System32\\config\\SAM')
    #print(fs_file)
    #data = await fs_file.read()
    ##print(data)
    #print(hashlib.sha1(data).hexdigest())

    #tf = await fs.open('Program Files\\Common Files\\microsoft shared\\ink\\fsdefinitions\\auxpad.xml')
    #data = await tf.read()
    #print(data)

    good_path = '/mnt/rawtest/'
    for root, dirs, files in os.walk(good_path):
        for file in files:
            filepath = os.path.join(root, file)
            #print(filepath)
            newpath = filepath.replace(good_path, '')
            newpath = newpath.replace('/', '\\')
            #print(newpath)
            #if newpath.startswith('$') is True:
            #    continue
    
            good_hash = None
            st = os.stat(filepath)
            if st.st_size > 512*1024*1024 or st.st_size < 1024:
                continue
            #st2 = await fs.stat(newpath)
            #input(st2)
            with open(filepath, 'rb') as f:
                data = f.read()
                good_hash = hashlib.sha1(data).hexdigest()
            
            try:
                tfile = await fs.open(newpath)
            except FileNotFoundError:
                print('[NOTFOUND] %s -> %s' % (filepath, newpath))
                continue
            tdata = await tfile.read()
            t_hash = hashlib.sha1(tdata).hexdigest()
            if t_hash != good_hash:
                print('[MISS] %s -> %s (%s -> %s)' % (filepath, newpath, len(data), len(tdata)))
                test_2 = tfile.get_record()
                temp3 = await test_2.get_attribute(0x80)
                input(temp3)
                print('Datarun: %s' % tfile.get_dataruns())
                print('DataAttr: %s' % tfile.get_dataattr())
                print('Expected: %s' % data)
                print('Got     : %s' % tdata)
                input()
            else:
                print('[MATCH] %s -> %s' % (filepath, newpath))
            #input()


def main():
    asyncio.run(amain())

if __name__ == '__main__':
    main()