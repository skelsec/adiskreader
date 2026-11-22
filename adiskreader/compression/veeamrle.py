import re
from adiskreader.compression import DecompressionError, with_native

# Run length encoding of the Veeam backup files written with the "dedupe-friendly" compression level.
# The bytes are stored as they are, two equal bytes after each other are followed by the length of the run
# (the two bytes included, so a length under 2 shortens it). Longer runs are stored as more of these triplets.

RLE_PAIR = re.compile(rb'(.)\1', re.DOTALL)

def veeam_rle_decompress_py(data:bytes, out_size:int):
    """Decompresses the data of one block (without the size it starts with), out_size is the size of the result"""
    size = len(data)
    pos = 0
    out = bytearray()
    while pos < size:
        # the literals up to the next pair of equal bytes are copied at once
        pair = RLE_PAIR.search(data, pos)
        if pair is None:
            out += data[pos:]
            break
        start = pair.start()
        if start + 2 >= size:
            raise DecompressionError('RLE data is truncated')
        out += data[pos:start]
        out += data[start:start+1] * data[start+2]
        pos = start + 3
        if len(out) > out_size:
            raise DecompressionError('RLE data is longer than the output')

    if len(out) != out_size:
        raise DecompressionError('RLE data does not match the size of the output')
    return bytes(out)

veeam_rle_decompress = with_native('veeam_rle_decompress')(veeam_rle_decompress_py)
