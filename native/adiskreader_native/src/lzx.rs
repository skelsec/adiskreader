//! LZX as used by the chunks of WIM files and of the files compressed by WOF.
//! Differences from the LZX of the CAB files: every chunk is compressed on its own, there is no header before
//! the first block and the E8 translation is always done with a fixed file size.

use crate::bits::Bits;
use crate::huffman::{copy_match, HuffmanDecoder};

const LZX_NUM_CHARS: usize = 256;
const LZX_PRECODE_NUM_SYMBOLS: usize = 20;
const LZX_LENCODE_NUM_SYMBOLS: usize = 249;
const LZX_ALIGNEDCODE_NUM_SYMBOLS: usize = 8;
const LZX_NUM_LEN_HEADERS: usize = 8;
const LZX_NUM_PRIMARY_LENS: usize = 7;
const LZX_MIN_MATCH: usize = 2;
const LZX_OFFSET_ADJUSTMENT: usize = 2;
const LZX_DEFAULT_BLOCK_SIZE: usize = 32768;
const LZX_MAX_OFFSET_SLOTS: usize = 50;
const LZX_WIM_MAGIC_FILESIZE: i32 = 12000000;

const LZX_BLOCKTYPE_VERBATIM: u64 = 1;
const LZX_BLOCKTYPE_ALIGNED: u64 = 2;
const LZX_BLOCKTYPE_UNCOMPRESSED: u64 = 3;

struct Slots {
    extra_bits: [u32; LZX_MAX_OFFSET_SLOTS],
    base: [usize; LZX_MAX_OFFSET_SLOTS + 1],
}

fn offset_slots() -> Slots {
    let mut slots = Slots { extra_bits: [0; LZX_MAX_OFFSET_SLOTS], base: [0; LZX_MAX_OFFSET_SLOTS + 1] };
    for slot in 0..LZX_MAX_OFFSET_SLOTS {
        slots.extra_bits[slot] = ((slot / 2).saturating_sub(1)).min(17) as u32;
        slots.base[slot + 1] = slots.base[slot] + (1usize << slots.extra_bits[slot]);
    }
    slots
}

fn num_offset_slots(slots: &Slots, window_size: usize) -> usize {
    // the offset of window_size - 2 is not allowed by the format
    let max_offset = window_size as i64 - LZX_MIN_MATCH as i64 - 1;
    let mut count = 30;
    while count < LZX_MAX_OFFSET_SLOTS && max_offset >= (slots.base[count] - LZX_OFFSET_ADJUSTMENT) as i64 {
        count += 1;
    }
    count
}

/// Restores the relative targets of the x86 call instructions
fn undo_e8(data: &mut [u8]) {
    let size = data.len();
    if size <= 10 {
        return;
    }
    let mut pos = 0;
    while pos < size - 10 {
        if data[pos] != 0xE8 {
            pos += 1;
            continue;
        }
        let target = i32::from_le_bytes([data[pos + 1], data[pos + 2], data[pos + 3], data[pos + 4]]);
        let ipos = pos as i32;
        let mut fixed = None;
        if target >= 0 {
            if target < LZX_WIM_MAGIC_FILESIZE {
                fixed = Some(target.wrapping_sub(ipos));
            }
        } else if target >= -ipos {
            fixed = Some(target.wrapping_add(LZX_WIM_MAGIC_FILESIZE));
        }
        if let Some(value) = fixed {
            data[pos + 1..pos + 5].copy_from_slice(&value.to_le_bytes());
        }
        pos += 5;
    }
}

#[inline(always)]
fn read(bits: &mut Bits, count: u32) -> Result<u64, &'static str> {
    while bits.bitcnt < count {
        if bits.pos > bits.data.len() + 4 {
            return Err("LZX data is truncated");
        }
        bits.refill();
    }
    Ok(bits.take(count))
}

#[inline(always)]
fn read_symbol(bits: &mut Bits, decoder: &HuffmanDecoder) -> Result<usize, &'static str> {
    while bits.bitcnt < 16 {
        bits.refill();
    }
    if decoder.max_len == 0 {
        return Err("LZX symbol of an empty code");
    }
    let (symbol, codelen) = decoder.decode(bits.bitbuf, bits.bitcnt)?;
    bits.take(codelen);
    Ok(symbol)
}

/// Updates lens, they are stored as differences from the lengths of the previous block
fn read_lens(bits: &mut Bits, lens: &mut [u8]) -> Result<(), &'static str> {
    let mut prelens = [0u8; LZX_PRECODE_NUM_SYMBOLS];
    for len in prelens.iter_mut() {
        *len = read(bits, 4)? as u8;
    }
    let precode = HuffmanDecoder::new(&prelens)?;
    let end = lens.len();
    let mut i = 0;
    while i < end {
        let mut presym = read_symbol(bits, &precode)?;
        if presym < 17 {
            lens[i] = ((lens[i] as usize + 17 - presym) % 17) as u8;
            i += 1;
            continue;
        }
        let run;
        let value;
        if presym == 17 {
            run = 4 + read(bits, 4)? as usize;
            value = 0;
        } else if presym == 18 {
            run = 20 + read(bits, 5)? as usize;
            value = 0;
        } else {
            run = 4 + read(bits, 1)? as usize;
            presym = read_symbol(bits, &precode)?;
            if presym > 17 {
                return Err("Invalid LZX code length");
            }
            value = ((lens[i] as usize + 17 - presym) % 17) as u8;
        }
        let run = run.min(end - i);
        lens[i..i + run].fill(value);
        i += run;
    }
    Ok(())
}

/// Decompresses one chunk, out_size is the size of the result, window_size is the chunk size of the WIM
pub fn decompress(data: &[u8], out_size: usize, window_size: usize) -> Result<Vec<u8>, &'static str> {
    let slots = offset_slots();
    let num_main_syms = LZX_NUM_CHARS + num_offset_slots(&slots, window_size) * LZX_NUM_LEN_HEADERS;
    let mut main_lens = vec![0u8; num_main_syms];
    let mut len_lens = [0u8; LZX_LENCODE_NUM_SYMBOLS];
    let mut recent = [1usize, 1, 1];
    let size = data.len();
    let mut bits = Bits::new(data, 0);
    let mut out: Vec<u8> = Vec::with_capacity(out_size);

    while out.len() < out_size {
        let block_type = read(&mut bits, 3)?;
        let mut block_size;
        if read(&mut bits, 1)? == 1 {
            block_size = LZX_DEFAULT_BLOCK_SIZE;
        } else {
            block_size = read(&mut bits, 16)? as usize;
            if window_size >= 65536 {
                block_size = (block_size << 8) | read(&mut bits, 8)? as usize;
            }
        }
        if block_size == 0 || block_size > out_size - out.len() {
            return Err("Invalid LZX block size");
        }
        let block_end = out.len() + block_size;

        if block_type == LZX_BLOCKTYPE_UNCOMPRESSED {
            // skips to the next 16 bit boundary, a whole word if already there
            let consumed = bits.pos * 8 - bits.bitcnt as usize;
            let mut pos = (consumed / 16 + 1) * 2;
            if pos + 12 + block_size > size {
                return Err("LZX data is truncated");
            }
            for i in 0..3 {
                recent[i] = u32::from_le_bytes([data[pos], data[pos + 1], data[pos + 2], data[pos + 3]]) as usize;
                if recent[i] == 0 {
                    return Err("Invalid LZX match offset");
                }
                pos += 4;
            }
            out.extend_from_slice(&data[pos..pos + block_size]);
            // the data is padded to 16 bits
            bits.pos = (pos + block_size + 1) / 2 * 2;
            bits.bitbuf = 0;
            bits.bitcnt = 0;
            continue;
        }
        if block_type != LZX_BLOCKTYPE_VERBATIM && block_type != LZX_BLOCKTYPE_ALIGNED {
            return Err("Invalid LZX block type");
        }

        let mut aligned = None;
        if block_type == LZX_BLOCKTYPE_ALIGNED {
            let mut aligned_lens = [0u8; LZX_ALIGNEDCODE_NUM_SYMBOLS];
            for len in aligned_lens.iter_mut() {
                *len = read(&mut bits, 3)? as u8;
            }
            aligned = Some(HuffmanDecoder::new(&aligned_lens)?);
        }
        read_lens(&mut bits, &mut main_lens[..LZX_NUM_CHARS])?;
        read_lens(&mut bits, &mut main_lens[LZX_NUM_CHARS..])?;
        read_lens(&mut bits, &mut len_lens)?;
        let main = HuffmanDecoder::new(&main_lens)?;
        let lencode = HuffmanDecoder::new(&len_lens)?;
        if main.max_len == 0 {
            return Err("Empty LZX main code");
        }

        while out.len() < block_end {
            if bits.bitcnt < 16 {
                bits.refill();
            }
            let (symbol, codelen) = main.decode(bits.bitbuf, bits.bitcnt)?;
            bits.take(codelen);
            if symbol < 256 {
                out.push(symbol as u8);
                continue;
            }

            let symbol = symbol - 256;
            let mut length = symbol & 7;
            let slot = symbol >> 3;
            if length == LZX_NUM_PRIMARY_LENS {
                length += read_symbol(&mut bits, &lencode)?;
            }
            length += LZX_MIN_MATCH;

            let offset;
            if slot == 0 {
                offset = recent[0];
            } else if slot == 1 {
                offset = recent[1];
                recent[1] = recent[0];
                recent[0] = offset;
            } else if slot == 2 {
                offset = recent[2];
                recent[2] = recent[0];
                recent[0] = offset;
            } else {
                let extra = slots.extra_bits[slot];
                let mut value = slots.base[slot] - LZX_OFFSET_ADJUSTMENT;
                match &aligned {
                    Some(decoder) if extra >= 3 => {
                        value += (read(&mut bits, extra - 3)? as usize) << 3;
                        value += read_symbol(&mut bits, decoder)?;
                    }
                    _ => {
                        if extra > 0 {
                            value += read(&mut bits, extra)? as usize;
                        }
                    }
                }
                offset = value;
                recent[2] = recent[1];
                recent[1] = recent[0];
                recent[0] = offset;
            }

            if bits.pos > size + 8 {
                return Err("LZX data is truncated");
            }
            if offset == 0 || offset > out.len() {
                return Err("Invalid match offset");
            }
            if length > block_end - out.len() {
                return Err("LZX match is longer than the block");
            }
            copy_match(&mut out, offset, length)?;
        }
    }

    undo_e8(&mut out);
    Ok(out)
}
