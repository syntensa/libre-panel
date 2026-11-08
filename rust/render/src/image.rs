//! Image widgets (render/renderer.py's _draw_image, _live_image, _fitted, _blurred and _rounded)
//! in a still frame: a picture a sensor source or a service has (an album cover, a game's logo,
//! the playing app's icon), fitted into the widget's box (stretched, contained where it says,
//! or covering it), blurred into a backdrop of its colours, its corners rounded; a widget with
//! no picture a clear piece of its size. The theme's own pictures, slideshows, animated
//! pictures and videos: not drawn here yet.

use std::hash::{DefaultHasher, Hash, Hasher};

use lp_pixels::{chops, Draw, Filter, Image, Mode, ALL_CORNERS};
use lp_py::{Dict, Value};

use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Renderer, MOST_PIXELS};
use crate::shapes::SUPERSAMPLE;
use crate::widget;

/// A picture `w` x `h` (ints of the theme), refused where no machine makes it.
fn sized(w: i64, h: i64) -> Result<(usize, usize)> {
    if w < 0 || h < 0 || w.saturating_mul(h) > MOST_PIXELS {
        return Err(Failed("a picture too large".into()));
    }
    Ok((w as usize, h as usize))
}

/// _fitted: `image` at w x h: stretched, inside it with room left (contain: at the left, in
/// the middle or at the right, by `align`) or filling it (cover), with LANCZOS.
fn fitted(image: &Image, w: usize, h: usize, fit: &str, align: &str) -> Result<Image> {
    match fit {
        "cover" => Ok(image.fit((w, h), Filter::Lanczos, 0.0, (0.5, 0.5))?),
        "contain" => {
            let inner = image.contain((w, h), Filter::Lanczos)?;
            let mut out = Image::blank(Mode::RGBA, w, h);
            let room = w as i64 - inner.width() as i64;
            let left = match align {
                "left" => 0,
                "right" => room,
                _ => room.div_euclid(2),
            };
            let top = (h as i64 - inner.height() as i64).div_euclid(2);
            out.paste(&inner, left, top, None)?;
            Ok(out)
        }
        _ => Ok(image.resize(w, h, Filter::Lanczos, None, None)?),
    }
}

/// _blurred: `image` at w x h (as fitted; 0: its own size) blurred by `radius` px: fitted and
/// blurred at a fraction of the size (premultiplied, so transparent edges stay clean), then
/// drawn up.
fn blurred(image: &Image, radius: f64, w: i64, h: i64, fit: &str, align: &str) -> Result<Image> {
    let (w, h) = if w <= 0 || h <= 0 {
        (image.width(), image.height())
    } else {
        sized(w, h)?
    };
    let scale = ((radius / 4.0).floor() as i64).clamp(1, 8);
    let part = |side: usize| (lp_py::round(side as f64 / scale as f64) as i64).max(1) as usize;
    let small = fitted(&image.convert(Mode::RGBA)?, part(w), part(h), fit, align)?;
    let small = small.convert(Mode::RGBa)?;
    let r = (radius / scale as f64) as f32;
    let small = small.gaussian_blur(r, r)?;
    Ok(small
        .resize(w, h, Filter::Bilinear, None, None)?
        .convert(Mode::RGBA)?)
}

/// _rounded: `image` with round corners of `radius` px, their mask drawn three times larger and
/// reduced (LANCZOS), multiplied into its alpha.
fn rounded(image: &Image, radius: i64) -> Result<Image> {
    let (w, h) = (image.width(), image.height());
    let s = SUPERSAMPLE;
    let (mw, mh) = sized(w as i64 * s as i64, h as i64 * s as i64)?;
    let mut mask = Image::blank(Mode::L, mw, mh);
    Draw::new(&mut mask).rounded_rectangle(
        (0.0, 0.0, mw as f64 - 1.0, mh as f64 - 1.0),
        radius.saturating_mul(s as i64) as f64,
        Some([255, 0, 0, 0]),
        None,
        1,
        ALL_CORNERS,
    )?;
    let mask = mask.resize(w, h, Filter::Lanczos, None, None)?;
    let mut out = image.clone();
    out.putalpha(&chops::multiply(&image.getchannel(3)?, &mask)?)?;
    Ok(out)
}

/// What a live picture is kept by: its name, mode, size and pixels (a new picture under the same
/// name is another).
pub(crate) fn picture_key(name: &str, image: &Image) -> Value {
    let mut hasher = DefaultHasher::new();
    image.as_bytes().hash(&mut hasher);
    Value::List(vec![
        name.into(),
        image.mode().name().into(),
        Value::int(image.width() as i64),
        Value::int(image.height() as i64),
        Value::Str(format!("{:016x}", hasher.finish())),
    ])
}

/// What an image widget shows in a still frame.
pub(crate) enum Shown {
    /// a picture a source or service has, by its name
    Live(String),
    /// no picture: a clear piece of the widget's size
    Clear,
    /// what Rust does not draw yet
    Unported(&'static str),
}

/// _draw_image's choice: the widget's picture, and its slideshow's others.
pub(crate) fn shown(w: &Dict) -> Shown {
    if widget::text(w, "slides")
        .lines()
        .any(|line| !line.trim().is_empty())
    {
        return Shown::Unported("slideshows");
    }
    let src = widget::text(w, "src");
    match src.strip_prefix('@') {
        Some(name) => Shown::Live(name.to_string()),
        None if src.is_empty() => Shown::Clear,
        None => Shown::Unported("pictures from the theme's files"),
    }
}

impl Renderer {
    /// _live_image's piece: the picture at (x, y), blurred into a backdrop (fitted as it says,
    /// filling the box), else fitted where the widget has a size, its corners rounded.
    pub(crate) fn live_image_piece(&self, w: &Dict, image: &Image) -> Result<Piece> {
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (iw, ih) = (widget::int(w, "w", 0), widget::int(w, "h", 0));
        let (fit, align) = (widget::text(w, "fit"), widget::text(w, "align"));
        let mut picture = image.convert(Mode::RGBA)?;
        let blur = widget::number(w, "blur", 0.0);
        if blur > 0.0 {
            picture = blurred(&picture, blur, iw, ih, fit, align)?;
        } else if iw > 0 && ih > 0 {
            let (fw, fh) = sized(iw, ih)?;
            picture = fitted(&picture, fw, fh, fit, align)?;
        }
        let radius = widget::int(w, "radius", 0);
        if radius > 0 {
            picture = rounded(&picture, radius)?;
        }
        Ok(Piece::new(picture, x, y))
    }

    /// _draw_image's piece of no picture: a clear one of the widget's size (a pixel at least).
    pub(crate) fn clear_image_piece(&self, w: &Dict) -> Result<Piece> {
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (iw, ih) = sized(widget::int(w, "w", 0).max(1), widget::int(w, "h", 0).max(1))?;
        Ok(Piece::new(Image::blank(Mode::RGBA, iw, ih), x, y))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_live_picture_is_kept_by_its_name_mode_size_and_pixels() {
        let picture = |mode, w, h, data: &[u8]| Image::from_bytes(mode, w, h, data.to_vec());
        let one = picture(Mode::RGBA, 1, 1, &[1, 2, 3, 4]).unwrap();
        let key = picture_key("media.cover", &one);
        assert_eq!(key, picture_key("media.cover", &one.clone()));
        let others = [
            ("game.logo", picture(Mode::RGBA, 1, 1, &[1, 2, 3, 4])),
            ("media.cover", picture(Mode::RGBa, 1, 1, &[1, 2, 3, 4])),
            ("media.cover", picture(Mode::RGBA, 1, 1, &[1, 2, 3, 5])),
            ("media.cover", picture(Mode::L, 2, 2, &[1, 2, 3, 4])),
            ("media.cover", picture(Mode::L, 4, 1, &[1, 2, 3, 4])),
            ("media.cover", picture(Mode::L, 1, 4, &[1, 2, 3, 4])),
        ];
        for (name, other) in others {
            assert_ne!(key, picture_key(name, &other.unwrap()));
        }
    }
}
