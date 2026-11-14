//! The panel's video frames as its encoder takes them: YUV 4:2:0, turned into the panel's
//! framebuffer, kept from frame to frame and made anew only in the blocks that changed, byte
//! for byte as devices/yuv.py makes them (Pillow's YCbCr, BT.601; the colours of each two by two
//! pixels their average; the panel's range).

use crate::tables::{CB_B, CB_G, CB_R, CR_B, CR_G, CR_R, LIMITED_C, LIMITED_Y, Y_B, Y_G, Y_R};

/// How the drawn frame is turned into the panel's framebuffer (yuv.py's ``rotate``).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Turn {
    /// As it is.
    None,
    /// Half a turn (180).
    Half,
    /// A quarter turn clockwise (270, ffmpeg's transpose=1): (x, y) -> (height - 1 - y, x).
    Quarter,
}

impl Turn {
    pub fn of(degrees: u32) -> Option<Turn> {
        match degrees {
            0 => Some(Turn::None),
            180 => Some(Turn::Half),
            270 => Some(Turn::Quarter),
            _ => None,
        }
    }
}

/// The blocks a frame of `width` x `height` is compared and converted in: 16 pixels, or 8
/// where the frame is not made of whole blocks of 16 (yuv.block_of).
pub fn block_of(width: usize, height: usize) -> Option<usize> {
    [16, 8]
        .into_iter()
        .find(|b| width.is_multiple_of(*b) && height.is_multiple_of(*b))
}

/// x264's macroblock flag for a block that stayed the same since the frame before.
pub const CONSTANT: u8 = 1;

pub struct Yuv {
    pub width: usize,
    pub height: usize,
    pub turn: Turn,
    pub full: bool,
    pub block: usize,
    /// The turned frame's size, as the encoder takes it.
    pub turned: (usize, usize),
    pub y: Vec<u8>,
    pub u: Vec<u8>,
    pub v: Vec<u8>,
    /// The frame as it was put, three bytes a pixel: kept only where asked for
    /// ([`Yuv::keeping_rgb`], --dump-rgb). Else the planes are the frame, and each block is
    /// known by a hash of its pixels as they were last put ([`block_hash`]): a 12.3" frame's
    /// 5400 blocks in 43 KB instead of its pixels again in 4.1 MB, the boxes then of whole
    /// blocks (as the scenes and Libre Panel's own frames make them).
    rgb: Vec<u8>,
    hashes: Vec<u64>,
    rows: usize,
    cols: usize,
    changed: Vec<bool>,
    whole: bool,
}

impl Yuv {
    /// A frame whose pixels are not kept, but its blocks' hashes (the boxes put: of whole
    /// blocks).
    pub fn new(width: usize, height: usize, turn: Turn, full: bool) -> Result<Yuv, String> {
        Yuv::make(width, height, turn, full, false)
    }

    /// A frame that keeps its pixels as they were put ([`Yuv::rgb`]: for --dump-rgb); its
    /// boxes may be of any size.
    pub fn keeping_rgb(width: usize, height: usize, turn: Turn, full: bool) -> Result<Yuv, String> {
        Yuv::make(width, height, turn, full, true)
    }

    fn make(
        width: usize,
        height: usize,
        turn: Turn,
        full: bool,
        keep: bool,
    ) -> Result<Yuv, String> {
        let block = block_of(width, height)
            .ok_or_else(|| format!("no blocks of 8 or 16 pixels make {width}x{height}"))?;
        let turned = if turn == Turn::Quarter {
            (height, width)
        } else {
            (width, height)
        };
        let (rows, cols) = (height / block, width / block);
        Ok(Yuv {
            width,
            height,
            turn,
            full,
            block,
            turned,
            y: vec![if full { 0 } else { 16 }; turned.0 * turned.1],
            u: vec![128; (turned.0 / 2) * (turned.1 / 2)],
            v: vec![128; (turned.0 / 2) * (turned.1 / 2)],
            rgb: if keep {
                vec![0; width * height * 3]
            } else {
                Vec::new()
            },
            // (as the pixels kept start: black, which a block put black did not change from)
            hashes: if keep {
                Vec::new()
            } else {
                vec![block_hash(&vec![0; block * block * 3], 0, block * 3, block); rows * cols]
            },
            rows,
            cols,
            changed: vec![false; rows * cols],
            whole: true,
        })
    }

    /// The frame as it was put, three bytes a pixel, row by row (before it is turned); empty
    /// where it is not kept ([`Yuv::keeping_rgb`]).
    pub fn rgb(&self) -> &[u8] {
        &self.rgb
    }

    /// The encoder's macroblocks across and down (the turned frame, in blocks of 16).
    pub fn macroblocks(&self) -> (usize, usize) {
        (self.turned.0.div_ceil(16), self.turned.1.div_ceil(16))
    }

    /// A part of the new frame that may have changed: the box (x0, y0)-(x1, y1) and its pixels,
    /// three bytes each, row by row. Where it differs from the frame before, its blocks are
    /// marked for :meth:`finish` (and, where the frame's pixels are not kept, made anew in the
    /// planes now: the box must then be of whole blocks).
    pub fn put(
        &mut self,
        x0: usize,
        y0: usize,
        x1: usize,
        y1: usize,
        pixels: &[u8],
    ) -> Result<(), String> {
        if x0 >= x1 || y0 >= y1 || x1 > self.width || y1 > self.height {
            return Err(format!("the box {x0},{y0},{x1},{y1} is not in the frame"));
        }
        let wide = x1 - x0;
        if pixels.len() != wide * (y1 - y0) * 3 {
            return Err("the box's pixels are not as many as it holds".into());
        }
        let block = self.block;
        if self.rgb.is_empty() {
            return self.put_blocks(x0, y0, x1, y1, pixels);
        }
        for row in y0..y1 {
            let src = &pixels[(row - y0) * wide * 3..(row - y0 + 1) * wide * 3];
            let at = (row * self.width + x0) * 3;
            let dst = &mut self.rgb[at..at + wide * 3];
            if !self.whole {
                let line = (row / block) * self.cols;
                let mut x = x0;
                while x < x1 {
                    let end = ((x / block + 1) * block).min(x1);
                    let (a, b) = ((x - x0) * 3, (end - x0) * 3);
                    if src[a..b] != dst[a..b] {
                        self.changed[line + x / block] = true;
                    }
                    x = end;
                }
            }
            dst.copy_from_slice(src);
        }
        Ok(())
    }

    /// The blocks of a box of whole blocks, the frame's pixels not kept: each whose pixels hash
    /// otherwise than when it was last put (every one, for the first frame) is made anew in
    /// the planes and marked.
    fn put_blocks(
        &mut self,
        x0: usize,
        y0: usize,
        x1: usize,
        y1: usize,
        pixels: &[u8],
    ) -> Result<(), String> {
        let n = self.block;
        let of_blocks = |v: usize| v.is_multiple_of(n);
        if !(of_blocks(x0) && of_blocks(y0) && of_blocks(x1) && of_blocks(y1)) {
            return Err(format!(
                "the box {x0},{y0},{x1},{y1} is not of whole blocks of {n} (the frame's pixels \
                 are not kept)"
            ));
        }
        let line = (x1 - x0) * 3;
        for r in y0 / n..y1 / n {
            for c in x0 / n..x1 / n {
                let first = (r * n - y0) * line + (c * n - x0) * 3;
                let hash = block_hash(pixels, first, line, n);
                let index = r * self.cols + c;
                if !self.whole && self.hashes[index] == hash {
                    continue;
                }
                self.hashes[index] = hash;
                self.changed[index] = true;
                let (light, cb, cr) = colours(pixels, first, line, n, self.full);
                self.lay_block(r, c, &light, &cb, &cr);
            }
        }
        Ok(())
    }

    /// Make the YUV anew in the blocks that changed (where the frame's pixels are kept: else
    /// [`Yuv::put`] made them) and say which of the encoder's macroblocks stayed the same
    /// (`mb`: CONSTANT, else 0). The number of blocks made anew.
    pub fn finish(&mut self, mb: &mut [u8]) -> usize {
        if self.whole {
            self.changed.iter_mut().for_each(|c| *c = true);
            self.whole = false;
        }
        mb.iter_mut().for_each(|m| *m = CONSTANT);
        let (mb_cols, _) = self.macroblocks();
        let kept = !self.rgb.is_empty();
        let mut count = 0;
        for r in 0..self.rows {
            for c in 0..self.cols {
                let index = r * self.cols + c;
                if !self.changed[index] {
                    continue;
                }
                self.changed[index] = false;
                count += 1;
                let (tr, tc) = if kept {
                    self.convert(r, c)
                } else {
                    self.turned_at(r, c)
                };
                let n = self.block;
                for mr in (tr * n) / 16..=((tr + 1) * n - 1) / 16 {
                    for mc in (tc * n) / 16..=((tc + 1) * n - 1) / 16 {
                        mb[mr * mb_cols + mc] = 0;
                    }
                }
            }
        }
        count
    }

    /// Convert the block at (r, c) of the drawn frame, as kept, and lay it, turned, into the
    /// planes: where it lies once turned (block row and column).
    fn convert(&mut self, r: usize, c: usize) -> (usize, usize) {
        let n = self.block;
        let line = self.width * 3;
        let first = (r * n * self.width + c * n) * 3;
        let (light, cb, cr) = colours(&self.rgb, first, line, n, self.full);
        self.lay_block(r, c, &light, &cb, &cr)
    }

    /// Where the block at (r, c) of the drawn frame lies once turned (block row and column).
    fn turned_at(&self, r: usize, c: usize) -> (usize, usize) {
        match self.turn {
            Turn::None => (r, c),
            Turn::Half => (self.rows - 1 - r, self.cols - 1 - c),
            Turn::Quarter => (c, self.rows - 1 - r),
        }
    }

    /// The block (r, c)'s colours (its light, and its two by two pixels' blue and red) laid,
    /// turned, into the planes; where it lies once turned.
    fn lay_block(
        &mut self,
        r: usize,
        c: usize,
        light: &[u8; 256],
        cb: &[u8; 64],
        cr: &[u8; 64],
    ) -> (usize, usize) {
        let n = self.block;
        let half = n / 2;
        let (tr, tc) = self.turned_at(r, c);
        let turn = self.turn;
        let stride = self.turned.0;
        lay(&mut self.y, stride, tr * n, tc * n, n, light, turn);
        lay(
            &mut self.u,
            stride / 2,
            tr * half,
            tc * half,
            half,
            cb,
            turn,
        );
        lay(
            &mut self.v,
            stride / 2,
            tr * half,
            tc * half,
            half,
            cr,
            turn,
        );
        (tr, tc)
    }
}

/// A block's colours as Pillow's YCbCr makes them: its light, and the blue and red of each two
/// by two of its pixels (their average), in the panel's range. `src` holds the pixels three
/// bytes each, the block's first at `first`, its rows `line` bytes apart. Eight pixels at a
/// time where the processor has AVX2 ([`colours_avx2`], the tables as the formulas they are),
/// else a pixel at a time through the tables: the same bytes.
fn colours(
    src: &[u8],
    first: usize,
    line: usize,
    n: usize,
    full: bool,
) -> ([u8; 256], [u8; 64], [u8; 64]) {
    #[cfg(target_arch = "x86_64")]
    if n.is_multiple_of(8) && std::arch::is_x86_feature_detected!("avx2") {
        // SAFETY: the processor has AVX2 (asked just now); colours_avx2 reads the rows
        // colours_by_table reads, through slices of them
        return unsafe { colours_avx2(src, first, line, n, full) };
    }
    colours_by_table(src, first, line, n, full)
}

/// [`colours`] a pixel at a time, through Pillow's tables.
fn colours_by_table(
    src: &[u8],
    first: usize,
    line: usize,
    n: usize,
    full: bool,
) -> ([u8; 256], [u8; 64], [u8; 64]) {
    let mut light = [0u8; 256];
    let mut blue = [0i32; 256];
    let mut red = [0i32; 256];
    for i in 0..n {
        let row = &src[first + i * line..first + i * line + n * 3];
        let out = i * n..i * n + n;
        let (l, b) = (&mut light[out.clone()], &mut blue[out.clone()]);
        for (((px, l), b), rd) in row.chunks_exact(3).zip(l).zip(b).zip(&mut red[out]) {
            let (pr, pg, pb) = (px[0] as usize, px[1] as usize, px[2] as usize);
            let y = (((Y_R[pr] + Y_G[pg] + Y_B[pb]) >> 6) & 255) as usize;
            *l = if full { y as u8 } else { LIMITED_Y[y] };
            *b = (((CB_R[pr] + CB_G[pg] + CB_B[pb]) >> 6) + 128) & 255;
            *rd = (((CR_R[pr] + CR_G[pg] + CR_B[pb]) >> 6) + 128) & 255;
        }
    }
    let (cb, cr) = averaged(&blue, &red, n, full);
    (light, cb, cr)
}

/// The blue and red of each two by two pixels of a block (`blue`, `red`: each pixel's), their
/// average in the panel's range.
fn averaged(blue: &[i32; 256], red: &[i32; 256], n: usize, full: bool) -> ([u8; 64], [u8; 64]) {
    let half = n / 2;
    let mut cb = [0u8; 64];
    let mut cr = [0u8; 64];
    for i in 0..half {
        let (top, low) = (2 * i * n, (2 * i + 1) * n);
        let (b0, b1) = (&blue[top..top + n], &blue[low..low + n]);
        let (r0, r1) = (&red[top..top + n], &red[low..low + n]);
        let (ob, or) = (
            &mut cb[i * half..i * half + half],
            &mut cr[i * half..i * half + half],
        );
        for j in 0..half {
            let k = 2 * j;
            let sum_b = b0[k] + b0[k + 1] + b1[k] + b1[k + 1];
            let sum_r = r0[k] + r0[k + 1] + r1[k] + r1[k + 1];
            let (b, r) = (((sum_b + 2) >> 2) as usize, ((sum_r + 2) >> 2) as usize);
            ob[j] = if full { b as u8 } else { LIMITED_C[b] };
            or[j] = if full { r as u8 } else { LIMITED_C[r] };
        }
    }
    (cb, cr)
}

/// Pillow's tables as the integer formulas they are, each `(value * M + B) >> S`: the same
/// number for every one of the 256 values (`the_formulas_are_the_tables`). Those whose entries
/// are below 0 are the formula's negative, and 0 for 0 (`max(.., 0)`).
const Y_R_F: (i32, i32, i32) = (39191, 962, 11);
const Y_G_F: (i32, i32, i32) = (153879, 1983, 12);
const Y_B_F: (i32, i32, i32) = (59769, 4055, 13);
const CB_R_F: (i32, i32, i32) = (5529, -216, 9); // (negative)
const CB_G_F: (i32, i32, i32) = (10855, -299, 9); // (negative)
const CR_G_F: (i32, i32, i32) = (109757, -2042, 12); // (negative)
const CR_B_F: (i32, i32, i32) = (21315, -2065, 12); // (negative)
const LIMITED_Y_F: (i32, i32, i32) = (1759, 33774, 11);
const CB_B_TIMES: i32 = 32; // CB_B and CR_R: the value times 32

/// [`colours`] eight pixels at a time (AVX2): each pixel's three bytes apart into lanes, the
/// tables' formulas in them, the light packed back into bytes; the two by two average as
/// [`colours_by_table`] takes it.
#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn colours_avx2(
    src: &[u8],
    first: usize,
    line: usize,
    n: usize,
    full: bool,
) -> ([u8; 256], [u8; 64], [u8; 64]) {
    use std::arch::x86_64::*;
    let mut light = [0u8; 256];
    let mut blue = [0i32; 256];
    let mut red = [0i32; 256];
    // (eight pixels' bytes: the first sixteen and the eight after them, each band's apart)
    let r_lo = _mm_setr_epi8(0, 3, 6, 9, 12, 15, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1);
    let r_hi = _mm_setr_epi8(-1, -1, -1, -1, -1, -1, 2, 5, -1, -1, -1, -1, -1, -1, -1, -1);
    let g_lo = _mm_setr_epi8(1, 4, 7, 10, 13, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1);
    let g_hi = _mm_setr_epi8(-1, -1, -1, -1, -1, 0, 3, 6, -1, -1, -1, -1, -1, -1, -1, -1);
    let b_lo = _mm_setr_epi8(2, 5, 8, 11, 14, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1);
    let b_hi = _mm_setr_epi8(-1, -1, -1, -1, -1, 1, 4, 7, -1, -1, -1, -1, -1, -1, -1, -1);
    let zero = _mm256_setzero_si256();
    let byte = _mm256_set1_epi32(255);
    let middle = _mm256_set1_epi32(128);
    let formula = |v: __m256i, (m, b, s): (i32, i32, i32)| {
        let t = _mm256_add_epi32(
            _mm256_mullo_epi32(v, _mm256_set1_epi32(m)),
            _mm256_set1_epi32(b),
        );
        _mm256_sra_epi32(t, _mm_cvtsi32_si128(s))
    };
    for i in 0..n {
        let row = &src[first + i * line..first + i * line + n * 3];
        for j in 0..n / 8 {
            let px = &row[j * 24..j * 24 + 24];
            let lo = _mm_loadu_si128(px.as_ptr().cast());
            let hi = _mm_loadl_epi64(px[16..].as_ptr().cast());
            let band = |l: __m128i, h: __m128i| {
                let bytes = _mm_or_si128(_mm_shuffle_epi8(lo, l), _mm_shuffle_epi8(hi, h));
                _mm256_cvtepu8_epi32(bytes)
            };
            let (r, g, b) = (band(r_lo, r_hi), band(g_lo, g_hi), band(b_lo, b_hi));
            let sum = _mm256_add_epi32(
                _mm256_add_epi32(formula(r, Y_R_F), formula(g, Y_G_F)),
                formula(b, Y_B_F),
            );
            let mut y = _mm256_and_si256(_mm256_srai_epi32(sum, 6), byte);
            if !full {
                y = formula(y, LIMITED_Y_F);
            }
            let times = _mm256_set1_epi32(CB_B_TIMES);
            let cb = _mm256_sub_epi32(
                _mm256_sub_epi32(
                    _mm256_mullo_epi32(b, times),
                    _mm256_max_epi32(formula(r, CB_R_F), zero),
                ),
                _mm256_max_epi32(formula(g, CB_G_F), zero),
            );
            let cr = _mm256_sub_epi32(
                _mm256_sub_epi32(
                    _mm256_mullo_epi32(r, times),
                    _mm256_max_epi32(formula(g, CR_G_F), zero),
                ),
                _mm256_max_epi32(formula(b, CR_B_F), zero),
            );
            let cb = _mm256_and_si256(_mm256_add_epi32(_mm256_srai_epi32(cb, 6), middle), byte);
            let cr = _mm256_and_si256(_mm256_add_epi32(_mm256_srai_epi32(cr, 6), middle), byte);
            let at = i * n + j * 8;
            _mm256_storeu_si256(blue[at..at + 8].as_mut_ptr().cast(), cb);
            _mm256_storeu_si256(red[at..at + 8].as_mut_ptr().cast(), cr);
            // (the light, 0 to 255 in each lane, into eight bytes)
            let words = _mm256_packus_epi32(y, y);
            let bytes = _mm256_packus_epi16(words, words);
            let both = _mm_unpacklo_epi32(
                _mm256_castsi256_si128(bytes),
                _mm256_extracti128_si256::<1>(bytes),
            );
            _mm_storel_epi64(light[at..at + 8].as_mut_ptr().cast(), both);
        }
    }
    let (cb, cr) = averaged(&blue, &red, n, full);
    (light, cb, cr)
}

/// A hash of the n x n block of three-byte pixels at `first` in `src` (rows `line` bytes apart):
/// eight bytes at a time, each step a bijection of the state, so that two blocks that differ
/// in one place never hash alike (and others only by a chance of one in 2^64).
fn block_hash(src: &[u8], first: usize, line: usize, n: usize) -> u64 {
    const K: u64 = 0x9E37_79B9_7F4A_7C15;
    let mut h: u64 = 0x243F_6A88_85A3_08D3;
    for i in 0..n {
        let row = &src[first + i * line..first + i * line + n * 3];
        let mut words = row.chunks_exact(8);
        for word in &mut words {
            let w = u64::from_le_bytes(word.try_into().expect("eight bytes"));
            h = (h ^ w).wrapping_mul(K).rotate_left(27);
        }
        for &b in words.remainder() {
            h = (h ^ u64::from(b)).wrapping_mul(K).rotate_left(27);
        }
    }
    h ^ (h >> 31)
}

/// The n x n `block` (row by row) into `plane` at (`top`, `left`), turned as `turn` says.
fn lay(
    plane: &mut [u8],
    stride: usize,
    top: usize,
    left: usize,
    n: usize,
    block: &[u8],
    turn: Turn,
) {
    let block = &block[..n * n];
    for a in 0..n {
        let row = &mut plane[(top + a) * stride + left..(top + a) * stride + left + n];
        match turn {
            Turn::None => row.copy_from_slice(&block[a * n..a * n + n]),
            Turn::Half => {
                let from = &block[(n - 1 - a) * n..(n - a) * n];
                for (out, value) in row.iter_mut().zip(from.iter().rev()) {
                    *out = *value;
                }
            }
            Turn::Quarter => {
                // (column a of the block, from its last row up)
                for (b, out) in row.iter_mut().enumerate() {
                    *out = block[(n - 1 - b) * n + a];
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_and_turned_size() {
        assert_eq!(block_of(1920, 720), Some(16));
        assert_eq!(block_of(1024, 600), Some(8));
        let yuv = Yuv::new(1920, 720, Turn::Quarter, false).unwrap();
        assert_eq!(yuv.turned, (720, 1920));
        assert_eq!(yuv.macroblocks(), (45, 120));
    }

    #[test]
    fn white_and_black_in_the_panels_range() {
        let mut yuv = Yuv::new(32, 32, Turn::None, false).unwrap();
        let white = vec![255u8; 32 * 32 * 3];
        yuv.put(0, 0, 32, 32, &white).unwrap();
        let mut mb = vec![0u8; 4];
        assert_eq!(yuv.finish(&mut mb), 4);
        assert!(yuv.y.iter().all(|&v| v == 235) && yuv.u.iter().all(|&v| v == 128));
        assert!(mb.iter().all(|&m| m == 0));
        // the same again: nothing changed, every macroblock constant
        yuv.put(0, 0, 32, 32, &white).unwrap();
        assert_eq!(yuv.finish(&mut mb), 0);
        assert!(mb.iter().all(|&m| m == CONSTANT));
    }

    /// A small generator of the same numbers every time (xorshift).
    struct Numbers(u64);

    impl Numbers {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }

        fn below(&mut self, n: usize) -> usize {
            (self.next() % n as u64) as usize
        }
    }

    #[test]
    fn the_planes_and_the_constant_macroblocks_are_the_same_with_the_pixels_kept_or_hashed() {
        // boxes of whole blocks, some with what they held already (the same again, or in
        // part), on frames of blocks of 16 and of 8, each way turned, in both ranges
        for (width, height) in [(96usize, 64usize), (40, 24)] {
            for turn in [Turn::None, Turn::Half, Turn::Quarter] {
                for full in [false, true] {
                    let mut kept = Yuv::keeping_rgb(width, height, turn, full).unwrap();
                    let mut hashed = Yuv::new(width, height, turn, full).unwrap();
                    assert!(hashed.rgb().is_empty() && kept.rgb().len() == width * height * 3);
                    let n = kept.block;
                    let (cols, rows) = kept.macroblocks();
                    let (mut mb_kept, mut mb_hashed) =
                        (vec![0u8; cols * rows], vec![0u8; cols * rows]);
                    let mut numbers = Numbers(0x1234_5678_9ABC_DEF1 ^ width as u64);
                    let mut frame = vec![0u8; width * height * 3];
                    for step in 0..40 {
                        let mut boxes = Vec::new();
                        for _ in 0..1 + numbers.below(3) {
                            let (bx, by) = (numbers.below(width / n), numbers.below(height / n));
                            let bw = 1 + numbers.below(width / n - bx);
                            let bh = 1 + numbers.below(height / n - by);
                            let (x0, y0, x1, y1) = (bx * n, by * n, (bx + bw) * n, (by + bh) * n);
                            if step % 3 != 0 {
                                for y in y0..y1 {
                                    for x in x0..x1 {
                                        if numbers.below(4) == 0 {
                                            let at = (y * width + x) * 3;
                                            frame[at + numbers.below(3)] = numbers.next() as u8;
                                        }
                                    }
                                }
                            }
                            let mut pixels = Vec::new();
                            for y in y0..y1 {
                                pixels.extend_from_slice(
                                    &frame[(y * width + x0) * 3..(y * width + x1) * 3],
                                );
                            }
                            boxes.push((x0, y0, x1, y1, pixels));
                        }
                        for (x0, y0, x1, y1, pixels) in &boxes {
                            kept.put(*x0, *y0, *x1, *y1, pixels).unwrap();
                            hashed.put(*x0, *y0, *x1, *y1, pixels).unwrap();
                        }
                        let made = kept.finish(&mut mb_kept);
                        assert_eq!(
                            hashed.finish(&mut mb_hashed),
                            made,
                            "{turn:?} {full} {step}"
                        );
                        assert_eq!(mb_hashed, mb_kept, "{turn:?} {full} {step}");
                        assert!(hashed.y == kept.y && hashed.u == kept.u && hashed.v == kept.v);
                    }
                }
            }
        }
    }

    #[test]
    fn the_formulas_are_the_tables() {
        let f = |v: i32, (m, b, s): (i32, i32, i32)| (v * m + b) >> s;
        for v in 0..256usize {
            let x = v as i32;
            assert_eq!(f(x, Y_R_F), Y_R[v], "Y_R {v}");
            assert_eq!(f(x, Y_G_F), Y_G[v], "Y_G {v}");
            assert_eq!(f(x, Y_B_F), Y_B[v], "Y_B {v}");
            assert_eq!(-f(x, CB_R_F).max(0), CB_R[v], "CB_R {v}");
            assert_eq!(-f(x, CB_G_F).max(0), CB_G[v], "CB_G {v}");
            assert_eq!(x * CB_B_TIMES, CB_B[v], "CB_B {v}");
            assert_eq!(x * CB_B_TIMES, CR_R[v], "CR_R {v}");
            assert_eq!(-f(x, CR_G_F).max(0), CR_G[v], "CR_G {v}");
            assert_eq!(-f(x, CR_B_F).max(0), CR_B[v], "CR_B {v}");
            assert_eq!(f(x, LIMITED_Y_F), i32::from(LIMITED_Y[v]), "LIMITED_Y {v}");
        }
    }

    #[test]
    fn every_colour_eight_at_a_time_is_as_the_tables_make_it() {
        // (all 2^24 colours, 256 to a block, in both ranges; then blocks of 16 and of 8 cut
        // anywhere from a larger picture of random pixels)
        #[cfg(target_arch = "x86_64")]
        if std::arch::is_x86_feature_detected!("avx2") {
            let mut block = vec![0u8; 256 * 3];
            for start in (0..1u32 << 24).step_by(256) {
                for (k, px) in block.chunks_exact_mut(3).enumerate() {
                    let c = start + k as u32;
                    px.copy_from_slice(&[(c >> 16) as u8, (c >> 8) as u8, c as u8]);
                }
                for full in [false, true] {
                    // SAFETY: the processor has AVX2 (asked above)
                    let fast = unsafe { colours_avx2(&block, 0, 48, 16, full) };
                    assert!(
                        fast == colours_by_table(&block, 0, 48, 16, full),
                        "{start} {full}"
                    );
                }
            }
            let mut numbers = Numbers(0x0DDB_A11C_0FFE_E123);
            let (w, h) = (57usize, 41usize);
            let picture: Vec<u8> = (0..w * h * 3).map(|_| numbers.next() as u8).collect();
            for _ in 0..4000 {
                let n = if numbers.below(2) == 0 { 8 } else { 16 };
                let (x, y) = (numbers.below(w - n + 1), numbers.below(h - n + 1));
                let first = (y * w + x) * 3;
                for full in [false, true] {
                    // SAFETY: the processor has AVX2 (asked above)
                    let fast = unsafe { colours_avx2(&picture, first, w * 3, n, full) };
                    let slow = colours_by_table(&picture, first, w * 3, n, full);
                    assert!(fast == slow, "{n} at {x},{y} {full}");
                }
            }
        }
    }

    #[test]
    fn a_box_of_part_blocks_wants_the_pixels_kept() {
        let mut hashed = Yuv::new(32, 32, Turn::None, false).unwrap();
        let pixels = vec![7u8; 8 * 16 * 3];
        let error = hashed.put(0, 0, 8, 16, &pixels).unwrap_err();
        assert!(error.contains("not of whole blocks"), "{error}");
        let mut kept = Yuv::keeping_rgb(32, 32, Turn::None, false).unwrap();
        assert!(kept.put(0, 0, 8, 16, &pixels).is_ok());
    }

    #[test]
    fn blocks_that_differ_in_one_place_never_hash_alike() {
        let mut numbers = Numbers(0xC0FF_EE00_1234_5678);
        let mut block = vec![0u8; 16 * 16 * 3];
        for byte in block.iter_mut() {
            *byte = numbers.next() as u8;
        }
        let first = block_hash(&block, 0, 16 * 3, 16);
        for at in 0..block.len() {
            let mut other = block.clone();
            other[at] ^= 1 << numbers.below(8);
            assert_ne!(block_hash(&other, 0, 16 * 3, 16), first, "byte {at}");
        }
    }
}
