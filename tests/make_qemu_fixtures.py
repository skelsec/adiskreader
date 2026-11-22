"""
Creates the QCOW2 and VDI test disks: testfiles/adiskreader_qemu_fixtures.zip
Needs qemu-img, qemu-io and vbox-img (VirtualBox), the zip is committed so the tests do not need them.

The zip holds the disks, what qemu reads from each of them (expected/<sha256>.raw) and the manifest:
{images: [{name, format, file, files, expected, snapshots: {name: expected}, parents: [name], requires}]}

Usage: python make_qemu_fixtures.py
"""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile

from config import *

MB = 1024 * 1024
# not a multiple of the cluster / block sizes
DISK_SIZE = 5 * MB + 3 * 512
VDI_BLOCK_SIZE = MB

def pattern(seed:int, size:int):
	"""Data which is different in every sector, but can be compressed"""
	res = bytearray()
	sector = 0
	while len(res) < size:
		res += (struct.pack('<II', seed, sector) + b'adiskreader %d %d ' % (seed, sector)) * 16
		res = res[:(sector + 1) * 512]
		sector += 1
	return bytes(res[:size])

def base_data():
	"""Contents of the disk the test disks are converted from: data with holes of different sizes"""
	data = bytearray(DISK_SIZE)
	src = pattern(1, DISK_SIZE)
	for start, size in [
			(0, 0x30000), (0x40200, 0x200), (0x7fe00, 0x10400), (0x100000, 0x1000), (0x180000, 0x9000),
			(0x200000, 0x100000), (0x3ff000, 0x2000), (0x4c0000, 0x40000), (DISK_SIZE - 0x600, 0x600)]:
		data[start:start+size] = src[start:start+size]
	return bytes(data)

# (offset, size) of the writes made to the overlays and after the snapshots, each list uses its own pattern
WRITES_1 = [(0x1000, 0x800), (0x20000, 0x20000), (0x100200, 0x200), (0x2f0000, 0x20000), (0x480000, 0x1000)]
WRITES_2 = [(0x1400, 0x200), (0x30000, 0x1000), (0x310000, 0x8000), (0x4ff000, 0x1000 + 3 * 512)]
ZEROES_2 = [(0x200000, 0x30000), (0x10000, 0x1200)]
DISCARDS_2 = [(0x280000, 0x40000)]
# small writes, for the disks where the parts of a cluster have their own state
WRITES_SUB = [(0x0, 0x800), (0x2000, 0x1000), (0x50800, 0x200), (0x207000, 0x800), (0x3ffe00, 0x400)]
ZEROES_SUB = [(0x1000, 0x800), (0x205000, 0x3000), (0x2f0000, 0x10000)]

def run(*args):
	subprocess.run([str(arg) for arg in args], check=True, stdout=subprocess.DEVNULL)

def qemu_io(fmt, path, commands):
	args = ['qemu-io', '-f', fmt, '-d', 'unmap']
	for command in commands:
		args += ['-c', command]
	run(*args, path)

class Fixtures:
	def __init__(self, folder):
		self.folder = folder
		self.images = []
		self.expected = {} # sha256 -> data
		self.patterns = {}

	def path(self, name):
		return os.path.join(self.folder, name)

	def pattern_file(self, seed):
		if seed not in self.patterns:
			self.patterns[seed] = self.path('pattern%d.bin' % seed)
			with open(self.patterns[seed], 'wb') as f:
				f.write(pattern(seed, DISK_SIZE + MB))
		return self.patterns[seed]

	def write(self, fmt, name, seed, writes, zeroes = [], discards = []):
		"""Writes to the disk with qemu-io: the data of the pattern (the same data as the pattern has at that offset)"""
		commands = []
		for offset, size in writes:
			if size > 0:
				# the pattern file is read from its start, so it is cut to begin at the offset
				cut = self.path('cut.bin')
				with open(self.pattern_file(seed), 'rb') as f:
					f.seek(offset)
					with open(cut + '.%x' % offset, 'wb') as o:
						o.write(f.read(size))
				commands.append('write -s %s.%x 0x%x 0x%x' % (cut, offset, offset, size))
		for offset, size in zeroes:
			commands.append('write -z 0x%x 0x%x' % (offset, size))
		for offset, size in discards:
			commands.append('discard 0x%x 0x%x' % (offset, size))
		qemu_io(fmt, self.path(name), commands)

	def read(self, fmt, name, snapshot = None):
		"""What qemu reads from the disk"""
		raw = self.path('read.raw')
		args = ['qemu-img', 'convert', '-f', fmt, '-O', 'raw']
		if snapshot is not None:
			args += ['-l', 'snapshot.name=%s' % snapshot]
		run(*args, self.path(name), raw)
		with open(raw, 'rb') as f:
			return f.read()

	def add_expected(self, data):
		sha = hashlib.sha256(data).hexdigest()
		self.expected[sha] = data
		return sha

	def add(self, name, fmt, file, files = None, expected = None, snapshots = [], parents = [], requires = None):
		if expected is None:
			expected = self.read('vdi' if fmt == 'vdi' else 'qcow2', file)
		image = {
			'name' : name,
			'format' : fmt,
			'file' : file,
			'files' : files if files is not None else [file],
			'expected' : self.add_expected(expected),
			'snapshots' : {},
			'parents' : parents,
			'requires' : requires,
		}
		for snapshot in snapshots:
			image['snapshots'][snapshot] = self.add_expected(self.read('qcow2', file, snapshot))
		self.images.append(image)
		return expected

	def convert(self, name, options = None, compress = False, src = 'base.raw', requires = None):
		file = '%s.qcow2' % name
		args = ['qemu-img', 'convert', '-f', 'raw', '-O', 'qcow2']
		if compress is True:
			args.append('-c')
		if options is not None:
			args += ['-o', options]
		run(*args, self.path(src), self.path(file))
		return file

	def build_qcow2(self):
		base = base_data()
		for name, options, compress, requires in [
				('v3', None, False, None),
				('v2', 'compat=0.10', False, None),
				('cluster512', 'cluster_size=512', False, None),
				('cluster2m', 'cluster_size=2M', False, None),
				('compressed', None, True, None),
				('compressed_v2_4k', 'compat=0.10,cluster_size=4k', True, None),
				('compressed_2m', 'cluster_size=2M', True, None),
				('zstd', 'compression_type=zstd', True, 'zstd'),
				('extl2', 'extended_l2=on', False, None),
				('extl2_compressed', 'extended_l2=on,cluster_size=16k', True, None),
				('prealloc', 'preallocation=metadata,lazy_refcounts=on', False, None),
			]:
			file = self.convert(name, options, compress)
			assert self.add(name, 'qcow2', file, requires = requires) == base

		# the dirty flag is set while the disk is open with lazy refcounts
		file = self.convert('dirty', 'lazy_refcounts=on')
		with open(self.path(file), 'r+b') as f:
			f.seek(72)
			features = struct.unpack('>Q', f.read(8))[0]
			f.seek(72)
			f.write(struct.pack('>Q', features | 1))
		self.add('dirty', 'qcow2', file, expected = base)

		# zero clusters with and without allocation, discarded clusters
		file = self.convert('zeroes')
		self.write('qcow2', file, 2, WRITES_2, ZEROES_2, DISCARDS_2)
		self.add('zeroes', 'qcow2', file)
		file = self.convert('zeroes_v2', 'compat=0.10,cluster_size=4k')
		self.write('qcow2', file, 2, WRITES_2, ZEROES_2, DISCARDS_2)
		self.add('zeroes_v2', 'qcow2', file)

		# chain of backing files with different cluster sizes, the last one is larger than its backing file
		self.convert('chain_base')
		run('qemu-img', 'create', '-f', 'qcow2', '-b', 'chain_base.qcow2', '-F', 'qcow2', '-o', 'cluster_size=4k', self.path('chain_mid.qcow2'))
		self.write('qcow2', 'chain_mid.qcow2', 3, WRITES_1)
		run('qemu-img', 'create', '-f', 'qcow2', '-b', 'chain_mid.qcow2', '-F', 'qcow2', self.path('chain_top.qcow2'), DISK_SIZE + 0x28000)
		self.write('qcow2', 'chain_top.qcow2', 2, WRITES_2 + [(DISK_SIZE + 0x10000, 0x8000)], ZEROES_2, DISCARDS_2)
		self.add('chain_base', 'qcow2', 'chain_base.qcow2')
		self.add('chain_mid', 'qcow2', 'chain_mid.qcow2', ['chain_mid.qcow2', 'chain_base.qcow2'], parents = ['chain_base'])
		self.add('chain_top', 'qcow2', 'chain_top.qcow2', ['chain_top.qcow2', 'chain_mid.qcow2', 'chain_base.qcow2'], parents = ['chain_mid', 'chain_base'])

		# extended L2 entries over a backing file: parts of the clusters are data, zeroes or read from the backing file
		run('qemu-img', 'create', '-f', 'qcow2', '-b', 'chain_base.qcow2', '-F', 'qcow2', '-o', 'extended_l2=on', self.path('extl2_overlay.qcow2'))
		self.write('qcow2', 'extl2_overlay.qcow2', 4, WRITES_SUB + WRITES_1, ZEROES_SUB)
		self.add('extl2_overlay', 'qcow2', 'extl2_overlay.qcow2', ['extl2_overlay.qcow2', 'chain_base.qcow2'], parents = ['chain_base'])

		# backing files of other types
		shutil.copy(self.path('base.raw'), self.path('backing.img'))
		run('qemu-img', 'create', '-f', 'qcow2', '-b', 'backing.img', '-F', 'raw', self.path('raw_overlay.qcow2'), DISK_SIZE + 0x10000)
		self.write('qcow2', 'raw_overlay.qcow2', 3, WRITES_1)
		self.add('raw_overlay', 'qcow2', 'raw_overlay.qcow2', ['raw_overlay.qcow2', 'backing.img'])
		run('qemu-img', 'convert', '-f', 'raw', '-O', 'vmdk', self.path('base.raw'), self.path('backing.vmdk'))
		run('qemu-img', 'create', '-f', 'qcow2', '-b', 'backing.vmdk', '-F', 'vmdk', self.path('vmdk_overlay.qcow2'))
		self.write('qcow2', 'vmdk_overlay.qcow2', 3, WRITES_1)
		self.add('vmdk_overlay', 'qcow2', 'vmdk_overlay.qcow2', ['vmdk_overlay.qcow2', 'backing.vmdk'])

		# external data files
		self.convert('datafile', 'data_file=%s' % self.path('datafile.data'))
		self.write('qcow2', 'datafile.qcow2', 2, WRITES_2, ZEROES_2, DISCARDS_2)
		self.add('datafile', 'qcow2', 'datafile.qcow2', ['datafile.qcow2', 'datafile.data'])
		self.convert('datafile_raw', 'data_file=%s,data_file_raw=on' % self.path('datafile_raw.data'))
		self.write('qcow2', 'datafile_raw.qcow2', 2, WRITES_2)
		self.add('datafile_raw', 'qcow2', 'datafile_raw.qcow2', ['datafile_raw.qcow2', 'datafile_raw.data'])
		for name in ['datafile', 'datafile_raw']:
			# the name is stored as it was given, the reader looks for the file next to the disk
			run('qemu-img', 'amend', '-f', 'qcow2', '-o', 'data_file=%s.data' % name, self.path('%s.qcow2' % name))

		# snapshots stored in the disk, the disk is resized after the first one
		file = self.convert('snapshots', 'cluster_size=8k')
		run('qemu-img', 'snapshot', '-c', 'first', self.path(file))
		self.write('qcow2', file, 3, WRITES_1)
		run('qemu-img', 'resize', '-f', 'qcow2', self.path(file), DISK_SIZE + 0x20000)
		self.write('qcow2', file, 3, [(DISK_SIZE + 0x8000, 0x4000)])
		run('qemu-img', 'snapshot', '-c', 'second snapshot', self.path(file))
		self.write('qcow2', file, 2, WRITES_2, ZEROES_2, DISCARDS_2)
		self.add('snapshots', 'qcow2', file, snapshots = ['first', 'second snapshot'])
		file = self.convert('snapshots_compressed', compress = True)
		run('qemu-img', 'snapshot', '-c', 'one', self.path(file))
		self.write('qcow2', file, 3, WRITES_1, ZEROES_2)
		self.add('snapshots_compressed', 'qcow2', file, snapshots = ['one'])

	def build_vdi(self):
		base = base_data()
		# made by VirtualBox
		for name, variant in [('vbox_dynamic', 'Standard'), ('vbox_fixed', 'Fixed')]:
			file = '%s.vdi' % name
			run('vbox-img', 'convert', '--srcfilename', self.path('base.raw'), '--dstfilename', self.path(file), '--srcformat', 'RAW', '--dstformat', 'VDI', '--variant', variant)
			assert self.add(name, 'vdi', file) == base
		# made by qemu
		for name, options in [('qemu_dynamic', 'static=off'), ('qemu_fixed', 'static=on')]:
			file = '%s.vdi' % name
			run('qemu-img', 'convert', '-f', 'raw', '-O', 'vdi', '-o', options, self.path('base.raw'), self.path(file))
			assert self.add(name, 'vdi', file) == base

		# blocks written in a different order than they are on the disk
		run('vbox-img', 'createbase', '--filename', self.path('vbox_written.vdi'), '--size', DISK_SIZE)
		self.write('vdi', 'vbox_written.vdi', 3, [(0x480000, 0x1000), (0x100200, 0x200), (0x2f0000, 0x20000)])
		self.write('vdi', 'vbox_written.vdi', 2, [(0x1400, 0x200), (0x310000, 0x8000)])
		written = self.add('vbox_written', 'vdi', 'vbox_written.vdi')

		# VirtualBox marks the discarded blocks, qemu reads those as zeroes
		shutil.copy(self.path('vbox_dynamic.vdi'), self.path('vbox_discarded.vdi'))
		blocks_offset, block_count = self.vdi_blocks('vbox_discarded.vdi')
		with open(self.path('vbox_discarded.vdi'), 'r+b') as f:
			for index in (2, block_count - 1):
				f.seek(blocks_offset + index * 4)
				f.write(struct.pack('<I', 0xFFFFFFFE))
		discarded = self.add('vbox_discarded', 'vdi', 'vbox_discarded.vdi')
		assert discarded[2*MB:3*MB] == b'\x00' * MB and discarded != base

		# differencing disks: the same format, with the type and the uuid of the parent set. The path of the parent is not stored.
		# Each write of VirtualBox allocates the full block, so the blocks which were written are the ones of the child.
		for name, parent, parent_data in [('vbox_diff1', 'vbox_dynamic', base), ('vbox_diff2', 'vbox_diff1', None)]:
			file = '%s.vdi' % name
			run('vbox-img', 'createbase', '--filename', self.path(file), '--size', DISK_SIZE)
			if parent_data is None:
				parent_data = diff
				self.write('vdi', file, 2, [(0x310000, 0x8000), (0x4ff000, 0x1000 + 3 * 512)])
			else:
				self.write('vdi', file, 3, [(0x100200, 0x200), (0x2f0000, 0x20000)])
			child_data = self.read('vdi', file)
			run('vbox-img', 'setuuid', '--filename', self.path(file), '--parentuuid', self.vdi_uuid('%s.vdi' % parent))
			with open(self.path(file), 'r+b') as f:
				f.seek(76)
				f.write(struct.pack('<I', 4))
			blocks_offset, block_count = self.vdi_blocks(file)
			diff = bytearray(parent_data)
			with open(self.path(file), 'rb') as f:
				f.seek(blocks_offset)
				block_map = struct.unpack('<%dI' % block_count, f.read(block_count * 4))
			for index, block in enumerate(block_map):
				if block != 0xFFFFFFFF:
					diff[index*VDI_BLOCK_SIZE:(index+1)*VDI_BLOCK_SIZE] = child_data[index*VDI_BLOCK_SIZE:(index+1)*VDI_BLOCK_SIZE]
			diff = bytes(diff)
			parents = ['vbox_diff1', 'vbox_dynamic'] if name == 'vbox_diff2' else ['vbox_dynamic']
			self.add(name, 'vdi', file, [file] + ['%s.vdi' % p for p in parents], expected = diff, parents = parents)

	def vdi_blocks(self, file):
		with open(self.path(file), 'rb') as f:
			data = f.read(512)
		return struct.unpack_from('<I', data, 340)[0], struct.unpack_from('<I', data, 384)[0]

	def vdi_uuid(self, file):
		with open(self.path(file), 'rb') as f:
			data = f.read(512)
		return str(uuid.UUID(bytes_le=data[392:408]))

	def build(self, zippath):
		with open(self.path('base.raw'), 'wb') as f:
			f.write(base_data())
		self.build_qcow2()
		self.build_vdi()

		files = []
		for image in self.images:
			for file in image['files']:
				if file not in files:
					files.append(file)
		with zipfile.ZipFile(zippath, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
			zf.writestr('manifest.json', json.dumps({'disk_size': DISK_SIZE, 'images': self.images}, indent=1))
			for file in files:
				zf.write(self.path(file), file)
			for sha, data in self.expected.items():
				zf.writestr('expected/%s.raw' % sha, data)


def main():
	for tool in ['qemu-img', 'qemu-io', 'vbox-img']:
		if shutil.which(tool) is None:
			print('%s is not installed' % tool)
			sys.exit(1)
	zippath = CURRENT_FILE_PATH.joinpath(QEMU_FIXTURES_ZIP)
	with tempfile.TemporaryDirectory() as folder:
		Fixtures(folder).build(zippath)
	print('%s created, %d bytes' % (zippath, zippath.stat().st_size))

if __name__ == '__main__':
	main()
