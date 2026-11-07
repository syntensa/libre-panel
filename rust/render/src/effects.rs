//! A widget's effects on its piece (render/renderer.py's _effects and _halo): its opacity, the
//! shadow and the glow its layer casts, worked out in floating point and made 8 bits with the
//! dither in the panel's video (_soft_light).

use lp_pixels::{alpha_composite, dither, Filter, FloatImage, Image, Mode};
use lp_py::Dict;

use crate::piece::{Failed, Piece, Result};
use crate::widget;

/// _scale_alpha: the layer's alpha times `factor` (each level's int, as Python's int() cuts
/// it); the layer as it is from 0.999 up.
pub fn scale_alpha(layer: &Image, factor: f64) -> Result<Option<Image>> {
    if factor >= 0.999 {
        return Ok(None);
    }
    if factor.is_nan() || factor == f64::NEG_INFINITY {
        // (int() of NaN raises, and so of 0 times minus infinity)
        return Err(Failed("cannot convert float NaN to integer".into()));
    }
    let alpha = layer.getchannel(3)?;
    let lut: Vec<i64> = (0..256)
        .map(|a| (f64::from(a) * factor).trunc() as i64)
        .collect();
    let mut out = layer.clone();
    out.putalpha(&alpha.point(&lut)?)?;
    Ok(Some(out))
}

/// _dimmed: a light piece `factor` as bright (light is added: all its channels scale).
pub fn dimmed(light: &Option<Box<Piece>>, factor: f64) -> Result<Option<Box<Piece>>> {
    let Some(light) = light else {
        return Ok(None);
    };
    if factor >= 0.999 {
        return Ok(Some(light.clone()));
    }
    if factor.is_nan() || factor == f64::NEG_INFINITY {
        return Err(Failed("cannot convert float NaN to integer".into()));
    }
    let once: Vec<i64> = (0..256)
        .map(|v| (f64::from(v) * factor).trunc() as i64)
        .collect();
    let lut: Vec<i64> = once
        .iter()
        .copied()
        .cycle()
        .take(256 * light.layer.bands())
        .collect();
    let mut dim = light.clone();
    dim.layer = light.layer.point(&lut)?;
    Ok(Some(dim))
}

/// numpy's np.minimum(255.0, x): NaN stays NaN.
fn at_most_255(x: f32) -> f32 {
    if x > 255.0 {
        255.0
    } else {
        x
    }
}

/// _soft_light: `base` (RGBA, straight) blurred as _blur blurs it, with its alpha times `gain`
/// (at most full), its alpha worked out in floating point and made 8 bits (with the dither
/// where `noise` says); its colour blurred as before, or, where all that is lit is one colour,
/// that colour. Only the part where there is light is worked on (what is lit, and three radii
/// round it).
pub fn soft_light(base: &Image, radius: i64, gain: f64, noise: bool) -> Result<Image> {
    let (bw, bh) = base.size();
    let Some(lit) = base.getchannel(3)?.getbbox(true) else {
        return Ok(Image::blank(Mode::RGBA, bw, bh));
    };
    let half = bw.max(bh) > 320 && radius >= 4;
    let reach = (3.0 * radius as f64).ceil() as i64 + if half { 8 } else { 2 };
    let bounds = (
        (lit.0 as i64 - reach).max(0),
        (lit.1 as i64 - reach).max(0),
        (lit.2 as i64 + reach).min(bw as i64),
        (lit.3 as i64 + reach).min(bh as i64),
    );
    if bounds.2 < bounds.0 {
        return Err(Failed("Coordinate 'right' is less than 'left'".into()));
    }
    if bounds.3 < bounds.1 {
        return Err(Failed("Coordinate 'lower' is less than 'upper'".into()));
    }
    let part = base.crop(bounds.0, bounds.1, bounds.2, bounds.3);
    let (w, h) = part.size();
    let pixels = part.as_bytes();
    // the first lit pixel, and whether every lit one is of its colour
    let lit_at = pixels.chunks_exact(4).position(|p| p[3] > 0);
    let first: [u8; 3] = match lit_at {
        Some(i) => [pixels[i * 4], pixels[i * 4 + 1], pixels[i * 4 + 2]],
        None => [pixels[0], pixels[1], pixels[2]],
    };
    let one = pixels
        .chunks_exact(4)
        .all(|p| p[3] == 0 || [p[0], p[1], p[2]] == first);
    let alpha: Vec<f32> = pixels.chunks_exact(4).map(|p| f32::from(p[3])).collect();
    let small = ((w / 2).max(1), (h / 2).max(1));
    let soft: Vec<f32> = if half {
        let a =
            FloatImage::from_vec(w, h, alpha)?.resize(small.0, small.1, Filter::Bilinear, None)?;
        let blurred = dither::blur(&a.data, a.height, a.width, radius as f64 / 2.0);
        let b = FloatImage::from_vec(small.0, small.1, blurred)?;
        b.resize(w, h, Filter::Bilinear, None)?.data
    } else {
        dither::blur(&alpha, h, w, radius as f64)
    };
    let mut out: Vec<u8> = if one {
        let mut out = vec![0u8; w * h * 4];
        for p in out.chunks_exact_mut(4) {
            p[..3].copy_from_slice(&first);
        }
        out
    } else if half {
        let r = radius as f32 / 2.0;
        let colour = part
            .convert(Mode::RGBa)?
            .resize(small.0, small.1, Filter::Bilinear, None, None)?
            .gaussian_blur(r, r)?
            .resize(w, h, Filter::Bilinear, None, None)?;
        colour.convert(Mode::RGBA)?.into_bytes()
    } else {
        let r = radius as f32;
        let colour = part.convert(Mode::RGBa)?.gaussian_blur(r, r)?;
        colour.convert(Mode::RGBA)?.into_bytes()
    };
    let gain = gain as f32;
    let soft: Vec<f32> = soft.iter().map(|&v| at_most_255(v * gain)).collect();
    let levels = dither::to_u8(&soft, w, h, 1, bounds.0, bounds.1, noise);
    for (p, a) in out.chunks_exact_mut(4).zip(levels) {
        p[3] = a;
    }
    let mut whole = Image::blank(Mode::RGBA, bw, bh);
    whole.paste(
        &Image::from_bytes(Mode::RGBA, w, h, out)?,
        bounds.0,
        bounds.1,
        None,
    )?;
    Ok(whole)
}

/// The shadow and the glow of `layer` (_halo, without the layer), `pad` px larger on every side;
/// None without either.
pub fn halo(
    widget: &Dict,
    layer: &Image,
    shadow_tint: Option<[i64; 4]>,
    dither_on: bool,
) -> Result<Option<(Image, i64)>> {
    let glow = widget::number(widget, "glow", 0.0);
    if glow == 0.0 && shadow_tint.is_none() {
        return Ok(None);
    }
    let mut pad = 0i64;
    let glow_radius = widget::int(widget, "glow_radius", 10);
    let shadow_blur = widget::int(widget, "shadow_blur", 6);
    let shadow_offset = widget::int(widget, "shadow_offset", 3);
    if glow != 0.0 {
        pad = pad.max(glow_radius.saturating_mul(2));
    }
    if shadow_tint.is_some() {
        pad = pad.max(
            shadow_blur
                .saturating_mul(2)
                .saturating_add(shadow_offset.saturating_abs()),
        );
    }
    let size = (
        layer.width() as i64 + 2 * pad,
        layer.height() as i64 + 2 * pad,
    );
    if size.0.saturating_mul(size.1) > crate::renderer::MOST_PIXELS {
        return Err(Failed("a layer too large".into()));
    }
    let size = (size.0 as usize, size.1 as usize);
    let mut out = Image::blank(Mode::RGBA, size.0, size.1);
    if let Some(tint) = shadow_tint {
        let ink = tint.map(|v| v.clamp(0, 255) as u8);
        let mut silhouette = Image::new(
            Mode::RGBA,
            layer.width(),
            layer.height(),
            &[ink[0], ink[1], ink[2], 0],
        );
        let lut: Vec<i64> = (0..256i64).map(|a| (a * tint[3]).div_euclid(255)).collect();
        silhouette.putalpha(&layer.getchannel(3)?.point(&lut)?)?;
        let mut base = Image::blank(Mode::RGBA, size.0, size.1);
        base.paste(&silhouette, pad + shadow_offset, pad + shadow_offset, None)?;
        let shade = soft_light(&base, shadow_blur, 1.0, dither_on)?;
        out = alpha_composite(&out, &shade)?;
    }
    if glow != 0.0 {
        let mut base = Image::blank(Mode::RGBA, size.0, size.1);
        base.paste(layer, pad, pad, None)?;
        let boost = 1.0 + glow * 2.5; // (light spills: brighter than the shape's own alpha)
        let light = soft_light(&base, glow_radius, boost * glow, dither_on)?;
        out = alpha_composite(&out, &light)?;
    }
    Ok(Some((out, pad)))
}

/// _clip_sides: `piece` without what lies left and right of its box (its glow there).
pub fn clip_sides(mut piece: Piece) -> Piece {
    let [x, _, w, _] = piece.bounds();
    let width = piece.layer.width() as i64;
    let left = (x - piece.x).max(0);
    let right = (x + w - piece.x).min(width);
    if left == 0 && right == width {
        return piece;
    }
    let height = piece.layer.height() as i64;
    piece.layer = piece.layer.crop(left, 0, right, height);
    piece.x += left;
    piece
}
