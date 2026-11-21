"""
Builds compressed WIM files. 7z can only create WIM files without compression, this recompresses such a file
with XPRESS or LZX. The compressors here are made to use every feature of the formats (all block types of LZX,
recent offsets, long matches, chunks stored without compression), not to compress well.
7z can read the result, test_wim.py compares against it when it is installed.

Usage: python wim_builder.py <uncompressed wim> <xpress|lzx> <output wim>
       python wim_builder.py source <directory>      writes the files testfiles/wim_test.wim is made of
"""

import hashlib
import heapq
import os
import random
import struct
import sys

WIM_HEADER_SIZE = 208
LOOKUP_ENTRY_SIZE = 50
CHUNK_SIZE = 32768
RESHDR_METADATA = 0x02
RESHDR_COMPRESSED = 0x04
HDR_COMPRESSION = 0x2
HDR_COMPRESS_XPRESS = 0x20000
HDR_COMPRESS_LZX = 0x40000

LZX_MAGIC_FILESIZE = 12000000
LZX_MAX_MATCH = 257
LZX_NUM_OFFSET_SLOTS = 30 # for the 32K window
LZX_EXTRA_OFFSET_BITS = [min(17, max(0, slot // 2 - 1)) for slot in range(LZX_NUM_OFFSET_SLOTS)]
LZX_OFFSET_SLOT_BASE = [0]
for _bits in LZX_EXTRA_OFFSET_BITS:
	LZX_OFFSET_SLOT_BASE.append(LZX_OFFSET_SLOT_BASE[-1] + (1 << _bits))


def huffman_lens(freqs, max_len):
	"""Code lengths of the symbols, 0 for the ones not used. At least two symbols get a code."""
	freqs = list(freqs)
	used = [sym for sym, freq in enumerate(freqs) if freq > 0]
	for sym in range(len(freqs)):
		if len(used) >= 2:
			break
		if freqs[sym] == 0:
			freqs[sym] = 1
			used.append(sym)
	while True:
		lens = [0] * len(freqs)
		heap = [(freqs[sym], sym, [sym]) for sym in used]
		heapq.heapify(heap)
		while len(heap) > 1:
			freq1, sym1, syms1 = heapq.heappop(heap)
			freq2, sym2, syms2 = heapq.heappop(heap)
			for sym in syms1 + syms2:
				lens[sym] += 1
			heapq.heappush(heap, (freq1 + freq2, min(sym1, sym2), syms1 + syms2))
		if max(lens) <= max_len:
			return lens
		# flatten the frequencies until the code fits
		for sym in used:
			freqs[sym] = freqs[sym] // 2 + 1

def huffman_codes(lens):
	"""Canonical codes: {symbol: (code, length)}"""
	codes = {}
	code = 0
	prev_len = 0
	for length, sym in sorted((length, sym) for sym, length in enumerate(lens) if length > 0):
		code <<= length - prev_len
		codes[sym] = (code, length)
		code += 1
		prev_len = length
	return codes

def find_matches(data, max_match, max_offset, rng, with_recent = False):
	"""
	Splits the data to literals (int) and matches (offset, length). With with_recent the matches are
	(offset, length, slot) where slot is the position of the offset in the list of the last 3 offsets, or None.
	"""
	tokens = []
	last = {}
	recent = [1, 1, 1]
	pos = 0
	size = len(data)
	while pos < size:
		best_len = 0
		best_offset = 0
		candidates = []
		if with_recent is True:
			candidates += recent
		prev = last.get(data[pos:pos+3])
		if prev is not None:
			candidates.append(pos - prev)
		for offset in candidates:
			if offset > pos or offset > max_offset:
				continue
			length = 0
			limit = min(max_match, size - pos)
			while length < limit and data[pos + length - offset] == data[pos + length]:
				length += 1
			if length > best_len:
				best_len = length
				best_offset = offset
		# some of the matches are made shorter or skipped to have all kinds of them
		if best_len >= 3 and rng.random() < 0.1:
			best_len = rng.randint(2, best_len)
		if best_len < 3:
			tokens.append(data[pos])
			best_len = 1
		elif with_recent is True:
			slot = recent.index(best_offset) if best_offset in recent else None
			if slot is None:
				recent = [best_offset, recent[0], recent[1]]
			else:
				recent[0], recent[slot] = recent[slot], recent[0]
			tokens.append((best_offset, best_len, slot))
		else:
			tokens.append((best_offset, best_len))
		for i in range(pos, min(pos + best_len, pos + 64)):
			last[data[i:i+3]] = i
		pos += best_len
	return tokens


def xpress_compress(data, rng):
	tokens = find_matches(data, 65538, 65535, rng)
	# symbols with the extra bits and bytes following them
	items = []
	freqs = [0] * 512
	for token in tokens:
		if isinstance(token, int):
			items.append((token, 0, 0, b''))
			freqs[token] += 1
			continue
		offset, length = token
		length -= 3
		offset_bits = offset.bit_length() - 1
		sym = 256 + offset_bits * 16 + min(length, 15)
		extra = b''
		if length >= 15:
			if length - 15 < 255:
				extra = bytes([length - 15])
			else:
				extra = b'\xff' + struct.pack('<H', length)
		items.append((sym, offset - (1 << offset_bits), offset_bits, extra))
		freqs[sym] += 1
	# end of data
	items.append((256, 0, 0, b''))
	freqs[256] += 1
	lens = huffman_lens(freqs, 15)
	codes = huffman_codes(lens)

	out = bytearray(lens[i] | (lens[i+1] << 4) for i in range(0, 512, 2))
	# the bits are stored in 16 bit words, the extra bytes of the lengths are between the words
	bits = []
	for sym, offset_value, offset_bits, extra in items:
		code, length = codes[sym]
		bits += [(code >> i) & 1 for i in range(length - 1, -1, -1)]
		bits += [(offset_value >> i) & 1 for i in range(offset_bits - 1, -1, -1)]
	bits += [0] * 48
	bitpos = 0
	available = 0
	def next_word():
		nonlocal bitpos, available
		value = 0
		for bit in bits[bitpos:bitpos+16]:
			value = (value << 1) | bit
		bitpos += 16
		available += 16
		return struct.pack('<H', value)
	# the decoder of MS-XCA starts with two words and reads the next one when it has less than 16 bits left
	out += next_word()
	out += next_word()
	for sym, offset_value, offset_bits, extra in items:
		available -= codes[sym][1]
		if available < 16:
			out += next_word()
		if sym > 256:
			out += extra
			available -= offset_bits
			if available < 16:
				out += next_word()
	return bytes(out)


class LZXWriter:
	def __init__(self):
		self.out = bytearray()
		self.buf = 0
		self.count = 0

	def bits(self, value, count):
		for i in range(count - 1, -1, -1):
			self.buf = (self.buf << 1) | ((value >> i) & 1)
			self.count += 1
			if self.count == 16:
				self.out += struct.pack('<H', self.buf)
				self.buf = 0
				self.count = 0

	def align(self):
		"""Pads to the next word, a whole word of padding is written when the output is already aligned"""
		self.bits(0, 16 - self.count)

	def flush(self):
		if self.count > 0:
			self.bits(0, 16 - self.count)

def lzx_do_e8(data):
	"""The targets of the x86 call instructions are made absolute"""
	data = bytearray(data)
	size = len(data)
	pos = 0
	while pos < size - 10:
		if data[pos] != 0xE8:
			pos += 1
			continue
		rel = struct.unpack_from('<i', data, pos + 1)[0]
		if rel >= -pos and rel < LZX_MAGIC_FILESIZE:
			if rel < LZX_MAGIC_FILESIZE - pos:
				struct.pack_into('<i', data, pos + 1, rel + pos)
			else:
				struct.pack_into('<i', data, pos + 1, rel - LZX_MAGIC_FILESIZE)
		pos += 5
	return bytes(data)

def lzx_write_lens(writer, lens, prev_lens, rng):
	"""Writes the code lengths as their difference to the lengths of the previous block"""
	# symbols of the precode: 0-16 difference, 17 and 18 runs of zeroes, 19 run of the same length
	items = []
	i = 0
	while i < len(lens):
		run = 1
		while i + run < len(lens) and lens[i + run] == lens[i]:
			run += 1
		if lens[i] == 0 and run >= 20 and rng.random() < 0.8:
			run = min(run, 51)
			items.append((18, run - 20, 5, None))
		elif lens[i] == 0 and run >= 4 and rng.random() < 0.8:
			run = min(run, 19)
			items.append((17, run - 4, 4, None))
		elif run >= 4 and rng.random() < 0.8:
			run = min(run, 5)
			items.append((19, run - 4, 1, (prev_lens[i] - lens[i]) % 17))
		else:
			run = 1
			items.append(((prev_lens[i] - lens[i]) % 17, 0, 0, None))
		i += run
	freqs = [0] * 20
	for sym, _, _, sym2 in items:
		freqs[sym] += 1
		if sym2 is not None:
			freqs[sym2] += 1
	prelens = huffman_lens(freqs, 15)
	codes = huffman_codes(prelens)
	for length in prelens:
		writer.bits(length, 4)
	for sym, extra, extra_bits, sym2 in items:
		writer.bits(*codes[sym])
		writer.bits(extra, extra_bits)
		if sym2 is not None:
			writer.bits(*codes[sym2])

def lzx_write_block(writer, tokens, block_type, state, rng):
	"""Writes a verbatim (1) or aligned offset (2) block"""
	main_freqs = [0] * (256 + LZX_NUM_OFFSET_SLOTS * 8)
	len_freqs = [0] * 249
	aligned_freqs = [0] * 8
	items = []
	size = 0
	for token in tokens:
		if isinstance(token, int):
			items.append((token, None, None))
			main_freqs[token] += 1
			size += 1
			continue
		offset, length, slot = token
		size += length
		if slot is None:
			formatted = offset + 2
			slot = max(s for s in range(LZX_NUM_OFFSET_SLOTS) if LZX_OFFSET_SLOT_BASE[s] <= formatted)
			extra = formatted - LZX_OFFSET_SLOT_BASE[slot]
		else:
			extra = 0
		main_sym = 256 + slot * 8 + min(length - 2, 7)
		main_freqs[main_sym] += 1
		len_sym = None
		if length - 2 >= 7:
			len_sym = length - 9
			len_freqs[len_sym] += 1
		extra_bits = LZX_EXTRA_OFFSET_BITS[slot] if slot >= 3 else 0
		if block_type == 2 and extra_bits >= 3:
			aligned_freqs[extra & 7] += 1
		items.append((main_sym, len_sym, (extra, extra_bits)))

	writer.bits(block_type, 3)
	if size == 32768:
		writer.bits(1, 1)
	else:
		writer.bits(0, 1)
		writer.bits(size, 16)
	aligned_codes = None
	if block_type == 2:
		aligned_lens = huffman_lens(aligned_freqs, 7)
		aligned_codes = huffman_codes(aligned_lens)
		for length in aligned_lens:
			writer.bits(length, 3)
	main_lens = huffman_lens(main_freqs, 16)
	len_lens = huffman_lens(len_freqs, 16)
	lzx_write_lens(writer, main_lens[:256], state['main'][:256], rng)
	lzx_write_lens(writer, main_lens[256:], state['main'][256:], rng)
	lzx_write_lens(writer, len_lens, state['len'], rng)
	state['main'] = main_lens
	state['len'] = len_lens
	main_codes = huffman_codes(main_lens)
	len_codes = huffman_codes(len_lens)
	for main_sym, len_sym, offset in items:
		writer.bits(*main_codes[main_sym])
		if len_sym is not None:
			writer.bits(*len_codes[len_sym])
		if offset is None:
			continue
		extra, extra_bits = offset
		if aligned_codes is not None and extra_bits >= 3:
			writer.bits(extra >> 3, extra_bits - 3)
			writer.bits(*aligned_codes[extra & 7])
		else:
			writer.bits(extra, extra_bits)

def lzx_compress(data, rng):
	data = lzx_do_e8(data)
	tokens = find_matches(data, LZX_MAX_MATCH, 32768 - 3, rng, True)
	# the tokens are split to blocks of different types
	blocks = []
	count = rng.choice([1, 1, 2, 3])
	per_block = len(tokens) // count + 1
	for i in range(0, len(tokens), per_block):
		blocks.append(tokens[i:i+per_block])

	writer = LZXWriter()
	state = {'main' : [0] * (256 + LZX_NUM_OFFSET_SLOTS * 8), 'len' : [0] * 249}
	recent = [1, 1, 1]
	pos = 0
	for index, block in enumerate(blocks):
		size = sum(1 if isinstance(token, int) else token[1] for token in block)
		block_type = rng.choice([1, 1, 2, 2, 3]) if len(blocks) > 1 else rng.choice([1, 2])
		# the offsets of the last matches after the block
		for token in block:
			if isinstance(token, int):
				continue
			if token[2] is None:
				recent = [token[0], recent[0], recent[1]]
			else:
				recent[0], recent[token[2]] = recent[token[2]], recent[0]
		if block_type == 3:
			# uncompressed block, the decoder continues with the offsets stored in it
			writer.bits(3, 3)
			if size == 32768:
				writer.bits(1, 1)
			else:
				writer.bits(0, 1)
				writer.bits(size, 16)
			writer.align()
			writer.out += struct.pack('<III', *recent)
			writer.out += data[pos:pos+size]
			if size & 1:
				writer.out += b'\x00'
		else:
			lzx_write_block(writer, block, block_type, state, rng)
		pos += size
	writer.flush()
	return bytes(writer.out)


def compress_resource(data, compress, rng):
	"""Returns the resource as stored in the WIM file and whether it is compressed"""
	chunks = []
	for offset in range(0, len(data), CHUNK_SIZE):
		chunk = data[offset:offset+CHUNK_SIZE]
		cdata = compress(chunk, rng)
		# chunks which did not get smaller are stored as they are
		chunks.append(cdata if len(cdata) < len(chunk) else chunk)
	table = b''
	offset = 0
	for chunk in chunks[:-1]:
		offset += len(chunk)
		table += struct.pack('<I', offset)
	res = table + b''.join(chunks)
	if len(res) >= len(data):
		return data, False
	return res, True

def pack_reshdr(size, flags, offset, original_size):
	return size.to_bytes(7, 'little') + bytes([flags]) + struct.pack('<QQ', offset, original_size)

def unpack_reshdr(data):
	offset, original_size = struct.unpack('<QQ', data[8:24])
	return int.from_bytes(data[0:7], 'little'), data[7], offset, original_size

def build_compressed_wim(source, compression, seed = 0):
	"""source is the contents of a WIM file without compression, returns the contents of the compressed one"""
	rng = random.Random(seed)
	compress = {'xpress' : xpress_compress, 'lzx' : lzx_compress}[compression]
	header = bytearray(source[:WIM_HEADER_SIZE])
	flags, chunk_size = struct.unpack('<II', header[16:24])
	if header[:8] != b'MSWIM\x00\x00\x00' or flags & HDR_COMPRESSION:
		raise Exception('Not an uncompressed WIM file')
	_, _, lookup_offset, lookup_size = unpack_reshdr(header[48:72])
	_, _, xml_offset, xml_size = unpack_reshdr(header[72:96])

	out = bytearray(WIM_HEADER_SIZE)
	lookup = b''
	for i in range(lookup_offset, lookup_offset + lookup_size, LOOKUP_ENTRY_SIZE):
		entry = source[i:i+LOOKUP_ENTRY_SIZE]
		size, resflags, offset, original_size = unpack_reshdr(entry)
		data, compressed = compress_resource(source[offset:offset+size], compress, rng)
		resflags = (resflags | RESHDR_COMPRESSED) if compressed is True else resflags
		lookup += pack_reshdr(len(data), resflags, len(out), original_size) + entry[24:]
		out += data

	header[48:72] = pack_reshdr(len(lookup), RESHDR_METADATA, len(out), len(lookup))
	out += lookup
	header[72:96] = pack_reshdr(xml_size, RESHDR_METADATA, len(out), xml_size)
	out += source[xml_offset:xml_offset+xml_size]
	# no boot metadata and integrity table
	header[96:120] = b'\x00' * 24
	header[124:148] = b'\x00' * 24
	flags |= HDR_COMPRESSION | (HDR_COMPRESS_XPRESS if compression == 'xpress' else HDR_COMPRESS_LZX)
	header[16:24] = struct.pack('<II', flags, CHUNK_SIZE)
	out[:WIM_HEADER_SIZE] = header
	return bytes(out)


def align8(data):
	return data + b'\x00' * (-len(data) % 8)

def build_dentry(name, attributes, security_id, subdir_offset, datahash, streams):
	"""Directory entry followed by the entries of its extra streams: [(name, hash)]"""
	name = name.encode('utf-16-le')
	entry = struct.pack('<IiQ', attributes, security_id, subdir_offset) + b'\x00' * 16
	entry += struct.pack('<QQQ', 0x01D9E0E0E0E0E0E0, 0x01D9E0E0E0E0E0E1, 0x01D9E0E0E0E0E0E2) + datahash + b'\x00' * 12
	entry += struct.pack('<HHH', len(streams), 0, len(name))
	if len(name) > 0:
		entry += name + b'\x00\x00'
	# the length of the entries includes their padding
	entry = align8(b'\x00' * 8 + entry)
	res = struct.pack('<Q', len(entry)) + entry[8:]
	for stream_name, stream_hash in streams:
		stream_name = stream_name.encode('utf-16-le')
		entry = b'\x00' * 8 + stream_hash + struct.pack('<H', len(stream_name))
		if len(stream_name) > 0:
			entry += stream_name + b'\x00\x00'
		entry = align8(b'\x00' * 8 + entry)
		res += struct.pack('<Q', len(entry)) + entry[8:]
	return res

def build_metadata(tree, security_descriptors, blobs):
	"""
	Metadata resource of an image. tree is {name: contents}, contents is a dict for a directory, bytes for a file
	or (bytes, {stream name: bytes}, index of the security descriptor) for a file with named data streams.
	The contents of the files are added to blobs (sha1 -> data).
	"""
	def add_blob(data):
		if len(data) == 0:
			return b'\x00' * 20
		datahash = hashlib.sha1(data).digest()
		blobs[datahash] = data
		return datahash

	def entry_of(name, contents, subdir_offset):
		if isinstance(contents, dict):
			return build_dentry(name, 0x10, -1, subdir_offset, b'\x00' * 20, [])
		if isinstance(contents, bytes):
			return build_dentry(name, 0x20, -1, 0, add_blob(contents), [])
		data, named, security_id = contents
		# the unnamed stream is one of the extra streams when the file has named ones
		streams = [('', add_blob(data))] + [(stream_name, add_blob(stream_data)) for stream_name, stream_data in named.items()]
		return build_dentry(name, 0x20, security_id, 0, b'\x00' * 20, streams)

	security = struct.pack('<I', len(security_descriptors)) + b''.join(struct.pack('<Q', len(sd)) for sd in security_descriptors)
	security += b''.join(security_descriptors)
	security = align8(struct.pack('<I', len(security) + 4) + security)

	# the root directory and the end of its list, then the lists of the children of each directory
	out = bytearray(security)
	root_offset = len(out)
	out += entry_of('', tree, 0) + b'\x00' * 8
	todo = [(root_offset, tree)]
	while len(todo) > 0:
		entry_offset, directory = todo.pop(0)
		struct.pack_into('<Q', out, entry_offset + 16, len(out))
		for name, contents in directory.items():
			if isinstance(contents, dict):
				todo.append((len(out), contents))
			out += entry_of(name, contents, 0)
		out += b'\x00' * 8
	return bytes(out)

def build_wim(images, compression = None, parts = 1, seed = 0):
	"""
	Builds a WIM file from scratch, for the features the files made by 7z do not have: more images,
	named data streams, security descriptors, split files.
	images: [(name, tree, security descriptors)], see build_metadata for the tree.
	Returns the contents of each part.
	"""
	rng = random.Random(seed)
	compress = {None : None, 'xpress' : xpress_compress, 'lzx' : lzx_compress}[compression]
	blobs = {}
	metadata = [build_metadata(tree, security_descriptors, blobs) for _, tree, security_descriptors in images]
	xml = '<WIM>'
	for index, image in enumerate(images):
		xml += '<IMAGE INDEX="%s"><NAME>%s</NAME><DESCRIPTION>%s description</DESCRIPTION><DIRCOUNT>1</DIRCOUNT></IMAGE>' % (index + 1, image[0], image[0])
	xml = b'\xff\xfe' + (xml + '</WIM>').encode('utf-16-le')
	guid = bytes(rng.randrange(256) for _ in range(16))

	# the metadata is in the first part, the contents of the files are spread over the parts
	resources = [[] for _ in range(parts)]
	for data in metadata:
		resources[0].append((hashlib.sha1(data).digest(), data, RESHDR_METADATA))
	for index, (datahash, data) in enumerate(blobs.items()):
		resources[index % parts].append((datahash, data, 0))

	outs = [bytearray(WIM_HEADER_SIZE) for _ in range(parts)]
	lookup = b''
	for part, out in enumerate(outs):
		for datahash, data, flags in resources[part]:
			stored, compressed = (data, False) if compress is None else compress_resource(data, compress, rng)
			flags |= RESHDR_COMPRESSED if compressed is True else 0
			lookup += pack_reshdr(len(stored), flags, len(out), len(data)) + struct.pack('<HI', part + 1, 1) + datahash
			out += stored
	res = []
	for part, out in enumerate(outs):
		# every part has the whole lookup table
		flags = 0 if parts == 1 else 0x8
		if compression is not None:
			flags |= HDR_COMPRESSION | (HDR_COMPRESS_XPRESS if compression == 'xpress' else HDR_COMPRESS_LZX)
		header = b'MSWIM\x00\x00\x00' + struct.pack('<IIII', WIM_HEADER_SIZE, 0x10d00, flags, CHUNK_SIZE if compression is not None else 0)
		header += guid + struct.pack('<HHI', part + 1, parts, len(images))
		header += pack_reshdr(len(lookup), RESHDR_METADATA, len(out), len(lookup))
		header += pack_reshdr(len(xml), RESHDR_METADATA, len(out) + len(lookup), len(xml))
		header += b'\x00' * 24 + struct.pack('<I', 0) + b'\x00' * 24 + b'\x00' * 60
		out[:WIM_HEADER_SIZE] = header
		res.append(bytes(out) + lookup + xml)
	return res

def write_source_tree(root):
	"""The files of the test WIM: data which compresses well and data which does not, files of whole chunks, x86 like code"""
	def write(path, data):
		path = os.path.join(root, path)
		os.makedirs(os.path.dirname(path), exist_ok=True)
		with open(path, 'wb') as f:
			f.write(data)
	rng = random.Random(7)
	write('hello.txt', b'hello wim\n')
	write('empty.txt', b'')
	write('dir1/pattern_a.bin', b''.join(bytes([(1 + i) & 0xFF]) * 4096 for i in range(40)))
	write('dir1/sub/deep.txt', b'deep file\n' * 100)
	write('long name with spaces.txt', b'spaces\n')
	write('\u00e1rv\u00edzt\u0171r\u0151.txt', '\u00e1rv\u00edzt\u0171r\u0151 t\u00fck\u00f6rf\u00far\u00f3g\u00e9p\n'.encode())
	dup = bytes(rng.randrange(256) for _ in range(40000))
	write('dup1.bin', dup)
	write('dir1/dup2.bin', dup)
	write('random.bin', bytes(rng.randrange(256) for _ in range(70000)))
	write('text.txt', b''.join(b'line %d of the text file\n' % i for i in range(6000)))
	words = [bytes(rng.choice(b'abcdefghijklmnopqrstuvwxyz') for _ in range(rng.randint(2, 9))) for _ in range(300)]
	write('words.txt', b' '.join(rng.choice(words) for _ in range(30000)))
	write('chunk1.bin', (b' '.join(rng.choice(words) for _ in range(8000)))[:32768])
	write('chunk2.bin', (b' '.join(rng.choice(words) for _ in range(16000)))[:65536])
	# call instructions with relative targets, LZX stores them as absolute ones
	code = bytearray()
	while len(code) < 100000:
		code += bytes(rng.choice(b'\x55\x89\xe5\x8b\x45\x08\x90\xc3\x00\x00\xff') for _ in range(rng.randint(1, 12)))
		code += b'\xe8' + struct.pack('<i', rng.choice([rng.randint(-len(code) - 50, 20000), rng.randint(-2**31, 2**31 - 1), rng.randint(11900000, 12100000), 0x1000 - len(code)]))
	write('dir2/code.bin', bytes(code))
	os.makedirs(os.path.join(root, 'dir2/emptydir'), exist_ok=True)


if __name__ == '__main__':
	if sys.argv[1] == 'source':
		write_source_tree(sys.argv[2])
		sys.exit(0)
	with open(sys.argv[1], 'rb') as f:
		source = f.read()
	with open(sys.argv[3], 'wb') as f:
		f.write(build_compressed_wim(source, sys.argv[2]))
