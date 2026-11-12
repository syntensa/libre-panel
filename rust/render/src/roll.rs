//! Clocks whose digits roll or fade (render/renderer.py's _draw_clock in a moving frame, with
//! _changing, _digit_band, _moved and _ramp): for digits_seconds after the second turns, each
//! letter that changes rolls (the old one up and out, the new one in from below, soft at the
//! edges of the band the digits live in) or fades where it stands, as SPUR II's clocks do; the
//! letters that stay stand where they are. At any other time the text is as it stands.

use lp_pixels::{chops, Filter, Image, Mode, Transform};
use lp_py::Dict;
use lp_text::text::TextFace;

use crate::compose;
use crate::piece::{Piece, Result};
use crate::renderer::Renderer;
use crate::shapes::ink;
use crate::text::{failed, Ink, DIGITS};
use crate::widget;

/// DIGITS_S: how long the digits take after the second turns (s), where a clock does not say.
pub(crate) const DIGITS_S: f64 = 0.42;
/// DIGITS_TRAVEL: how far a letter rolls, of the band the digits live in.
const DIGITS_TRAVEL: f64 = 0.92;

/// _ramp: an opacity over `h` rows, full but soft over `edge` rows at the top and the bottom,
/// `w` columns of it (its column resized as Image.resize resizes it, bicubic).
fn ramp(w: usize, h: usize, edge: i64) -> Result<Image> {
    let edge = edge as f64;
    let column: Vec<u8> = (0..h)
        .map(|y| {
            let (a, b) = (y as f64 / edge, (h as f64 - 1.0 - y as f64) / edge);
            // (Python's max(0.0, min(1.0, a, b)))
            let least = if a < 1.0 { a } else { 1.0 };
            let least = if b < least { b } else { least };
            let share = if least > 0.0 { least } else { 0.0 };
            lp_py::round(255.0 * share) as u8
        })
        .collect();
    let column = Image::from_bytes(Mode::L, 1, h, column)?;
    Ok(column.resize(w, h, Filter::Bicubic, None, None)?)
}

/// _moved: `image` moved down by `dy` px, its fraction too (a fraction blends two rows, as a
/// letter in motion is: bilinear, a row taller), and the whole row it goes to; a letter all in
/// `colour` (the renderer's own) moves its opacity alone and stays all in its colour.
fn moved(image: &Image, dy: f64, colour: Option<[u8; 3]>) -> Result<(Image, i64)> {
    let row = dy.floor();
    let part = dy - row;
    let row = row as i64;
    if part < 1.0 / 64.0 {
        return Ok((image.clone(), row));
    }
    if part > 63.0 / 64.0 {
        return Ok((image.clone(), row + 1));
    }
    let size = (image.width(), image.height() + 1);
    let shift = Transform::Affine([1.0, 0.0, 0.0, 0.0, 1.0, -part]);
    if let Some([r, g, b]) = colour {
        let opacity = image
            .getchannel(3)?
            .transform(size, shift, Filter::Bilinear, None)?;
        let mut out = Image::new(Mode::RGBA, size.0, size.1, &[r, g, b, 0]);
        out.putalpha(&opacity)?;
        return Ok((out, row));
    }
    Ok((image.transform(size, shift, Filter::Bilinear, None)?, row))
}

impl Renderer {
    /// _digit_band: the box the digits fill (getbbox, from the top left) in the font of
    /// `reference` at `size`, kept.
    fn digit_band(&self, reference: &str, size: i64) -> Result<(f64, f64, f64, f64)> {
        let key = (reference.to_string(), size);
        if let Some(found) = self.digit_bands.borrow().get(&key) {
            return Ok(*found);
        }
        let font = self.font(reference, size)?;
        let found = font
            .getbbox(DIGITS, "", None, None, None, 0.0, Some("la"))
            .map_err(failed)?;
        self.digit_bands.borrow_mut().insert(key, found);
        Ok(found)
    }

    /// _changing: the clock `e` of the way from `old` to `new`, letter by letter: the letters
    /// that stay where they stand, those that change rolling, or with `kind` "fade" fading; None
    /// where the two are not laid out alike (another size, another width).
    pub(crate) fn changing(
        &self,
        w: &Dict,
        old: &str,
        new: &str,
        colour: &str,
        e: f64,
        kind: &str,
    ) -> Result<Option<Piece>> {
        let (old, size) = self.fit(w, old)?;
        let (new, size_new) = self.fit(w, new)?;
        if size != size_new {
            return Ok(None);
        }
        let before = self.letters(w, &old, colour, size)?;
        let after = self.letters(w, &new, colour, size)?;
        let (Some(before), Some(after)) = (before, after) else {
            return Ok(None);
        };
        if old.chars().count() != new.chars().count() {
            return Ok(None);
        }
        if (before.size, before.left) != (after.size, after.left) {
            return Ok(None);
        }
        let (lw, lh) = (after.size.0, after.size.1 as i64);
        // (the band a digit lives in, with room above and below where the letters fade: outside
        // the resting digit, so it does not end its roll dimmed and then light up)
        let (_, top, _, bottom) = self.digit_band(widget::text(w, "font"), size)?;
        let (top, bottom) = (top.floor() as i64, bottom.ceil() as i64);
        let edge = (bottom - top).div_euclid(5).max(6);
        let (band_top, band_h) = (top - edge, bottom - top + 2 * edge);
        let (up, down) = ((-band_top).max(0), (band_top + band_h - lh).max(0));
        let mut layer = Image::blank(Mode::RGBA, lw, (lh + up + down) as usize);
        let travel = DIGITS_TRAVEL * band_h as f64;
        let [r, g, b, _] = ink(self.color(Some(colour), 1.0)?.unwrap_or([0, 0, 0, 0]));
        // (the renderer's own letters are all in their colour; a finish's are not: no ink known)
        let own = |inks: &[Ink]| -> Vec<bool> {
            inks.iter().map(|i| !matches!(i, Ink::Unknown)).collect()
        };
        let (own_before, own_after) = (own(&before.inks), own(&after.inks));
        let letters = old.chars().zip(new.chars());
        let glyphs = before.glyphs.iter().zip(&after.glyphs);
        for (((was, comes), (one, other)), (&one_own, &other_own)) in
            letters.zip(glyphs).zip(own_before.iter().zip(&own_after))
        {
            if was == comes {
                if let Some((image, x, y)) = other {
                    compose::paste(&mut layer, image, *x, *y + up, false)?;
                }
                continue;
            }
            let shown: Vec<&(Image, i64, i64)> = [one, other].into_iter().flatten().collect();
            let (Some(x0), Some(x1)) = (
                shown.iter().map(|g| g.1).min(),
                shown.iter().map(|g| g.1 + g.0.width() as i64).max(),
            ) else {
                continue;
            };
            let width = (x1 - x0) as usize;
            if kind == "fade" {
                // (each where it stands, the old one out as the new one comes in)
                let mut pair = Vec::with_capacity(2);
                for glyph in [one, other] {
                    let mut band = Image::blank(Mode::RGBa, width, lh as usize);
                    if let Some((image, x, y)) = glyph {
                        band.paste(&image.convert(Mode::RGBa)?, x - x0, *y, None)?;
                    }
                    pair.push(band);
                }
                let mixed = chops::blend(&pair[0], &pair[1], e as f32)?.convert(Mode::RGBA)?;
                compose::paste(&mut layer, &mixed, x0, up, false)?;
                continue;
            }
            let mut band = Image::blank(Mode::RGBA, width, band_h as usize);
            let rolls = [
                (one, one_own, -e * travel),
                (other, other_own, (1.0 - e) * travel),
            ];
            for (glyph, own, dy) in rolls {
                if let Some((image, x, y)) = glyph {
                    let colour = own.then_some([r, g, b]);
                    let (image, row) = moved(image, (*y - band_top) as f64 + dy, colour)?;
                    compose::paste(&mut band, &image, x - x0, row, false)?;
                }
            }
            let soft = ramp(width, band_h as usize, edge)?;
            band.putalpha(&chops::multiply(&band.getchannel(3)?, &soft)?)?;
            compose::paste(&mut layer, &band, x0, band_top + up, false)?;
        }
        let mut piece = Piece::new(layer, after.left, after.y - up);
        piece.hit = Some(after.hit);
        Ok(Some(piece))
    }
}
