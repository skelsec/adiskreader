//! Decoder of canonical Huffman codes read MSB first.

pub struct HuffmanDecoder {
    /// indexed with the next table_bits bits of the input, the entries are (symbol << 5) | code length, 0 if there is no such code
    table: Vec<u16>,
    pub table_bits: u32,
    /// 0 for an empty code, valid until a symbol is read with it
    pub max_len: u32,
}

impl HuffmanDecoder {
    /// lens: length of the code of each symbol (0: not used), at most 16
    pub fn new(lens: &[u8]) -> Result<HuffmanDecoder, &'static str> {
        let max_len = lens.iter().copied().max().unwrap_or(0) as u32;
        if max_len > 16 {
            return Err("Invalid Huffman code");
        }
        let table_bits = max_len.max(1);
        let mut table = vec![0u16; 1 << table_bits];
        if max_len == 0 {
            return Ok(HuffmanDecoder { table, table_bits, max_len });
        }

        let mut code: usize = 0;
        for codelen in 1..=max_len {
            let shift = table_bits - codelen;
            let size = 1usize << shift;
            for (symbol, &len) in lens.iter().enumerate() {
                if len as u32 != codelen {
                    continue;
                }
                let start = code << shift;
                if start + size > table.len() {
                    return Err("Invalid Huffman code");
                }
                let entry = ((symbol as u16) << 5) | codelen as u16;
                table[start..start + size].fill(entry);
                code += 1;
            }
            if code > (1 << codelen) {
                return Err("Invalid Huffman code");
            }
            code <<= 1;
        }
        Ok(HuffmanDecoder { table, table_bits, max_len })
    }

    /// Returns the symbol and the length of the code at the top of the bitcnt (>= 16) bits of bitbuf
    #[inline(always)]
    pub fn decode(&self, bitbuf: u64, bitcnt: u32) -> Result<(usize, u32), &'static str> {
        let entry = self.table[((bitbuf >> (bitcnt - self.table_bits)) as usize) & ((1 << self.table_bits) - 1)];
        if entry == 0 {
            return Err("Invalid Huffman code in the input");
        }
        Ok(((entry >> 5) as usize, (entry & 0x1F) as u32))
    }
}

/// Appends length bytes starting offset bytes back, the two ranges can overlap
#[inline(always)]
pub fn copy_match(out: &mut Vec<u8>, offset: usize, length: usize) -> Result<(), &'static str> {
    if offset == 0 || offset > out.len() {
        return Err("Invalid match offset");
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
    Ok(())
}
