//! Image.thumbnail, ImageOps.contain, cover, pad and fit, the EXIF orientation's transpose,
//! getextrema and the gradients Image.linear_gradient and radial_gradient (PIL/Image.py,
//! PIL/ImageOps.py, libImaging/Fill.c, Pillow 13.0), in Python's arithmetic: its round() half
//! to even, its floor and ceil, min and max taking the first of equals.

use crate::geometry::Transpose;
use crate::resample::Filter;
use crate::{error, Image, Mode, Result};

/// Python's round(x) of a float: the whole number nearest, half to even.
fn py_round(x: f64) -> i64 {
    x.round_ties_even() as i64
}

impl Image {
    /// Image.thumbnail(size, resample, reducing_gap) of a picture already decoded (a JPEG's
    /// draft is the decoder's): the largest size within `size` of the picture's aspect, as
    /// Pillow rounds it; the picture as it is where it fits already.
    pub fn thumbnail(
        &self,
        size: (f64, f64),
        resample: Filter,
        reducing_gap: Option<f64>,
    ) -> Result<Image> {
        let (x, y) = (size.0.floor() as i64, size.1.floor() as i64);
        let (w, h) = (self.width as i64, self.height as i64);
        if x >= w && y >= h {
            return Ok(self.clone());
        }
        if h == 0 || y == 0 {
            return error("division by zero");
        }
        // round_aspect: the floor or the ceiling, whichever is nearer by `key` (the floor
        // where they tie), and at least 1
        let round_aspect = |number: f64, key: &dyn Fn(i64) -> f64| -> i64 {
            let (lo, hi) = (number.floor() as i64, number.ceil() as i64);
            let pick = if key(hi) < key(lo) { hi } else { lo };
            pick.max(1)
        };
        let aspect = w as f64 / h as f64;
        let (fx, fy) = (x as f64, y as f64);
        let (x, y) = if fx / fy >= aspect {
            (
                round_aspect(fy * aspect, &|n| (aspect - n as f64 / fy).abs()),
                y,
            )
        } else {
            let key = |n: i64| {
                if n == 0 {
                    0.0
                } else {
                    (aspect - fx / n as f64).abs()
                }
            };
            (x, round_aspect(fx / aspect, &key))
        };
        if (x, y) == (w, h) {
            return Ok(self.clone());
        }
        self.resize(x as usize, y as usize, resample, None, reducing_gap)
    }

    /// ImageOps.contain(image, size, method): as large as fits in `size`, its aspect kept.
    pub fn contain(&self, size: (usize, usize), method: Filter) -> Result<Image> {
        if self.height == 0 {
            return Ok(Image::blank(self.mode, size.0, 0));
        }
        let im_ratio = self.width as f64 / self.height as f64;
        if size.1 == 0 {
            return Ok(Image::blank(self.mode, 0, 0));
        }
        let dest_ratio = size.0 as f64 / size.1 as f64;
        let mut size = size;
        if im_ratio != dest_ratio {
            if im_ratio > dest_ratio {
                let tall = self.height as f64 / self.width as f64 * size.0 as f64;
                size.1 = py_round(tall).max(0) as usize;
            } else {
                let wide = self.width as f64 / self.height as f64 * size.1 as f64;
                size.0 = py_round(wide).max(0) as usize;
            }
        }
        if size.0 == 0 || size.1 == 0 {
            return Ok(Image::blank(self.mode, size.0, size.1));
        }
        self.resize(size.0, size.1, method, None, None)
    }

    /// ImageOps.cover(image, size, method): as small as covers `size`, its aspect kept.
    pub fn cover(&self, size: (usize, usize), method: Filter) -> Result<Image> {
        if self.height == 0 {
            return Ok(Image::blank(self.mode, size.0, 0));
        }
        let im_ratio = self.width as f64 / self.height as f64;
        if size.1 == 0 {
            return Ok(Image::blank(self.mode, 0, 0));
        }
        let dest_ratio = size.0 as f64 / size.1 as f64;
        let mut size = size;
        if im_ratio != dest_ratio {
            if im_ratio < dest_ratio {
                let tall = self.height as f64 / self.width as f64 * size.0 as f64;
                size.1 = py_round(tall).max(0) as usize;
            } else {
                let wide = self.width as f64 / self.height as f64 * size.1 as f64;
                size.0 = py_round(wide).max(0) as usize;
            }
        }
        self.resize(size.0, size.1, method, None, None)
    }

    /// ImageOps.pad(image, size, method, color, centering): contained, on `colour` (0 where
    /// none) where it does not fill `size`, placed by `centering` (each clamped to 0..1).
    pub fn pad(
        &self,
        size: (usize, usize),
        method: Filter,
        colour: Option<&[u8]>,
        centering: (f64, f64),
    ) -> Result<Image> {
        let resized = self.contain(size, method)?;
        if resized.size() == size {
            return Ok(resized);
        }
        let mut out = match colour {
            None => Image::blank(self.mode, size.0, size.1),
            Some(c) if c.len() == self.bands() => Image::new(self.mode, size.0, size.1, c),
            Some(_) => return error("a colour of another number of bands"),
        };
        // max(0, min(c, 1)) as Python takes them (the first of equals, NaN never the larger)
        let within = |c: f64| {
            let low = if 1.0 < c { 1.0 } else { c };
            if low > 0.0 {
                low
            } else {
                0.0
            }
        };
        let place =
            |room: usize, have: usize, c: f64| py_round((room as f64 - have as f64) * within(c));
        if resized.width != size.0 {
            out.paste(&resized, place(size.0, resized.width, centering.0), 0, None)?;
        } else {
            out.paste(
                &resized,
                0,
                place(size.1, resized.height, centering.1),
                None,
            )?;
        }
        Ok(out)
    }

    /// ImageOps.fit(image, size, method, bleed, centering): the part of the picture of
    /// `size`'s aspect (less `bleed` round it), placed by `centering`, resized to `size`.
    pub fn fit(
        &self,
        size: (usize, usize),
        method: Filter,
        bleed: f64,
        centering: (f64, f64),
    ) -> Result<Image> {
        if size.0 == 0 || size.1 == 0 {
            return Ok(Image::blank(self.mode, size.0, size.1));
        }
        let within = |c: f64| if (0.0..=1.0).contains(&c) { c } else { 0.5 };
        let (cx, cy) = (within(centering.0), within(centering.1));
        let bleed = if (0.0..0.5).contains(&bleed) {
            bleed
        } else {
            0.0
        };
        let (w, h) = (self.width as f64, self.height as f64);
        let bleed_pixels = (bleed * w, bleed * h);
        let live = (w - bleed_pixels.0 * 2.0, h - bleed_pixels.1 * 2.0);
        if live.1 == 0.0 {
            return Ok(Image::blank(self.mode, size.0, size.1));
        }
        let live_ratio = live.0 / live.1;
        let output_ratio = size.0 as f64 / size.1 as f64;
        let (crop_w, crop_h) = if live_ratio == output_ratio {
            live
        } else if live_ratio >= output_ratio {
            (output_ratio * live.1, live.1)
        } else {
            (live.0, live.0 / output_ratio)
        };
        let left = bleed_pixels.0 + (live.0 - crop_w) * cx;
        let top = bleed_pixels.1 + (live.1 - crop_h) * cy;
        let crop = (left, top, left + crop_w, top + crop_h);
        self.resize(size.0, size.1, method, Some(crop), None)
    }

    /// ImageOps.exif_transpose's turn for an EXIF orientation (1 to 8; the picture as it is
    /// for 1 and any other).
    pub fn exif_transposed(&self, orientation: u16) -> Image {
        let method = match orientation {
            2 => Transpose::FlipLeftRight,
            3 => Transpose::Rotate180,
            4 => Transpose::FlipTopBottom,
            5 => Transpose::Transpose,
            6 => Transpose::Rotate270,
            7 => Transpose::Transverse,
            8 => Transpose::Rotate90,
            _ => return self.clone(),
        };
        self.transpose(method)
    }

    /// Image.getextrema: each band's smallest and largest value (None for a picture of no
    /// pixels).
    pub fn getextrema(&self) -> Option<Vec<(u8, u8)>> {
        if self.width == 0 || self.height == 0 {
            return None;
        }
        let b = self.bands();
        let mut out = vec![(255u8, 0u8); b];
        for px in self.data.chunks_exact(b) {
            for (e, &v) in out.iter_mut().zip(px) {
                e.0 = e.0.min(v);
                e.1 = e.1.max(v);
            }
        }
        Some(out)
    }

    /// Image.linear_gradient("L"): 256 x 256, each row its number.
    pub fn linear_gradient() -> Image {
        let mut out = Image::blank(Mode::L, 256, 256);
        for y in 0..256 {
            out.row_mut(y).fill(y as u8);
        }
        out
    }

    /// Image.radial_gradient("L"): 256 x 256, each pixel its distance from (128, 128) times
    /// the square root of 2, cut to whole numbers and at 255 (Fill.c).
    pub fn radial_gradient() -> Image {
        let mut out = Image::blank(Mode::L, 256, 256);
        for y in 0..256i32 {
            let row = out.row_mut(y as usize);
            for (x, v) in (0..256i32).zip(row.iter_mut()) {
                let sum = (x - 128) * (x - 128) + (y - 128) * (y - 128);
                let d = (f64::from(sum) * 2.0).sqrt() as i32;
                *v = d.min(255) as u8;
            }
        }
        out
    }
}
