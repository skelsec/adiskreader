//! The bits of the input, stored in 16 bit little endian words and read MSB first.

pub struct Bits<'a> {
    pub data: &'a [u8],
    /// position of the next word, in bytes
    pub pos: usize,
    pub bitbuf: u64,
    pub bitcnt: u32,
}

impl<'a> Bits<'a> {
    pub fn new(data: &'a [u8], pos: usize) -> Bits<'a> {
        Bits { data, pos, bitbuf: 0, bitcnt: 0 }
    }

    /// zero after the end of the data
    #[inline(always)]
    pub fn byte(&self, pos: usize) -> u64 {
        if pos < self.data.len() { self.data[pos] as u64 } else { 0 }
    }

    /// Adds the next word to the buffer, the words after the end of the data are zero
    #[inline(always)]
    pub fn refill(&mut self) {
        let word = if self.pos + 2 <= self.data.len() {
            self.data[self.pos] as u64 | ((self.data[self.pos + 1] as u64) << 8)
        } else {
            self.byte(self.pos) | (self.byte(self.pos + 1) << 8)
        };
        self.bitbuf = (self.bitbuf << 16) | word;
        self.pos += 2;
        self.bitcnt += 16;
    }

    /// Removes count bits from the buffer, they must be in it
    #[inline(always)]
    pub fn take(&mut self, count: u32) -> u64 {
        self.bitcnt -= count;
        let res = self.bitbuf >> self.bitcnt;
        self.bitbuf &= (1u64 << self.bitcnt) - 1;
        res
    }
}
