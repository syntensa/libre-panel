//! Image.alpha_composite: one RGBA picture over another (libImaging/AlphaComposite.c), and
//! the method's way of laying a part of one at a place in the other (PIL/Image.py).

use crate::ints::shift_div255;
use crate::{error, Image, Mode, Result};

const PRECISION_BITS: u32 = 7;

/// One pixel of ``src`` over one of ``dst`` (both RGBA), as AlphaComposite.c computes it.
/// Over a clear pixel, and where ``src`` is opaque, that is ``src`` itself, to the bit (the
/// other's share is nought and the sums give back each level): taken as it is.
#[inline(always)]
pub(crate) fn over_pixel(dst: &[u8], src: &[u8], out: &mut [u8]) {
    let sa = u32::from(src[3]);
    if sa == 0 {
        out.copy_from_slice(&dst[..4]);
        return;
    }
    if sa == 255 || dst[3] == 0 {
        out.copy_from_slice(&src[..4]);
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

/// Image.alpha_composite(dst, tint) in place, `tint` an RGBA picture of one colour whose alpha
/// is `alpha` (one byte a pixel, row by row): the same bytes, without making the tint.
pub fn tint_over(dst: &mut Image, colour: [u8; 3], alpha: &[u8]) -> Result<()> {
    if dst.mode != Mode::RGBA {
        return error("image has wrong mode");
    }
    if alpha.len() != dst.width * dst.height {
        return error("images do not match");
    }
    let mut tint = Tint::new(colour);
    for (d, &a) in dst.data.chunks_exact_mut(4).zip(alpha) {
        tint.over(d, a);
    }
    Ok(())
}

/// tint_over on a part of `dst`: the colour through `alpha` (`width` bytes a row), its corner
/// at (`left`, `top`), as Image.alpha_composite_at lays a picture of one colour there; nothing
/// of it beyond `dst`.
pub fn tint_over_at(
    dst: &mut Image,
    colour: [u8; 3],
    alpha: &[u8],
    width: usize,
    (left, top): (usize, usize),
) -> Result<()> {
    if dst.mode != Mode::RGBA {
        return error("image has wrong mode");
    }
    if width == 0 || left >= dst.width || top >= dst.height {
        return Ok(());
    }
    let w = width.min(dst.width - left);
    let mut tint = Tint::new(colour);
    for (y, line) in alpha.chunks_exact(width).enumerate().take(dst.height - top) {
        let at = ((top + y) * dst.width + left) * 4;
        let row = &mut dst.data[at..at + w * 4];
        for (d, &a) in row.chunks_exact_mut(4).zip(&line[..w]) {
            tint.over(d, a);
        }
    }
    Ok(())
}

/// One colour laid over pixels through an alpha of each, as over_pixel lays it.
struct Tint {
    colour: [u8; 3],
}

impl Tint {
    fn new(colour: [u8; 3]) -> Tint {
        Tint { colour }
    }

    #[inline(always)]
    fn over(&mut self, d: &mut [u8], a: u8) {
        if a == 0 {
            return; // (over_pixel leaves it as it is)
        }
        let src = [self.colour[0], self.colour[1], self.colour[2], a];
        let was = [d[0], d[1], d[2], d[3]];
        over_pixel(&was, &src, d);
    }
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
        let inside = sx1 <= im.width as i64 && sy1 <= im.height as i64 && sx0 <= sx1 && sy0 <= sy1;
        let cropped;
        let (overlay, ox, oy, w, h) = if inside {
            // (the part read where it is: Image.py's crop of it holds the same pixels)
            (im, sx0, sy0, sx1 - sx0, sy1 - sy0)
        } else {
            cropped = im.crop(sx0, sy0, sx1, sy1);
            let (w, h) = (cropped.width as i64, cropped.height as i64);
            (&cropped, 0, 0, w, h)
        };
        let (dx, dy) = dest;
        // the part of the overlay that lands on this image (Image.py composites the crop of
        // this image at the box and pastes it back, which keeps only that part)
        let (x0, x1) = (dx.max(0), (dx + w).min(self.width as i64));
        let (y0, y1) = (dy.max(0), (dy + h).min(self.height as i64));
        if x0 >= x1 || y0 >= y1 {
            return Ok(());
        }
        let n = (x1 - x0) as usize * 4;
        let mut pixel = [0u8; 4];
        for y in y0..y1 {
            let s = ((y - dy + oy) as usize * overlay.width + (x0 - dx + ox) as usize) * 4;
            let d = (y as usize * self.width + x0 as usize) * 4;
            let from = &overlay.data[s..s + n];
            let to = &mut self.data[d..d + n];
            for (out, src) in to.chunks_exact_mut(4).zip(from.chunks_exact(4)) {
                match src[3] {
                    0 => {}                          // (it stays as it is)
                    255 => out.copy_from_slice(src), // (exactly the source, opaque)
                    _ => {
                        over_pixel(out, src, &mut pixel);
                        out.copy_from_slice(&pixel);
                    }
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A picture of every alpha over every alpha, in colours that change with them.
    fn ground(w: usize, h: usize) -> Image {
        let mut data = Vec::with_capacity(w * h * 4);
        for i in 0..w * h {
            let a = (i * 7 % 256) as u8;
            data.extend_from_slice(&[(i % 251) as u8, (i * 3 % 256) as u8, (i / 5 % 256) as u8]);
            data.push(if i % 3 == 0 { 0 } else { a });
        }
        Image::from_bytes(Mode::RGBA, w, h, data).unwrap()
    }

    /// AlphaComposite.c's sums for one pixel, without a short way.
    fn over_by_sums(dst: &[u8; 4], src: &[u8; 4]) -> [u8; 4] {
        let sa = u32::from(src[3]);
        if sa == 0 {
            return *dst;
        }
        let blend = u32::from(dst[3]) * (255 - sa);
        let outa255 = sa * 255 + blend;
        let coef1 = sa * 255 * 255 * (1 << PRECISION_BITS) / outa255;
        let coef2 = 255 * (1 << PRECISION_BITS) - coef1;
        let mut out = [0u8; 4];
        for c in 0..3 {
            let tmp = u32::from(src[c]) * coef1 + u32::from(dst[c]) * coef2;
            out[c] = (shift_div255(tmp + (0x80 << PRECISION_BITS)) >> PRECISION_BITS) as u8;
        }
        out[3] = shift_div255(outa255 + 0x80) as u8;
        out
    }

    #[test]
    fn over_a_clear_pixel_and_opaque_a_pixel_is_itself_as_the_sums_give_it() {
        let mut out = [0u8; 4];
        for level in 0..=255u8 {
            for a in 0..=255u8 {
                let src = [level, 255 - level, level / 3, a];
                for dst in [
                    [0, 0, 0, 0],
                    [9, 200, 77, 0],
                    [level, 3, 250, a],
                    [1, 2, 3, 255],
                ] {
                    over_pixel(&dst, &src, &mut out);
                    assert_eq!(out, over_by_sums(&dst, &src), "{src:?} over {dst:?}");
                }
            }
        }
    }

    #[test]
    fn a_colour_tinted_over_as_a_picture_of_it_laid_over() {
        let (w, h) = (256, 9);
        let colour = [200, 31, 99];
        let alpha: Vec<u8> = (0..w * h).map(|i| (i * 13 % 256) as u8).collect();
        let mut tint = Image::new(Mode::RGBA, w, h, &[200, 31, 99, 0]);
        for (p, &a) in tint.data.chunks_exact_mut(4).zip(&alpha) {
            p[3] = a;
        }
        let want = alpha_composite(&ground(w, h), &tint).unwrap();
        let mut got = ground(w, h);
        tint_over(&mut got, colour, &alpha).unwrap();
        assert!(got.data == want.data);
        // a part of it, at a place, cut where it leaves the picture
        let (pw, ph) = (40, 6);
        for at in [(0usize, 0usize), (17, 2), (230, 5)] {
            let part: Vec<u8> = alpha[..pw * ph].to_vec();
            let mut laid = Image::new(Mode::RGBA, pw, ph, &[200, 31, 99, 0]);
            for (p, &a) in laid.data.chunks_exact_mut(4).zip(&part) {
                p[3] = a;
            }
            let mut want = ground(w, h);
            let whole = (0, 0, pw as i64, ph as i64);
            want.alpha_composite_at(&laid, (at.0 as i64, at.1 as i64), whole)
                .unwrap();
            let mut got = ground(w, h);
            tint_over_at(&mut got, colour, &part, pw, at).unwrap();
            assert!(got.data == want.data, "{at:?}");
        }
    }
}
