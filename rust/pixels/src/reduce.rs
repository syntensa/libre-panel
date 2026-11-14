//! Image.reduce(factor, box): blocks of factor by factor pixels averaged into one
//! (libImaging/Reduce.c, PIL/Image.py).
//!
//! Reduce.c has a function of its own for some factors (2x2, 3x3, 1xN, ...) and one for the
//! last column and row, whose blocks are smaller; each computes (the block's sum + half its
//! count) times a multiplier, >> 24, the multiplier 2^32 / (256 * count) in single precision
//! (division_UINT32), or the shift that is the same where the count is a power of two. So one
//! formula with each block's own count gives what each of them gives.

use crate::ints::{muldiv255, unmultiplied};
use crate::{error, Image, Mode, Result};

/// division_UINT32(divider, 8): computed in float, as Reduce.c computes it.
fn multiplier(count: u32) -> u32 {
    let max_dividend = (1u32 << 8) * count;
    let max_int = (1u64 << 32) as f32;
    (max_int / max_dividend as f32) as u32
}

impl Image {
    /// The `w` x `h` blocks of `s` x `s` pixels from (`x0`, `y0`) of an RGBA picture reduced
    /// as `crop(x0, y0, x0 + w * s, y0 + h * s).reduce(s, s)` reduces them (premultiplied, then
    /// back), in one pass: the same bytes. (Outside the picture its pixels are transparent
    /// black, as a crop has them.)
    pub fn reduce_rgba_part(
        &self,
        x0: i64,
        y0: i64,
        w: usize,
        h: usize,
        s: usize,
    ) -> Result<Image> {
        if self.mode != Mode::RGBA || s < 1 {
            return error("reduce_rgba_part takes RGBA and a scale above 0");
        }
        let mut out = vec![0u8; w * h * 4];
        let (width, height) = (self.width as i64, self.height as i64);
        for oy in 0..h {
            // (blocks of 2 x 2 wholly inside the picture: four at a time, below)
            let (mut lo, mut hi) = (w, w);
            let y = y0 + (oy * s) as i64;
            if s == 2 && y >= 0 && y + 1 < height && width - 2 - x0 >= 0 {
                lo = ((-x0).max(0) as usize).div_ceil(2).min(w);
                hi = (((width - 2 - x0) / 2 + 1) as usize).min(w).max(lo);
            }
            let row = &mut out[oy * w * 4..(oy + 1) * w * 4];
            if lo < hi {
                let at = (y as usize * self.width + (x0 + 2 * lo as i64) as usize) * 4;
                let n = hi - lo;
                let top = &self.data[at..at + n * 8];
                let next = at + self.width * 4;
                let bottom = &self.data[next..next + n * 8];
                halved(top, bottom, &mut row[lo * 4..hi * 4]);
            }
            for ox in (0..lo).chain(hi..w) {
                let block = self.reduced_block(x0, y0, ox, oy, s);
                row[ox * 4..ox * 4 + 4].copy_from_slice(&block);
            }
        }
        Image::from_bytes(Mode::RGBA, w, h, out)
    }

    /// The block (`ox`, `oy`) of `s` x `s` pixels from (`x0`, `y0`) reduced (premultiplied,
    /// then back): its sum and half its count, times the multiplier, >> 24; the pixels outside
    /// the picture transparent black.
    fn reduced_block(&self, x0: i64, y0: i64, ox: usize, oy: usize, s: usize) -> [u8; 4] {
        let count = (s * s) as u32;
        let m = u64::from(multiplier(count));
        let (width, height) = (self.width as i64, self.height as i64);
        let reduced = |sum: u32| ((u64::from(sum) * m) as u32 >> 24) as u8;
        let mut sums = [count / 2; 4];
        for dy in 0..s {
            let y = y0 + (oy * s + dy) as i64;
            if y < 0 || y >= height {
                continue;
            }
            for dx in 0..s {
                let x = x0 + (ox * s + dx) as i64;
                if x < 0 || x >= width {
                    continue;
                }
                let at = (y as usize * self.width + x as usize) * 4;
                let p = &self.data[at..at + 4];
                let a = u32::from(p[3]);
                sums[0] += muldiv255(u32::from(p[0]), a);
                sums[1] += muldiv255(u32::from(p[1]), a);
                sums[2] += muldiv255(u32::from(p[2]), a);
                sums[3] += a;
            }
        }
        let a = reduced(sums[3]);
        [
            unmultiplied(reduced(sums[0]), a),
            unmultiplied(reduced(sums[1]), a),
            unmultiplied(reduced(sums[2]), a),
            a,
        ]
    }
}

/// Blocks of 2 x 2 RGBA pixels reduced as reduced_block reduces them: `top` and `bottom` the
/// two rows of pixels (8 bytes a block), `out` a pixel a block. For four pixels the multiplier
/// is 2^22 exactly ((sum * 2^22) >> 24: the sum >> 2, the sum at most 4 * 255 + 2), so the
/// premultiplied sums are worked out in 16 bits, eight at a time.
fn halved(top: &[u8], bottom: &[u8], out: &mut [u8]) {
    let n = out.len() / 4;
    let mut done = 0;
    #[cfg(target_arch = "x86_64")]
    {
        // SAFETY: SSE2 is part of x86_64; every load and store is within the slices (16
        // blocks' bytes of each row and 16 of out, four blocks a turn).
        unsafe {
            use std::arch::x86_64::*;
            let zero = _mm_setzero_si128();
            let alpha = _mm_set_epi16(-1, 0, 0, 0, -1, 0, 0, 0);
            let half = _mm_set1_epi16(128);
            let two = _mm_set1_epi16(2);
            // two pixels in 16 bits, their colours premultiplied (MULDIV255), their alpha kept
            let premultiplied = |v: __m128i| {
                let a = _mm_shufflehi_epi16(_mm_shufflelo_epi16(v, 0xFF), 0xFF);
                let t = _mm_add_epi16(_mm_mullo_epi16(v, a), half);
                let m = _mm_srli_epi16(_mm_add_epi16(t, _mm_srli_epi16(t, 8)), 8);
                _mm_or_si128(_mm_andnot_si128(alpha, m), _mm_and_si128(alpha, v))
            };
            // the sums of the pairs of eight pixels: four blocks' halves
            let pairs = |row: *const u8| {
                let (lo, hi) = (
                    _mm_loadu_si128(row.cast()),
                    _mm_loadu_si128(row.add(16).cast()),
                );
                let p01 = premultiplied(_mm_unpacklo_epi8(lo, zero));
                let p23 = premultiplied(_mm_unpackhi_epi8(lo, zero));
                let p45 = premultiplied(_mm_unpacklo_epi8(hi, zero));
                let p67 = premultiplied(_mm_unpackhi_epi8(hi, zero));
                (
                    _mm_add_epi16(_mm_unpacklo_epi64(p01, p23), _mm_unpackhi_epi64(p01, p23)),
                    _mm_add_epi16(_mm_unpacklo_epi64(p45, p67), _mm_unpackhi_epi64(p45, p67)),
                )
            };
            let mut sums = [0u8; 16];
            while done + 4 <= n {
                let (a, b) = pairs(top.as_ptr().add(done * 8));
                let (c, d) = pairs(bottom.as_ptr().add(done * 8));
                let first = _mm_srli_epi16(_mm_add_epi16(_mm_add_epi16(a, c), two), 2);
                let second = _mm_srli_epi16(_mm_add_epi16(_mm_add_epi16(b, d), two), 2);
                _mm_storeu_si128(sums.as_mut_ptr().cast(), _mm_packus_epi16(first, second));
                for (pixel, sum) in out[done * 4..done * 4 + 16]
                    .chunks_exact_mut(4)
                    .zip(sums.chunks_exact(4))
                {
                    let a = sum[3];
                    pixel[0] = unmultiplied(sum[0], a);
                    pixel[1] = unmultiplied(sum[1], a);
                    pixel[2] = unmultiplied(sum[2], a);
                    pixel[3] = a;
                }
                done += 4;
            }
        }
    }
    for i in done..n {
        let mut sums = [2u32; 4];
        for row in [top, bottom] {
            for p in row[i * 8..i * 8 + 8].chunks_exact(4) {
                let a = u32::from(p[3]);
                sums[0] += muldiv255(u32::from(p[0]), a);
                sums[1] += muldiv255(u32::from(p[1]), a);
                sums[2] += muldiv255(u32::from(p[2]), a);
                sums[3] += a;
            }
        }
        let a = (sums[3] >> 2) as u8;
        let pixel = &mut out[i * 4..i * 4 + 4];
        for k in 0..3 {
            pixel[k] = unmultiplied((sums[k] >> 2) as u8, a);
        }
        pixel[3] = a;
    }
}

impl Image {
    /// ``xscale`` by ``yscale`` reduced, the part of the picture in ``bounds`` (left, top,
    /// right, bottom; the whole picture where None). RGBA is reduced premultiplied (Image.py
    /// converts it to RGBa and back).
    pub fn reduce(
        &self,
        xscale: usize,
        yscale: usize,
        bounds: Option<(usize, usize, usize, usize)>,
    ) -> Result<Image> {
        if (xscale, yscale) == (1, 1) {
            // (Image.py: a crop of the box, as Image.crop takes it)
            let Some((x0, y0, x1, y1)) = bounds else {
                return Ok(self.clone());
            };
            if x1 < x0 {
                return error("Coordinate 'right' is less than 'left'");
            }
            if y1 < y0 {
                return error("Coordinate 'lower' is less than 'upper'");
            }
            return Ok(self.crop(x0 as i64, y0 as i64, x1 as i64, y1 as i64));
        }
        let (x0, y0, x1, y1) = bounds.unwrap_or((0, 0, self.width, self.height));
        if self.mode == Mode::RGBA {
            return self
                .convert(Mode::RGBa)?
                .reduce(xscale, yscale, bounds)?
                .convert(Mode::RGBA);
        }
        if xscale < 1 || yscale < 1 {
            return error("scale must be > 0");
        }
        if x1 > self.width || y1 > self.height {
            return error("box can't exceed original image size");
        }
        if x1 <= x0 || y1 <= y0 {
            return error("box can't be empty");
        }
        let (bw, bh) = (x1 - x0, y1 - y0);
        let (w, h) = (bw.div_ceil(xscale), bh.div_ceil(yscale));
        let bands = self.bands();
        let mut out = Image::blank(self.mode, w, h);
        let mut sums = vec![0u32; bands];
        // (the multiplier of a block's count, worked out again only where the count is another:
        // the last column's and row's blocks)
        let mut known = (0u32, 0u32);
        if bands == 1 {
            // (a row of sums for each row of blocks, each row of the picture added to it in
            // one go: the same sums, as whole numbers add up alike in any order)
            let mut acc = vec![0u32; w];
            for oy in 0..h {
                let (fy, ty) = (y0 + oy * yscale, (y0 + (oy + 1) * yscale).min(y1));
                acc.fill(0);
                for y in fy..ty {
                    let row = &self.data[y * self.width + x0..y * self.width + x1];
                    for (a, block) in acc.iter_mut().zip(row.chunks(xscale)) {
                        *a += block.iter().map(|&v| u32::from(v)).sum::<u32>();
                    }
                }
                for (ox, &sum) in acc.iter().enumerate() {
                    let across = (x0 + (ox + 1) * xscale).min(x1) - (x0 + ox * xscale);
                    let count = (across * (ty - fy)) as u32;
                    if known.0 != count {
                        known = (count, multiplier(count));
                    }
                    let s = count / 2 + sum;
                    out.data[oy * w + ox] =
                        ((u64::from(s) * u64::from(known.1)) as u32 >> 24) as u8;
                }
            }
            return Ok(out);
        }
        for oy in 0..h {
            let (fy, ty) = (y0 + oy * yscale, (y0 + (oy + 1) * yscale).min(y1));
            for ox in 0..w {
                let (fx, tx) = (x0 + ox * xscale, (x0 + (ox + 1) * xscale).min(x1));
                let count = ((tx - fx) * (ty - fy)) as u32;
                if known.0 != count {
                    known = (count, multiplier(count));
                }
                let m = u64::from(known.1);
                let at = (oy * w + ox) * bands;
                sums.fill(count / 2);
                for y in fy..ty {
                    let row =
                        &self.data[(y * self.width + fx) * bands..(y * self.width + tx) * bands];
                    for p in row.chunks_exact(bands) {
                        for (s, &v) in sums.iter_mut().zip(p) {
                            *s += u32::from(v);
                        }
                    }
                }
                for (o, &s) in out.data[at..at + bands].iter_mut().zip(&sums) {
                    *o = ((u64::from(s) * m) as u32 >> 24) as u8;
                }
            }
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_column_resized_across_stays_as_it_is_in_every_column() {
        // (what the graph's fill takes for its fade: BICUBIC from one column)
        for (cw, ch) in [(1usize, 1usize), (5, 7), (600, 360), (33, 2), (1200, 141)] {
            let rows: Vec<u8> = (0..ch).map(|i| (i * 37 % 256) as u8).collect();
            let column = Image::from_bytes(Mode::L, 1, ch, rows.clone()).unwrap();
            let wide = column
                .resize(cw, ch, crate::Filter::Bicubic, None, None)
                .unwrap();
            let want: Vec<u8> = rows
                .iter()
                .flat_map(|&v| std::iter::repeat_n(v, cw))
                .collect();
            assert_eq!(wide.as_bytes(), &want[..], "{cw}x{ch}");
        }
    }

    #[test]
    fn a_tint_over_a_picture_as_alpha_composite_lays_it() {
        let (w, h) = (31usize, 9usize);
        let data: Vec<u8> = (0..w * h * 4).map(|i| (i * 53 % 256) as u8).collect();
        let under = Image::from_bytes(Mode::RGBA, w, h, data).unwrap();
        let alpha: Vec<u8> = (0..w * h).map(|i| (i * 29 % 256) as u8).collect();
        let mut tint = Image::new(Mode::RGBA, w, h, &[200, 40, 90, 255]);
        tint.putalpha(&Image::from_bytes(Mode::L, w, h, alpha.clone()).unwrap())
            .unwrap();
        let want = crate::alpha_composite(&under, &tint).unwrap();
        let mut got = under.clone();
        crate::tint_over(&mut got, [200, 40, 90], &alpha).unwrap();
        assert_eq!(got, want);
    }

    #[test]
    fn every_colour_at_every_alpha_reduced_by_two_as_cut_and_reduced() {
        // every colour byte at every alpha, in each corner of a block
        let (w, h) = (2 * 65536usize, 2usize);
        let mut data = vec![0u8; w * h * 4];
        for i in 0..65536usize {
            let (c, a) = ((i % 256) as u8, (i / 256) as u8);
            let corners = [
                [c, c.wrapping_mul(7), c ^ 0x5a, a],
                [c.wrapping_add(85), c, c.wrapping_mul(3), a.wrapping_add(17)],
                [c.wrapping_mul(5), c.wrapping_add(170), c, a ^ 0x33],
                [c ^ 0xff, c.wrapping_mul(11), c.wrapping_add(1), 255 - a],
            ];
            for (k, pixel) in corners.iter().enumerate() {
                let at = ((k / 2) * w + 2 * i + k % 2) * 4;
                data[at..at + 4].copy_from_slice(pixel);
            }
        }
        let im = Image::from_bytes(Mode::RGBA, w, h, data).unwrap();
        let want = im
            .convert(Mode::RGBa)
            .unwrap()
            .reduce(2, 2, None)
            .unwrap()
            .convert(Mode::RGBA)
            .unwrap();
        assert_eq!(im.reduce_rgba_part(0, 0, w / 2, 1, 2).unwrap(), want);
    }

    #[test]
    fn an_rgba_part_reduced_in_one_go_as_cut_and_reduced() {
        let (w, h) = (23usize, 17usize);
        let mut seed = 0x9e37_79b9_u64;
        let data: Vec<u8> = (0..w * h * 4)
            .map(|i| {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                // (alpha 0, 255 and between, colours anywhere)
                match (i % 4, seed % 5) {
                    (3, 0) => 0,
                    (3, 1) => 255,
                    _ => (seed >> 8) as u8,
                }
            })
            .collect();
        let im = Image::from_bytes(Mode::RGBA, w, h, data).unwrap();
        for s in [1usize, 2, 3] {
            for (x0, y0, ow, oh) in [
                (0i64, 0i64, 5usize, 4usize),
                (1, 0, 11, 8),
                (3, 2, 4, 5),
                (-2, -1, 6, 3),
                (20, 15, 3, 3),
                // (four blocks at a time, and the ones beside them, inside and over the edges)
                (0, 0, 11, 8),
                (-3, 1, 13, 7),
                (-1, -2, 14, 10),
                (2, 3, 9, 4),
                (5, 0, 12, 9),
            ] {
                let cut = im.crop(x0, y0, x0 + (ow * s) as i64, y0 + (oh * s) as i64);
                let want = cut
                    .convert(Mode::RGBa)
                    .unwrap()
                    .reduce(s, s, None)
                    .unwrap()
                    .convert(Mode::RGBA)
                    .unwrap();
                assert_eq!(
                    im.reduce_rgba_part(x0, y0, ow, oh, s).unwrap(),
                    want,
                    "{s} {x0} {y0}"
                );
            }
        }
    }
}
