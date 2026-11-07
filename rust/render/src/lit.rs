//! The lit finish (render/lit.py), SPUR II's light: a text's and a metric's glow, its colours
//! blurred and added over the text and around it as light (LitText, text_light); a bar's
//! light in its milled groove, and its glow (LitBar, bar_piece).

use lp_pixels::{Draw, Filter, Image, Mode, Transpose, ALL_CORNERS};
use lp_py::{Dict, Value};
use num_bigint::{BigInt, Sign};
use num_traits::ToPrimitive;

use crate::bar::{self, Paint};
use crate::facets;
use crate::finish::{LIGHT, OPACITY};
use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Drawn, Renderer};
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
// (to go into lit.rs) The lit finish's bars (lit.py's LitBar, bar_piece): the groove milled
// into the panel, the light of each lit span over it (a pill centred in the groove, its core
// towards white, a soft halo in its colour beside it), its glow light added over it.

/// The core runs this far towards white ...
const LIGHT_WHITE: f32 = 0.75;
/// ... and the halo stands this strongly beside it.
const LIGHT_HALO: f32 = 1.45;
/// A bar's light is this share of its groove's thickness, and at least this many pixels.
const BAR_CORE: f64 = 0.62;
const BAR_CORE_MIN: i64 = 3;
/// The halo's softness (px) and the room around the light (px), on a panel 480 high.
const BAR_HALO: f64 = 4.5;
const BAR_ROOM: f64 = 9.0;
/// The bar's glow rises over this many px (on a panel 480 high) to the head.
const GLOW_HEAD: f64 = 30.0;
/// A bar's glow along its length, of its colour, and how far the head runs towards white.
const GLOW_TAIL: f64 = 0.55 * 0.34;
const GLOW_WHITE: f64 = 0.65;
const GLOW_STEPS: usize = 7;

/// _RISE: each step of the glow's rise: the share of the colour it lights, how far it runs
/// towards white.
fn rise_steps() -> [(f64, f64); GLOW_STEPS] {
    std::array::from_fn(|i| {
        let t = (i + 1) as f64 / GLOW_STEPS as f64;
        (
            GLOW_TAIL + (1.0 - GLOW_TAIL) * t.powf(1.4),
            GLOW_WHITE * t.powf(2.0),
        )
    })
}

/// light: SPUR II's light of a coverage `mask` (h x w, 0 to 1) in `colour` (RGB 0 to 255, one,
/// or one per column), straight RGBA: the core towards white, the halo in the colour; its
/// halo the coverage cut to whole levels and blurred in float32, `halo` px.
fn light(
    mask: &[f32],
    h: usize,
    w: usize,
    colour: &dyn Fn(usize) -> [f32; 3],
    halo: f64,
) -> Vec<u8> {
    let cut: Vec<f32> = mask.iter().map(|&m| f32::from((m * 255.0) as u8)).collect();
    let share = (1.0f64 / 255.0) as f32;
    let soft: Vec<f32> = lp_pixels::dither::blur(&cut, h, w, halo)
        .iter()
        .map(|&v| v * share)
        .collect();
    let exponent = 0.7f64 as f32;
    let mut out = Vec::with_capacity(h * w * 4);
    for y in 0..h {
        for x in 0..w {
            let i = y * w + x;
            let m = mask[i];
            let core = m.powf(exponent);
            let c = colour(x);
            for v in c {
                out.push(fclip(v + (255.0 - v) * (LIGHT_WHITE * core) + 0.5) as u8);
            }
            let a = clip01(core + LIGHT_HALO * clip01(soft[i] - m)) * 255.0;
            out.push(fclip(a + 0.5) as u8);
        }
    }
    out
}

/// np.clip(x, 0, 255) of float32 (NaN stays NaN).
fn fclip(x: f32) -> f32 {
    let x = if x < 0.0 { 0.0 } else { x };
    if x > 255.0 {
        255.0
    } else {
        x
    }
}

/// np.clip(x, 0, 1) of float32.
fn clip01(x: f32) -> f32 {
    let x = if x < 0.0 { 0.0 } else { x };
    if x > 1.0 {
        1.0
    } else {
        x
    }
}

/// _pill: a pill `length` x `thick` with `room` around it, as coverage (drawn twice as large
/// with round ends, reduced with LANCZOS, over 255 in float32).
fn pill(length: usize, thick: usize, room: usize) -> Result<Vec<f32>> {
    let u = 2;
    let (w, h) = (length + 2 * room, thick + 2 * room);
    let mut mask = Image::blank(Mode::L, w * u, h * u);
    let r = |v: usize| (v * u) as f64;
    Draw::new(&mut mask).rounded_rectangle(
        (
            r(room),
            r(room),
            r(room + length) - 1.0,
            r(room + thick) - 1.0,
        ),
        (thick * u) as f64 / 2.0,
        Some([255, 0, 0, 0]),
        None,
        1,
        ALL_CORNERS,
    )?;
    let small = mask.resize(w, h, Filter::Lanczos, None, None)?;
    Ok(small
        .as_bytes()
        .iter()
        .map(|&v| f32::from(v) / 255.0)
        .collect())
}

/// _layout: a bar's length and thickness, whether it stands upright and fills from its far end.
fn layout(w: &Dict) -> (usize, usize, bool, bool) {
    let (width, height) = (widget::int(w, "w", 200), widget::int(w, "h", 16));
    let direction = widget::text(w, "direction");
    let upright = matches!(direction, "up" | "down");
    let (length, thick) = if upright {
        (height, width)
    } else {
        (width, height)
    };
    (
        length.max(1) as usize,
        thick.max(1) as usize,
        upright,
        matches!(direction, "left" | "up"),
    )
}

/// _spans: the lit spans (start, end, how much) along a bar from its start; and its cells.
fn spans(w: &Dict, length: usize, frac: f64, reverse: bool) -> Vec<(f64, f64, f64)> {
    let n = widget::int(w, "segments", 0);
    let length = length as f64;
    let mut lit: Vec<(f64, f64, f64)> = if n <= 0 {
        if frac > 0.0 {
            vec![(0.0, frac * length, 1.0)]
        } else {
            vec![]
        }
    } else {
        let gap = widget::number(w, "segment_gap", 2.0).max(0.0);
        let cell = ((length - gap * (n - 1) as f64) / n as f64).max(1.0);
        (0..n)
            .filter_map(|i| {
                let (a, b) = (i as f64 * (cell + gap), i as f64 * (cell + gap) + cell);
                let amount = (frac * n as f64 - i as f64).clamp(0.0, 1.0);
                (amount > 0.0).then_some((a, b, amount))
            })
            .collect()
    };
    if reverse {
        lit = lit
            .iter()
            .map(|&(a, b, m)| (length - b, length - a, m))
            .collect();
    }
    lit
}

/// A bar's colours: one (RGB, 0 to 1, float32) or one per column along it.
enum BarColours {
    One([f32; 3]),
    Columns(Vec<[f32; 3]>),
}

impl BarColours {
    fn at(&self, column: usize) -> [f32; 3] {
        match self {
            BarColours::One(c) => *c,
            BarColours::Columns(c) => c[column],
        }
    }
}

/// _unit: a colour's RGB as shares of 255, in float32.
fn unit(colour: [i64; 4]) -> [f32; 3] {
    [0, 1, 2].map(|i| colour[i] as f32 / 255.0)
}

/// _bar_colours: the colour of every column, or one; a gradient spans the whole track (a
/// colour per segment where it has segments), so a colour always means the same value.
fn bar_colours(w: &Dict, length: usize, paint: Paint, reverse: bool) -> BarColours {
    let (a, b) = paint;
    let Some(b) = b else {
        return BarColours::One(unit(a));
    };
    let n = widget::int(w, "segments", 0);
    let (start, end) = (unit(a), unit(b));
    let len = length as f32;
    BarColours::Columns(
        (0..length)
            .map(|i| {
                let mut t = (i as f32 + 0.5) / len;
                if n > 0 {
                    let cell = (t * n as f32).floor();
                    let last = (n - 1) as f32;
                    t = (if cell < last { cell } else { last }) / (n - 1).max(1) as f32;
                }
                if reverse {
                    t = 1.0 - t;
                }
                [0, 1, 2].map(|c| start[c] + (end[c] - start[c]) * t)
            })
            .collect(),
    )
}

/// _glow_source: SPUR II's bar glow before it is blurred, lying (thick rows of length, RGB):
/// on the bar a pixel in from each edge, dim along the lit part and rising in steps over
/// `head_px` to the head, which runs towards white; whole pixels and whole colour levels.
fn glow_source(
    length: usize,
    thick: usize,
    spans: &[(f64, f64, f64)],
    colours: &BarColours,
    head_px: f64,
    reverse: bool,
) -> Vec<[u8; 3]> {
    let mut out = vec![[0u8; 3]; thick * length];
    if spans.is_empty() {
        return out;
    }
    // (filling from the far end: worked out mirrored)
    let spans: Vec<(f64, f64, f64)> = if reverse {
        let l = length as f64;
        spans.iter().map(|&(a, b, m)| (l - b, l - a, m)).collect()
    } else {
        spans.to_vec()
    };
    let colour = |column: usize| colours.at(if reverse { length - 1 - column } else { column });
    let low = spans
        .iter()
        .map(|s| s.0)
        .fold(f64::INFINITY, |m, v| if v < m { v } else { m });
    let head = spans
        .iter()
        .map(|s| s.1)
        .fold(f64::NEG_INFINITY, |m, v| if v > m { v } else { m });
    let start = lp_py::round(low) as i64;
    let (first, last) = (
        start + 1,
        ((length as f64 - 2.0) * head / length as f64).floor() as i64,
    );
    if last < first {
        return out;
    }
    // (whole levels, as the theme gave them: in float64, rounded half to even)
    let c = |column: usize| colour(column).map(|v| (f64::from(v) * 255.0).round_ties_even());
    let mut source = vec![[0f64; 3]; length];
    let span = (last - first) as f64;
    let rise = if span < head_px { span } else { head_px }; // (Python's min(head_px, span))
    let len = length as i64;
    let clipped = |a: i64, b: i64| (a.max(0)..(b + 1).min(len)).map(|i| i as usize);
    if last as f64 - rise > first as f64 {
        for i in clipped(first, (last as f64 - rise).floor() as i64) {
            source[i] = c(i).map(|v| (v * GLOW_TAIL).floor());
        }
    }
    for (i, (level, white)) in rise_steps().iter().enumerate() {
        let at =
            |k: usize| (last as f64 - rise + rise * k as f64 / GLOW_STEPS as f64).floor() as i64;
        for j in clipped(at(i), at(i + 1)) {
            source[j] = c(j).map(|v| {
                let step = (v * level).floor();
                (step + (255.0 - step) * white).floor()
            });
        }
    }
    let rows = if thick > 2 { 1..thick - 1 } else { 0..thick };
    for row in rows {
        for (column, level) in source.iter().enumerate() {
            let at = if reverse { length - 1 - column } else { column };
            out[row * length + at] = level.map(|v| v as u8);
        }
    }
    out
}

/// _glow_area: where a bar's glow is worked out (x, y, w, h on the panel): its glow area, else
/// round the bar as far as the glow reaches. The theme's ints are of any size, as Python's:
/// held here to i128, beyond which a corner meets no panel and a side is one no machine makes.
fn glow_area(w: &Dict, radius: f64) -> Result<[i128; 4]> {
    if let Some(Value::List(v)) = w.get("glow_area") {
        if v.len() == 4 {
            let mut area = [0i128; 4];
            for (at, v) in area.iter_mut().zip(v) {
                let Value::Int(n) = v else {
                    return Err(Failed("a glow area of no ints".into()));
                };
                *at = n.to_i128().unwrap_or(if n.sign() == Sign::Minus {
                    i128::MIN
                } else {
                    i128::MAX
                });
            }
            if area[2] > 0 && area[3] > 0 {
                return Ok(area);
            }
        }
    }
    let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
    let (bw, bh) = (widget::int(w, "w", 200), widget::int(w, "h", 16));
    // (a reach past what i128 holds: one as large, which no machine makes)
    let pad = (2.5 * radius).ceil() as i128;
    let wide = |side: i64| i128::from(side).saturating_add(pad.saturating_mul(2));
    Ok([
        i128::from(x).saturating_sub(pad),
        i128::from(y).saturating_sub(pad),
        wide(bw),
        wide(bh),
    ])
}

/// _glow_scale: the fraction of its area's size a bar's glow is worked out at.
fn glow_scale(radius: f64) -> usize {
    ((radius / 3.0).floor() as i64).clamp(1, 4) as usize
}

/// _glow_piece: SPUR II's bar glow as light to add over the bar: its source blurred over the
/// glow area (else round the bar), at a fraction of the size, nothing outside it.
fn glow_piece(
    w: &Dict,
    unit_px: f64,
    paint: Paint,
    spans: &[(f64, f64, f64)],
    strength: f64,
) -> Result<Option<Piece>> {
    let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
    let (length, thick, upright, reverse) = layout(w);
    let radius = widget::number(w, "glow_radius", 10.0).max(1.0);
    let colours = bar_colours(w, length, paint, reverse);
    let lying = glow_source(length, thick, spans, &colours, GLOW_HEAD * unit_px, reverse);
    // (upright: the source turned, its rows the bar's columns)
    let (sw, sh) = if upright {
        (thick, length)
    } else {
        (length, thick)
    };
    let source = |sx: usize, sy: usize| {
        if upright {
            lying[sx * length + sy]
        } else {
            lying[sy * length + sx]
        }
    };
    if lying.iter().all(|p| *p == [0, 0, 0]) {
        return Ok(None);
    }
    // (numpy makes the area's plane before it looks where the bar lies in it)
    let [ax, ay, aw, ah] = glow_area(w, radius)?;
    if aw.saturating_mul(ah) > i128::from(crate::renderer::MOST_PIXELS) {
        return Err(Failed("a glow too large".into()));
    }
    let (x, y) = (i128::from(x), i128::from(y));
    let (x0, y0) = (x.max(ax), y.max(ay));
    let (x1, y1) = (
        (x + sw as i128).min(ax.saturating_add(aw)),
        (y + sh as i128).min(ay.saturating_add(ah)),
    );
    if x1 <= x0 || y1 <= y0 {
        return Ok(None);
    }
    let corner = |v: i128| i64::try_from(v).map_err(|_| Failed("a glow too far".into()));
    let (left, top) = (corner(ax)?, corner(ay)?);
    let (pw, ph) = (aw as usize, ah as usize);
    let mut plane = vec![0u8; pw * ph * 3];
    for py in y0..y1 {
        for px in x0..x1 {
            let p = source((px - x) as usize, (py - y) as usize);
            let at = ((py - ay) as usize * pw + (px - ax) as usize) * 3;
            plane[at..at + 3].copy_from_slice(&p);
        }
    }
    let scale = glow_scale(radius);
    let mut small = Image::from_bytes(Mode::RGB, pw, ph, plane)?;
    if scale > 1 {
        small = small.reduce(scale, scale, None)?;
    }
    let r = (radius / scale as f64) as f32;
    let mut glow = small.gaussian_blur(r, r)?;
    if scale > 1 {
        glow = glow.resize(pw, ph, Filter::Bilinear, None, None)?;
    }
    if strength < 0.999 {
        let table: Vec<i64> = (0..256)
            .map(|v| (lp_py::round(v as f64 * strength) as i64).min(255))
            .collect();
        glow = glow.point(&table.repeat(3))?;
    }
    // (its alpha its strongest channel: it is added, not laid)
    let rgb = glow.as_bytes();
    let mut out = Vec::with_capacity(pw * ph * 4);
    for p in rgb.chunks_exact(3) {
        out.extend_from_slice(p);
        out.push(p[0].max(p[1]).max(p[2]));
    }
    Ok(Some(Piece::new(
        Image::from_bytes(Mode::RGBA, pw, ph, out)?,
        left,
        top,
    )))
}

/// _bar_core: a bar's light: its thickness, and its top on the canvas below the room (whole
/// pixels on the panel, centred in the groove).
fn bar_core(w: &Dict, thick: usize, upright: bool) -> (usize, i64) {
    let core = (lp_py::round(thick as f64 * BAR_CORE) as i64)
        .max(BAR_CORE_MIN)
        .min(thick as i64);
    let at = widget::int(w, if upright { "x" } else { "y" }, 0);
    let top = lp_py::round(at as f64 + (thick as i64 - core) as f64 / 2.0) as i64 - at;
    (core as usize, top)
}

/// _bar_canvas: the lit bar's picture: its groove (milled into the panel in the track's colour,
/// plain where a bevel does not read), the light of each lit span over it (a pill centred in the
/// groove), turned where it stands.
fn bar_canvas(
    w: &Dict,
    track: Option<[i64; 4]>,
    spans: &[(f64, f64, f64)],
    paint: Paint,
    pad: usize,
    unit_px: f64,
) -> Result<Image> {
    let (length, thick, upright, reverse) = layout(w);
    let (cw, ch) = (length + 2 * pad, thick + 2 * pad);
    if (cw as i64).saturating_mul(ch as i64) > crate::renderer::MOST_PIXELS {
        return Err(Failed("a bar too large".into()));
    }
    let mut canvas = Image::blank(Mode::RGBA, cw, ch);
    if let Some(track) = track {
        let groove = match facets::groove_bar(length, thick, track, false)? {
            Some(milled) => milled,
            None => {
                let ink = track.map(|v| v.clamp(0, 255) as u8);
                Image::new(Mode::RGBA, length, thick, &ink)
            }
        };
        canvas.paste(&groove, pad as i64, pad as i64, None)?;
    }
    if !spans.is_empty() {
        let colours = bar_colours(w, length, paint, reverse);
        let fade = paint.0[3] as f64 / 255.0;
        let (core, offset) = bar_core(w, thick, upright);
        for &(a, b, _amount) in spans {
            let start = lp_py::round(a) as i64;
            let span = (lp_py::round(b - a) as i64).max(1) as usize;
            let mask = pill(span, core, pad)?;
            let (mw, mh) = (span + 2 * pad, core + 2 * pad);
            let colour = |column: usize| {
                let at = (start - pad as i64 + column as i64).clamp(0, length as i64 - 1) as usize;
                colours.at(at).map(|v| v * 255.0)
            };
            let rgba = light(&mask, mh, mw, &colour, BAR_HALO * unit_px);
            let mut image = Image::from_bytes(Mode::RGBA, mw, mh, rgba)?;
            if fade < 0.999 {
                image = alpha_scaled(&image, fade)?;
            }
            let whole = (0, 0, mw as i64, mh as i64);
            canvas.alpha_composite_at(&image, (start, offset), whole)?;
        }
    }
    if upright {
        canvas = canvas.transpose(Transpose::Transpose);
    }
    Ok(canvas)
}

/// bar_piece: the lit bar, lying: the groove, the light of each lit span over it; its glow is
/// light added over it.
pub(crate) fn bar_piece(
    w: &Dict,
    unit_px: f64,
    track: Option<[i64; 4]>,
    frac: f64,
    paint: Paint,
) -> Result<Piece> {
    let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
    let (bw, bh) = (widget::int(w, "w", 200), widget::int(w, "h", 16));
    let (length, _thick, _upright, reverse) = layout(w);
    let lit = spans(w, length, frac, reverse);
    let pad = (BAR_ROOM * unit_px).ceil() as usize;
    let strength = widget::number(w, "glow", 0.0);
    let canvas = bar_canvas(w, track, &lit, paint, pad, unit_px)?;
    let mut piece = Piece::new(canvas, x - pad as i64, y - pad as i64);
    piece.hit = Some([x, y, bw, bh]);
    if !lit.is_empty() && strength > 0.0 {
        let strength = if strength < 1.0 { strength } else { 1.0 };
        piece.light = glow_piece(w, unit_px, paint, &lit, strength)?.map(Box::new);
    }
    Ok(piece)
}

impl Renderer {
    /// LitBar.draw: the lit bar of `frac` (its reading `value`, its colour rules), kept with
    /// its effects as the renderer keeps a bar (without the renderer's glow: its own is light);
    /// None for one too thin for light, drawn flat.
    pub(crate) fn lit_bar(
        &mut self,
        w: &Dict,
        frac: f64,
        value: Option<f64>,
        rules: &[(Value, String)],
    ) -> Option<Drawn> {
        let (length, thick, _upright, _reverse) = layout(w);
        if thick < 2 || length < 2 {
            return None;
        }
        let made = bar::steps(frac, length as i64)
            .and_then(|frac| Ok((frac, self.rule_paint(w, value, rules)?)))
            .and_then(|(frac, paint)| Ok((frac, paint, self.colour_of(w, "background")?)));
        let (frac, paint, track) = match made {
            Ok(made) => made,
            Err(e) => return Some(Drawn::Failed(e)),
        };
        let unit_px = self.theme.width.min(self.theme.height) as f64 / 480.0;
        let mut plain = w.clone();
        plain.insert("glow".into(), Value::Float(0.0));
        let content = Value::List(vec!["lit".into(), bar::content(frac, paint)]);
        let lit = w.clone();
        Some(
            match self.cached(&plain, content, |_| {
                bar_piece(&lit, unit_px, track, frac, paint)
            }) {
                Ok(piece) => Drawn::Piece(piece),
                Err(e) => Drawn::Failed(e),
            },
        )
    }
}
