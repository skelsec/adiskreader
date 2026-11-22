from adiskreader.compression import HuffmanDecoder, DecompressionError, copy_match, with_native

# XPRESS with Huffman coding (MS-XCA 2.2), as used by the chunks of WIM files and of the files compressed by WOF

XPRESS_NUM_SYMBOLS = 512
XPRESS_MIN_MATCH = 3
XPRESS_TABLE_BITS = 11

def xpress_decompress_py(data:bytes, out_size:int):
    """Decompresses one chunk which starts with its Huffman code lengths, out_size is the size of the result"""
    if len(data) < XPRESS_NUM_SYMBOLS // 2:
        raise DecompressionError('XPRESS data is too short')
    lens = []
    for b in data[:XPRESS_NUM_SYMBOLS // 2]:
        lens.append(b & 0xF)
        lens.append(b >> 4)
    decoder = HuffmanDecoder(lens, XPRESS_TABLE_BITS)
    table = decoder.table
    table_bits = decoder.table_bits
    table_mask = (1 << table_bits) - 1

    # the bits are in 16 bit little endian words, the extra bytes of the match lengths are between them
    size = len(data)
    data = data + b'\x00' * 8
    pos = XPRESS_NUM_SYMBOLS // 2
    bitbuf = 0
    bitcnt = 0
    out = bytearray()
    while len(out) < out_size:
        if bitcnt < 16:
            bitbuf = (bitbuf << 16) | data[pos] | (data[pos+1] << 8)
            pos += 2
            bitcnt += 16
            if pos > size + 4:
                raise DecompressionError('XPRESS data is truncated')
        entry = table[(bitbuf >> (bitcnt - table_bits)) & table_mask]
        if entry != 0:
            symbol = entry >> 5
            bitcnt -= entry & 0x1F
        else:
            symbol, codelen = decoder.decode_long(bitbuf, bitcnt)
            bitcnt -= codelen
        bitbuf &= (1 << bitcnt) - 1

        if symbol < 256:
            out.append(symbol)
            continue

        length = symbol & 0xF
        offset_bits = (symbol >> 4) & 0xF
        if bitcnt < 16:
            bitbuf = (bitbuf << 16) | data[pos] | (data[pos+1] << 8)
            pos += 2
            bitcnt += 16
        bitcnt -= offset_bits
        offset = (1 << offset_bits) | (bitbuf >> bitcnt)
        bitbuf &= (1 << bitcnt) - 1
        if length == 0xF:
            length += data[pos]
            pos += 1
            if length == 0xF + 0xFF:
                length = data[pos] | (data[pos+1] << 8)
                pos += 2
        if pos > size + 4:
            raise DecompressionError('XPRESS data is truncated')
        copy_match(out, offset, length + XPRESS_MIN_MATCH)

    if len(out) != out_size:
        raise DecompressionError('XPRESS match is longer than the output')
    return bytes(out)

xpress_decompress = with_native('xpress_decompress')(xpress_decompress_py)
