//! LZ4 block format (no frame), as used by the blocks of the Veeam backup files.

use crate::huffman::copy_match;

const LZ4_MIN_MATCH: usize = 4;

fn read_length(data: &[u8], pos: &mut usize, mut length: usize) -> Result<usize, &'static str> {
    if length == 15 {
        loop {
            let b = *data.get(*pos).ok_or("LZ4 data is truncated")?;
            *pos += 1;
            length += b as usize;
            if b != 255 {
                break;
            }
        }
    }
    Ok(length)
}

/// Decompresses one LZ4 block, out_size is the size of the result
pub fn decompress(data: &[u8], out_size: usize) -> Result<Vec<u8>, &'static str> {
    let size = data.len();
    let mut pos = 0;
    let mut out: Vec<u8> = Vec::with_capacity(out_size);
    while pos < size {
        let token = data[pos];
        pos += 1;
        let length = read_length(data, &mut pos, (token >> 4) as usize)?;
        if length > size - pos {
            return Err("LZ4 data is truncated");
        }
        if out.len() + length > out_size {
            return Err("LZ4 literals are longer than the output");
        }
        out.extend_from_slice(&data[pos..pos + length]);
        pos += length;
        if pos == size {
            // the last sequence has literals only
            break;
        }
        if pos + 2 > size {
            return Err("LZ4 data is truncated");
        }
        let offset = data[pos] as usize | (data[pos + 1] as usize) << 8;
        pos += 2;
        let length = read_length(data, &mut pos, (token & 15) as usize)? + LZ4_MIN_MATCH;
        if out.len() + length > out_size {
            return Err("LZ4 match is longer than the output");
        }
        copy_match(&mut out, offset, length)?;
    }
    if out.len() != out_size {
        return Err("LZ4 data is shorter than the output");
    }
    Ok(out)
}
