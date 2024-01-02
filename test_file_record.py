from adiskreader.filesystems.ntfs.structures.filerecord import FileRecord
import asyncio

async def amain():
    with open('download.dat', 'rb') as x:
        fr = FileRecord.from_bytes(x.read(), None, None)
        print(fr)





def main():
    asyncio.run(amain())

if __name__ == '__main__':
    main()