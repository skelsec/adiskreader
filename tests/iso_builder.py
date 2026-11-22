"""
Builds small ISO9660 (with Joliet and Rock Ridge) and UDF images for the tests, there is no tool for this
on most machines. The images are made to use the features of the formats the reader has to deal with
(continuation areas, relocated directories, files in more extents, all kinds of UDF partitions and
allocation descriptors), they are not meant to be burned: there are no path tables and no free space tables.
7z reads most of them, which was used for checking the builder itself.

Usage: python iso_builder.py <iso|udf> <output file> [udf mode] [udf revision, like 201] [block size]
"""

import datetime
import hashlib
import re
import struct
import sys

SECTOR = 2048
DEFAULT_TIME = datetime.datetime(2024, 5, 6, 7, 8, 9, tzinfo=datetime.timezone.utc)


class Node:
	def __init__(self, name, parent = None):
		self.name = name
		self.parent = parent
		self.children = None
		self.data = None
		self.symlink = None
		self.options = {}
		self.iso_name = None

	@property
	def is_dir(self):
		return self.children is not None

	@property
	def mtime(self):
		return self.options.get('mtime', DEFAULT_TIME)

	@property
	def tz(self):
		# minutes from GMT, multiple of 15
		return self.options.get('tz', 0)

	@property
	def mode(self):
		if 'mode' in self.options:
			return self.options['mode']
		return 0o755 if self.is_dir else (0o777 if self.symlink is not None else 0o644)

	def sorted_children(self):
		return sorted(self.children, key = lambda child: child.name)


def make_tree(entries):
	"""
	entries: list of dicts with 'path' (separated by /) and either 'data' (file), 'symlink' (target) or neither (directory).
	The other keys are options for the builders.
	"""
	root = Node('')
	root.children = []
	for entry in entries:
		node = root
		parts = entry['path'].split('/')
		for i, part in enumerate(parts):
			for child in node.children:
				if child.name == part:
					break
			else:
				child = Node(part, node)
				node.children.append(child)
				if i < len(parts) - 1:
					child.children = []
			node = child
		node.options = {key: value for key, value in entry.items() if key not in ('path', 'data', 'symlink')}
		if 'data' in entry:
			node.data = entry['data']
		elif 'symlink' in entry:
			node.symlink = entry['symlink']
		elif node.children is None:
			node.children = []
	assign_iso_names(root)
	return root

def assign_iso_names(root):
	"""The names of the primary tree of ISO9660: upper case 8.3, the ones which do not fit are replaced"""
	counter = 0
	todo = [root]
	while len(todo) > 0:
		node = todo.pop()
		for child in node.sorted_children():
			upper = child.name.upper()
			if child.is_dir:
				todo.append(child)
				if re.fullmatch(r'[A-Z0-9_]{1,8}', upper) is not None:
					child.iso_name = upper
				else:
					counter += 1
					child.iso_name = 'ZD%06d' % counter
			else:
				if re.fullmatch(r'[A-Z0-9_]{1,8}(\.[A-Z0-9_]{0,3})?', upper) is not None:
					child.iso_name = upper + ('' if '.' in upper else '.') + ';1'
				else:
					counter += 1
					child.iso_name = 'ZF%06d.DAT;1' % counter

def shown_iso_name(node):
	name = node.iso_name
	if name.endswith(';1'):
		name = name[:-2]
	return name.rstrip('.')

def joliet_name(node):
	# Joliet allows 64 characters
	if len(node.name) <= 64:
		return node.name
	return node.name[:60] + node.name[-4:]

def manifest(root, names = 'real', symlinks = True):
	"""
	The dirs, files and symlinks of the tree with \\ separated paths.
	names: 'real', 'joliet' (the long ones are cut) or 'iso' (as the primary tree of ISO9660 has them without Rock Ridge)
	symlinks: False if the filesystem does not have them (Joliet), 'files' if they are empty files (plain ISO9660)
	"""
	res = {'dirs': [], 'files': {}, 'symlinks': {}}
	todo = [(root, '')]
	while len(todo) > 0:
		node, path = todo.pop()
		for child in node.children:
			name = {'real': child.name, 'joliet': joliet_name(child), 'iso': shown_iso_name(child)}[names]
			cpath = name if path == '' else path + '\\' + name
			if child.is_dir:
				res['dirs'].append(cpath)
				todo.append((child, cpath))
			elif child.symlink is not None:
				if symlinks is True:
					res['symlinks'][cpath] = child.symlink
				elif symlinks == 'files':
					res['files'][cpath] = {'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
			else:
				res['files'][cpath] = {'size': len(child.data), 'sha256': hashlib.sha256(child.data).hexdigest()}
	return res

def pad(data, size):
	return data + b'\x00' * (-len(data) % size)


#### ISO9660

def both16(value):
	return struct.pack('<H', value) + struct.pack('>H', value)

def both32(value):
	return struct.pack('<I', value) + struct.pack('>I', value)

def iso_time7(dt, tz = 0):
	local = dt + datetime.timedelta(minutes = tz)
	return bytes([local.year - 1900, local.month, local.day, local.hour, local.minute, local.second, (tz // 15) & 0xFF])

def iso_time17(dt, tz = 0):
	local = dt + datetime.timedelta(minutes = tz)
	return local.strftime('%Y%m%d%H%M%S').encode() + b'%02d' % (local.microsecond // 10000) + bytes([(tz // 15) & 0xFF])

def susp(signature, body):
	return signature + bytes([len(body) + 4, 1]) + body


class ISOBuilder:
	def __init__(self, entries, volume = 'ADR_TEST', joliet = True, rock_ridge = True):
		self.root = make_tree(entries)
		self.volume = volume
		self.joliet = joliet
		self.rock_ridge = rock_ridge
		self.moved = Node('rr_moved', self.root)
		self.moved.children = []
		self.moved.iso_name = 'RR_MOVED'
		self.relocated = []
		if rock_ridge is True:
			self.relocated = [node for node in self.all_nodes() if node.is_dir and node.options.get('relocate') is True]
		self.inodes = {}

	def all_nodes(self):
		res = []
		todo = [self.root]
		while len(todo) > 0:
			node = todo.pop()
			res.append(node)
			if node.is_dir:
				todo += node.children
		return res

	def inode(self, node):
		if id(node) not in self.inodes:
			self.inodes[id(node)] = len(self.inodes) + 100
		return self.inodes[id(node)]

	def directories(self, tree):
		"""The directories having a listing in the tree"""
		res = [node for node in self.all_nodes() if node.is_dir]
		if tree == 'iso' and len(self.relocated) > 0:
			res.append(self.moved)
		return res

	def listing(self, tree, directory):
		"""The records of the directory as (node, kind) where kind tells how the node is listed"""
		if directory is self.moved:
			return [(node, 'moved') for node in self.relocated]
		res = []
		for child in directory.children:
			if tree == 'joliet':
				if child.symlink is None:
					res.append((child, 'normal'))
			elif child in self.relocated:
				res.append((child, 'link'))
			else:
				res.append((child, 'normal'))
		if tree == 'iso' and directory is self.root and len(self.relocated) > 0:
			res.append((self.moved, 'normal'))
		if tree == 'joliet':
			return sorted(res, key = lambda item: joliet_name(item[0]).encode('utf-16-be'))
		return sorted(res, key = lambda item: item[0].iso_name)

	def file_extents(self, node, addr):
		"""The parts of the data of the file as (sector, size)"""
		if node.data is None or len(node.data) == 0:
			return [(0, 0)]
		first = addr.get(('file', id(node)), 0)
		count = node.options.get('extents', 1)
		sectors = (len(node.data) + SECTOR - 1) // SECTOR
		per_extent = max(1, sectors // count)
		res = []
		pos = 0
		while pos < sectors:
			size = per_extent if len(res) < count - 1 else sectors - pos
			res.append((first + pos, min(size * SECTOR, len(node.data) - pos * SECTOR)))
			pos += size
		return res

	def rock_ridge_entries(self, node, kind, addr, special = None):
		"""The system use entries of the record of the node"""
		res = []
		if special == 'rootself':
			res.append(susp(b'SP', b'\xbe\xef\x00'))
		if node.is_dir:
			mode = 0o040000 | node.mode
		elif node.symlink is not None:
			mode = 0o120000 | node.mode
		else:
			mode = 0o100000 | node.mode
		res.append(susp(b'PX', both32(mode) + both32(1) + both32(node.options.get('uid', 0)) + both32(node.options.get('gid', 0)) + both32(self.inode(node))))
		if node.options.get('long_tf') is True:
			res.append(susp(b'TF', bytes([0x8E]) + iso_time17(node.mtime, node.tz) * 3))
		else:
			res.append(susp(b'TF', bytes([0x0E]) + iso_time7(node.mtime, node.tz) * 3))
		if special == 'rootself':
			ident, desc, src = b'RRIP_1991A', b'THE ROCK RIDGE INTERCHANGE PROTOCOL PROVIDES SUPPORT FOR POSIX FILE SYSTEM SEMANTICS', b'PLEASE CONTACT DISC PUBLISHER FOR SPECIFICATION SOURCE.'
			res.append(susp(b'ER', bytes([len(ident), len(desc), len(src), 1]) + ident + desc + src))
		if special == 'parent' and node in self.relocated:
			res.append(susp(b'PL', both32(addr.get(('iso', id(node.parent)), (0, 0))[0])))
		if special is not None:
			return res

		name = node.name.encode('utf-8')
		chunks = [name[i:i+100] for i in range(0, len(name), 100)]
		for i, chunk in enumerate(chunks):
			res.append(susp(b'NM', bytes([1 if i < len(chunks) - 1 else 0]) + chunk))
		if node.symlink is not None:
			components = b''
			parts = node.symlink.split('/')
			for i, part in enumerate(parts):
				if part == '' and i == 0:
					components += bytes([8, 0])
				elif part == '..':
					components += bytes([4, 0])
				elif part == '.':
					components += bytes([2, 0])
				else:
					raw = part.encode('utf-8')
					components += bytes([0, len(raw)]) + raw
			res.append(susp(b'SL', bytes([0]) + components))
		if kind == 'link':
			res.append(susp(b'CL', both32(addr.get(('iso', id(node)), (0, 0))[0])))
		if kind == 'moved':
			res.append(susp(b'RE', b''))
		return res

	def record(self, tree, name, extent, size, flags, node, kind, addr, continuations, special = None):
		fixed = 33 + len(name) + (1 if len(name) % 2 == 0 else 0)
		system_use = b''
		if tree == 'iso' and self.rock_ridge is True:
			entries = self.rock_ridge_entries(node, kind, addr, special)
			available = 254 - fixed
			inline = []
			used = 0
			while len(entries) > 0 and used + len(entries[0]) + 28 <= available:
				used += len(entries[0])
				inline.append(entries.pop(0))
			if len(entries) > 0:
				# the rest goes to a continuation area, which can not cross the sectors
				rest = b''.join(entries)
				if len(continuations) % SECTOR + len(rest) > SECTOR:
					continuations += b'\x00' * (-len(continuations) % SECTOR)
				offset = len(continuations)
				continuations += rest
				inline.append(susp(b'CE', both32(addr.get('ce', 0) + offset // SECTOR) + both32(offset % SECTOR) + both32(len(rest))))
			system_use = b''.join(inline)
		length = fixed + len(system_use)
		if length % 2 == 1:
			system_use += b'\x00'
			length += 1
		res = bytes([length, 0]) + both32(extent) + both32(size) + iso_time7(node.mtime, node.tz)
		res += bytes([flags, 0, 0]) + both16(1) + bytes([len(name)]) + name
		if len(name) % 2 == 0:
			res += b'\x00'
		res += system_use
		assert len(res) == length and length <= 255
		return res

	def render_directory(self, tree, directory, addr, continuations):
		own = addr.get((tree, id(directory)), (0, 0))
		parent_node = directory.parent if directory.parent is not None else directory
		parent = addr.get((tree, id(parent_node)), (0, 0))
		records = []
		records.append(self.record(tree, b'\x00', own[0], own[1], 2, directory, 'normal', addr, continuations, 'rootself' if directory is self.root else 'self'))
		records.append(self.record(tree, b'\x01', parent[0], parent[1], 2, directory, 'normal', addr, continuations, 'parent'))
		for node, kind in self.listing(tree, directory):
			name = joliet_name(node).encode('utf-16-be') if tree == 'joliet' else node.iso_name.encode('ascii')
			flags = 1 if node.options.get('hidden') is True else 0
			if node.is_dir and kind != 'link':
				extent, size = addr.get((tree, id(node)), (0, 0))
				records.append(self.record(tree, name, extent, size, flags | 2, node, kind, addr, continuations))
				continue
			if node.is_dir:
				# the place of a relocated directory has a file record with the link
				records.append(self.record(tree, name + b'.;1', 0, 0, flags, node, kind, addr, continuations))
				continue
			extents = self.file_extents(node, addr)
			for i, (extent, size) in enumerate(extents):
				more = 0x80 if i < len(extents) - 1 else 0
				records.append(self.record(tree, name, extent, size, flags | more, node, kind, addr, continuations))

		data = b''
		for record in records:
			if len(data) % SECTOR + len(record) > SECTOR:
				data = pad(data, SECTOR)
			data += record
		return pad(data, SECTOR)

	def render(self, addr):
		continuations = bytearray()
		directories = {}
		for tree in ['iso'] + (['joliet'] if self.joliet is True else []):
			for directory in self.directories(tree):
				directories[(tree, id(directory))] = self.render_directory(tree, directory, addr, continuations)
		return directories, pad(bytes(continuations), SECTOR)

	def volume_descriptor(self, tree, addr, total):
		joliet = tree == 'joliet'
		def text(value, size):
			if joliet is True:
				return value.encode('utf-16-be')[:size & ~1].ljust(size, b'\x00') if value == '' else pad(value.encode('utf-16-be'), 2)[:size].ljust(size, b'\x00')
			return value.encode('ascii')[:size].ljust(size, b' ')
		root = addr[(tree, id(self.root))]
		res = bytes([2 if joliet is True else 1]) + b'CD001' + bytes([1, 0])
		res += text('', 32) + text(self.volume, 32) + b'\x00' * 8
		res += both32(total) + (b'%/E' if joliet is True else b'').ljust(32, b'\x00')
		res += both16(1) + both16(1) + both16(SECTOR)
		res += both32(0) + b'\x00' * 16 # no path tables
		res += bytes([34, 0]) + both32(root[0]) + both32(root[1]) + iso_time7(DEFAULT_TIME) + bytes([2, 0, 0]) + both16(1) + bytes([1, 0])
		res += text('', 128) * 4 + text('', 37) * 3
		res += iso_time17(DEFAULT_TIME) * 2 + b'0' * 16 + b'\x00' + b'0' * 16 + b'\x00'
		res += bytes([1, 0])
		return pad(res, SECTOR)

	def build(self):
		# the sizes do not depend on the addresses, so the first pass tells where everything goes
		directories, continuations = self.render({})
		addr = {}
		pos = 16 + (3 if self.joliet is True else 2)
		for key, data in directories.items():
			addr[key] = (pos, len(data))
			pos += len(data) // SECTOR
		addr['ce'] = pos
		pos += len(continuations) // SECTOR
		files = []
		for node in self.all_nodes():
			if node.data is not None and len(node.data) > 0:
				addr[('file', id(node))] = pos
				files.append(node)
				pos += (len(node.data) + SECTOR - 1) // SECTOR
		total = pos

		directories, continuations = self.render(addr)
		image = bytearray(16 * SECTOR)
		image += self.volume_descriptor('iso', addr, total)
		if self.joliet is True:
			image += self.volume_descriptor('joliet', addr, total)
		image += pad(bytes([255]) + b'CD001' + bytes([1]), SECTOR)
		for key, data in directories.items():
			assert len(image) == addr[key][0] * SECTOR
			image += data
		image += continuations
		for node in files:
			assert len(image) == addr[('file', id(node))] * SECTOR
			image += pad(node.data, SECTOR)
		return bytes(image)

	def manifest(self, view):
		"""view: 'rockridge', 'joliet' or 'iso'"""
		if view == 'joliet':
			return manifest(self.root, 'joliet', False)
		if view == 'rockridge':
			return manifest(self.root, 'real', True)
		return manifest(self.root, 'iso', 'files')


#### UDF

def crc16(data):
	crc = 0
	for byte in data:
		crc ^= byte << 8
		for _ in range(8):
			crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
	return crc

def cs0(text):
	if text == '':
		return b''
	try:
		return b'\x08' + text.encode('latin-1')
	except UnicodeEncodeError:
		return b'\x10' + text.encode('utf-16-be')

def dstring(text, size):
	raw = cs0(text)
	return raw.ljust(size - 1, b'\x00') + bytes([len(raw)])

def regid(ident, suffix = b''):
	return b'\x00' + ident.ljust(23, b'\x00') + suffix.ljust(8, b'\x00')

def udf_time(dt, tz = 0):
	local = dt + datetime.timedelta(minutes = tz)
	micro = local.microsecond
	return struct.pack('<HhBBBBBBBB', 0x1000 | (tz & 0xFFF), local.year, local.month, local.day, local.hour, local.minute, local.second,
		micro // 10000, (micro // 100) % 100, micro % 100)

CHARSPEC = b'\x00' + b'OSTA Compressed Unicode'.ljust(63, b'\x00')
IMPLEMENTATION = regid(b'*adiskreader tests')
META_EXTENT_BLOCKS = 96
PACKET_LENGTH = 32
PARTITION_NUMBER = 3


class UDFBuilder:
	"""
	mode: 'plain', 'vat' (write once disc with virtual partition), 'sparable' (with a replaced packet),
	      'metadata' (metadata partition, the revision must be at least 0x250)
	ad: 'short', 'long' or 'ext', the kind of the allocation descriptors
	efe: use extended file entries
	broken_metadata: the main metadata file is damaged, the mirror must be used
	anchor256: False leaves out the anchor of block 256, only the one in the last block remains
	"""
	def __init__(self, entries, block_size = 2048, revision = 0x0201, mode = 'plain', ad = 'short', efe = False, volume = 'ADR_UDF',
			broken_metadata = False, anchor256 = True, padding = 0):
		self.root = make_tree(entries)
		self.bs = block_size
		self.revision = revision
		self.mode = mode
		self.ad = ad
		self.efe = efe
		self.volume = volume
		self.broken_metadata = broken_metadata
		self.anchor256 = anchor256
		self.padding = padding
		self.dver = 3 if revision >= 0x0200 else 2
		self.image = bytearray()
		self.pstart = 288
		self.pnext = 0
		self.vat = []
		self.meta_next = 0
		self.meta_extents = []
		self.mref = 1 if mode in ('vat', 'metadata') else 0
		self.unique = 16
		self.skipped_by_7z = []

	#### blocks

	def put(self, block, data):
		end = block * self.bs + len(data)
		if len(self.image) < end:
			self.image += b'\x00' * (end - len(self.image))
		self.image[block * self.bs:end] = data

	def alloc_phys(self, count = 1):
		block = self.pnext
		self.pnext += count
		return block

	def write_phys(self, block, data):
		self.put(self.pstart + block, pad(data, self.bs))

	def meta_to_phys(self, block):
		for index, start in enumerate(self.meta_extents):
			if block < (index + 1) * META_EXTENT_BLOCKS:
				return start + block - index * META_EXTENT_BLOCKS
		raise Exception('The metadata file of the builder is full')

	def alloc_meta(self, count = 1):
		"""Blocks for the file entries and such, in the partition of the metadata"""
		if self.mode == 'vat':
			first = len(self.vat)
			for _ in range(count):
				self.vat.append(self.alloc_phys())
			return first
		if self.mode == 'metadata':
			first = self.meta_next
			self.meta_next += count
			self.meta_to_phys(self.meta_next - 1)
			return first
		return self.alloc_phys(count)

	def write_meta(self, block, data):
		data = pad(data, self.bs)
		for i in range(len(data) // self.bs):
			part = data[i * self.bs:(i + 1) * self.bs]
			if self.mode == 'vat':
				self.write_phys(self.vat[block + i], part)
			elif self.mode == 'metadata':
				self.write_phys(self.meta_to_phys(block + i), part)
			else:
				self.write_phys(block + i, part)

	#### descriptors

	def tag(self, ident, body, location):
		head = struct.pack('<HHBBHHHI', ident, self.dver, 0, 0, 1, crc16(body), len(body), location)
		return head[:4] + bytes([sum(head) & 0xFF]) + head[5:] + body

	def long_ad(self, length, block, partition, extent_type = 0):
		return struct.pack('<IIH6x', (extent_type << 30) | length, block, partition)

	def make_ads(self, extents, own_partition, kind = None):
		"""extents: (type, length in bytes, block, partition reference). Returns the kind and the list of the descriptors"""
		if kind is None:
			kind = self.ad
			if kind == 'short' and any(partition != own_partition for _, _, _, partition in extents):
				kind = 'long'
		res = []
		for extent_type, length, block, partition in extents:
			if kind == 'short':
				res.append(struct.pack('<II', (extent_type << 30) | length, block))
			elif kind == 'long':
				res.append(self.long_ad(length, block, partition, extent_type))
			else:
				res.append(struct.pack('<IIIIH2x', (extent_type << 30) | length, length, length, block, partition))
		return kind, res

	def icb_tag(self, file_type, flags, strategy = 4):
		return struct.pack('<IHHHBB', 0, strategy, 0, 1, 0, file_type) + b'\x00' * 6 + struct.pack('<H', flags)

	def file_entry(self, block, file_type, node, size, ad_flags, ad_data, strategy = 4, blocks = 0, efe = None):
		mode = node.mode if node is not None else 0o444
		permissions = (mode & 7) | (((mode >> 3) & 7) << 5) | (((mode >> 6) & 7) << 10)
		mtime = udf_time(node.mtime, node.tz) if node is not None else udf_time(DEFAULT_TIME)
		uid = node.options.get('uid', 0) if node is not None else 0
		gid = node.options.get('gid', 0) if node is not None else 0
		self.unique += 1
		body = self.icb_tag(file_type, ad_flags, strategy)
		body += struct.pack('<IIIHBBI', uid, gid, permissions, 1, 0, 0, 0)
		if (self.efe if efe is None else efe) is True:
			body += struct.pack('<QQQ', size, size, blocks) + mtime * 4 + struct.pack('<II', 1, 0)
			body += b'\x00' * 32 + IMPLEMENTATION + struct.pack('<QII', self.unique, 0, len(ad_data)) + ad_data
			ident = 266
		else:
			body += struct.pack('<QQ', size, blocks) + mtime * 3 + struct.pack('<I', 1)
			body += b'\x00' * 16 + IMPLEMENTATION + struct.pack('<QII', self.unique, 0, len(ad_data)) + ad_data
			ident = 261
		assert len(body) + 16 <= self.bs
		return self.tag(ident, body, block)

	def file_identifier(self, name, icb_block, characteristics, location):
		raw = cs0(name)
		body = struct.pack('<HBB', 1, characteristics, len(raw)) + self.long_ad(self.bs, icb_block, self.mref) + struct.pack('<H', 0) + raw
		body += b'\x00' * (-(len(body) + 16) % 4)
		return self.tag(257, body, location)

	#### files

	def store_data(self, node):
		"""Writes the data of the file, returns the extents"""
		data = node.data
		layout = node.options.get('layout')
		bs = self.bs
		blocks = [data[i:i+bs] for i in range(0, len(data), bs)]
		extents = []
		if layout in ('fragments', 'aed'):
			# pieces of one to three blocks, not next to each other
			pos = 0
			size = 1
			while pos < len(blocks):
				part = b''.join(blocks[pos:pos+size])
				first = self.alloc_phys(size)
				self.write_phys(first, part)
				self.alloc_phys()
				extents.append((0, len(part), first, 0))
				pos += size
				size = 1 if layout == 'aed' else size % 3 + 1
		elif layout == 'sparse':
			# the blocks of zeros are not recorded, by turns allocated or not
			runs = []
			for block in blocks:
				zero = block.strip(b'\x00') == b''
				if len(runs) > 0 and runs[-1][0] == zero:
					runs[-1][1].append(block)
				else:
					runs.append([zero, [block]])
			holes = 0
			for zero, run in runs:
				length = sum(len(block) for block in run)
				if zero is False:
					first = self.alloc_phys(len(run))
					self.write_phys(first, b''.join(run))
					extents.append((0, length, first, 0))
				elif holes % 2 == 0:
					holes += 1
					extents.append((1, length, self.alloc_phys(len(run)), 0))
				else:
					holes += 1
					extents.append((2, length, 0, 0))
		elif len(blocks) > 0:
			first = self.alloc_phys(len(blocks))
			self.write_phys(first, data)
			extents.append((0, len(data), first, 0))
		return extents

	def add_file(self, node):
		layout = node.options.get('layout')
		if node.symlink is not None:
			data = b''
			for i, part in enumerate(node.symlink.split('/')):
				if part == '' and i == 0:
					data += struct.pack('<BBH', 2, 0, 0)
				elif part == '..':
					data += struct.pack('<BBH', 3, 0, 0)
				elif part == '.':
					data += struct.pack('<BBH', 4, 0, 0)
				else:
					data += struct.pack('<BBH', 5, len(cs0(part)), 0) + cs0(part)
			block = self.alloc_meta()
			self.write_meta(block, self.file_entry(block, 12, node, len(data), 3, data))
			return block
		if layout == 'embedded':
			block = self.alloc_meta()
			self.write_meta(block, self.file_entry(block, 5, node, len(node.data), 3, node.data))
			return block
		if layout == 'strategy4096':
			# an old version of the file, followed by the address of the new one, which has nothing after it
			self.skipped_by_7z.append(node)
			old = self.alloc_meta(2)
			new = self.alloc_meta(2)
			self.write_meta(old, self.file_entry(old, 5, node, 3, 3, b'old', strategy = 4096))
			self.write_meta(old + 1, self.tag(259, self.icb_tag(3, 0, 4096) + self.long_ad(self.bs, new, self.mref), old + 1))
			self.write_meta(new, self.file_entry(new, 5, node, len(node.data), 3, node.data, strategy = 4096))
			return old

		extents = self.store_data(node)
		recorded = sum((length + self.bs - 1) // self.bs for extent_type, length, _, _ in extents if extent_type == 0)
		kind, ads = self.make_ads(extents, self.mref)
		# the descriptors which do not fit in the file entry go to allocation extents: each place has
		# some of them and the address of the next place
		per_place = (self.bs - 256) // len(ads[0]) - 1 if len(ads) > 0 else 1
		if layout == 'aed':
			per_place = 2
		if len(ads) > per_place:
			groups = [ads[i:i+per_place] for i in range(0, len(ads), per_place)]
			following = None
			for group in reversed(groups[1:]):
				area = self.alloc_phys()
				if following is not None:
					group = group + [self.make_ads([(3, self.bs, following, 0)], 0, kind)[1][0]]
				body = b''.join(group)
				self.write_phys(area, self.tag(258, struct.pack('<II', 0, len(body)) + body, area))
				following = area
			ads = groups[0] + [self.make_ads([(3, self.bs, following, 0)], 0, kind)[1][0]]
		block = self.alloc_meta()
		flags = {'short': 0, 'long': 1, 'ext': 2}[kind]
		self.write_meta(block, self.file_entry(block, 5, node, len(node.data), flags, b''.join(ads), blocks = recorded))
		return block

	def add_directory(self, node, parent_block = None):
		block = self.alloc_meta()
		entries = [('', parent_block if parent_block is not None else block, 0x0A)]
		for child in node.sorted_children():
			if child.is_dir:
				entries.append((child.name, self.add_directory(child, block), 0x02 | (0x01 if child.options.get('hidden') is True else 0)))
			else:
				entries.append((child.name, self.add_file(child), 0x01 if child.options.get('hidden') is True else 0))
		if node is self.root:
			# a deleted file, its entry is left in the directory
			entries.append(('deleted.txt', 0, 0x04))

		def render(first_block):
			data = b''
			for name, icb, characteristics in entries:
				data += self.file_identifier(name, icb, characteristics, first_block + len(data) // self.bs)
			return data

		data = render(block)
		if node.options.get('layout') == 'embedded' and len(data) + 220 <= self.bs:
			self.write_meta(block, self.file_entry(block, 4, node, len(data), 3, data))
			return block
		count = (len(data) + self.bs - 1) // self.bs
		if self.mode == 'vat':
			# the virtual partition only has the file entries
			first = self.alloc_phys(count)
			self.write_phys(first, render(first))
			extents = [(0, len(data), first, 0)]
		else:
			first = self.alloc_meta(count)
			self.write_meta(first, render(first))
			extents = [(0, len(data), first, self.mref)]
		kind, ads = self.make_ads(extents, self.mref)
		self.write_meta(block, self.file_entry(block, 4, node, len(data), {'short': 0, 'long': 1, 'ext': 2}[kind], b''.join(ads), blocks = count))
		return block

	#### volume

	def partition_maps(self, metadata_file = 0, metadata_mirror = 0):
		maps = [struct.pack('<BBHH', 1, 6, 1, PARTITION_NUMBER)]
		suffix = struct.pack('<H', self.revision)
		if self.mode == 'sparable':
			maps = [struct.pack('<BB2x', 2, 64) + regid(b'*UDF Sparable Partition', suffix) + struct.pack('<HH', 1, PARTITION_NUMBER)
				+ struct.pack('<HBxIII', PACKET_LENGTH, 2, self.bs, 60, 61) + b'\x00' * 8]
		elif self.mode == 'vat':
			maps.append(struct.pack('<BB2x', 2, 64) + regid(b'*UDF Virtual Partition', suffix) + struct.pack('<HH', 1, PARTITION_NUMBER) + b'\x00' * 24)
		elif self.mode == 'metadata':
			maps.append(struct.pack('<BB2x', 2, 64) + regid(b'*UDF Metadata Partition', suffix) + struct.pack('<HH', 1, PARTITION_NUMBER)
				+ struct.pack('<IIIIHB', metadata_file, metadata_mirror, 0xFFFFFFFF, 32, 32, 0) + b'\x00' * 5)
		assert all(len(pmap) in (6, 64) for pmap in maps)
		return maps

	def volume_descriptors(self, first, fileset_block, maps):
		suffix = struct.pack('<H', self.revision)
		res = []
		body = struct.pack('<II', 1, 0) + dstring(self.volume, 32) + struct.pack('<HHHHII', 1, 1, 2, 2, 1, 1)
		body += dstring('adiskreader test volume set', 128) + CHARSPEC + CHARSPEC + b'\x00' * 16
		body += regid(b'') + udf_time(DEFAULT_TIME) + IMPLEMENTATION + b'\x00' * 64 + struct.pack('<IH', 0, 0) + b'\x00' * 22
		res.append((1, body))
		body = struct.pack('<IHH', 2, 1, PARTITION_NUMBER) + regid(b'+NSR03' if self.dver == 3 else b'+NSR02') + b'\x00' * 128
		body += struct.pack('<III', 1, self.pstart, self.pnext) + IMPLEMENTATION + b'\x00' * 128 + b'\x00' * 156
		res.append((5, body))
		table = b''.join(maps)
		body = struct.pack('<I', 3) + CHARSPEC + dstring(self.volume, 128) + struct.pack('<I', self.bs)
		body += regid(b'*OSTA UDF Compliant', suffix) + self.long_ad(self.bs, fileset_block, self.mref)
		body += struct.pack('<II', len(table), len(maps)) + IMPLEMENTATION + b'\x00' * 128 + b'\x00' * 8 + table
		res.append((6, body))
		res.append((8, b'\x00' * 496))
		for index, (ident, body) in enumerate(res):
			self.put(first + index, pad(self.tag(ident, body, first + index), self.bs))

	def build(self):
		bs = self.bs
		metadata_file = 0
		metadata_mirror = 0
		if self.mode == 'metadata':
			# the metadata file is made of two extents with something between them
			metadata_file = self.alloc_phys()
			self.meta_extents.append(self.alloc_phys(META_EXTENT_BLOCKS))
			self.write_phys(self.alloc_phys(), b'\xEE' * bs)
			self.meta_extents.append(self.alloc_phys(META_EXTENT_BLOCKS))

		fileset = self.alloc_meta()
		root = self.add_directory(self.root)
		body = udf_time(DEFAULT_TIME) + struct.pack('<HHIIII', 3, 3, 1, 1, 0, 0) + CHARSPEC + dstring(self.volume, 128)
		body += CHARSPEC + dstring('adiskreader file set', 32) + dstring('', 32) + dstring('', 32)
		body += self.long_ad(bs, root, self.mref) + regid(b'*OSTA UDF Compliant', struct.pack('<H', self.revision)) + b'\x00' * 16 + b'\x00' * 16 + b'\x00' * 32
		self.write_meta(fileset, self.tag(256, body, fileset))

		if self.mode == 'metadata':
			# both the file and its mirror have their own copy of the metadata
			extents = [(0, META_EXTENT_BLOCKS * bs, start, 0) for start in self.meta_extents]
			ads = b''.join(struct.pack('<II', length, block) for _, length, block, _ in extents)
			entry = self.file_entry(metadata_file, 250, None, 2 * META_EXTENT_BLOCKS * bs, 0, ads, blocks = 2 * META_EXTENT_BLOCKS, efe = True)
			self.write_phys(metadata_file, b'\xBB' * bs if self.broken_metadata is True else entry)
			mirror_extents = []
			for start in self.meta_extents:
				mirror = self.alloc_phys(META_EXTENT_BLOCKS)
				source = (self.pstart + start) * bs
				self.write_phys(mirror, pad(bytes(self.image[source:source + META_EXTENT_BLOCKS * bs]), META_EXTENT_BLOCKS * bs))
				mirror_extents.append(mirror)
				if self.broken_metadata is True:
					self.write_phys(start, b'\xBB' * (META_EXTENT_BLOCKS * bs))
			metadata_mirror = self.alloc_phys()
			ads = b''.join(struct.pack('<II', META_EXTENT_BLOCKS * bs, block) for block in mirror_extents)
			self.write_phys(metadata_mirror, self.file_entry(metadata_mirror, 251, None, 2 * META_EXTENT_BLOCKS * bs, 0, ads, blocks = 2 * META_EXTENT_BLOCKS, efe = True))

		if self.mode == 'sparable':
			# the second packet of the partition is replaced by a spare one
			while self.pnext < 3 * PACKET_LENGTH:
				self.alloc_phys()
			source = (self.pstart + PACKET_LENGTH) * bs
			packet = bytes(self.image[source:source + PACKET_LENGTH * bs])
			self.put(96, pad(packet, PACKET_LENGTH * bs))
			self.put(self.pstart + PACKET_LENGTH, b'\xDE\xAD' * (PACKET_LENGTH * bs // 2))
			body = regid(b'*UDF Sparing Table', struct.pack('<H', self.revision)) + struct.pack('<HHI', 2, 0, 1)
			body += struct.pack('<II', PACKET_LENGTH, 96) + struct.pack('<II', 0xFFFFFFFF, 128)
			for location in (60, 61):
				self.put(location, pad(self.tag(0, body, location), bs))

		if self.mode == 'vat':
			# the table and its file entry are the last things written to the disc
			if self.revision >= 0x0200:
				content = struct.pack('<HH', 152, 0) + dstring(self.volume, 128) + struct.pack('<IIIHHHH', 0xFFFFFFFF, 0, 0, self.revision, self.revision, self.revision, 0)
				content += b''.join(struct.pack('<I', block) for block in self.vat)
				file_type = 248
			else:
				content = b''.join(struct.pack('<I', block) for block in self.vat)
				content += regid(b'*UDF Virtual Alloc Tbl', struct.pack('<H', self.revision)) + struct.pack('<I', 0xFFFFFFFF)
				file_type = 0
			count = (len(content) + bs - 1) // bs
			first = self.alloc_phys(count)
			self.write_phys(first, content)
			block = self.alloc_phys()
			self.write_phys(block, self.file_entry(block, file_type, None, len(content), 0, struct.pack('<II', len(content), first), blocks = count))
			self.pnext += self.padding
			self.put(self.pstart + self.pnext - 1, self.image[(self.pstart + self.pnext - 1) * bs:(self.pstart + self.pnext) * bs].ljust(bs, b'\x00'))

		# volume recognition sequence, it is at the same place for every block size
		step = max(bs, 2048)
		for index, ident in enumerate((b'BEA01', b'NSR03' if self.dver == 3 else b'NSR02', b'TEA01')):
			offset = 32768 + index * step
			if len(self.image) < offset + 2048:
				self.image += b'\x00' * (offset + 2048 - len(self.image))
			self.image[offset:offset + 7] = b'\x00' + ident + b'\x01'

		maps = self.partition_maps(metadata_file, metadata_mirror)
		self.volume_descriptors(32, fileset, maps)
		self.volume_descriptors(48, fileset, maps)
		anchor = struct.pack('<IIII', 16 * bs, 32, 16 * bs, 48)
		if self.anchor256 is True:
			self.put(256, pad(self.tag(2, anchor, 256), bs))
		if self.mode != 'vat':
			last = self.pstart + self.pnext
			self.put(last, pad(self.tag(2, anchor, last), bs))
		return bytes(self.image)

	def manifest(self):
		return manifest(self.root, 'real', True)


def sample_entries():
	"""The tree used by the tests"""
	import random
	rnd = random.Random(1234)
	def blob(size):
		return bytes(rnd.getrandbits(8) for _ in range(size))
	sparse = blob(3000) + b'\x00' * 9192 + blob(5000) + b'\x00' * 20000 + blob(100)
	entries = [
		{'path': 'README.TXT', 'data': b'adiskreader optical test image\n'},
		{'path': 'empty.txt', 'data': b''},
		{'path': 'small file with a long name and spaces.txt', 'data': b'x' * 100, 'layout': 'embedded', 'mode': 0o600, 'uid': 1000, 'gid': 100},
		{'path': 'big.bin', 'data': blob(300000), 'extents': 3, 'layout': 'fragments'},
		{'path': 'sparse.bin', 'data': sparse, 'layout': 'sparse'},
		{'path': 'many extents.bin', 'data': blob(40000), 'layout': 'aed', 'long_tf': True},
		{'path': 'rewritten.txt', 'data': b'the new version of the file\n', 'layout': 'strategy4096'},
		{'path': 'hidden.dat', 'data': blob(5000), 'hidden': True},
		{'path': 'script.sh', 'data': b'#!/bin/sh\necho hello\n', 'mode': 0o755,
			'mtime': datetime.datetime(2001, 2, 3, 4, 5, 6, tzinfo = datetime.timezone.utc), 'tz': 120},
		{'path': 'árvíztűrő tükörfúrógép.txt', 'data': 'unicode name\n'.encode()},
		{'path': 'n' * 180 + '.txt', 'data': b'long name\n'},
		{'path': 'link to readme', 'symlink': 'README.TXT'},
		{'path': 'DOCS/absolute link', 'symlink': '/usr/share/doc/../doc/./file'},
		{'path': 'DOCS/NOTES.TXT', 'data': b'notes\n' * 1000},
		{'path': 'DOCS', 'layout': 'embedded'},
		{'path': 'empty dir'},
		{'path': 'a/b/c/d/e/f/g/h/deep.txt', 'data': b'deep file\n'},
		{'path': 'a/b/c/d/e/f/g/h', 'relocate': True},
		{'path': 'a/b/c/d/e/f/g/h/i/deeper.txt', 'data': blob(2500)},
	]
	for i in range(70):
		entries.append({'path': 'many/file number %03d ő.txt' % i, 'data': b'file %d\n' % i * (i + 1)})
	return entries


if __name__ == '__main__':
	if len(sys.argv) < 3 or sys.argv[1] not in ('iso', 'udf'):
		print(__doc__)
		sys.exit(1)
	if sys.argv[1] == 'iso':
		image = ISOBuilder(sample_entries()).build()
	else:
		mode = sys.argv[3] if len(sys.argv) > 3 else 'plain'
		revision = int(sys.argv[4], 16) if len(sys.argv) > 4 else (0x0250 if mode == 'metadata' else 0x0201)
		block_size = int(sys.argv[5]) if len(sys.argv) > 5 else 2048
		image = UDFBuilder(sample_entries(), block_size, revision, mode).build()
	with open(sys.argv[2], 'wb') as f:
		f.write(image)
