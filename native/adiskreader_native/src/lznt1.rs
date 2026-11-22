//! LZNT1, the compression of NTFS files ([MS-XCA] 2.5).
//! The data is a list of chunks, each of them starts with a 16 bit header: the size of its data - 1 in the low 12 bits,
//! the highest bit is set when the data is compressed. A chunk holds 4096 bytes of the file, matches do not leave it.
//! A compressed chunk is made of groups of a flag byte and 8 items, an item is a literal byte or a 16 bit match:
//! offset - 1 in its high bits, length - 3 in the low ones, the more bytes the chunk has the more bits the offset takes.

use crate::huffman::copy_match;

const CHUNK_SIZE: usize = 4096;
const MIN_MATCH: usize = 3;

/// shift of the offset in a match by the count of bytes already in the chunk
fn offset_shift(written: usize) -> u32 {
    let mut shift = 12;
    let mut pos = written.saturating_sub(1);
    while pos >= 0x10 {
        shift -= 1;
        pos >>= 1;
    }
    shift
}

/// Decompresses the chunks of a compression unit of an NTFS file, out_size is the size of the result.
/// Each chunk gives 4096 bytes, the shorter ones and the end of the result are filled with zeroes.
pub fn decompress(data: &[u8], out_size: usize) -> Result<Vec<u8>, &'static str> {
    let size = data.len();
    let mut pos = 0;
    let mut out: Vec<u8> = Vec::with_capacity(out_size);
    while pos < size && out.len() < out_size {
        if pos + 2 > size {
            return Err("LZNT1 data is truncated");
        }
        let header = data[pos] as usize | (data[pos + 1] as usize) << 8;
        pos += 2;
        if header == 0 {
            // end of the chunks
            break;
        }
        let chunk_end = pos + (header & 0xFFF) + 1;
        if chunk_end > size {
            return Err("LZNT1 data is truncated");
        }
        let chunk_start = out.len();
        if header & 0x8000 == 0 {
            out.extend_from_slice(&data[pos..chunk_end]);
            pos = chunk_end;
        }
        while pos < chunk_end {
            let mut flags = data[pos];
            pos += 1;
            for _ in 0..8 {
                if pos >= chunk_end {
                    break;
                }
                if flags & 1 == 0 {
                    out.push(data[pos]);
                    pos += 1;
                } else {
                    if pos + 2 > chunk_end {
                        return Err("LZNT1 data is truncated");
                    }
                    let token = data[pos] as usize | (data[pos + 1] as usize) << 8;
                    pos += 2;
                    let written = out.len() - chunk_start;
                    if written > CHUNK_SIZE {
                        return Err("LZNT1 chunk is longer than 4096 bytes");
                    }
                    let shift = offset_shift(written);
                    let offset = (token >> shift) + 1;
                    if offset > written {
                        return Err("Invalid match offset");
                    }
                    copy_match(&mut out, offset, (token & ((1 << shift) - 1)) + MIN_MATCH)?;
                }
                flags >>= 1;
            }
        }
        let chunk_size = out.len() - chunk_start;
        if chunk_size > CHUNK_SIZE {
            return Err("LZNT1 chunk is longer than 4096 bytes");
        }
        if out.len() > out_size {
            return Err("LZNT1 data is longer than the output");
        }
        let fill = (CHUNK_SIZE - chunk_size).min(out_size - out.len());
        out.resize(out.len() + fill, 0);
    }
    out.resize(out_size, 0);
    Ok(out)
}
