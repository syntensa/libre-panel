//! Image.alpha_composite: one RGBA picture over another (libImaging/AlphaComposite.c), and
//! the method's way of laying a part of one at a place in the other (PIL/Image.py).

use crate::ints::shift_div255;
use crate::{error, Image, Mode, Result};

const PRECISION_BITS: u32 = 7;

/// One pixel of ``src`` over one of ``dst`` (both RGBA), as AlphaComposite.c computes it.
#[inline(always)]
pub(crate) fn over_pixel(dst: &[u8], src: &[u8], out: &mut [u8]) {
    let sa = u32::from(src[3]);
    if sa == 0 {
        out.copy_from_slice(&dst[..4]);
        return;
    }
    let blend = u32::from(dst[3]) * (255 - sa);
    let outa255 = sa * 255 + blend;
    let coef1 = sa * 255 * 255 * (1 << PRECISION_BITS) / outa255;
    let coef2 = 255 * (1 << PRECISION_BITS) - coef1;
    for c in 0..3 {
        let tmp = u32::from(src[c]) * coef1 + u32::from(dst[c]) * coef2;
        out[c] = (shift_div255(tmp + (0x80 << PRECISION_BITS)) >> PRECISION_BITS) as u8;
    }
    out[3] = shift_div255(outa255 + 0x80) as u8;
}

/// Image.alpha_composite(dst, src): ``src`` over ``dst``, both RGBA and of one size.
pub fn alpha_composite(dst: &Image, src: &Image) -> Result<Image> {
    if dst.mode != Mode::RGBA || src.mode != Mode::RGBA {
        return error("image has wrong mode");
    }
    if dst.size() != src.size() {
        return error("images do not match");
    }
    let mut out = Image::blank(Mode::RGBA, dst.width, dst.height);
    for ((d, s), o) in dst
        .data
        .chunks_exact(4)
        .zip(src.data.chunks_exact(4))
        .zip(out.data.chunks_exact_mut(4))
    {
        over_pixel(d, s, o);
    }
    Ok(out)
}

impl Image {
    /// Image.alpha_composite(im, dest, source) in place: the part of ``im`` from
    /// ``source`` (left, top, and right, bottom: Image.py takes im's own size where only two are
    /// given) over this image, its corner at ``dest``; nothing of it beyond this image.
    pub fn alpha_composite_at(
        &mut self,
        im: &Image,
        dest: (i64, i64),
        source: (i64, i64, i64, i64),
    ) -> Result<()> {
        if self.mode != Mode::RGBA || im.mode != Mode::RGBA {
            return error("image has wrong mode");
        }
        let (sx0, sy0, sx1, sy1) = source;
        if sx0.min(sy0).min(sx1).min(sy1) < 0 {
            return error("Source must be non-negative");
        }
        let whole = (0, 0, im.width as i64, im.height as i64);
        let cropped;
        let overlay = if source == whole {
            im
        } else {
            cropped = im.crop(sx0, sy0, sx1, sy1);
            &cropped
        };
        let (w, h) = (overlay.width as i64, overlay.height as i64);
        let (dx, dy) = dest;
        // the part of the overlay that lands on this image (Image.py composites the crop of
        // this image at the box and pastes it back, which keeps only that part)
        let (x0, x1) = (dx.max(0), (dx + w).min(self.width as i64));
        let (y0, y1) = (dy.max(0), (dy + h).min(self.height as i64));
        if x0 >= x1 || y0 >= y1 {
            return Ok(());
        }
        let mut pixel = [0u8; 4];
        for y in y0..y1 {
            for x in x0..x1 {
                let s = ((y - dy) as usize * overlay.width + (x - dx) as usize) * 4;
                let d = (y as usize * self.width + x as usize) * 4;
                over_pixel(&self.data[d..d + 4], &overlay.data[s..s + 4], &mut pixel);
                self.data[d..d + 4].copy_from_slice(&pixel);
            }
        }
        Ok(())
    }
}
