from adiskreader.compression import DecompressionError, copy_match, with_native

# LZ4 block format (no frame), as used by the blocks of the Veeam backup files
# https://github.com/lz4/lz4/blob/dev/doc/lz4_Block_format.md

LZ4_MIN_MATCH = 4

def lz4_decompress_py(data:bytes, out_size:int):
    """Decompresses one LZ4 block, out_size is the size of the result"""
    size = len(data)
    pos = 0
    out = bytearray()
    while pos < size:
        token = data[pos]
        pos += 1
        length = token >> 4
        if length == 15:
            while True:
                if pos >= size:
                    raise DecompressionError('LZ4 data is truncated')
                b = data[pos]
                pos += 1
                length += b
                if b != 255:
                    break
        if pos + length > size:
            raise DecompressionError('LZ4 data is truncated')
        out += data[pos:pos+length]
        pos += length
        if len(out) > out_size:
            raise DecompressionError('LZ4 literals are longer than the output')
        if pos == size:
            # the last sequence has literals only
            break
        if pos + 2 > size:
            raise DecompressionError('LZ4 data is truncated')
        offset = data[pos] | (data[pos+1] << 8)
        pos += 2
        length = token & 15
        if length == 15:
            while True:
                if pos >= size:
                    raise DecompressionError('LZ4 data is truncated')
                b = data[pos]
                pos += 1
                length += b
                if b != 255:
                    break
        length += LZ4_MIN_MATCH
        if len(out) + length > out_size:
            raise DecompressionError('LZ4 match is longer than the output')
        copy_match(out, offset, length)

    if len(out) != out_size:
        raise DecompressionError('LZ4 data is shorter than the output')
    return bytes(out)

lz4_decompress = with_native('lz4_decompress')(lz4_decompress_py)
