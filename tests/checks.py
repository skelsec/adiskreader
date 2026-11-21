"""Filesystem checks shared by the tests, comparing a filesystem against the manifest of its test image"""

import hashlib
import ntpath
import random

from config import fs_read


async def check_file_list(fs, manifest):
	paths = {}
	async for root, dirs, files in fs.walk():
		for dir in dirs:
			paths[ntpath.join(str(root), str(dir))] = 1
		for file in files:
			paths[ntpath.join(str(root), str(file))] = 1

	for dir_path in manifest['dirs']:
		assert '\\' + dir_path in paths
	for file_path in manifest['files']:
		assert '\\' + file_path in paths

async def check_file_open(fs, manifest):
	for file_path in manifest['files']:
		ffile = await fs.open(file_path)
		assert ffile is not None

async def check_file_stat(fs, manifest):
	for file_path, expected in manifest['files'].items():
		st_fs = await fs.stat(file_path)
		assert st_fs.st_size == expected['size']

async def check_file_fullread(fs, manifest):
	for file_path, expected in manifest['files'].items():
		ffile = await fs.open(file_path)
		assert ffile is not None
		data = await ffile.read()
		assert len(data) == expected['size']
		assert hashlib.sha256(data).hexdigest() == expected['sha256']

async def check_file_ops(fs, manifest):
	rng = random.Random(0)
	for file_path, expected in manifest['files'].items():
		ffile = await fs.open(file_path)
		assert ffile is not None
		# the full read is verified against the manifest, the partial reads against the full read
		odata = await ffile.read()
		assert hashlib.sha256(odata).hexdigest() == expected['sha256']
		fsize = expected['size']
		for _ in range(0, 50):
			pos = rng.randint(0, fsize)
			size = rng.randint(0, fsize-pos)
			data = await fs_read(ffile, pos, size)
			assert data == odata[pos:pos+size]
