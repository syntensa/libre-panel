//! font_render (_imagingft.c): a text drawn into a mask as Pillow 13.0 draws it. Its numbers
//! are C's: the stroke's width and the start's fractions are single-precision floats, the pen
//! 26.6 ints, and the glyphs blend into the mask with Pillow's integer arithmetic.

use std::ffi::c_int;

use lp_pixels::{Image, Mode};

use crate::{ft, pixel, Error, Font, Kind, Result};

/// Image.MAX_IMAGE_PIXELS: a mask of more than twice as many is refused.
const MAX_IMAGE_PIXELS: i64 = 1024 * 1024 * 1024 / 4 / 3;

/// What render is asked (its arguments after the text's).
pub(crate) struct Ask<'a> {
    pub text: &'a str,
    pub mode: &'a str,
    pub direction: Option<&'a str>,
    pub features: Option<&'a [String]>,
    pub language: Option<&'a str>,
    pub stroke_width: f32,
    pub stroke_filled: bool,
    pub anchor: Option<&'a str>,
    pub ink: i64,
    pub start: (f32, f32),
}

fn clip8(v: i64) -> u8 {
    v.clamp(0, 255) as u8
}

/// MULDIV255: a * b / 255, rounded as Pillow rounds it.
fn muldiv255(a: u32, b: u32) -> u32 {
    let tmp = a * b + 128;
    ((tmp >> 8) + tmp) >> 8
}

/// BLEND: in1 toward in2 by mask / 255.
fn blend(mask: u32, in1: u32, in2: u32) -> u8 {
    let tmp = in1 * (255 - mask) + in2 * mask + 128;
    (((tmp >> 8) + tmp) >> 8) as u8
}

/// What FreeType made that has to be given back, whatever way render ends.
struct Held<'a> {
    ft: &'a ft::Ft,
    stroker: ft::Stroker,
    glyph: ft::Glyph,
    converted: Option<ft::Bitmap>,
}

impl Drop for Held<'_> {
    fn drop(&mut self) {
        // SAFETY: each made by FreeType for this render, given back once
        unsafe {
            if !self.glyph.is_null() {
                (self.ft.done_glyph)(self.glyph);
            }
            if let Some(bitmap) = self.converted.as_mut() {
                self.ft.bitmap_done(bitmap);
            }
            if !self.stroker.is_null() {
                (self.ft.stroker_done)(self.stroker);
            }
        }
    }
}

/// The fill function of getmask2: a mask of the size, after Image's check for bombs.
fn fill(colour: bool, width: i64, height: i64) -> Result<Image> {
    let pixels = width.max(1).saturating_mul(height.max(1));
    if pixels > 2 * MAX_IMAGE_PIXELS {
        return Err(Error::new(
            Kind::Bomb,
            format!(
                "Image size ({pixels} pixels) exceeds limit of {} pixels, could be \
                 decompression bomb DOS attack.",
                2 * MAX_IMAGE_PIXELS
            ),
        ));
    }
    if width < 0 || height < 0 {
        return Err(Error::new(Kind::Value, "bad image size"));
    }
    let mode = if colour { Mode::RGBA } else { Mode::L };
    Ok(Image::blank(mode, width as usize, height as usize))
}

pub(crate) fn render(font: &Font, ask: Ask<'_>) -> Result<(Image, (i32, i64))> {
    let ft = ft::ft().map_err(|e| Error::new(Kind::Os, e))?;
    let face = font.face;
    let mask = ask.mode == "1";
    let colour = ask.mode == "RGBA";
    let foreground = (ask.ink as u32).to_ne_bytes();
    if colour {
        if let Some(set) = ft.palette_set_foreground_color {
            let ink = ft::Color {
                red: foreground[0],
                green: foreground[1],
                blue: foreground[2],
                alpha: 255, // (the ink's alpha is ImageDraw.text's)
            };
            // SAFETY: a live face
            unsafe { set(face, ink) };
        }
    }
    let glyphs = font.layout_text(
        ask.text,
        ask.mode,
        ask.direction,
        ask.features,
        ask.language,
    )?;
    let stroke = ask.stroke_width;
    let mut flags: c_int = if stroke != 0.0 {
        ft::LOAD_NO_BITMAP
    } else {
        ft::LOAD_DEFAULT
    };
    if mask {
        flags |= ft::LOAD_TARGET_MONO;
    }
    if colour {
        flags |= ft::LOAD_COLOR;
    }
    let horizontal = ask.direction != Some("ttb");
    let e = font.extent(ask.anchor, horizontal, &glyphs, flags)?;
    let (x_start, y_start) = ask.start;
    let width = (e.width as f64 + f64::from(stroke * 2.0 + x_start).ceil()) as i64;
    let height = (e.height as f64 + f64::from(stroke * 2.0 + y_start).ceil()) as i64;
    let mut image = fill(colour, width, height)?;
    let x_offset = f64::from(e.x_offset as f32 - stroke).round() as i32;
    let y_offset = f64::from(e.y_offset as f32 - stroke).round() as i64;
    if glyphs.is_empty() || width == 0 || height == 0 {
        return Ok((image, (x_offset, y_offset)));
    }

    let mut held = Held {
        ft,
        stroker: std::ptr::null_mut(),
        glyph: std::ptr::null_mut(),
        converted: None,
    };
    if stroke != 0.0 {
        held.stroker = ft.stroker().map_err(Error::freetype)?;
        let radius = f64::from(stroke * 64.0).round() as ft::Fixed;
        // SAFETY: a live stroker
        unsafe {
            (ft.stroker_set)(
                held.stroker,
                radius,
                ft::STROKER_LINECAP_ROUND,
                ft::STROKER_LINEJOIN_ROUND,
                0,
            )
        };
    }

    // where the bitmaps start: the leftmost and the topmost of them (as getsize found them)
    let (mut x, mut y, mut x_min, mut y_max) = (0i32, 0i32, 0i32, 0i32);
    for g in &glyphs {
        let px = pixel(i64::from(x.wrapping_add(g.x_offset))) as i32;
        let py = pixel(i64::from(y.wrapping_add(g.y_offset))) as i32;
        // SAFETY: a live face
        let error = unsafe { (ft.load_glyph)(face, g.index, flags | ft::LOAD_RENDER) };
        if error != 0 {
            return Err(Error::freetype(error));
        }
        // SAFETY: the face's slot, just loaded
        let slot = unsafe { &*font.face().glyph };
        y_max = y_max.max(slot.bitmap_top + py);
        x_min = x_min.min(slot.bitmap_left + px);
        x = x.wrapping_add(g.x_advance);
        y = y.wrapping_add(g.y_advance);
    }

    // the pen at the text's origin
    let mut x = f64::from(((-x_min) as f32 + stroke + x_start) * 64.0).round() as i32;
    let mut y = f64::from(((-y_max) as f32 + -stroke - y_start) * 64.0).round() as i32;
    if held.stroker.is_null() {
        flags |= ft::LOAD_RENDER;
    }

    let (iw, ih) = (image.width() as i32, image.height() as i32);
    let bands = if colour { 4 } else { 1 };
    let stride = image.width() * bands;
    let data = image.as_bytes_mut();
    for g in &glyphs {
        let px = pixel(i64::from(x.wrapping_add(g.x_offset))) as i32;
        let py = pixel(i64::from(y.wrapping_add(g.y_offset))) as i32;
        // SAFETY: a live face
        let error = unsafe { (ft.load_glyph)(face, g.index, flags) };
        if error != 0 {
            return Err(Error::freetype(error));
        }
        // SAFETY: the face's slot, just loaded
        let slot = unsafe { &*font.face().glyph };
        let (mut bitmap, xx, mut yy);
        if !held.stroker.is_null() {
            // SAFETY: FreeType's glyph functions on the slot and the glyph made of it; the
            // glyph is held, so that it is done whatever happens
            let error = unsafe {
                let mut error = (ft.get_glyph)(font.face().glyph, &mut held.glyph);
                if error == 0 {
                    error = if ask.stroke_filled {
                        (ft.glyph_stroke_border)(&mut held.glyph, held.stroker, 0, 1)
                    } else {
                        (ft.glyph_stroke)(&mut held.glyph, held.stroker, 1)
                    };
                }
                if error == 0 {
                    let origin = ft::Vector::default();
                    error =
                        (ft.glyph_to_bitmap)(&mut held.glyph, ft::RENDER_MODE_NORMAL, &origin, 1);
                }
                error
            };
            if error != 0 {
                return Err(Error::freetype(error));
            }
            // SAFETY: FT_Glyph_To_Bitmap made it a bitmap glyph
            let glyph = unsafe { &*(held.glyph as *const ft::BitmapGlyphRec) };
            bitmap = glyph.bitmap;
            xx = px + glyph.left;
            yy = -(py + glyph.top);
        } else {
            bitmap = slot.bitmap;
            xx = px + slot.bitmap_left;
            yy = -(py + slot.bitmap_top);
        }

        if !bitmap.buffer.is_null() {
            let scale: u32 = match bitmap.pixel_mode {
                ft::PIXEL_MODE_MONO => 255,
                ft::PIXEL_MODE_GRAY2 => 255 / 3,
                ft::PIXEL_MODE_GRAY4 => 255 / 15,
                _ => 1,
            };
            match bitmap.pixel_mode {
                ft::PIXEL_MODE_MONO | ft::PIXEL_MODE_GRAY2 | ft::PIXEL_MODE_GRAY4 => {
                    let converted = held.converted.get_or_insert_with(|| {
                        let mut b = ft::Bitmap::empty();
                        // SAFETY: an FT_Bitmap to set up
                        unsafe { (ft.bitmap_init)(&mut b) };
                        b
                    });
                    let error = ft.convert(&bitmap, converted);
                    if error != 0 {
                        return Err(Error::freetype(error));
                    }
                    bitmap = *converted;
                }
                ft::PIXEL_MODE_GRAY => {}
                ft::PIXEL_MODE_BGRA if colour => {}
                _ => return Err(Error::new(Kind::Os, "unsupported bitmap pixel mode")),
            }
            let bgra = bitmap.pixel_mode == ft::PIXEL_MODE_BGRA;
            // the bitmap's columns within the mask
            let x0 = if xx < 0 { -xx } else { 0 };
            let mut x1 = bitmap.width as i32;
            if xx + x1 > iw {
                x1 = iw - xx;
            }
            let per = if bgra { 4 } else { 1 };
            let mut source = bitmap.buffer as *const u8;
            for _ in 0..bitmap.rows {
                if yy >= 0 && yy < ih && x0 < x1 {
                    // SAFETY: a row of FreeType's bitmap, `width` pixels of `per` bytes
                    let row =
                        unsafe { std::slice::from_raw_parts(source, bitmap.width as usize * per) };
                    let target = &mut data[yy as usize * stride..(yy as usize + 1) * stride];
                    for k in x0..x1 {
                        let at = (xx + k) as usize * bands;
                        let k = k as usize;
                        if colour && bgra {
                            let alpha = u32::from(row[k * 4 + 3]);
                            if alpha == 0 {
                                continue;
                            }
                            let unpremultiplied =
                                |v: u8| clip8(i64::from(255 * i32::from(v) / alpha as i32));
                            let red = unpremultiplied(row[k * 4 + 2]);
                            let green = unpremultiplied(row[k * 4 + 1]);
                            let blue = unpremultiplied(row[k * 4]);
                            put(&mut target[at..at + 4], alpha, [red, green, blue]);
                        } else if colour {
                            let alpha = u32::from(row[k]) * scale;
                            if alpha > 0 {
                                let ink = [foreground[0], foreground[1], foreground[2]];
                                put(&mut target[at..at + 4], alpha, ink);
                            }
                        } else {
                            let alpha = u32::from(row[k]) * scale;
                            if alpha > 0 {
                                let t = &mut target[at];
                                *t = if *t > 0 {
                                    clip8(i64::from(alpha + muldiv255(u32::from(*t), 255 - alpha)))
                                } else {
                                    alpha as u8
                                };
                            }
                        }
                    }
                }
                // SAFETY: the next row of the bitmap (or one past its last, not read)
                source = source.wrapping_offset(bitmap.pitch as isize);
                yy += 1;
            }
        }
        x = x.wrapping_add(g.x_advance);
        y = y.wrapping_add(g.y_advance);
        if !held.stroker.is_null() {
            // SAFETY: the glyph made for this one, done before the next
            unsafe { (ft.done_glyph)(held.glyph) };
            held.glyph = std::ptr::null_mut();
        }
    }
    drop(held);
    Ok((image, (x_offset, y_offset)))
}

/// A colour of `alpha` into an RGBA pixel of the colour mask: blended where the pixel has
/// some already, else put as it is.
fn put(target: &mut [u8], alpha: u32, colour: [u8; 3]) {
    if target[3] > 0 {
        for c in 0..3 {
            target[c] = blend(alpha, u32::from(target[c]), u32::from(colour[c]));
        }
        target[3] = clip8(i64::from(
            alpha + muldiv255(u32::from(target[3]), 255 - alpha),
        ));
    } else {
        target[..3].copy_from_slice(&colour);
        target[3] = alpha as u8;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pillow_s_rounding_of_26_6_and_of_its_blends() {
        // PIXEL rounds half up, negative numbers too (an arithmetic shift)
        assert_eq!(
            [-97, -96, -33, -32, -1, 0, 31, 32, 95, 96].map(pixel),
            [-2, -1, -1, 0, 0, 0, 0, 1, 1, 2]
        );
        assert_eq!(muldiv255(255, 255), 255);
        assert_eq!(muldiv255(128, 255), 128);
        assert_eq!(muldiv255(1, 127), 0);
        assert_eq!(muldiv255(1, 128), 1);
        assert_eq!(blend(0, 200, 10), 200);
        assert_eq!(blend(255, 200, 10), 10);
        assert_eq!(blend(128, 0, 255), 128);
    }
}
