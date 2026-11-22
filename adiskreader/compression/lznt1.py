from adiskreader.compression import DecompressionError, copy_match, with_native

# LZNT1, the compression of NTFS files ([MS-XCA] 2.5)
# The data is a list of chunks, each of them starts with a 16 bit header: the size of its data - 1 in the low 12 bits,
# the highest bit is set when the data is compressed. A chunk holds 4096 bytes of the file, matches do not leave it.
# A compressed chunk is made of groups of a flag byte and 8 items, an item is a literal byte or a 16 bit match:
# offset - 1 in its high bits, length - 3 in the low ones, the more bytes the chunk has the more bits the offset takes.

LZNT1_CHUNK_SIZE = 4096
LZNT1_MIN_MATCH = 3

def _offset_shifts():
    # shift of the offset in a match by the count of bytes already in the chunk
    shifts = [12]
    for written in range(1, LZNT1_CHUNK_SIZE + 1):
        shift = 12
        pos = written - 1
        while pos >= 0x10:
            shift -= 1
            pos >>= 1
        shifts.append(shift)
    return shifts

LZNT1_SHIFTS = _offset_shifts()

def lznt1_decompress_py(data:bytes, out_size:int):
    """
    Decompresses the chunks of a compression unit of an NTFS file, out_size is the size of the result.
    Each chunk gives 4096 bytes, the shorter ones and the end of the result are filled with zeroes.
    """
    size = len(data)
    pos = 0
    out = bytearray()
    while pos < size and len(out) < out_size:
        if pos + 2 > size:
            raise DecompressionError('LZNT1 data is truncated')
        header = data[pos] | (data[pos+1] << 8)
        pos += 2
        if header == 0:
            # end of the chunks
            break
        chunk_end = pos + (header & 0xFFF) + 1
        if chunk_end > size:
            raise DecompressionError('LZNT1 data is truncated')
        chunk_start = len(out)
        if header & 0x8000 == 0:
            out += data[pos:chunk_end]
            pos = chunk_end
        while pos < chunk_end:
            flags = data[pos]
            pos += 1
            for _ in range(8):
                if pos >= chunk_end:
                    break
                if flags & 1 == 0:
                    out.append(data[pos])
                    pos += 1
                else:
                    if pos + 2 > chunk_end:
                        raise DecompressionError('LZNT1 data is truncated')
                    token = data[pos] | (data[pos+1] << 8)
                    pos += 2
                    written = len(out) - chunk_start
                    if written > LZNT1_CHUNK_SIZE:
                        raise DecompressionError('LZNT1 chunk is longer than 4096 bytes')
                    shift = LZNT1_SHIFTS[written]
                    offset = (token >> shift) + 1
                    if offset > written:
                        raise DecompressionError('Invalid match offset')
                    copy_match(out, offset, (token & ((1 << shift) - 1)) + LZNT1_MIN_MATCH)
                flags >>= 1
        chunk_size = len(out) - chunk_start
        if chunk_size > LZNT1_CHUNK_SIZE:
            raise DecompressionError('LZNT1 chunk is longer than 4096 bytes')
        if len(out) > out_size:
            raise DecompressionError('LZNT1 data is longer than the output')
        out += b'\x00' * min(LZNT1_CHUNK_SIZE - chunk_size, out_size - len(out))

    out += b'\x00' * (out_size - len(out))
    return bytes(out)

lznt1_decompress = with_native('lznt1_decompress')(lznt1_decompress_py)
