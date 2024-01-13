import asyncio
import os
from adiskreader.datasource.file import FileSource
from adiskreader.disks.raw import RAWDisk
from adiskreader.filesystems.ntfs import NTFS
from adiskreader.utils.debug import hook_input, hook_print
import tracemalloc
import objgraph


#hook_print()
#hook_input()

tracemalloc.start() 
async def test():
    #filepath = '/home/webdev/Desktop/projects/adiskreader/tests/testfiles/ntfs_test.img'
    filepath = '/mnt/hgfs/vhdxtest/fulldisk_new.dd.raw'
    ds = await FileSource.from_file(filepath)
    disk = await RAWDisk.from_datasource(ds)
    partitions = await disk.list_partitions()
    fs = await NTFS.from_partition(partitions[2])
    assert fs is not None
    
    #print('Testing get_inode')
    #await fs.mft.get_inode(100209)
    #print('done')
    i= 0
    async for root, dirs, files in fs.walk():
        for dir in dirs:
            print(os.path.join(str(root), str(dir)))
        for file in files:
            print(os.path.join(str(root), str(file)))
        i += 1
        #if i > 10000:
        #    break
    
    snapshot = tracemalloc.take_snapshot() 
    top_stats = snapshot.statistics('lineno') 
    
    for stat in top_stats[:10]: 
        print(stat)
    
    objgraph.show_growth(limit=10)
        
def walktest():
    for root, dirs, files in os.walk('/mnt/hgfs/vhdxtest/'):
        for dir in dirs:
            print(os.path.join(str(root), str(dir)))
        for file in files:
            print(os.path.join(str(root), str(file)))

asyncio.run(test())
#walktest()

