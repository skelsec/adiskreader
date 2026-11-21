
import os

class DecompressionError(Exception):
    pass

# The decompressors have a native version in the optional adiskreader_native module (native/adiskreader_native),
# it is used when it can be imported. ADISKREADER_NO_NATIVE=1 turns it off.
native = None
if os.environ.get('ADISKREADER_NO_NATIVE', '0') in ('', '0'):
    try:
        import adiskreader_native as native
    except ImportError:
        native = None

def with_native(name:str):
    """Makes the decorated decompressor call the function of the native module with the same name, if there is one"""
    def decorator(func):
        native_func = getattr(native, name, None)
        if native_func is None:
            return func
        def wrapper(*args, **kwargs):
            try:
                return native_func(*args, **kwargs)
            except ValueError as e:
                raise DecompressionError(str(e))
        wrapper.__doc__ = func.__doc__
        wrapper.__name__ = name
        return wrapper
    return decorator

class HuffmanDecoder:
    """
    Decoder of canonical Huffman codes read MSB first (XPRESS, LZX, LZMS).
    table is indexed with the next table_bits bits of the input, the entries are (symbol << 5) | code length.
    Entries of codes longer than table_bits are 0, those are decoded by decode_long.
    """
    def __init__(self, lens, table_bits:int = 11):
        self.max_len = max(lens) if len(lens) > 0 else 0
        self.table_bits = max(1, min(table_bits, self.max_len))
        self.table = [0] * (1 << self.table_bits)
        self.first_code = [0] * (self.max_len + 2)
        self.count = [0] * (self.max_len + 2)
        self.offset = [0] * (self.max_len + 2)
        self.symbols = []
        if self.max_len == 0:
            # empty code, valid until a symbol is read with it
            return

        for codelen in lens:
            self.count[codelen] += 1
        self.count[0] = 0
        by_len = [[] for _ in range(self.max_len + 1)]
        for symbol, codelen in enumerate(lens):
            if codelen > 0:
                by_len[codelen].append(symbol)

        code = 0
        for codelen in range(1, self.max_len + 1):
            self.first_code[codelen] = code
            self.offset[codelen] = len(self.symbols)
            self.symbols += by_len[codelen]
            if codelen <= self.table_bits:
                shift = self.table_bits - codelen
                size = 1 << shift
                for symbol in by_len[codelen]:
                    start = code << shift
                    if start + size > len(self.table):
                        raise DecompressionError('Invalid Huffman code')
                    self.table[start:start+size] = [(symbol << 5) | codelen] * size
                    code += 1
            else:
                code += self.count[codelen]
            if code > (1 << codelen):
                raise DecompressionError('Invalid Huffman code')
            code <<= 1

    def decode_long(self, bitbuf:int, bitcnt:int):
        """Returns the symbol and the length of the code at the top of the bitcnt bits of bitbuf"""
        for codelen in range(self.table_bits + 1, self.max_len + 1):
            if codelen > bitcnt:
                break
            idx = ((bitbuf >> (bitcnt - codelen)) & ((1 << codelen) - 1)) - self.first_code[codelen]
            if 0 <= idx < self.count[codelen]:
                return self.symbols[self.offset[codelen] + idx], codelen
        raise DecompressionError('Invalid Huffman code in the input')

def copy_match(out:bytearray, offset:int, length:int):
    """Appends length bytes starting offset bytes back, the two ranges can overlap"""
    start = len(out) - offset
    if offset <= 0 or start < 0:
        raise DecompressionError('Invalid match offset')
    if offset >= length:
        out += out[start:start+length]
    elif offset == 1:
        out += out[start:] * length
    else:
        out += (out[start:] * (length // offset + 1))[:length]
