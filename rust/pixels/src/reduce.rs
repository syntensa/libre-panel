//! Image.reduce(factor, box): blocks of factor by factor pixels averaged into one
//! (libImaging/Reduce.c, PIL/Image.py).
//!
//! Reduce.c has a function of its own for some factors (2x2, 3x3, 1xN, ...) and one for the
//! last column and row, whose blocks are smaller; each computes (the block's sum + half its
//! count) times a multiplier, >> 24, the multiplier 2^32 / (256 * count) in single precision
//! (division_UINT32), or the shift that is the same where the count is a power of two. So one
//! formula with each block's own count gives what each of them gives.

use crate::{error, Image, Mode, Result};

/// division_UINT32(divider, 8): computed in float, as Reduce.c computes it.
fn multiplier(count: u32) -> u32 {
    let max_dividend = (1u32 << 8) * count;
    let max_int = (1u64 << 32) as f32;
    (max_int / max_dividend as f32) as u32
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
