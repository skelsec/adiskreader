import asyncio
from adiskreader.disks.raw import RAWDisk
from adiskreader.partitions import Partitions

from adiskreader.filesystems.ntfs import NTFS
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

    pbar_total    = tqdm(desc = 'Total Files     ', unit='files', position=0)
    pbar_match    = tqdm(desc = 'Matched Files   ', unit='files', position=1)
    pbar_mismatch = tqdm(desc = 'Mismatched Files', unit='files', position=2)
    pbar_notfound = tqdm(desc = 'Not Found Files ', unit='files', position=3)

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
            if st.st_size > 512*1024*1024:
                continue
            pbar_total.update()
            #st2 = await fs.stat(newpath)
            #input(st2)
            with open(filepath, 'rb') as f:
                data = f.read()
                good_hash = hashlib.sha1(data).hexdigest()
            
            try:
                tfile = await fs.open(newpath)
            except FileNotFoundError:
                #print('[NOTFOUND] %s -> %s' % (filepath, newpath))
                pbar_notfound.update()
                continue
            tdata = await tfile.read()
            t_hash = hashlib.sha1(tdata).hexdigest()
            if t_hash != good_hash:
                pbar_mismatch.update()
                #print('[MISS] %s -> %s (%s -> %s)' % (filepath, newpath, len(data), len(tdata)))
                #for c in range(len(data)):
                #    if data[c] != tdata[c]:
                #        print('Mismatch at %s' % c)
                #        break
                #print(tdata[c-10:c+10])
                #print(data[c-10:c+10])
                #test_2 = tfile.get_record()
                #temp3 = await test_2.get_attribute(0x80)
                #input(temp3)
                #print(test_2.update_seq)
                #print('Datarun: %s' % tfile.get_dataruns())
                #print('DataAttr: %s' % tfile.get_dataattr())
                #print('Expected: %s' % data)
                #print('Got     : %s' % tdata)
                #input()
            else:
                pbar_match.update()
                #print('[MATCH] %s -> %s' % (filepath, newpath))
            #input()


def main():
    asyncio.run(amain())

if __name__ == '__main__':
    main()