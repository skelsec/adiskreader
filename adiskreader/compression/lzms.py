import re
from adiskreader.compression import HuffmanDecoder, DecompressionError, copy_match, with_native

# LZMS, the compression of the solid resources of WIM files (ESD) and of the Windows compression API.
# The format is not documented by Microsoft, https://wimlib.net/ has the description this decoder follows.
#
# The data is made of 16 bit little endian words read from both ends: the range coder reads forward from the
# beginning (the bits deciding the type of the items), the Huffman codes and the extra bits are read backward
# from the end. All the Huffman codes are adaptive, they are rebuilt from the frequencies of the symbols.
#
# This decoder is slow (the chunks of the ESD files are 64 MB), the one of adiskreader_native is used when available.

LZMS_NUM_LITERAL_SYMS = 256
LZMS_NUM_LENGTH_SYMS = 54
LZMS_NUM_DELTA_POWER_SYMS = 8
LZMS_MAX_NUM_OFFSET_SYMS = 799
LZMS_MAX_CODEWORD_LENGTH = 15

LZMS_LITERAL_REBUILD_FREQ = 1024
LZMS_LZ_OFFSET_REBUILD_FREQ = 1024
LZMS_LENGTH_REBUILD_FREQ = 512
LZMS_DELTA_OFFSET_REBUILD_FREQ = 1024
LZMS_DELTA_POWER_REBUILD_FREQ = 512

LZMS_NUM_MAIN_PROBS = 16
LZMS_NUM_MATCH_PROBS = 32
LZMS_NUM_LZ_PROBS = 64
LZMS_NUM_LZ_REP_PROBS = 64
LZMS_NUM_DELTA_PROBS = 64
LZMS_NUM_DELTA_REP_PROBS = 64

LZMS_PROBABILITY_BITS = 6
LZMS_PROBABILITY_DENOMINATOR = 1 << LZMS_PROBABILITY_BITS
LZMS_INITIAL_PROBABILITY = 48
LZMS_INITIAL_RECENT_BITS = 0x0000000055555555

LZMS_X86_ID_WINDOW_SIZE = 65535
LZMS_X86_MAX_TRANSLATION_OFFSET = 1023
LZMS_MAX_SIZE = 0x7FFFFFFF

def lzms_slot_bases(runs, last:int):
    """
    Returns the base value and the number of extra bits of each slot.
    The bases are stored as the number of slots following each other with the same distance (1, 2, 4, ...)
    """
    bases = []
    extra = []
    base = 0
    for order, run in enumerate(runs):
        for _ in range(run):
            base += 1 << order
            if len(bases) > 0:
                extra.append(order)
            bases.append(base)
    extra.append((last - base).bit_length() - 1)
    bases.append(last)
    return bases, extra

LZMS_OFFSET_SLOT_BASE, LZMS_EXTRA_OFFSET_BITS = lzms_slot_bases(
    [9, 0, 9, 7, 10, 15, 15, 20, 20, 30, 33, 40, 42, 45, 60, 73, 80, 85, 95, 105, 6], 0x7fffffff)
LZMS_LENGTH_SLOT_BASE, LZMS_EXTRA_LENGTH_BITS = lzms_slot_bases(
    [27, 4, 6, 4, 5, 2, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1], 0x400108ab)

def lzms_num_offset_slots(out_size:int):
    """Number of the offset slots which can be used with the given size of the data"""
    slots = 1
    while slots < LZMS_MAX_NUM_OFFSET_SYMS and LZMS_OFFSET_SLOT_BASE[slots] <= out_size - 1:
        slots += 1
    return slots

def lzms_code_lengths(freqs, max_len:int = LZMS_MAX_CODEWORD_LENGTH):
    """
    Lengths of the codes of the canonical Huffman code made from the frequencies (all of them above zero).
    The compressor and the decompressor must build the same code, so the ties are to be resolved the same way:
    the symbols are sorted by frequency then by value, and the leaves are taken before the inner nodes.
    """
    count = len(freqs)
    if count < 2:
        return [1] * count
    order = sorted((freq, symbol) for symbol, freq in enumerate(freqs))

    # the tree is built in place: the entries are the frequencies of the leaves at first,
    # they are replaced by the frequencies and then by the parents of the inner nodes
    nodes = [item[0] for item in order]
    leaf = 0
    inner = 0
    last = 0
    while count - last > 1:
        pair = []
        for _ in range(2):
            if leaf != count and (inner == last or nodes[leaf] <= nodes[inner]):
                pair.append(leaf)
                leaf += 1
            else:
                pair.append(inner)
                inner += 1
        freq = nodes[pair[0]] + nodes[pair[1]]
        nodes[pair[0]] = last
        nodes[pair[1]] = last
        nodes[last] = freq
        last += 1

    # number of codes of each length: the depths of the inner nodes are calculated from the root,
    # each inner node takes the place of a leaf and adds two leaves one level deeper
    root = count - 2
    len_counts = [0] * (max_len + 2)
    len_counts[1] = 2
    nodes[root] = 0
    for node in range(root - 1, -1, -1):
        codelen = nodes[node] = nodes[nodes[node]] + 1
        if codelen >= max_len:
            codelen = max_len - 1
            while len_counts[codelen] == 0:
                codelen -= 1
        len_counts[codelen] -= 1
        len_counts[codelen + 1] += 2

    # the rarest symbols get the longest codes
    lens = [0] * count
    index = 0
    for codelen in range(max_len, 0, -1):
        for _ in range(len_counts[codelen]):
            lens[order[index][1]] = codelen
            index += 1
    return lens


class LZMSAdaptiveCode:
    """Huffman code rebuilt from the frequencies of the symbols after every rebuild_freq symbols"""
    def __init__(self, num_syms:int, rebuild_freq:int):
        self.freqs = [1] * num_syms
        self.rebuild_freq = rebuild_freq
        self.until_rebuild = rebuild_freq
        self.table = None
        self.table_bits = 0
        self.build()

    def build(self):
        # the codes are complete: all of them are in the table
        decoder = HuffmanDecoder(lzms_code_lengths(self.freqs), LZMS_MAX_CODEWORD_LENGTH)
        self.table = decoder.table
        self.table_bits = decoder.table_bits

    def rebuild(self):
        self.build()
        self.freqs = [(freq >> 1) + 1 for freq in self.freqs]
        self.until_rebuild = self.rebuild_freq


class LZMSBitContext:
    """
    The bits of one kind read by the range decoder, the probabilities are selected by the previous bits of the context.
    The probability of a bit being zero is the number of zeroes in the last 64 bits read in the same state.
    """
    def __init__(self, num_states:int):
        self.state = 0
        self.mask = num_states - 1
        self.zero_bits = [LZMS_INITIAL_PROBABILITY] * num_states
        self.recent_bits = [LZMS_INITIAL_RECENT_BITS] * num_states


X86_OPCODES = re.compile(b'[\\x48\\x4c\\xe8\\xe9\\xf0\\xff]')

def lzms_undo_x86(data:bytearray):
    """
    Restores the targets of the x86 instructions with relative addresses, the compressor replaces them with absolute ones.
    Always done: the parts of the data which look like machine code are found the same way by both sides.
    """
    size = len(data)
    if size <= 17:
        return
    # last position where each (16 bit) target was seen
    last_target_usages = [-LZMS_X86_ID_WINDOW_SIZE - 1] * 65536
    # last position which is likely to be machine code
    last_x86_pos = -LZMS_X86_MAX_TRANSLATION_OFFSET - 1
    end = size - 16
    search = X86_OPCODES.search

    i = 1
    while i < end:
        m = search(data, i, end)
        if m is None:
            break
        i = m.start()
        opcode = data[i]
        max_trans_offset = LZMS_X86_MAX_TRANSLATION_OFFSET
        opcode_size = 0
        if opcode == 0xE8:
            # call, done only when more sure that this is code
            opcode_size = 1
            max_trans_offset >>= 1
        elif opcode == 0xE9:
            # jmp, not translated
            i += 5
            continue
        elif opcode == 0x48:
            # mov / lea with a relative address (x86_64)
            if data[i+1] == 0x8B:
                if data[i+2] in (0x05, 0x0D):
                    opcode_size = 3
            elif data[i+1] == 0x8D and data[i+2] & 0x7 == 0x5:
                opcode_size = 3
        elif opcode == 0x4C:
            if data[i+1] == 0x8D and data[i+2] & 0x7 == 0x5:
                opcode_size = 3
        elif opcode == 0xF0:
            # lock add
            if data[i+1] == 0x83 and data[i+2] == 0x05:
                opcode_size = 3
        elif data[i+1] == 0x15:
            # indirect call (FF 15)
            opcode_size = 2
        if opcode_size == 0:
            i += 1
            continue

        operand = i + opcode_size
        if i - last_x86_pos <= max_trans_offset:
            value = (int.from_bytes(data[operand:operand+4], 'little') - i) & 0xFFFFFFFF
            data[operand:operand+4] = value.to_bytes(4, 'little')
        target = (i + data[operand] + (data[operand+1] << 8)) & 0xFFFF
        i += opcode_size + 3
        if i - last_target_usages[target] <= LZMS_X86_ID_WINDOW_SIZE:
            last_x86_pos = i
        last_target_usages[target] = i
        i += 1


def lzms_decompress_py(data:bytes, out_size:int):
    """Decompresses one LZMS chunk, out_size is the size of the result"""
    size = len(data)
    if size < 4 or size % 2 != 0:
        raise DecompressionError('Invalid size of the LZMS data')
    if out_size > LZMS_MAX_SIZE:
        raise DecompressionError('Output size is too large')

    # range decoder, reads the words from the beginning
    rd_range = 0xFFFFFFFF
    rd_code = (data[0] << 16) | (data[1] << 24) | data[2] | (data[3] << 8)
    rd_pos = 4

    def decode_bit(ctx:LZMSBitContext):
        nonlocal rd_range, rd_code, rd_pos
        state = ctx.state
        if rd_range <= 0xFFFF:
            rd_range <<= 16
            rd_code = (rd_code << 16) & 0xFFFFFFFF
            if rd_pos < size:
                rd_code |= data[rd_pos] | (data[rd_pos+1] << 8)
                rd_pos += 2
        # neither of the bits can be impossible
        zero_bits = ctx.zero_bits[state]
        prob = zero_bits
        if prob == 0:
            prob = 1
        elif prob == LZMS_PROBABILITY_DENOMINATOR:
            prob = LZMS_PROBABILITY_DENOMINATOR - 1
        bound = (rd_range >> LZMS_PROBABILITY_BITS) * prob
        if rd_code < bound:
            rd_range = bound
            bit = 0
        else:
            rd_range -= bound
            rd_code -= bound
            bit = 1
        recent_bits = ctx.recent_bits[state]
        ctx.zero_bits[state] = zero_bits + (recent_bits >> 63) - bit
        ctx.recent_bits[state] = ((recent_bits << 1) | bit) & 0xFFFFFFFFFFFFFFFF
        ctx.state = ((state << 1) | bit) & ctx.mask
        return bit

    # the words read from the end with their bits MSB first, the words before the beginning of the data are zero
    bitbuf = 0
    bitcnt = 0
    bits_pos = size

    def decode_symbol(code:LZMSAdaptiveCode):
        nonlocal bitbuf, bitcnt, bits_pos
        if bitcnt < LZMS_MAX_CODEWORD_LENGTH:
            bitbuf <<= 16
            if bits_pos >= 2:
                bits_pos -= 2
                bitbuf |= data[bits_pos] | (data[bits_pos+1] << 8)
            bitcnt += 16
        entry = code.table[(bitbuf >> (bitcnt - code.table_bits)) & ((1 << code.table_bits) - 1)]
        if entry == 0:
            # only the code of one symbol has unused entries
            raise DecompressionError('Invalid Huffman code in the input')
        symbol = entry >> 5
        bitcnt -= entry & 0x1F
        bitbuf &= (1 << bitcnt) - 1
        code.freqs[symbol] += 1
        code.until_rebuild -= 1
        if code.until_rebuild == 0:
            code.rebuild()
        return symbol

    def read_bits(count:int):
        nonlocal bitbuf, bitcnt, bits_pos
        while bitcnt < count:
            bitbuf <<= 16
            if bits_pos >= 2:
                bits_pos -= 2
                bitbuf |= data[bits_pos] | (data[bits_pos+1] << 8)
            bitcnt += 16
        bitcnt -= count
        res = bitbuf >> bitcnt
        bitbuf &= (1 << bitcnt) - 1
        return res

    offset_slots = lzms_num_offset_slots(out_size)
    literal_code = LZMSAdaptiveCode(LZMS_NUM_LITERAL_SYMS, LZMS_LITERAL_REBUILD_FREQ)
    lz_offset_code = LZMSAdaptiveCode(offset_slots, LZMS_LZ_OFFSET_REBUILD_FREQ)
    length_code = LZMSAdaptiveCode(LZMS_NUM_LENGTH_SYMS, LZMS_LENGTH_REBUILD_FREQ)
    delta_offset_code = LZMSAdaptiveCode(offset_slots, LZMS_DELTA_OFFSET_REBUILD_FREQ)
    delta_power_code = LZMSAdaptiveCode(LZMS_NUM_DELTA_POWER_SYMS, LZMS_DELTA_POWER_REBUILD_FREQ)

    main_ctx = LZMSBitContext(LZMS_NUM_MAIN_PROBS)
    match_ctx = LZMSBitContext(LZMS_NUM_MATCH_PROBS)
    lz_ctx = LZMSBitContext(LZMS_NUM_LZ_PROBS)
    lz_rep_ctx = [LZMSBitContext(LZMS_NUM_LZ_REP_PROBS), LZMSBitContext(LZMS_NUM_LZ_REP_PROBS)]
    delta_ctx = LZMSBitContext(LZMS_NUM_DELTA_PROBS)
    delta_rep_ctx = [LZMSBitContext(LZMS_NUM_DELTA_REP_PROBS), LZMSBitContext(LZMS_NUM_DELTA_REP_PROBS)]

    # The last offsets used. A match is added to its queue only after the next item, which is done here
    # by using the entries one further when the previous item was a match of the same kind.
    recent_offsets = [1, 2, 3, 4]
    recent_deltas = [(0, 1), (0, 2), (0, 3), (0, 4)]
    prev_lz = 0
    prev_delta = 0

    out = bytearray()
    while len(out) < out_size:
        if decode_bit(main_ctx) == 0:
            out.append(decode_symbol(literal_code))
            prev_lz = 0
            prev_delta = 0
            continue

        if decode_bit(match_ctx) == 0:
            # LZ match
            if decode_bit(lz_ctx) == 0:
                slot = decode_symbol(lz_offset_code)
                offset = LZMS_OFFSET_SLOT_BASE[slot] + read_bits(LZMS_EXTRA_OFFSET_BITS[slot])
                recent_offsets.pop()
            else:
                index = 0
                while index < 2 and decode_bit(lz_rep_ctx[index]) == 1:
                    index += 1
                offset = recent_offsets[index + prev_lz]
                recent_offsets[index + prev_lz] = recent_offsets[index]
                del recent_offsets[index]
            recent_offsets.insert(0, offset)
            prev_lz = 1
            prev_delta = 0

            slot = decode_symbol(length_code)
            length = LZMS_LENGTH_SLOT_BASE[slot] + read_bits(LZMS_EXTRA_LENGTH_BITS[slot])
            if offset > len(out):
                raise DecompressionError('Invalid match offset')
            if length > out_size - len(out):
                raise DecompressionError('Invalid match length')
            copy_match(out, offset, length)
        else:
            # delta match: the differences of the bytes span apart are the same as at the offset
            if decode_bit(delta_ctx) == 0:
                power = decode_symbol(delta_power_code)
                slot = decode_symbol(delta_offset_code)
                pair = (power, LZMS_OFFSET_SLOT_BASE[slot] + read_bits(LZMS_EXTRA_OFFSET_BITS[slot]))
                recent_deltas.pop()
            else:
                index = 0
                while index < 2 and decode_bit(delta_rep_ctx[index]) == 1:
                    index += 1
                pair = recent_deltas[index + prev_delta]
                recent_deltas[index + prev_delta] = recent_deltas[index]
                del recent_deltas[index]
            recent_deltas.insert(0, pair)
            prev_lz = 0
            prev_delta = 1

            slot = decode_symbol(length_code)
            length = LZMS_LENGTH_SLOT_BASE[slot] + read_bits(LZMS_EXTRA_LENGTH_BITS[slot])
            span = 1 << pair[0]
            offset = pair[1] << pair[0]
            if offset + span > len(out):
                raise DecompressionError('Invalid match offset')
            if length > out_size - len(out):
                raise DecompressionError('Invalid match length')
            start = len(out)
            for pos in range(start, start + length):
                out.append((out[pos - offset] + out[pos - span] - out[pos - offset - span]) & 0xFF)

    lzms_undo_x86(out)
    return bytes(out)

lzms_decompress = with_native('lzms_decompress')(lzms_decompress_py)
