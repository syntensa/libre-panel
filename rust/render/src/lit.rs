//! The lit finish (render/lit.py), SPUR II's light: so far a text's and a metric's glow, its
//! colours blurred and added over the text and around it as light (LitText, text_light).

use lp_pixels::{Image, Mode};
use lp_py::{Dict, Value};
use num_bigint::BigInt;
use num_traits::ToPrimitive;

use crate::finish::{LIGHT, OPACITY};
use crate::piece::{Failed, Piece, Result};
use crate::widget;

/// _alpha_scaled: the picture's alpha times `factor` (none where it is 0.999 or more), each
/// level rounded half to even, 255 at most.
fn alpha_scaled(image: &Image, factor: f64) -> Result<Image> {
    let lut: Vec<i64> = (0..256)
        .map(|v| (lp_py::round(v as f64 * factor) as i64).min(255))
        .collect();
    let mut out = image.clone();
    out.putalpha(&image.getchannel(3)?.point(&lut)?)?;
    Ok(out)
}

/// text_light: a text's glow as light (SPUR II's headings): the text in its colours on black,
/// blurred by `radius`, `strength` of it in whole levels (the light's alpha its brightest
/// band), to add over the text and around it; inside `area` ([x, y, w, h] on the panel, ints of
/// any size, as Python's) only, if given. None where there is no light.
pub(crate) fn text_light(
    piece: &Piece,
    radius: f64,
    strength: f64,
    area: Option<&[BigInt]>,
) -> Result<Option<Piece>> {
    let layer = &piece.layer;
    let (w, h) = (layer.width(), layer.height());
    let pad = (3.0 * radius).ceil() as i64 + 2;
    // (the text on black, as SPUR II draws it: each band times its alpha, in float32, rounded
    // half to even)
    let shown: Vec<u8> = layer
        .as_bytes()
        .chunks_exact(4)
        .flat_map(|px| {
            let a = f32::from(px[3]) / 255.0;
            [0, 1, 2].map(|c| (f32::from(px[c]) * a).round_ties_even() as u8)
        })
        .collect();
    let (pw, ph) = (w as i64 + 2 * pad, h as i64 + 2 * pad);
    if pw.saturating_mul(ph) > crate::renderer::MOST_PIXELS {
        return Err(Failed("a light too large".into()));
    }
    let mut plane = Image::blank(Mode::RGB, pw as usize, ph as usize);
    plane.paste(&Image::from_bytes(Mode::RGB, w, h, shown)?, pad, pad, None)?;
    let blurred = plane.gaussian_blur(radius as f32, radius as f32)?;
    let s = strength as f32;
    let (mut x, mut y) = (piece.x - pad, piece.y - pad);
    let (mut x0, mut y0, mut x1, mut y1) = (0i64, 0i64, pw, ph);
    if let Some([ax, ay, aw, ah]) = area {
        let (bx, by) = (BigInt::from(x), BigInt::from(y));
        let left = (&bx).max(ax).clone();
        let top = (&by).max(ay).clone();
        let right = (&bx + pw).min(ax + aw);
        let bottom = (&by + ph).min(ay + ah);
        if right <= left || bottom <= top {
            return Ok(None);
        }
        // (inside the light: within its own size)
        let inside = |v: BigInt| v.to_i64().expect("a place inside the light");
        (x0, y0) = (inside(&left - &bx), inside(&top - &by));
        (x1, y1) = (inside(right - &bx), inside(bottom - &by));
        (x, y) = (x + x0, y + y0);
    }
    let (cw, ch) = ((x1 - x0) as usize, (y1 - y0) as usize);
    let mut out = Vec::with_capacity(cw * ch * 4);
    let mut any = false;
    for row in y0..y1 {
        let line = blurred.row(row as usize);
        for column in x0..x1 {
            let px = &line[column as usize * 3..column as usize * 3 + 3];
            // (whole levels, as SPUR II's blend: strength is 0 to 1, a level 255 at most)
            let rgb = [0, 1, 2].map(|c| (f32::from(px[c]) * s).floor() as u8);
            any |= rgb != [0, 0, 0];
            out.extend_from_slice(&rgb);
            out.push(rgb[0].max(rgb[1]).max(rgb[2]));
        }
    }
    if !any {
        return Ok(None);
    }
    Ok(Some(Piece::new(
        Image::from_bytes(Mode::RGBA, cw, ch, out)?,
        x,
        y,
    )))
}

/// LitText.draw: the text or metric as the renderer drew it (without its glow and fading),
/// its letters faded by the widget's opacity and its glow as light.
pub(crate) fn lit_text(w: &Dict, piece: &Piece) -> Result<Option<Piece>> {
    let strength = widget::number(w, LIGHT, 0.0);
    let opacity = widget::number(w, OPACITY, 1.0);
    if strength <= 0.0 && opacity >= 0.999 {
        return Ok(None); // (the piece as it is)
    }
    let radius = widget::number(w, "glow_radius", 10.0).max(1.0);
    // (the theme's box: four ints, made so as it is read)
    let area: Option<Vec<BigInt>> = match w.get("glow_area") {
        Some(Value::List(v)) if !v.is_empty() => Some(
            v.iter()
                .map(|v| match v {
                    Value::Int(n) => Ok(n.clone()),
                    _ => Err(Failed("a glow area of no ints".into())),
                })
                .collect::<Result<_>>()?,
        ),
        _ => None,
    };
    let light = if strength != 0.0 {
        text_light(piece, radius, strength, area.as_deref())?
    } else {
        None
    };
    let mut lit = piece.clone();
    if opacity < 0.999 {
        lit.layer = alpha_scaled(&piece.layer, opacity)?;
    }
    lit.light = light.map(Box::new);
    Ok(Some(lit))
}
