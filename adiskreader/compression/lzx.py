import struct
from adiskreader.compression import HuffmanDecoder, DecompressionError, copy_match, with_native

# LZX as used by the chunks of WIM files and of the files compressed by WOF.
# Differences from the LZX of the CAB files: every chunk is compressed on its own, there is no header before
# the first block and the E8 translation is always done with a fixed file size.

LZX_NUM_CHARS = 256
LZX_PRECODE_NUM_SYMBOLS = 20
LZX_LENCODE_NUM_SYMBOLS = 249
LZX_ALIGNEDCODE_NUM_SYMBOLS = 8
LZX_NUM_LEN_HEADERS = 8
LZX_NUM_PRIMARY_LENS = 7
LZX_MIN_MATCH = 2
LZX_NUM_RECENT_OFFSETS = 3
LZX_OFFSET_ADJUSTMENT = 2
LZX_DEFAULT_BLOCK_SIZE = 32768
LZX_MAX_OFFSET_SLOTS = 50
LZX_WIM_MAGIC_FILESIZE = 12000000

LZX_BLOCKTYPE_VERBATIM = 1
LZX_BLOCKTYPE_ALIGNED = 2
LZX_BLOCKTYPE_UNCOMPRESSED = 3

LZX_EXTRA_OFFSET_BITS = [min(17, max(0, slot // 2 - 1)) for slot in range(LZX_MAX_OFFSET_SLOTS)]
LZX_OFFSET_SLOT_BASE = [0]
for _bits in LZX_EXTRA_OFFSET_BITS:
    LZX_OFFSET_SLOT_BASE.append(LZX_OFFSET_SLOT_BASE[-1] + (1 << _bits))

def lzx_num_offset_slots(window_size:int):
    # the offset of window_size - 2 is not allowed by the format
    max_offset = window_size - LZX_MIN_MATCH - 1
    slots = 30
    while slots < LZX_MAX_OFFSET_SLOTS and max_offset >= LZX_OFFSET_SLOT_BASE[slots] - LZX_OFFSET_ADJUSTMENT:
        slots += 1
    return slots

def lzx_undo_e8(data:bytearray):
    """Restores the relative targets of the x86 call instructions"""
    size = len(data)
    pos = 0
    while size > 10:
        pos = data.find(b'\xe8', pos, size - 10)
        if pos == -1:
            break
        target = struct.unpack_from('<i', data, pos + 1)[0]
        if target >= 0:
            if target < LZX_WIM_MAGIC_FILESIZE:
                struct.pack_into('<i', data, pos + 1, target - pos)
        elif target >= -pos:
            struct.pack_into('<i', data, pos + 1, target + LZX_WIM_MAGIC_FILESIZE)
        pos += 5


class LZXBits:
    """The bits of the input, stored in 16 bit little endian words and read MSB first"""
    def __init__(self, data:bytes):
        self.size = len(data)
        self.data = data + b'\x00' * (16 + (len(data) & 1))
        self.words = struct.unpack('<%dH' % (len(self.data) // 2), self.data)
        self.pos = 0 # in words
        self.bitbuf = 0
        self.bitcnt = 0

    def read(self, count:int):
        while self.bitcnt < count:
            if self.pos * 2 > self.size + 4:
                raise DecompressionError('LZX data is truncated')
            self.bitbuf = (self.bitbuf << 16) | self.words[self.pos]
            self.pos += 1
            self.bitcnt += 16
        self.bitcnt -= count
        res = self.bitbuf >> self.bitcnt
        self.bitbuf &= (1 << self.bitcnt) - 1
        return res

    def read_symbol(self, decoder:HuffmanDecoder):
        while self.bitcnt < 16:
            self.bitbuf = (self.bitbuf << 16) | self.words[min(self.pos, len(self.words) - 1)]
            self.pos += 1
            self.bitcnt += 16
        if decoder.max_len == 0:
            raise DecompressionError('LZX symbol of an empty code')
        entry = decoder.table[(self.bitbuf >> (self.bitcnt - decoder.table_bits)) & ((1 << decoder.table_bits) - 1)]
        if entry != 0:
            symbol = entry >> 5
            self.bitcnt -= entry & 0x1F
        else:
            symbol, codelen = decoder.decode_long(self.bitbuf, self.bitcnt)
            self.bitcnt -= codelen
        self.bitbuf &= (1 << self.bitcnt) - 1
        return symbol

    def align(self):
        """Skips to the next 16 bit boundary (a whole word if already there), returns the position in bytes"""
        consumed = self.pos * 16 - self.bitcnt
        self.pos = consumed // 16 + 1
        self.bitbuf = 0
        self.bitcnt = 0
        return self.pos * 2

    def set_byte_pos(self, pos:int):
        self.pos = (pos + 1) // 2
        self.bitbuf = 0
        self.bitcnt = 0


def lzx_read_lens(bits:LZXBits, lens:list, start:int, end:int):
    """Updates lens[start:end], they are stored as differences from the lengths of the previous block"""
    precode = HuffmanDecoder([bits.read(4) for _ in range(LZX_PRECODE_NUM_SYMBOLS)], 8)
    i = start
    while i < end:
        presym = bits.read_symbol(precode)
        if presym < 17:
            lens[i] = (lens[i] - presym) % 17
            i += 1
            continue
        if presym == 17:
            run = 4 + bits.read(4)
            value = 0
        elif presym == 18:
            run = 20 + bits.read(5)
            value = 0
        else:
            run = 4 + bits.read(1)
            presym = bits.read_symbol(precode)
            if presym > 17:
                raise DecompressionError('Invalid LZX code length')
            value = (lens[i] - presym) % 17
        run = min(run, end - i)
        lens[i:i+run] = [value] * run
        i += run

def lzx_decompress_py(data:bytes, out_size:int, window_size:int = 32768):
    """Decompresses one chunk, out_size is the size of the result, window_size is the chunk size of the WIM"""
    num_main_syms = LZX_NUM_CHARS + lzx_num_offset_slots(window_size) * LZX_NUM_LEN_HEADERS
    main_lens = [0] * num_main_syms
    len_lens = [0] * LZX_LENCODE_NUM_SYMBOLS
    recent = [1, 1, 1]
    bits = LZXBits(data)
    words = bits.words
    last_word = len(words) - 1
    out = bytearray()

    while len(out) < out_size:
        block_type = bits.read(3)
        if bits.read(1) == 1:
            block_size = LZX_DEFAULT_BLOCK_SIZE
        else:
            block_size = bits.read(16)
            if window_size >= 65536:
                block_size = (block_size << 8) | bits.read(8)
        if block_size == 0 or block_size > out_size - len(out):
            raise DecompressionError('Invalid LZX block size')
        block_end = len(out) + block_size

        if block_type == LZX_BLOCKTYPE_UNCOMPRESSED:
            pos = bits.align()
            if pos + 12 + block_size > bits.size:
                raise DecompressionError('LZX data is truncated')
            recent = list(struct.unpack_from('<III', bits.data, pos))
            if 0 in recent:
                raise DecompressionError('Invalid LZX match offset')
            pos += 12
            out += bits.data[pos:pos+block_size]
            # the data is padded to 16 bits
            bits.set_byte_pos(pos + block_size)
            continue
        if block_type not in (LZX_BLOCKTYPE_VERBATIM, LZX_BLOCKTYPE_ALIGNED):
            raise DecompressionError('Invalid LZX block type %s' % block_type)

        aligned = None
        if block_type == LZX_BLOCKTYPE_ALIGNED:
            aligned = HuffmanDecoder([bits.read(3) for _ in range(LZX_ALIGNEDCODE_NUM_SYMBOLS)], 7)
        lzx_read_lens(bits, main_lens, 0, LZX_NUM_CHARS)
        lzx_read_lens(bits, main_lens, LZX_NUM_CHARS, num_main_syms)
        lzx_read_lens(bits, len_lens, 0, LZX_LENCODE_NUM_SYMBOLS)
        main = HuffmanDecoder(main_lens, 11)
        lencode = HuffmanDecoder(len_lens, 10)
        if main.max_len == 0:
            raise DecompressionError('Empty LZX main code')
        main_table = main.table
        main_bits = main.table_bits
        main_mask = (1 << main_bits) - 1

        # the main loop works on local copies of the state of the bit reader
        pos = bits.pos
        bitbuf = bits.bitbuf
        bitcnt = bits.bitcnt
        r0, r1, r2 = recent
        while len(out) < block_end:
            if bitcnt < 16:
                bitbuf = (bitbuf << 16) | words[pos if pos < last_word else last_word]
                pos += 1
                bitcnt += 16
            entry = main_table[(bitbuf >> (bitcnt - main_bits)) & main_mask]
            if entry != 0:
                symbol = entry >> 5
                bitcnt -= entry & 0x1F
            else:
                symbol, codelen = main.decode_long(bitbuf, bitcnt)
                bitcnt -= codelen
            bitbuf &= (1 << bitcnt) - 1
            if symbol < 256:
                out.append(symbol)
                continue

            symbol -= 256
            length = symbol & 7
            slot = symbol >> 3
            if length == LZX_NUM_PRIMARY_LENS:
                bits.pos, bits.bitbuf, bits.bitcnt = pos, bitbuf, bitcnt
                length += bits.read_symbol(lencode)
                pos, bitbuf, bitcnt = bits.pos, bits.bitbuf, bits.bitcnt
            length += LZX_MIN_MATCH

            if slot == 0:
                offset = r0
            elif slot == 1:
                offset = r1
                r1 = r0
                r0 = offset
            elif slot == 2:
                offset = r2
                r2 = r0
                r0 = offset
            else:
                extra = LZX_EXTRA_OFFSET_BITS[slot]
                offset = LZX_OFFSET_SLOT_BASE[slot] - LZX_OFFSET_ADJUSTMENT
                bits.pos, bits.bitbuf, bits.bitcnt = pos, bitbuf, bitcnt
                if aligned is not None and extra >= 3:
                    offset += bits.read(extra - 3) << 3
                    offset += bits.read_symbol(aligned)
                elif extra > 0:
                    offset += bits.read(extra)
                pos, bitbuf, bitcnt = bits.pos, bits.bitbuf, bits.bitcnt
                r2 = r1
                r1 = r0
                r0 = offset

            if pos * 2 > bits.size + 8:
                raise DecompressionError('LZX data is truncated')
            copy_match(out, offset, length)

        if len(out) != block_end:
            raise DecompressionError('LZX match is longer than the block')
        bits.pos, bits.bitbuf, bits.bitcnt = pos, bitbuf, bitcnt
        recent = [r0, r1, r2]

    lzx_undo_e8(out)
    return bytes(out)

lzx_decompress = with_native('lzx_decompress')(lzx_decompress_py)
