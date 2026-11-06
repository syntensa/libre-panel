//! Image.resize(size, resample, box, reducing_gap): libImaging/Resample.c's two passes (the
//! rows first, rounded to 8 bits, then the columns), Geometry.c's nearest-neighbour scaling
//! for NEAREST, _imaging.c's crop where the box is the size asked for, and PIL/Image.py's way
//! round (RGBA premultiplied, reduce first by ``reducing_gap``, a very tall picture in two
//! steps).
//!
//! The arithmetic is Resample.c's: the box's corners are floats (_imaging.c parses them so),
//! the filter's weights doubles, normalised and made 22-bit fixed point with C's truncation.

use crate::{error, FloatImage, Image, Mode, Result};
use std::f64::consts::PI;

/// Pillow's resampling filters (Image.Resampling).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Filter {
    Nearest,
    Box,
    Bilinear,
    Hamming,
    Bicubic,
    Lanczos,
}

impl Filter {
    /// Image.Resampling's numbers.
    pub fn from_number(n: i64) -> Option<Filter> {
        Some(match n {
            0 => Filter::Nearest,
            1 => Filter::Lanczos,
            2 => Filter::Bilinear,
            3 => Filter::Bicubic,
            4 => Filter::Box,
            5 => Filter::Hamming,
            _ => return None,
        })
    }

    /// The filter's support (Resample.c's, and Image.py's _filters_support).
    fn support(self) -> f64 {
        match self {
            Filter::Nearest | Filter::Box => 0.5,
            Filter::Bilinear | Filter::Hamming => 1.0,
            Filter::Bicubic => 2.0,
            Filter::Lanczos => 3.0,
        }
    }

    fn weight(self, x: f64) -> f64 {
        match self {
            Filter::Nearest | Filter::Box => {
                if x > -0.5 && x <= 0.5 {
                    1.0
                } else {
                    0.0
                }
            }
            Filter::Bilinear => {
                let x = x.abs();
                if x < 1.0 {
                    1.0 - x
                } else {
                    0.0
                }
            }
            Filter::Hamming => {
                let x = x.abs();
                if x == 0.0 {
                    return 1.0;
                }
                if x >= 1.0 {
                    return 0.0;
                }
                let x = x * PI;
                // (0.54f and 0.46f: float constants in Resample.c)
                x.sin() / x * (f64::from(0.54f32) + f64::from(0.46f32) * x.cos())
            }
            Filter::Bicubic => {
                let a = -0.5;
                let x = x.abs();
                if x < 1.0 {
                    ((a + 2.0) * x - (a + 3.0)) * x * x + 1.0
                } else if x < 2.0 {
                    (((x - 5.0) * x + 8.0) * x - 4.0) * a
                } else {
                    0.0
                }
            }
            Filter::Lanczos => {
                if (-3.0..3.0).contains(&x) {
                    sinc(x) * sinc(x / 3.0)
                } else {
                    0.0
                }
            }
        }
    }
}

fn sinc(x: f64) -> f64 {
    if x == 0.0 {
        return 1.0;
    }
    let x = x * PI;
    x.sin() / x
}

const PRECISION_BITS: u32 = 32 - 8 - 2;

/// Resample.c's precompute_coeffs: for each place of the output, the first input it takes and
/// how many, and their weights (``ksize`` a place).
struct Coeffs {
    ksize: usize,
    bounds: Vec<(usize, usize)>,
    kk: Vec<f64>,
}

fn coeffs(in_size: usize, in0: f32, in1: f32, out_size: usize, filter: Filter) -> Coeffs {
    let scale = f64::from(in1 - in0) / out_size as f64;
    let filterscale = scale.max(1.0);
    let support = filter.support() * filterscale;
    let ksize = support.ceil() as usize * 2 + 1;
    let mut kk = vec![0.0; out_size * ksize];
    let mut bounds = Vec::with_capacity(out_size);
    let inv_filterscale = 1.0 / filterscale;
    for xx in 0..out_size {
        let center = f64::from(in0) + (xx as f64 + 0.5) * scale;
        let mut ww = 0.0;
        let xmin = ((center - support + 0.5) as i32).max(0);
        let xmax = ((center + support + 0.5) as i32).min(in_size as i32) - xmin;
        let k = &mut kk[xx * ksize..(xx + 1) * ksize];
        for x in 0..xmax.max(0) {
            let w = filter.weight((f64::from(x + xmin) - center + 0.5) * inv_filterscale);
            k[x as usize] = w;
            ww += w;
        }
        if ww != 0.0 {
            for v in &mut k[..xmax.max(0) as usize] {
                *v /= ww;
            }
        }
        bounds.push((xmin as usize, xmax.max(0) as usize));
    }
    Coeffs { ksize, bounds, kk }
}

/// normalize_coeffs_8bpc: the weights in 22-bit fixed point, rounded away from 0 by C's cast.
fn fixed(kk: &[f64]) -> Vec<i32> {
    let one = f64::from(1u32 << PRECISION_BITS);
    kk.iter()
        .map(|&k| {
            if k < 0.0 {
                (-0.5 + k * one) as i32
            } else {
                (0.5 + k * one) as i32
            }
        })
        .collect()
}

/// clip8: the fixed-point sum to 0..255 (Resample.c's table of -640..639 >> 22).
#[inline(always)]
fn clip8(ss: i32) -> u8 {
    (ss >> PRECISION_BITS).clamp(0, 255) as u8
}

/// The rows of ``src`` from ``offset`` on, each resampled to ``c``'s width (8 bits a band).
fn horizontal_8(src: &Image, offset: usize, rows: usize, c: &Coeffs) -> Image {
    let w = c.bounds.len();
    let b = src.bands();
    let kk = fixed(&c.kk);
    let mut out = Image::blank(src.mode, w, rows);
    for yy in 0..rows {
        let line = src.row(yy + offset);
        let dst = out.row_mut(yy);
        for (xx, &(xmin, xmax)) in c.bounds.iter().enumerate() {
            let k = &kk[xx * c.ksize..xx * c.ksize + xmax];
            for band in 0..b {
                let mut ss: i32 = 1 << (PRECISION_BITS - 1);
                for (x, &kx) in k.iter().enumerate() {
                    ss = ss.wrapping_add(i32::from(line[(x + xmin) * b + band]).wrapping_mul(kx));
                }
                dst[xx * b + band] = clip8(ss);
            }
        }
    }
    out
}

/// The columns of ``src``, resampled to ``c``'s height (``c``'s bounds shifted by ``offset``).
fn vertical_8(src: &Image, offset: usize, c: &Coeffs) -> Image {
    let h = c.bounds.len();
    let b = src.bands();
    let kk = fixed(&c.kk);
    let mut out = Image::blank(src.mode, src.width, h);
    let n = src.width * b;
    for (yy, &(ymin, ymax)) in c.bounds.iter().enumerate() {
        let k = &kk[yy * c.ksize..yy * c.ksize + ymax];
        let dst = out.row_mut(yy);
        for (i, o) in dst.iter_mut().enumerate().take(n) {
            let mut ss: i32 = 1 << (PRECISION_BITS - 1);
            for (y, &ky) in k.iter().enumerate() {
                let v = src.data[(y + ymin - offset) * n + i];
                ss = ss.wrapping_add(i32::from(v).wrapping_mul(ky));
            }
            *o = clip8(ss);
        }
    }
    out
}

/// Resample.c's ImagingResampleInner for 8-bit bands.
fn resample_8(src: &Image, w: usize, h: usize, filter: Filter, bx: [f32; 4]) -> Image {
    let need_h = w != src.width || bx[0] != 0.0 || bx[2] != w as f32;
    let need_v = h != src.height || bx[1] != 0.0 || bx[3] != h as f32;
    let cv = coeffs(src.height, bx[1], bx[3], h, filter);
    let first = cv.bounds[0].0;
    let last = cv.bounds[h - 1].0 + cv.bounds[h - 1].1;
    let mut current: Option<Image> = None;
    let mut offset = 0;
    if need_h {
        let ch = coeffs(src.width, bx[0], bx[2], w, filter);
        current = Some(horizontal_8(src, first, last - first, &ch));
        offset = first;
    }
    if need_v {
        let from = current.as_ref().unwrap_or(src);
        return vertical_8(from, offset, &cv);
    }
    current.unwrap_or_else(|| src.clone())
}

/// Geometry.c's ImagingScaleAffine: NEAREST, each output pixel the input's under its middle
/// (the positions summed step by step, as Geometry.c sums them).
fn nearest(src: &Image, w: usize, h: usize, bx: [f32; 4]) -> Image {
    let a0 = f64::from(bx[2] - bx[0]) / w as f64;
    let a4 = f64::from(bx[3] - bx[1]) / h as f64;
    let coord = |v: f64| -> i64 {
        if v < 0.0 {
            -1
        } else {
            v as i64
        }
    };
    let b = src.bands();
    let mut out = Image::blank(src.mode, w, h);
    let mut xin = vec![None; w];
    let mut xo = f64::from(bx[0]) + a0 * 0.5;
    for slot in xin.iter_mut() {
        let x = coord(xo);
        if x >= 0 && (x as usize) < src.width {
            *slot = Some(x as usize);
        }
        xo += a0;
    }
    let mut yo = f64::from(bx[1]) + a4 * 0.5;
    for y in 0..h {
        let yi = coord(yo);
        if yi >= 0 && (yi as usize) < src.height {
            let line = src.row(yi as usize);
            let dst = out.row_mut(y);
            for (x, slot) in xin.iter().enumerate() {
                if let Some(sx) = slot {
                    dst[x * b..(x + 1) * b].copy_from_slice(&line[sx * b..(sx + 1) * b]);
                }
            }
        }
        yo += a4;
    }
    out
}

/// _imaging.c's _resize, with the box as the floats it parses.
fn core_resize(src: &Image, w: usize, h: usize, filter: Filter, bx: [f32; 4]) -> Result<Image> {
    if w < 1 || h < 1 {
        return error("height and width must be > 0");
    }
    if bx[0] < 0.0 || bx[1] < 0.0 {
        return error("box offset can't be negative");
    }
    if bx[2] > src.width as f32 || bx[3] > src.height as f32 {
        return error("box can't exceed original image size");
    }
    if bx[2] - bx[0] < 0.0 || bx[3] - bx[1] < 0.0 {
        return error("box can't be empty");
    }
    let whole = |v: f32| v - (v as i32) as f32 == 0.0;
    if whole(bx[0]) && bx[2] - bx[0] == w as f32 && whole(bx[1]) && bx[3] - bx[1] == h as f32 {
        let c = |v: f32| v as i32 as i64;
        return Ok(src.crop(c(bx[0]), c(bx[1]), c(bx[2]), c(bx[3])));
    }
    if filter == Filter::Nearest {
        return Ok(nearest(src, w, h, bx));
    }
    Ok(resample_8(src, w, h, filter, bx))
}

impl Image {
    /// Image.resize((w, h), filter, box, reducing_gap); ``bounds`` (left, top, right, bottom) in
    /// the doubles Python computes with, the whole picture where None.
    pub fn resize(
        &self,
        w: usize,
        h: usize,
        filter: Filter,
        bounds: Option<(f64, f64, f64, f64)>,
        reducing_gap: Option<f64>,
    ) -> Result<Image> {
        if reducing_gap.is_some_and(|g| g < 1.0) {
            return error("reducing_gap must be 1.0 or greater");
        }
        let full = (0.0, 0.0, self.width as f64, self.height as f64);
        let mut bx = bounds.unwrap_or(full);
        if (w, h) == self.size() && bx == full {
            return Ok(self.clone());
        }
        if self.mode == Mode::RGBA && filter != Filter::Nearest {
            // (Image.py resizes the premultiplied copy without reducing_gap)
            return self
                .convert(Mode::RGBa)?
                .resize(w, h, filter, Some(bx), None)?
                .convert(Mode::RGBA);
        }
        let reduced;
        let mut src = self;
        if let (Some(gap), false) = (reducing_gap, filter == Filter::Nearest) {
            let fx = (((bx.2 - bx.0) / w as f64 / gap) as i64).max(1) as usize;
            let fy = (((bx.3 - bx.1) / h as f64 / gap) as i64).max(1) as usize;
            if fx > 1 || fy > 1 {
                let safe = self.safe_box(w, h, filter, bx);
                reduced = self.reduce(fx, fy, Some(safe))?;
                src = &reduced;
                bx = (
                    (bx.0 - safe.0 as f64) / fx as f64,
                    (bx.1 - safe.1 as f64) / fy as f64,
                    (bx.2 - safe.0 as f64) / fx as f64,
                    (bx.3 - safe.1 as f64) / fy as f64,
                );
            }
        }
        let f = |b: (f64, f64, f64, f64)| [b.0 as f32, b.1 as f32, b.2 as f32, b.3 as f32];
        if src.height > src.width * 100 && h < src.height {
            // a very tall picture: its height first, then its width (Image.py)
            let tall = core_resize(
                src,
                src.width,
                h,
                filter,
                f((0.0, bx.1, src.width as f64, bx.3)),
            )?;
            return core_resize(&tall, w, h, filter, f((bx.0, 0.0, bx.2, h as f64)));
        }
        core_resize(src, w, h, filter, f(bx))
    }

    /// Image._get_safe_box: the box with the pixels round it that the filter reaches.
    fn safe_box(
        &self,
        w: usize,
        h: usize,
        filter: Filter,
        bx: (f64, f64, f64, f64),
    ) -> (usize, usize, usize, usize) {
        let support = filter.support() - 0.5;
        let sx = support * ((bx.2 - bx.0) / w as f64);
        let sy = support * ((bx.3 - bx.1) / h as f64);
        (
            ((bx.0 - sx) as i64).max(0) as usize,
            ((bx.1 - sy) as i64).max(0) as usize,
            ((bx.2 + sx).ceil() as i64).min(self.width as i64) as usize,
            ((bx.3 + sy).ceil() as i64).min(self.height as i64) as usize,
        )
    }
}

/// Resample.c's passes for 32-bit floats (mode F): doubles summed, the result a float.
fn resample_f(src: &FloatImage, w: usize, h: usize, filter: Filter, bx: [f32; 4]) -> FloatImage {
    let need_h = w != src.width || bx[0] != 0.0 || bx[2] != w as f32;
    let need_v = h != src.height || bx[1] != 0.0 || bx[3] != h as f32;
    let cv = coeffs(src.height, bx[1], bx[3], h, filter);
    let first = cv.bounds[0].0;
    let last = cv.bounds[h - 1].0 + cv.bounds[h - 1].1;
    let mut current: Option<FloatImage> = None;
    let mut offset = 0;
    if need_h {
        let ch = coeffs(src.width, bx[0], bx[2], w, filter);
        let rows = last - first;
        let mut out = FloatImage::new(w, rows);
        for yy in 0..rows {
            let line = &src.data[(yy + first) * src.width..(yy + first + 1) * src.width];
            for (xx, &(xmin, xmax)) in ch.bounds.iter().enumerate() {
                let k = &ch.kk[xx * ch.ksize..xx * ch.ksize + xmax];
                let mut ss = 0.0f64;
                for (x, &kx) in k.iter().enumerate() {
                    ss += f64::from(line[x + xmin]) * kx;
                }
                out.data[yy * w + xx] = ss as f32;
            }
        }
        current = Some(out);
        offset = first;
    }
    if need_v {
        let from = current.as_ref().unwrap_or(src);
        let mut out = FloatImage::new(from.width, h);
        for (yy, &(ymin, ymax)) in cv.bounds.iter().enumerate() {
            let k = &cv.kk[yy * cv.ksize..yy * cv.ksize + ymax];
            for xx in 0..from.width {
                let mut ss = 0.0f64;
                for (y, &ky) in k.iter().enumerate() {
                    ss += f64::from(from.data[(y + ymin - offset) * from.width + xx]) * ky;
                }
                out.data[yy * from.width + xx] = ss as f32;
            }
        }
        return out;
    }
    current.unwrap_or_else(|| src.clone())
}

impl FloatImage {
    /// Image.resize for mode F (no reducing_gap: Libre Panel asks for none there).
    pub fn resize(
        &self,
        w: usize,
        h: usize,
        filter: Filter,
        bounds: Option<(f64, f64, f64, f64)>,
    ) -> Result<FloatImage> {
        let full = (0.0, 0.0, self.width as f64, self.height as f64);
        let bx = bounds.unwrap_or(full);
        if (w, h) == (self.width, self.height) && bx == full {
            return Ok(self.clone());
        }
        let bx = [bx.0 as f32, bx.1 as f32, bx.2 as f32, bx.3 as f32];
        if w < 1 || h < 1 {
            return error("height and width must be > 0");
        }
        if bx[0] < 0.0 || bx[1] < 0.0 {
            return error("box offset can't be negative");
        }
        if bx[2] > self.width as f32 || bx[3] > self.height as f32 {
            return error("box can't exceed original image size");
        }
        if bx[2] - bx[0] < 0.0 || bx[3] - bx[1] < 0.0 {
            return error("box can't be empty");
        }
        let whole = |v: f32| v - (v as i32) as f32 == 0.0;
        if whole(bx[0]) && bx[2] - bx[0] == w as f32 && whole(bx[1]) && bx[3] - bx[1] == h as f32 {
            return Ok(self.crop(bx[0] as usize, bx[1] as usize, w, h));
        }
        if filter == Filter::Nearest {
            let a0 = f64::from(bx[2] - bx[0]) / w as f64;
            let a4 = f64::from(bx[3] - bx[1]) / h as f64;
            let mut out = FloatImage::new(w, h);
            let mut yo = f64::from(bx[1]) + a4 * 0.5;
            for y in 0..h {
                let mut xo = f64::from(bx[0]) + a0 * 0.5;
                for x in 0..w {
                    let (xi, yi) = (xo as i64, yo as i64);
                    if xo >= 0.0
                        && yo >= 0.0
                        && (xi as usize) < self.width
                        && (yi as usize) < self.height
                    {
                        out.data[y * w + x] = self.data[yi as usize * self.width + xi as usize];
                    }
                    xo += a0;
                }
                yo += a4;
            }
            return Ok(out);
        }
        Ok(resample_f(self, w, h, filter, bx))
    }

    fn crop(&self, x0: usize, y0: usize, w: usize, h: usize) -> FloatImage {
        let mut out = FloatImage::new(w, h);
        for y in 0..h {
            for x in 0..w {
                out.data[y * w + x] = self.data[(y0 + y) * self.width + x0 + x];
            }
        }
        out
    }
}
