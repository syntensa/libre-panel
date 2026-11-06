//! ImageChops and Image.blend, Image.composite (libImaging/Chops.c, Blend.c, Offset.c): two
//! pictures of as many bands, value by value; the result in the first one's mode, as large as
//! both cover.

use crate::{error, Image, Result};

/// Chops.c's CHOP: ``op`` on each pair of values, clipped to 0..255.
fn chop(a: &Image, b: &Image, op: impl Fn(i32, i32) -> i32) -> Result<Image> {
    if a.bands() != b.bands() {
        return error("images do not match");
    }
    let (w, h) = (a.width.min(b.width), a.height.min(b.height));
    let n = a.bands();
    let mut out = Image::blank(a.mode, w, h);
    for y in 0..h {
        let (ra, rb) = (&a.row(y)[..w * n], &b.row(y)[..w * n]);
        for ((o, &x1), &x2) in out.row_mut(y).iter_mut().zip(ra).zip(rb) {
            *o = op(i32::from(x1), i32::from(x2)).clamp(0, 255) as u8;
        }
    }
    Ok(out)
}

/// ImageChops.multiply: a * b / 255, rounded down.
pub fn multiply(a: &Image, b: &Image) -> Result<Image> {
    chop(a, b, |x, y| x * y / 255)
}

/// ImageChops.screen: 255 - (255 - a) * (255 - b) / 255.
pub fn screen(a: &Image, b: &Image) -> Result<Image> {
    chop(a, b, |x, y| 255 - (255 - x) * (255 - y) / 255)
}

/// ImageChops.add: (a + b) / scale + offset, in floats, truncated (Chops.c).
pub fn add(a: &Image, b: &Image, scale: f32, offset: i32) -> Result<Image> {
    chop(a, b, |x, y| ((x + y) as f32 / scale + offset as f32) as i32)
}

/// ImageChops.subtract: (a - b) / scale + offset.
pub fn subtract(a: &Image, b: &Image, scale: f32, offset: i32) -> Result<Image> {
    chop(a, b, |x, y| ((x - y) as f32 / scale + offset as f32) as i32)
}

/// ImageChops.lighter: the larger of each pair.
pub fn lighter(a: &Image, b: &Image) -> Result<Image> {
    chop(a, b, |x, y| x.max(y))
}

/// ImageChops.darker: the smaller of each pair.
pub fn darker(a: &Image, b: &Image) -> Result<Image> {
    chop(a, b, |x, y| x.min(y))
}

/// ImageChops.difference: |a - b|.
pub fn difference(a: &Image, b: &Image) -> Result<Image> {
    chop(a, b, |x, y| (x - y).abs())
}

/// ImageChops.add_modulo, subtract_modulo: wrapping around 256.
pub fn add_modulo(a: &Image, b: &Image) -> Result<Image> {
    chop(a, b, |x, y| (x + y) & 255)
}

pub fn subtract_modulo(a: &Image, b: &Image) -> Result<Image> {
    chop(a, b, |x, y| (x - y) & 255)
}

/// Image.blend(a, b, alpha): a + alpha * (b - a) in floats, truncated (Blend.c), clipped where
/// alpha lies beyond 0..1.
pub fn blend(a: &Image, b: &Image, alpha: f32) -> Result<Image> {
    if a.bands() != b.bands() || a.size() != b.size() {
        return error("images do not match");
    }
    if alpha == 0.0 {
        return Ok(a.clone());
    }
    if alpha == 1.0 {
        return Ok(b.clone());
    }
    let inside = (0.0..=1.0).contains(&alpha);
    let mut out = Image::blank(a.mode, a.width, a.height);
    for ((o, &x1), &x2) in out.data.iter_mut().zip(&a.data).zip(&b.data) {
        let v = i32::from(x1) as f32 + alpha * (i32::from(x2) - i32::from(x1)) as f32;
        *o = if inside || (v > 0.0 && v < 255.0) {
            v as u8
        } else if v <= 0.0 {
            0
        } else {
            255
        };
    }
    Ok(out)
}

/// Image.composite(a, b, mask): ``a`` pasted over a copy of ``b`` through ``mask``.
pub fn composite(a: &Image, b: &Image, mask: &Image) -> Result<Image> {
    let mut out = b.clone();
    out.paste(a, 0, 0, Some(mask))?;
    Ok(out)
}

impl Image {
    /// ImageChops.offset(image, x, y): the picture moved, what leaves on one side coming in on
    /// the other (Offset.c, its remainders as C takes them).
    pub fn offset(&self, x: i64, y: i64) -> Image {
        let (w, h) = (self.width as i64, self.height as i64);
        let mut out = Image::blank(self.mode, self.width, self.height);
        if w == 0 || h == 0 {
            return out;
        }
        let mut xo = w - x % w;
        if xo < 0 {
            xo += w;
        }
        let mut yo = h - y % h;
        if yo < 0 {
            yo += h;
        }
        let b = self.bands();
        for row in 0..h {
            let yi = (row + yo) % h;
            for col in 0..w {
                let xi = (col + xo) % w;
                let (d, s) = (((row * w + col) as usize) * b, ((yi * w + xi) as usize) * b);
                out.data[d..d + b].copy_from_slice(&self.data[s..s + b]);
            }
        }
        out
    }
}
