"""
Generates the NTFS images with compressed files (testfiles/ntfs_compressed_*.img.gz) and their manifest.
The images are made by mkntfs and written by ntfs-3g mounted with its compression option, in a user namespace: no root needed.
The manifest is made of the data written, not of what is read back from the images.

Usage: python make_ntfs_compressed.py
Needs: ntfs-3g (mkntfs, ntfs-3g), unshare
"""

import gzip
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

from config import MANIFESTS_FOLDER_PATH, CURRENT_FILE_PATH, NTFS_COMPRESSED_FILES_GZ

IMAGE_SIZE = 8*1024*1024
# FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_COMPRESSED, the files made in the directory inherit the compression
ATTRIB_COMPRESSED_DIR = (0x10 | 0x800).to_bytes(4, 'big')

WORDS = [b'cluster', b'runlist', b'attribute', b'record', b'index', b'bitmap', b'volume', b'stream', b'sparse', b'journal']

def make_text(rng, size):
    out = bytearray()
    while len(out) < size:
        out += rng.choice(WORDS) + rng.choice([b' ', b' ', b'\r\n', b', ', b' %d ' % rng.randrange(100000)])
    return bytes(out[:size])

def make_files():
    """Returns the files of the images: path -> list of (offset, data) writes, the rest of the file is a hole"""
    rng = random.Random(0x4c5a4e54)
    text = make_text(rng, 300000)
    mixed = b''
    for i in range(8):
        # the parts do not end at the end of the compression units
        mixed += make_text(rng, 50000) if i % 2 == 0 else rng.randbytes(30000)
    files = {
        # all of its units are compressed
        'compressed\\text.txt' : [(0, text)],
        # can not be compressed, its units are stored as they are
        'compressed\\random.bin' : [(0, rng.randbytes(100000))],
        # both kinds of units, and units where only some of the chunks are compressed
        'compressed\\mixed.bin' : [(0, mixed)],
        'compressed\\low_entropy.bin' : [(0, bytes(rng.choice(b'ACGT') for _ in range(150000)))],
        # units without clusters
        'compressed\\zeros.bin' : [(0, b'\x00' * 200000)],
        # stored in the file record
        'compressed\\small.txt' : [(0, make_text(rng, 100))],
        # shorter than a chunk, shorter than a unit of both of the images
        'compressed\\short.txt' : [(0, make_text(rng, 3000))],
        'compressed\\unit_4k.txt' : [(0, make_text(rng, 65536))],
        'compressed\\unit_512.txt' : [(0, make_text(rng, 8192))],
        'compressed\\unit_and_byte.txt' : [(0, make_text(rng, 2*65536 + 1))],
        'compressed\\long_matches.bin' : [(0, b'\xAA' * 70000 + b'ab' * 30000 + b'\x00' * 5000 + b'xyz' * 9000)],
        # holes between the data
        'compressed\\holes.bin' : [(0, make_text(rng, 20000)), (300000, rng.randbytes(10000)), (1000000, make_text(rng, 70000))],
        'compressed\\sub\\nested.txt' : [(0, make_text(rng, 90000))],
        # the same data without compression
        'plain\\text.txt' : [(0, text)],
    }
    return files

def file_data(writes):
    out = bytearray()
    for offset, data in writes:
        out += b'\x00' * (offset - len(out))
        out += data
    return bytes(out)

def make_manifest(files):
    dirs = set()
    manifest = {}
    for path, writes in files.items():
        parts = path.split('\\')[:-1]
        for i in range(len(parts)):
            dirs.add('\\'.join(parts[:i+1]))
        data = file_data(writes)
        manifest[path] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    return {'dirs': sorted(dirs), 'files': dict(sorted(manifest.items()))}

# run in the user namespace: ntfs-3g is not detached, the image is complete when it exits, not when the umount returns
MOUNT_SCRIPT = """
ntfs-3g -o "$1" "$2" "$3" &
pid=$!
until mountpoint -q "$3"; do
    kill -0 $pid 2>/dev/null || exit 1
    sleep 0.1
done
"$4" "$5" "$3"
res=$?
umount "$3"
wait $pid
exit $res
"""

def write_files(mnt, files):
    for path, writes in files.items():
        parts = path.split('\\')
        dirpath = os.path.join(mnt, *parts[:-1])
        if not os.path.isdir(dirpath):
            os.makedirs(dirpath)
            if parts[0] == 'compressed' and len(parts) == 2:
                os.setxattr(dirpath, 'system.ntfs_attrib_be', ATTRIB_COMPRESSED_DIR)
        with open(os.path.join(dirpath, parts[-1]), 'wb') as f:
            for offset, data in writes:
                f.seek(offset, 0)
                f.write(data)

def make_image(image_path, cluster_size):
    """Makes the filesystem, mounts it and writes the files"""
    with open(image_path, 'wb') as f:
        f.truncate(IMAGE_SIZE)
    subprocess.run(['mkntfs', '-F', '-q', '-c', str(cluster_size), '-L', 'COMPRESSED', image_path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory() as mnt:
        options = 'compression,no_detach,uid=%d,gid=%d,umask=0' % (os.getuid(), os.getgid())
        # mounting needs the capabilities of a user namespace, the user stays the same in it
        # the files are written by this script, started by the shell: python may not keep the capabilities
        subprocess.run([
            'unshare', '--user', '--mount', '--map-user=%d' % os.getuid(), '--map-group=%d' % os.getgid(), '--keep-caps',
            'sh', '-c', MOUNT_SCRIPT, 'sh', options, image_path, mnt, sys.executable, os.path.abspath(__file__)
        ], check=True)

def main():
    files = make_files()
    if len(sys.argv) > 1:
        # in the user namespace
        write_files(sys.argv[1], files)
        return

    manifest = make_manifest(files)
    outpath = MANIFESTS_FOLDER_PATH.joinpath('ntfs_compressed.json')
    with open(outpath, 'w') as f:
        json.dump(manifest, f, indent='\t')
        f.write('\n')
    print('%s: %s dirs, %s files' % (outpath.name, len(manifest['dirs']), len(manifest['files'])))

    with tempfile.TemporaryDirectory() as tmpdir:
        for cluster_size, image_gz in NTFS_COMPRESSED_FILES_GZ.items():
            image_path = os.path.join(tmpdir, 'ntfs.img')
            make_image(image_path, cluster_size)
            gz_path = CURRENT_FILE_PATH.joinpath(image_gz)
            with open(image_path, 'rb') as src, open(gz_path, 'wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as dst:
                shutil.copyfileobj(src, dst)
            # the decompressed image the tests made of the previous one
            old = gz_path.with_suffix('')
            if old.exists():
                old.unlink()
            print('%s: %s bytes' % (gz_path.name, gz_path.stat().st_size))

if __name__ == '__main__':
    main()
