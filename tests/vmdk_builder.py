"""
Builds the VMDK files which can not be created with qemu-img: disks of multiple extents, the delta extents
of ESXi snapshots (VMFSSPARSE/COWD and SESPARSE) and stream optimized extents with a footer.
qemu-img can read all of them, test_vmdk.py compares against it when it is installed.
"""

from config import *

SECTOR = 512

def build_descriptor(extents, create_type, parent = None):
	"""extents: list of (size in sectors, type, filename, offset), filename and offset might be None"""
	lines = ['# Disk DescriptorFile', 'version=1', 'CID=12345678']
	lines.append('parentCID=%s' % ('ffffffff' if parent is None else '12345678'))
	lines.append('createType="%s"' % create_type)
	if parent is not None:
		lines.append('parentFileNameHint="%s"' % parent)
	lines += ['', '# Extent description']
	for size, etype, filename, offset in extents:
		line = 'RW %d %s' % (size, etype)
		if filename is not None:
			line += ' "%s"' % filename
		if offset is not None:
			line += ' %d' % offset
		lines.append(line)
	lines += ['', '# The Disk Data Base', '#DDB', '', 'ddb.adapterType = "lsilogic"']
	return ('\n'.join(lines) + '\n').encode()

def pad(data, size = SECTOR):
	return data + b'\x00' * (-len(data) % size)

def build_cowd(capacity, grains, grain_sectors = 1):
	"""
	VMFSSPARSE extent
	capacity: in sectors
	grains: {grain index: data of the grain}
	"""
	gt_entries = 4096
	gd_entries = -(-capacity // (grain_sectors * gt_entries))
	gd_offset = 4 # in sectors
	next_free = gd_offset + -(-gd_entries * 4 // SECTOR)

	gd = [0] * gd_entries
	tables = {} # gd index -> entries
	body = b''
	for gd_index in sorted(set(grain_index // gt_entries for grain_index in grains)):
		gd[gd_index] = next_free
		tables[gd_index] = [0] * gt_entries
		next_free += gt_entries * 4 // SECTOR
	for grain_index in sorted(grains):
		assert len(grains[grain_index]) == grain_sectors * SECTOR
		tables[grain_index // gt_entries][grain_index % gt_entries] = next_free
		body += grains[grain_index]
		next_free += grain_sectors

	header = b'COWD'
	for value in [1, 3, capacity, grain_sectors, gd_offset, gd_entries, next_free]:
		header += value.to_bytes(4, 'little')
	data = pad(header, gd_offset * SECTOR)
	data += pad(b''.join(entry.to_bytes(4, 'little') for entry in gd))
	for gd_index in sorted(tables):
		data += b''.join(entry.to_bytes(4, 'little') for entry in tables[gd_index])
	return data + body

def build_sesparse(capacity, grains):
	"""
	SESPARSE extent
	capacity: in sectors
	grains: {grain index: data of the grain (4096 bytes), 'zero' or 'unmap'}
	"""
	grain_sectors = 8
	gt_sectors = 64
	gt_entries = gt_sectors * SECTOR // 8
	gd_entries = -(-capacity // (grain_sectors * gt_entries))
	gd_offset = 4096
	gd_sectors = -(-gd_entries * 8 // SECTOR)
	gt_offset = gd_offset + gd_sectors

	gd = [0] * gd_entries
	tables = []
	table_of = {}
	for gd_index in sorted(set(grain_index // gt_entries for grain_index in grains)):
		gd[gd_index] = 0x1000000000000000 | len(tables)
		table_of[gd_index] = len(tables)
		tables.append([0] * gt_entries)
	body = b''
	stored = 0
	for grain_index in sorted(grains):
		value = grains[grain_index]
		if value == 'unmap':
			entry = 0x1000000000000000
		elif value == 'zero':
			entry = 0x2000000000000000
		else:
			assert len(value) == grain_sectors * SECTOR
			entry = 0x3000000000000000 | ((stored & 0xfff) << 48) | (stored >> 12)
			body += value
			stored += 1
		tables[table_of[grain_index // gt_entries]][grain_index % gt_entries] = entry

	gt_total = len(tables) * gt_sectors
	free_bitmap_offset = gt_offset + gt_total
	free_bitmap_size = 8
	backmap_offset = free_bitmap_offset + free_bitmap_size
	backmap_size = 8
	grains_offset = backmap_offset + backmap_size
	grains_size = stored * grain_sectors

	fields = [
		0xcafebabe, 0x0000000200000001, capacity, grain_sectors, gt_sectors, 0,
		0, 0, 0, 0,
		1, 1, # volatile header
		2, 2, # journal header
		2048, 2048, # journal
		gd_offset, gd_sectors,
		gt_offset, gt_total,
		free_bitmap_offset, free_bitmap_size,
		backmap_offset, backmap_size,
		grains_offset, grains_size,
	]
	data = pad(b''.join(value.to_bytes(8, 'little') for value in fields))
	# volatile header: magic, free grain tables, next transaction, replay journal
	data += pad(b''.join(value.to_bytes(8, 'little') for value in [0xcafecafe, 0, 0, 0]))
	data = pad(data, gd_offset * SECTOR)
	data += pad(b''.join(entry.to_bytes(8, 'little') for entry in gd))
	for table in tables:
		data += b''.join(entry.to_bytes(8, 'little') for entry in table)
	data += b'\x00' * ((free_bitmap_size + backmap_size) * SECTOR)
	return data + body

def add_stream_footer(data):
	"""
	Moves the grain directory offset of a stream optimized extent to a footer, like the disks of OVA files have it.
	The extent then ends with: footer marker, footer (copy of the header with the offset), end-of-stream marker.
	"""
	assert data[:4] == b'KDMV'
	header = data[:SECTOR]
	data = header[:56] + b'\xff' * 8 + data[64:]
	data = pad(data)
	footer_marker = (1).to_bytes(8, 'little') + (0).to_bytes(4, 'little') + (3).to_bytes(4, 'little')
	return data + pad(footer_marker) + header + b'\x00' * SECTOR

def patch(data, writes):
	data = bytearray(data)
	for offset, length, value in writes:
		data[offset:offset+length] = bytes([value]) * length
	return bytes(data)

def grains_of(expected, grain_size, grain_indexes):
	return {grain_index: expected[grain_index*grain_size:(grain_index+1)*grain_size] for grain_index in grain_indexes}

_test_disks = None
def build_test_disks():
	"""
	Creates the VMDK test disks in the testfiles folder, all based on the raw ntfs image.
	Returns {name: (file name of the disk, expected data of the disk, readable by qemu)}
	"""
	global _test_disks
	if _test_disks is not None:
		return _test_disks

	rawpath = get_image(NTFS_TEST_FILE_GZ)
	sparsepath = get_image(NTFS_TEST_FILE_VMDK_SPARSE_GZ)
	streampath = get_image(NTFS_TEST_FILE_VMDK_STREAM_GZ)
	get_image(NTFS_TEST_FILE_VMDK_SNAPSHOT_GZ)
	get_image(NTFS_TEST_FILE_VMDK_SNAPSHOT2_GZ)
	with open(rawpath, 'rb') as f:
		raw = f.read()
	capacity = len(raw) // SECTOR
	disks = {}

	def write(name, data):
		with open(TESTFILES_FOLDER_PATH.joinpath(name), 'wb') as f:
			f.write(data)

	def add(name, expected, descriptor = None, qemu = True):
		if descriptor is not None:
			write(name, descriptor)
		disks[name[:-len('.vmdk')].replace('built_', '').replace('ntfs_test_', '')] = (name, expected, qemu)

	# made by qemu-img
	add(sparsepath.name, raw)
	add(streampath.name, raw)
	snapshot = patch(raw, VMDK_SNAPSHOT_WRITES)
	add('ntfs_test_snapshot.vmdk', snapshot)
	add('ntfs_test_snapshot2.vmdk', patch(snapshot, VMDK_SNAPSHOT2_WRITES))

	# made by VMware Workstation
	add(get_image(NTFS_TEST_FILE_VMDK_VMWARE_GZ).name, raw)
	add(get_image(NTFS_TEST_FILE_VMDK_VMWARE_SNAPSHOT_GZ).name, snapshot)
	add(get_image(NTFS_TEST_FILE_VMDK_VMWARE_OVA_GZ).name, raw)

	# descriptor file with the extents in other files
	add('built_flat.vmdk', raw, build_descriptor([(capacity, 'FLAT', rawpath.name, 0)], 'monolithicFlat'))
	add('built_vmfs.vmdk', raw, build_descriptor([(capacity, 'VMFS', rawpath.name, None)], 'vmfs'))
	add('built_split_sparse.vmdk', raw, build_descriptor([(capacity, 'SPARSE', sparsepath.name, None)], 'twoGbMaxExtentSparse'))
	add('built_multi.vmdk',
		raw[:30000*SECTOR] + raw[40000*SECTOR:] + raw,
		build_descriptor([
			(30000, 'FLAT', rawpath.name, 0),
			(capacity - 40000, 'FLAT', rawpath.name, 40000),
			(capacity, 'SPARSE', sparsepath.name, None),
		], 'twoGbMaxExtentFlat')
	)
	# qemu skips the ZERO extents
	add('built_multi_zero.vmdk',
		raw[:30000*SECTOR] + b'\x00' * (1000*SECTOR) + raw[40000*SECTOR:] + raw[:5000*SECTOR],
		build_descriptor([
			(30000, 'FLAT', rawpath.name, 0),
			(1000, 'ZERO', None, None),
			(capacity - 40000, 'FLAT', rawpath.name, 40000),
			(5000, 'SPARSE', sparsepath.name, None),
		], 'custom'),
		qemu = False
	)

	# stream optimized with the footer
	with open(streampath, 'rb') as f:
		write('built_stream_footer.vmdk', add_stream_footer(f.read()))
	add('built_stream_footer.vmdk', raw)

	# ESXi snapshot with 1 sector grains, on the flat disk
	cowd = patch(raw, [(5*SECTOR, 2*SECTOR, 0x01), ((4096*3+1)*SECTOR, SECTOR, 0x03), ((capacity-1)*SECTOR, SECTOR, 0x04)])
	write('built_cowd-delta.vmdk', build_cowd(capacity, grains_of(cowd, SECTOR, [5, 6, 4096*3+1, capacity-1]), 1))
	add('built_cowd.vmdk', cowd, build_descriptor([(capacity, 'VMFSSPARSE', 'built_cowd-delta.vmdk', None)], 'vmfsSparse', parent = 'built_flat.vmdk'))

	# snapshot of the snapshot with 64k grains, the parent is given with its full path on the datastore
	cowd2 = patch(cowd, [(0x100, 0x1ff00, 0x05), (700*0x10000 + 0x8000, 0x200, 0x07)])
	write('built_cowd2-delta.vmdk', build_cowd(capacity, grains_of(cowd2, 0x10000, [0, 1, 700]), 128))
	add('built_cowd2.vmdk', cowd2, build_descriptor([(capacity, 'VMFSSPARSE', 'built_cowd2-delta.vmdk', None)], 'vmfsSparse', parent = '/vmfs/volumes/datastore1/vm/built_cowd.vmdk'), qemu = False)

	# ESXi snapshot in the SESPARSE format, on the flat disk
	last = capacity // 8 - 1
	sesparse = patch(raw, [(3*4096 + 0x10, 0x100, 0x08), (4*4096, 2*4096, 0x00), ((4096+7)*4096, 2*4096, 0x09), (last*4096, 4096, 0x0b)])
	grains = grains_of(sesparse, 4096, [3, 4096+7, 4096+8, last])
	grains[4] = 'zero'
	grains[5] = 'unmap'
	write('built_sesparse-sesparse.vmdk', build_sesparse(capacity, grains))
	add('built_sesparse.vmdk', sesparse, build_descriptor([(capacity, 'SESPARSE', 'built_sesparse-sesparse.vmdk', None)], 'seSparse', parent = 'built_flat.vmdk'))

	_test_disks = disks
	return disks
