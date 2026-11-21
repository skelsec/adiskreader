//! LZMS, the compression of the solid resources of WIM files (ESD) and of the Windows compression API.
//! The format is not documented by Microsoft, https://wimlib.net/ has the description this decoder follows.
//!
//! The data is made of 16 bit little endian words read from both ends: the range coder reads forward from the
//! beginning (the bits deciding the type of the items), the Huffman codes and the extra bits are read backward
//! from the end. All the Huffman codes are adaptive, they are rebuilt from the frequencies of the symbols.

use crate::huffman::HuffmanDecoder;

const NUM_LITERAL_SYMS: usize = 256;
const NUM_LENGTH_SYMS: usize = 54;
const NUM_DELTA_POWER_SYMS: usize = 8;
const MAX_NUM_OFFSET_SYMS: usize = 799;
const MAX_CODEWORD_LENGTH: usize = 15;

const LITERAL_REBUILD_FREQ: u32 = 1024;
const LZ_OFFSET_REBUILD_FREQ: u32 = 1024;
const LENGTH_REBUILD_FREQ: u32 = 512;
const DELTA_OFFSET_REBUILD_FREQ: u32 = 1024;
const DELTA_POWER_REBUILD_FREQ: u32 = 512;

const NUM_MAIN_PROBS: usize = 16;
const NUM_MATCH_PROBS: usize = 32;
const NUM_LZ_PROBS: usize = 64;
const NUM_LZ_REP_PROBS: usize = 64;
const NUM_DELTA_PROBS: usize = 64;
const NUM_DELTA_REP_PROBS: usize = 64;

const PROBABILITY_BITS: u32 = 6;
const PROBABILITY_DENOMINATOR: u32 = 1 << PROBABILITY_BITS;
const INITIAL_PROBABILITY: u32 = 48;
const INITIAL_RECENT_BITS: u64 = 0x0000000055555555;

const X86_ID_WINDOW_SIZE: i32 = 65535;
const X86_MAX_TRANSLATION_OFFSET: i32 = 1023;

/// the slot bases are stored as the number of slots following each other with the same distance (1, 2, 4, ...)
const OFFSET_SLOT_RUNS: [u8; 21] = [9, 0, 9, 7, 10, 15, 15, 20, 20, 30, 33, 40, 42, 45, 60, 73, 80, 85, 95, 105, 6];
const OFFSET_SLOT_FINAL: u32 = 0x7fffffff;
const LENGTH_SLOT_RUNS: [u8; 17] = [27, 4, 6, 4, 5, 2, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 1];
const LENGTH_SLOT_FINAL: u32 = 0x400108ab;

/// Returns the base value and the number of extra bits of each slot
fn slot_bases(runs: &[u8], last: u32) -> (Vec<u32>, Vec<u8>) {
    let mut bases: Vec<u32> = Vec::new();
    let mut extra: Vec<u8> = Vec::new();
    let mut base: u32 = 0;
    for (order, &run) in runs.iter().enumerate() {
        for _ in 0..run {
            base += 1 << order;
            if !bases.is_empty() {
                extra.push(order as u8);
            }
            bases.push(base);
        }
    }
    extra.push((31 - (last - base).leading_zeros()) as u8);
    bases.push(last);
    (bases, extra)
}

/// Lengths of the codes of the canonical Huffman code made from the frequencies (all of them above zero).
/// The compressor and the decompressor must build the same code, so the ties are to be resolved the same way:
/// the symbols are sorted by frequency then by value, and the leaves are taken before the inner nodes.
fn code_lengths(freqs: &[u32], lens: &mut [u8]) {
    let count = freqs.len();
    let mut order: Vec<(u32, u16)> = freqs.iter().enumerate().map(|(sym, &freq)| (freq, sym as u16)).collect();
    order.sort_unstable();
    if count < 2 {
        lens.fill(1);
        return;
    }

    // the tree is built in place: the entries are the frequencies of the leaves at first,
    // they are replaced by the frequencies and then by the parents of the inner nodes
    let mut nodes: Vec<u32> = order.iter().map(|item| item.0).collect();
    let mut leaf = 0;
    let mut inner = 0;
    let mut next = 0;
    while count - next > 1 {
        let mut pair = [0usize; 2];
        for item in pair.iter_mut() {
            if leaf != count && (inner == next || nodes[leaf] <= nodes[inner]) {
                *item = leaf;
                leaf += 1;
            } else {
                *item = inner;
                inner += 1;
            }
        }
        let freq = nodes[pair[0]] + nodes[pair[1]];
        nodes[pair[0]] = next as u32;
        nodes[pair[1]] = next as u32;
        nodes[next] = freq;
        next += 1;
    }

    // number of codes of each length: the depths of the inner nodes are calculated from the root,
    // each inner node takes the place of a leaf and adds two leaves one level deeper
    let root = count - 2;
    let mut len_counts = [0u32; MAX_CODEWORD_LENGTH + 2];
    len_counts[1] = 2;
    nodes[root] = 0;
    for node in (0..root).rev() {
        let depth = nodes[nodes[node] as usize] + 1;
        nodes[node] = depth;
        let mut len = depth as usize;
        if len >= MAX_CODEWORD_LENGTH {
            len = MAX_CODEWORD_LENGTH - 1;
            while len_counts[len] == 0 {
                len -= 1;
            }
        }
        len_counts[len] -= 1;
        len_counts[len + 1] += 2;
    }

    // the rarest symbols get the longest codes
    let mut index = 0;
    for len in (1..=MAX_CODEWORD_LENGTH).rev() {
        for _ in 0..len_counts[len] {
            lens[order[index].1 as usize] = len as u8;
            index += 1;
        }
    }
}

struct AdaptiveCode {
    freqs: Vec<u32>,
    lens: Vec<u8>,
    decoder: HuffmanDecoder,
    rebuild_freq: u32,
    until_rebuild: u32,
}

impl AdaptiveCode {
    fn new(num_syms: usize, rebuild_freq: u32) -> Result<AdaptiveCode, &'static str> {
        let freqs = vec![1u32; num_syms];
        let mut lens = vec![0u8; num_syms];
        code_lengths(&freqs, &mut lens);
        let decoder = HuffmanDecoder::new(&lens)?;
        Ok(AdaptiveCode { freqs, lens, decoder, rebuild_freq, until_rebuild: rebuild_freq })
    }

    #[inline(never)]
    fn rebuild(&mut self) -> Result<(), &'static str> {
        code_lengths(&self.freqs, &mut self.lens);
        self.decoder = HuffmanDecoder::new(&self.lens)?;
        for freq in self.freqs.iter_mut() {
            *freq = (*freq >> 1) + 1;
        }
        self.until_rebuild = self.rebuild_freq;
        Ok(())
    }

    #[inline(always)]
    fn decode(&mut self, bits: &mut BackwardBits) -> Result<usize, &'static str> {
        bits.ensure(MAX_CODEWORD_LENGTH as u32);
        let (symbol, codelen) = self.decoder.decode(bits.bitbuf, bits.bitcnt)?;
        bits.bitcnt -= codelen;
        self.freqs[symbol] += 1;
        self.until_rebuild -= 1;
        if self.until_rebuild == 0 {
            self.rebuild()?;
        }
        Ok(symbol)
    }
}

/// The words of the input read from the end, their bits MSB first. The words before the beginning of the data are zero.
struct BackwardBits<'a> {
    data: &'a [u8],
    /// position of the last word read, in bytes
    pos: usize,
    /// the low bitcnt bits are the ones not used yet
    bitbuf: u64,
    bitcnt: u32,
}

impl<'a> BackwardBits<'a> {
    #[inline(always)]
    fn ensure(&mut self, count: u32) {
        while self.bitcnt < count {
            let mut word = 0;
            if self.pos >= 2 {
                self.pos -= 2;
                word = self.data[self.pos] as u64 | ((self.data[self.pos + 1] as u64) << 8);
            }
            self.bitbuf = (self.bitbuf << 16) | word;
            self.bitcnt += 16;
        }
    }

    /// count is at most 32
    #[inline(always)]
    fn read(&mut self, count: u32) -> u32 {
        self.ensure(count);
        self.bitcnt -= count;
        ((self.bitbuf >> self.bitcnt) & ((1u64 << count) - 1)) as u32
    }
}

/// Probability of a bit being zero, out of the last 64 bits read in the same state
#[derive(Clone, Copy)]
struct Probability {
    zero_bits: u32,
    recent_bits: u64,
}

/// The bits read in a context: the probabilities are selected by the previous bits of the context
struct BitContext {
    state: usize,
    probs: Vec<Probability>,
}

impl BitContext {
    fn new(num_states: usize) -> BitContext {
        BitContext { state: 0, probs: vec![Probability { zero_bits: INITIAL_PROBABILITY, recent_bits: INITIAL_RECENT_BITS }; num_states] }
    }
}

struct RangeDecoder<'a> {
    data: &'a [u8],
    pos: usize,
    range: u32,
    code: u32,
}

impl<'a> RangeDecoder<'a> {
    #[inline(always)]
    fn bit(&mut self, ctx: &mut BitContext) -> bool {
        let mask = ctx.probs.len() - 1;
        let entry = &mut ctx.probs[ctx.state & mask];
        if self.range <= 0xFFFF {
            self.range <<= 16;
            self.code <<= 16;
            if self.pos + 2 <= self.data.len() {
                self.code |= self.data[self.pos] as u32 | ((self.data[self.pos + 1] as u32) << 8);
                self.pos += 2;
            }
        }
        // neither of the bits can be impossible
        let prob = entry.zero_bits.clamp(1, PROBABILITY_DENOMINATOR - 1);
        let bound = (self.range >> PROBABILITY_BITS) * prob;
        let bit = self.code >= bound;
        if bit {
            self.range -= bound;
            self.code -= bound;
        } else {
            self.range = bound;
        }
        entry.zero_bits = entry.zero_bits + (entry.recent_bits >> 63) as u32 - bit as u32;
        entry.recent_bits = (entry.recent_bits << 1) | bit as u64;
        ctx.state = ((ctx.state << 1) | bit as usize) & mask;
        bit
    }
}

/// Number of the offset slots which can be used with the given size of the data
fn num_offset_slots(bases: &[u32], out_size: usize) -> usize {
    if out_size < 2 {
        return 1;
    }
    let max_offset = (out_size - 1).min(u32::MAX as usize) as u32;
    let mut slots = 1;
    while slots < MAX_NUM_OFFSET_SYMS && bases[slots] <= max_offset {
        slots += 1;
    }
    slots
}

/// Decompresses one LZMS chunk, out_size is the size of the result
pub fn decompress(data: &[u8], out_size: usize) -> Result<Vec<u8>, &'static str> {
    if data.len() < 4 || data.len() % 2 != 0 {
        return Err("Invalid size of the LZMS data");
    }
    if out_size > i32::MAX as usize {
        return Err("Output size is too large");
    }
    let (offset_bases, offset_extra) = slot_bases(&OFFSET_SLOT_RUNS, OFFSET_SLOT_FINAL);
    let (length_bases, length_extra) = slot_bases(&LENGTH_SLOT_RUNS, LENGTH_SLOT_FINAL);
    let offset_slots = num_offset_slots(&offset_bases, out_size);

    let mut rd = RangeDecoder {
        data,
        pos: 4,
        range: 0xFFFFFFFF,
        code: ((data[0] as u32) << 16) | ((data[1] as u32) << 24) | data[2] as u32 | ((data[3] as u32) << 8),
    };
    let mut bits = BackwardBits { data, pos: data.len(), bitbuf: 0, bitcnt: 0 };

    let mut literal_code = AdaptiveCode::new(NUM_LITERAL_SYMS, LITERAL_REBUILD_FREQ)?;
    let mut lz_offset_code = AdaptiveCode::new(offset_slots, LZ_OFFSET_REBUILD_FREQ)?;
    let mut length_code = AdaptiveCode::new(NUM_LENGTH_SYMS, LENGTH_REBUILD_FREQ)?;
    let mut delta_offset_code = AdaptiveCode::new(offset_slots, DELTA_OFFSET_REBUILD_FREQ)?;
    let mut delta_power_code = AdaptiveCode::new(NUM_DELTA_POWER_SYMS, DELTA_POWER_REBUILD_FREQ)?;

    let mut main_ctx = BitContext::new(NUM_MAIN_PROBS);
    let mut match_ctx = BitContext::new(NUM_MATCH_PROBS);
    let mut lz_ctx = BitContext::new(NUM_LZ_PROBS);
    let mut lz_rep_ctx = [BitContext::new(NUM_LZ_REP_PROBS), BitContext::new(NUM_LZ_REP_PROBS)];
    let mut delta_ctx = BitContext::new(NUM_DELTA_PROBS);
    let mut delta_rep_ctx = [BitContext::new(NUM_DELTA_REP_PROBS), BitContext::new(NUM_DELTA_REP_PROBS)];

    // The last offsets used. A match is added to its queue only after the next item, which is done here
    // by using the entries one further when the previous item was a match of the same kind.
    let mut recent_offsets: [u32; 4] = [1, 2, 3, 4];
    let mut recent_deltas: [(u32, u32); 4] = [(0, 1), (0, 2), (0, 3), (0, 4)];
    let mut prev_lz = 0usize;
    let mut prev_delta = 0usize;

    let mut out: Vec<u8> = Vec::with_capacity(out_size);
    while out.len() < out_size {
        if !rd.bit(&mut main_ctx) {
            out.push(literal_code.decode(&mut bits)? as u8);
            prev_lz = 0;
            prev_delta = 0;
            continue;
        }

        if !rd.bit(&mut match_ctx) {
            // LZ match
            let offset;
            if !rd.bit(&mut lz_ctx) {
                let slot = lz_offset_code.decode(&mut bits)?;
                offset = offset_bases[slot] + bits.read(offset_extra[slot] as u32);
                recent_offsets[3] = recent_offsets[2];
                recent_offsets[2] = recent_offsets[1];
                recent_offsets[1] = recent_offsets[0];
            } else {
                let mut index = 0;
                while index < 2 && rd.bit(&mut lz_rep_ctx[index]) {
                    index += 1;
                }
                offset = recent_offsets[index + prev_lz];
                recent_offsets[index + prev_lz] = recent_offsets[index];
                for i in (1..=index).rev() {
                    recent_offsets[i] = recent_offsets[i - 1];
                }
            }
            recent_offsets[0] = offset;
            prev_lz = 1;
            prev_delta = 0;

            let slot = length_code.decode(&mut bits)?;
            let length = (length_bases[slot] + bits.read(length_extra[slot] as u32)) as usize;
            let offset = offset as usize;
            if offset > out.len() {
                return Err("Invalid match offset");
            }
            if length > out_size - out.len() {
                return Err("Invalid match length");
            }
            let start = out.len() - offset;
            if offset >= length {
                out.extend_from_within(start..start + length);
            } else {
                for i in start..start + length {
                    let b = out[i];
                    out.push(b);
                }
            }
        } else {
            // delta match: the differences of the bytes span apart are the same as at the offset
            let pair;
            if !rd.bit(&mut delta_ctx) {
                let power = delta_power_code.decode(&mut bits)? as u32;
                let slot = delta_offset_code.decode(&mut bits)?;
                pair = (power, offset_bases[slot] + bits.read(offset_extra[slot] as u32));
                recent_deltas[3] = recent_deltas[2];
                recent_deltas[2] = recent_deltas[1];
                recent_deltas[1] = recent_deltas[0];
            } else {
                let mut index = 0;
                while index < 2 && rd.bit(&mut delta_rep_ctx[index]) {
                    index += 1;
                }
                pair = recent_deltas[index + prev_delta];
                recent_deltas[index + prev_delta] = recent_deltas[index];
                for i in (1..=index).rev() {
                    recent_deltas[i] = recent_deltas[i - 1];
                }
            }
            recent_deltas[0] = pair;
            prev_lz = 0;
            prev_delta = 1;

            let slot = length_code.decode(&mut bits)?;
            let length = (length_bases[slot] + bits.read(length_extra[slot] as u32)) as usize;
            let span = 1u64 << pair.0;
            let offset = (pair.1 as u64) << pair.0;
            if offset + span > out.len() as u64 {
                return Err("Invalid match offset");
            }
            if length > out_size - out.len() {
                return Err("Invalid match length");
            }
            let span = span as usize;
            let offset = offset as usize;
            for _ in 0..length {
                let pos = out.len();
                let b = out[pos - offset].wrapping_add(out[pos - span]).wrapping_sub(out[pos - offset - span]);
                out.push(b);
            }
        }
    }

    x86_filter(&mut out);
    Ok(out)
}

/// Restores the targets of the x86 instructions with relative addresses, the compressor replaces them with absolute ones.
/// Always done: the parts of the data which look like machine code are found the same way by both sides.
fn x86_filter(data: &mut [u8]) {
    let size = data.len() as i32;
    if size <= 17 {
        return;
    }
    // last position where each (16 bit) target was seen
    let mut last_target_usages = vec![-X86_ID_WINDOW_SIZE - 1; 65536];
    // last position which is likely to be machine code
    let mut last_x86_pos: i32 = -X86_MAX_TRANSLATION_OFFSET - 1;

    let mut i: i32 = 1;
    while i < size - 16 {
        let p = i as usize;
        let mut max_trans_offset = X86_MAX_TRANSLATION_OFFSET;
        let opcode_size: usize = match data[p] {
            // mov / lea with a relative address (x86_64)
            0x48 if data[p + 1] == 0x8B && (data[p + 2] == 0x05 || data[p + 2] == 0x0D) => 3,
            0x48 | 0x4C if data[p + 1] == 0x8D && data[p + 2] & 0x7 == 0x5 => 3,
            // call, done only when more sure that this is code
            0xE8 => {
                max_trans_offset /= 2;
                1
            }
            // jmp, not translated
            0xE9 => {
                i += 5;
                continue;
            }
            // lock add
            0xF0 if data[p + 1] == 0x83 && data[p + 2] == 0x05 => 3,
            // indirect call
            0xFF if data[p + 1] == 0x15 => 2,
            _ => {
                i += 1;
                continue;
            }
        };

        let operand = p + opcode_size;
        if i - last_x86_pos <= max_trans_offset {
            let value = u32::from_le_bytes([data[operand], data[operand + 1], data[operand + 2], data[operand + 3]]);
            data[operand..operand + 4].copy_from_slice(&value.wrapping_sub(i as u32).to_le_bytes());
        }
        let target = (i as u32).wrapping_add(data[operand] as u32 | ((data[operand + 1] as u32) << 8)) as u16 as usize;
        i += opcode_size as i32 + 3;
        if i - last_target_usages[target] <= X86_ID_WINDOW_SIZE {
            last_x86_pos = i;
        }
        last_target_usages[target] = i;
        i += 1;
    }
}
