//! Run length encoding of the Veeam backup files written with the "dedupe-friendly" compression level.
//! The bytes are stored as they are, two equal bytes after each other are followed by the length of the run
//! (the two bytes included). Longer runs are stored as more of these triplets.

/// Decompresses the data of one block (without the size it starts with), out_size is the size of the result
pub fn decompress(data: &[u8], out_size: usize) -> Result<Vec<u8>, &'static str> {
    let size = data.len();
    let mut pos = 0;
    let mut out: Vec<u8> = Vec::with_capacity(out_size);
    while pos < size {
        let b = data[pos];
        if pos + 1 < size && data[pos + 1] == b {
            if pos + 2 >= size {
                return Err("RLE data is truncated");
            }
            let count = data[pos + 2] as usize;
            if out.len() + count > out_size {
                return Err("RLE data is longer than the output");
            }
            out.resize(out.len() + count, b);
            pos += 3;
        } else {
            if out.len() >= out_size {
                return Err("RLE data is longer than the output");
            }
            out.push(b);
            pos += 1;
        }
    }
    if out.len() != out_size {
        return Err("RLE data does not match the size of the output");
    }
    Ok(out)
}
