//! Ring gauges (render/renderer.py's _draw_gauge, _gauge_piece, _gauge_static, _gauge_mask and
//! _gauge_colors) in a still frame: where the reading stands, in steps of the supersampled grid
//! along the arc; the track and its ticks drawn three times larger and reduced; the value's arc
//! a mask (an arc with round caps, reduced by its blocks) over the gauge's colour, or over a
//! conic gradient along the scale made of thin wedges.

use lp_pixels::{alpha_composite, chops, Draw, Image, Mode};
use lp_py::Dict;

use crate::bar::Paint;
use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Renderer, MOST_PIXELS};
use crate::shapes::{down, ink, lerp, Rgba, SUPERSAMPLE};
use crate::widget;

const S: i64 = SUPERSAMPLE as i64;

/// round(x) of a float, as a float (it is the int exactly): Python's errors for NaN and the
/// infinities.
fn whole(x: f64) -> Result<f64> {
    if x.is_nan() {
        return Err(Failed("cannot convert float NaN to integer".into()));
    }
    if x.is_infinite() {
        return Err(Failed("cannot convert float infinity to integer".into()));
    }
    Ok(lp_py::round(x))
}

/// _steps along an arc `length` pixels long: `frac` in steps of the supersampled grid.
pub(crate) fn steps_along(frac: f64, length: f64) -> Result<f64> {
    let steps = whole(length * SUPERSAMPLE as f64)?.max(1.0);
    // (both ints a float holds: their quotient rounded once, as Python divides ints)
    Ok(whole(frac * steps)? / steps)
}

/// _gauge_geometry at `scale` times the gauge's size: the box, the centre, the radii of the
/// ring's middle line and its thickness.
struct Geometry {
    bounds: (f64, f64, f64, f64),
    centre: (f64, f64),
    radius: (f64, f64),
    thick: i64,
}

fn geometry(w: &Dict, scale: i64) -> Geometry {
    let big_w = widget::int(w, "w", 120).saturating_mul(scale);
    let big_h = widget::int(w, "h", 120).saturating_mul(scale);
    let thick = widget::int(w, "thickness", 12).max(1).saturating_mul(scale);
    let (right, bottom) = (
        big_w.saturating_sub(1) as f64,
        big_h.saturating_sub(1) as f64,
    );
    Geometry {
        bounds: (0.0, 0.0, right, bottom),
        centre: (right / 2.0, bottom / 2.0),
        radius: (
            big_w.saturating_sub(thick) as f64 / 2.0,
            big_h.saturating_sub(thick) as f64 / 2.0,
        ),
        thick,
    }
}

/// A picture's size as Image.new takes it (its ValueError below 0; Rust's refusal of one too
/// large to hold).
fn sized(w: i64, h: i64) -> Result<(usize, usize)> {
    if w < 0 || h < 0 {
        return Err(Failed("Width and height must be >= 0".into()));
    }
    if w.saturating_mul(h) > MOST_PIXELS {
        return Err(Failed("a gauge too large".into()));
    }
    Ok((w as usize, h as usize))
}

/// The ring's thickness as a line's width (the C int it is passed as).
fn width_of(thick: i64) -> Result<i32> {
    i32::try_from(thick).map_err(|_| Failed("signed integer is greater than maximum".into()))
}

/// The box of a round cap of `thick` across at (`px`, `py`).
fn cap(px: f64, py: f64, thick: f64) -> (f64, f64, f64, f64) {
    (
        px - thick / 2.0,
        py - thick / 2.0,
        px + thick / 2.0,
        py + thick / 2.0,
    )
}

impl Renderer {
    /// _gauge_static: the track (an arc, round caps at its ends) and the ticks across it.
    fn gauge_static(&self, w: &Dict) -> Result<Image> {
        let (gw, gh) = (widget::int(w, "w", 120), widget::int(w, "h", 120));
        // (_canvas: a pixel at least)
        let (lw, lh) = sized(gw.saturating_mul(S).max(1), gh.saturating_mul(S).max(1))?;
        let mut layer = Image::blank(Mode::RGBA, lw, lh);
        let g = geometry(w, S);
        let start = widget::number(w, "start_angle", 135.0);
        let end = widget::number(w, "end_angle", 405.0);
        let sweep = end - start;
        let point = |angle: f64, inset: f64| {
            let rad = angle.to_radians();
            (
                g.centre.0 + (g.radius.0 - inset) * rad.cos(),
                g.centre.1 + (g.radius.1 - inset) * rad.sin(),
            )
        };
        let th = g.thick as f64;
        if let Some(track) = self.colour_of(w, "background")? {
            let mut draw = Draw::new(&mut layer);
            draw.arc(g.bounds, start, end, Some(ink(track)), width_of(g.thick)?)?;
            if widget::text(w, "cap") == "round" {
                for angle in [start, end] {
                    let (px, py) = point(angle, 0.0);
                    draw.ellipse(cap(px, py, th), Some(ink(track)), None, 1)?;
                }
            }
        }
        let ticks = widget::int(w, "ticks", 0);
        if ticks > 0 {
            // (tick_color or background or color: the first that is something)
            let named = ["tick_color", "background", "color"]
                .into_iter()
                .find(|k| widget::truthy(w, k))
                .unwrap_or("color");
            let tick = self.colour_of(w, named)?.map(ink);
            let length = th * 0.6;
            let inset = th / 2.0 + (2 * S) as f64;
            let mut draw = Draw::new(&mut layer);
            for i in 0..=ticks {
                let angle = start + sweep * i as f64 / ticks as f64;
                let line = [point(angle, inset), point(angle, inset + length)];
                draw.line(&line, tick, S as i32, false)?;
            }
        }
        down(&layer, gw, gh, SUPERSAMPLE)
    }

    /// _gauge_mask: the value's arc, `frac` of the way, with round caps, reduced by its blocks.
    fn gauge_mask(&self, w: &Dict, frac: f64) -> Result<Image> {
        let g = geometry(w, S);
        let start = widget::number(w, "start_angle", 135.0);
        let sweep = widget::number(w, "end_angle", 405.0) - start;
        let stop = start + sweep * frac;
        let (gw, gh) = (widget::int(w, "w", 120), widget::int(w, "h", 120));
        let (mw, mh) = sized(gw.saturating_mul(S), gh.saturating_mul(S))?;
        let mut mask = Image::blank(Mode::L, mw, mh);
        let th = g.thick as f64;
        let mut draw = Draw::new(&mut mask);
        draw.arc(
            g.bounds,
            start,
            stop,
            Some([255, 0, 0, 0]),
            width_of(g.thick)?,
        )?;
        if widget::text(w, "cap") == "round" {
            for angle in [start, stop] {
                let rad = angle.to_radians();
                let px = g.centre.0 + g.radius.0 * rad.cos();
                let py = g.centre.1 + g.radius.1 * rad.sin();
                draw.ellipse(cap(px, py, th), Some([255, 0, 0, 0]), None, 1)?;
            }
        }
        Ok(mask.reduce(SUPERSAMPLE, SUPERSAMPLE, None)?)
    }

    /// _gauge_colors: the colour along the scale, `a` at the start and `b` at the end: thin
    /// wedges round the centre; past the end the end's colour, before the start the start's.
    fn gauge_colours(&self, w: &Dict, a: Rgba, b: Rgba) -> Result<Image> {
        let (gw, gh) = (widget::int(w, "w", 120), widget::int(w, "h", 120));
        let (uw, uh) = sized(gw, gh)?;
        let mut field = Image::new(Mode::RGBA, uw, uh, &ink(a));
        let start = widget::number(w, "start_angle", 135.0);
        let end = widget::number(w, "end_angle", 405.0);
        let sweep = end - start;
        // (the wedges reach past the corners)
        let (fw, fh) = (gw as f64, gh as f64);
        let bounds = (-fw, -fh, 2.0 * fw, 2.0 * fh);
        let steps = ((sweep.abs() / 1.5) as i64).max(2);
        let mut draw = Draw::new(&mut field);
        for i in 0..steps {
            let a0 = start + sweep * i as f64 / steps as f64;
            let a1 = start + sweep * (i + 1) as f64 / steps as f64;
            // (min() and max(): the first of two where the other is not less, or more)
            let low = if a1 < a0 { a1 } else { a0 };
            let high = if a1 > a0 { a1 } else { a0 };
            let colour = lerp(a, b, i as f64 / steps as f64);
            draw.pieslice(bounds, low, high + 0.8, Some(ink(colour)), None, 1)?;
        }
        let (t0, t1) = if sweep > 0.0 {
            (end, end + 30.0)
        } else {
            (end - 30.0, end)
        };
        draw.pieslice(bounds, t0, t1, Some(ink(b)), None, 1)?;
        Ok(field)
    }

    /// _gauge_piece: the ring gauge at `frac` in `paint`: the track and ticks, and the value's
    /// arc over them.
    pub(crate) fn gauge_piece(&self, w: &Dict, frac: f64, paint: Paint) -> Result<Piece> {
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (gw, gh) = (widget::int(w, "w", 120), widget::int(w, "h", 120));
        let mut layer = self.gauge_static(w)?;
        if frac > 0.0 {
            let (a, b) = paint;
            let mask = self.gauge_mask(w, frac)?;
            let mut arc = match b {
                None => {
                    let (uw, uh) = sized(gw, gh)?;
                    Image::new(Mode::RGBA, uw, uh, &ink(a))
                }
                Some(b) => self.gauge_colours(w, a, b)?,
            };
            let alpha = arc.getchannel(3)?;
            let alpha = if alpha.getextrema().is_some_and(|e| e == [(255, 255)]) {
                mask
            } else {
                chops::multiply(&alpha, &mask)?
            };
            arc.putalpha(&alpha)?;
            layer = alpha_composite(&layer, &arc)?;
        }
        let mut piece = Piece::new(layer, x, y);
        piece.hit = Some([x, y, gw, gh]);
        Ok(piece)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn steps_along_an_arc_as_python_rounds_them() {
        // (round(length * 3) steps, at least one; round() half to even: Python's _steps)
        assert_eq!(steps_along(0.5, 0.5), Ok(0.5));
        assert_eq!(steps_along(0.3, 0.0), Ok(0.0));
        assert_eq!(steps_along(0.25, 0.5), Ok(0.0));
        assert_eq!(steps_along(0.75, 0.5), Ok(1.0));
        assert_eq!(steps_along(0.7, -4.0), Ok(1.0));
        assert_eq!(steps_along(1.0 / 3.0, 1.0), Ok(1.0 / 3.0));
        let ring = 270f64.to_radians() * 120.0 / 2.0;
        assert_eq!(steps_along(0.123456789, ring), Ok(0.12382075471698113));
        assert_eq!(steps_along(1.0, 1e300), Ok(1.0));
        assert!(steps_along(0.5, f64::INFINITY).is_err());
        assert!(steps_along(0.5, f64::NAN).is_err());
        assert!(steps_along(f64::NAN, 10.0).is_err());
    }
}
