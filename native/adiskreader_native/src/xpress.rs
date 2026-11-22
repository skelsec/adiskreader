//! XPRESS with Huffman coding (MS-XCA 2.2), as used by the chunks of WIM files and of the files compressed by WOF.

use crate::bits::Bits;
use crate::huffman::{copy_match, HuffmanDecoder};

const XPRESS_NUM_SYMBOLS: usize = 512;
const XPRESS_MIN_MATCH: usize = 3;

/// Decompresses one chunk which starts with its Huffman code lengths, out_size is the size of the result
pub fn decompress(data: &[u8], out_size: usize) -> Result<Vec<u8>, &'static str> {
    if data.len() < XPRESS_NUM_SYMBOLS / 2 {
        return Err("XPRESS data is too short");
    }
    let mut lens = [0u8; XPRESS_NUM_SYMBOLS];
    for i in 0..XPRESS_NUM_SYMBOLS / 2 {
        lens[2 * i] = data[i] & 0xF;
        lens[2 * i + 1] = data[i] >> 4;
    }
    let decoder = HuffmanDecoder::new(&lens)?;

    // the bits are in 16 bit little endian words, the extra bytes of the match lengths are between them
    let size = data.len();
    let mut bits = Bits::new(data, XPRESS_NUM_SYMBOLS / 2);
    let mut out: Vec<u8> = Vec::with_capacity(out_size);
    while out.len() < out_size {
        if bits.bitcnt < 16 {
            bits.refill();
            if bits.pos > size + 4 {
                return Err("XPRESS data is truncated");
            }
        }
        let (symbol, codelen) = decoder.decode(bits.bitbuf, bits.bitcnt)?;
        bits.take(codelen);
        if symbol < 256 {
            out.push(symbol as u8);
            continue;
        }

        let mut length = symbol & 0xF;
        let offset_bits = ((symbol >> 4) & 0xF) as u32;
        if bits.bitcnt < 16 {
            bits.refill();
        }
        let offset = (1usize << offset_bits) | bits.take(offset_bits) as usize;
        if length == 0xF {
            length += bits.byte(bits.pos) as usize;
            bits.pos += 1;
            if length == 0xF + 0xFF {
                length = (bits.byte(bits.pos) | (bits.byte(bits.pos + 1) << 8)) as usize;
                bits.pos += 2;
            }
        }
        if bits.pos > size + 4 {
            return Err("XPRESS data is truncated");
        }
        length += XPRESS_MIN_MATCH;
        if offset > out.len() {
            return Err("Invalid match offset");
        }
        if length > out_size - out.len() {
            return Err("XPRESS match is longer than the output");
        }
        copy_match(&mut out, offset, length)?;
    }
    Ok(out)
}
