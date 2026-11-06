//! Image.paste: an image or a colour into this one, through a mask or none
//! (libImaging/Paste.c's paste, paste_mask_L/RGBA/RGBa, fill, fill_mask_L/RGBA; PIL/Image.py's
//! way of choosing what is pasted).

use crate::ints::{blend, preblend};
use crate::{error, Image, Mode, Result};

/// The part of a box that lies on an image of ``w`` by ``h``: where it goes (dx, dy), where it
/// comes from in the box (sx, sy), how much of it (xsize, ysize). None where nothing does.
fn clipped(w: usize, h: usize, x0: i64, y0: i64, x1: i64, y1: i64) -> Option<[usize; 6]> {
    let (mut xsize, mut ysize) = (x1 - x0, y1 - y0);
    let (mut dx, mut dy, mut sx, mut sy) = (x0, y0, 0, 0);
    if dx < 0 {
        xsize += dx;
        sx = -dx;
        dx = 0;
    }
    if dx + xsize > w as i64 {
        xsize = w as i64 - dx;
    }
    if dy < 0 {
        ysize += dy;
        sy = -dy;
        dy = 0;
    }
    if dy + ysize > h as i64 {
        ysize = h as i64 - dy;
    }
    if xsize <= 0 || ysize <= 0 {
        return None;
    }
    Some([
        dx as usize,
        dy as usize,
        sx as usize,
        sy as usize,
        xsize as usize,
        ysize as usize,
    ])
}

/// A mask's weight at a pixel and how it mixes (Paste.c's three kinds of matte).
#[derive(Clone, Copy)]
enum Matte {
    /// L: the mask's value mixes (BLEND)
    Plain,
    /// RGBA: its alpha mixes (BLEND)
    Alpha,
    /// RGBa: its alpha, the pasted colours premultiplied (PREBLEND)
    Premultiplied,
}

fn matte_of(mask: &Image) -> Result<Matte> {
    match mask.mode {
        Mode::L => Ok(Matte::Plain),
        Mode::RGBA => Ok(Matte::Alpha),
        Mode::RGBa => Ok(Matte::Premultiplied),
        Mode::RGB => error("bad transparency mask"),
    }
}

#[inline(always)]
fn weight(mask: &Image, matte: Matte, i: usize) -> u32 {
    match matte {
        Matte::Plain => u32::from(mask.data[i]),
        Matte::Alpha | Matte::Premultiplied => u32::from(mask.data[i * 4 + 3]),
    }
}

impl Image {
    /// Image.paste(im, (x, y), mask): ``im`` with its corner at (x, y); through ``mask`` (of
    /// im's size: L, or the alpha of RGBA or RGBa) where one is given. Image.py converts ``im``
    /// to this image's mode first, but for RGBA and RGBa into RGB, whose colours Paste.c takes
    /// as they are.
    pub fn paste(&mut self, im: &Image, x: i64, y: i64, mask: Option<&Image>) -> Result<()> {
        let converted;
        let im = if im.mode == self.mode
            || (self.mode == Mode::RGB && matches!(im.mode, Mode::RGBA | Mode::RGBa))
        {
            im
        } else {
            converted = im.convert(self.mode)?;
            &converted
        };
        if mask.is_some_and(|m| m.size() != im.size()) {
            return error("images do not match");
        }
        let Some([dx, dy, sx, sy, xsize, ysize]) = clipped(
            self.width,
            self.height,
            x,
            y,
            x + im.width as i64,
            y + im.height as i64,
        ) else {
            return Ok(()); // (Paste.c looks at the mask's mode only where something is pasted)
        };
        let matte = mask.map(matte_of).transpose()?;
        let (db, sb) = (self.bands(), im.bands());
        for row in 0..ysize {
            for col in 0..xsize {
                let s = (sy + row) * im.width + sx + col;
                let d = ((dy + row) * self.width + dx + col) * db;
                let from = &im.data[s * sb..s * sb + sb];
                match (matte, mask) {
                    (Some(matte), Some(mask)) => {
                        let a = weight(mask, matte, s);
                        for (out, &i) in self.data[d..d + db].iter_mut().zip(from) {
                            let (o, i) = (u32::from(*out), u32::from(i));
                            *out = match matte {
                                Matte::Premultiplied => preblend(a, o, i),
                                _ => blend(a, o, i),
                            };
                        }
                    }
                    _ => self.data[d..d + db].copy_from_slice(&from[..db]),
                }
            }
        }
        Ok(())
    }

    /// Image.paste(colour, (x0, y0, x1, y1), mask): the box filled with ``colour`` (one value a
    /// band), through ``mask`` (of the box's size) where one is given (ImagingFill2).
    pub fn paste_colour(
        &mut self,
        colour: &[u8],
        bounds: (i64, i64, i64, i64),
        mask: Option<&Image>,
    ) -> Result<()> {
        let (x0, y0, x1, y1) = bounds;
        if colour.len() != self.bands() {
            return error("wrong number of bands in the colour");
        }
        if let Some(m) = mask {
            if (m.width as i64, m.height as i64) != (x1 - x0, y1 - y0) {
                return error("images do not match");
            }
        }
        let Some([dx, dy, sx, sy, xsize, ysize]) = clipped(self.width, self.height, x0, y0, x1, y1)
        else {
            return Ok(());
        };
        if let Some(m) = mask {
            if !matches!(m.mode, Mode::L | Mode::RGBA) {
                return error(format!("a fill through a mask of {}", m.mode));
            }
        }
        let db = self.bands();
        let alpha_channel = self.mode.has_alpha();
        for row in 0..ysize {
            for col in 0..xsize {
                let d = ((dy + row) * self.width + dx + col) * db;
                let Some(mask) = mask else {
                    self.data[d..d + db].copy_from_slice(colour);
                    continue;
                };
                let s = (sy + row) * mask.width + sx + col;
                match mask.mode {
                    Mode::L => {
                        // fill_mask_L: where this image is clear, its colours take the ink
                        // whole wherever the mask has any of it (the alpha mixes as given)
                        let m = mask.data[s];
                        let clear = self.data[d + db - 1] == 0;
                        let px = &mut self.data[d..d + db];
                        for (b, (out, &ink)) in px.iter_mut().zip(colour).enumerate() {
                            let mut w = u32::from(m);
                            if alpha_channel && b != 3 && m != 0 && clear {
                                w = 255;
                            }
                            *out = blend(w, u32::from(*out), u32::from(ink));
                        }
                    }
                    Mode::RGBA => {
                        let w = u32::from(mask.data[s * 4 + 3]);
                        for (out, &ink) in self.data[d..d + db].iter_mut().zip(colour) {
                            *out = blend(w, u32::from(*out), u32::from(ink));
                        }
                    }
                    other => return error(format!("a fill through a mask of {other}")),
                }
            }
        }
        Ok(())
    }
}
