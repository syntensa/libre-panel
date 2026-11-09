//! A download as the Studio's inset shows it (render/download.py): what loads (the phase, the
//! source and the name), a progress under it, the speed and the time left, the loaded amount
//! against the whole, and the speed of the last minutes as a graph. The progress is a flat bar,
//! or with the `lit` finish light in its groove; a download of unknown size runs a stretch of
//! light to and fro in the groove instead (in a still frame: where it is at the frame's time).
//!
//! The card is laid out in the numbers of the inset it comes from (596 x 130 px; stacked, the
//! graph under the readings, 330 x 254) and scaled with its box. The state is the one the
//! `downloads` service publishes, read as download.read reads it.

use std::f64::consts::PI;

use num_bigint::BigInt;
use num_integer::Integer;
use num_traits::{FromPrimitive, Zero};

use lp_pixels::{chops, Draw, Filter, Image, Mode, ALL_CORNERS};
use lp_py::{Dict, Value};
use lp_text::fallback::FallbackFont;
use lp_text::text::{draw, Text};

use crate::bar::Paint;
use crate::formatting;
use crate::light;
use crate::lit;
use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Drawn, Renderer, MOST_PIXELS};
use crate::shapes::{ink, Rgba};
use crate::snapshot::Snapshot;
use crate::widget;

const MARGIN: f64 = 13.0;
const MARGIN_RIGHT: f64 = 16.0;
/// where the numbers of the readings end (their units follow)
const VALUE_X: f64 = 240.0;
/// a graph narrower than this (in reference px) is left out
const GRAPH_LEAST: f64 = 40.0;
/// the share of the line's colour under it, at its top (of 255)
const GRAPH_FILL: f64 = 105.0;
/// the speeds a state carries: the graph's width, if there is room
const HISTORY: usize = 180;
/// how much further apart than their size lines may stand in a taller box
const SPREAD: f64 = 1.45;
/// a box less wide than this many times its height stacks the card
const STACK_ASPECT: f64 = 2.4;
/// the progress's thickness
const BAR_H: f64 = 6.0;
const GRAPH_X: f64 = 392.0;
const MB: f64 = 1048576.0;
/// seconds a stretch of light takes to and fro, for a download of unknown size
const RUN: f64 = 2.4;
/// its share of the groove
const STRETCH: f64 = 0.25;
const PHASES: [&str; 5] = ["downloading", "preparing", "paused", "installing", "done"];

/// Python's max(a, b) of two floats: the first unless the second is greater.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python's min(a, b) of two floats: the first unless the second is less.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// round(x) as Python rounds a float to an int (half to even), for the sizes here.
fn round(x: f64) -> i64 {
    lp_py::round(x) as i64
}

fn failed(e: lp_text::Error) -> Failed {
    Failed(e.to_string())
}

/// A picture's side that Rust holds (Python's fails far past any panel's).
fn held(w: i64, h: i64) -> Result<(usize, usize)> {
    if w < 0 || h < 0 {
        return Err(Failed("Width and height must be >= 0".into()));
    }
    if w.saturating_mul(h) > MOST_PIXELS {
        return Err(Failed("a download card too large".into()));
    }
    Ok((w as usize, h as usize))
}

/// heading: the word at the head of a card for a phase.
fn heading(phase: &str) -> String {
    lp_i18n::t(match phase {
        "paused" => "Paused",
        "installing" => "Installing",
        "done" => "Done",
        _ => "Download",
    })
}

fn fixed(x: f64, digits: &str) -> String {
    lp_py::format::format(&Value::Float(x), digits).expect("a float in a fixed format")
}

/// amount: a size as a number and its unit: GB from one GB on, else MB.
fn amount(size: f64) -> (String, String) {
    let size = py_max(0.0, size);
    if size >= 1024.0 * MB {
        (fixed(size / (1024.0 * MB), ".1f"), "GB".into())
    } else {
        (fixed(size / MB, ".0f"), "MB".into())
    }
}

/// time_left: a span as a number and its unit: seconds below a minute, then minutes, then
/// hours (`int(max(0, seconds))`: the whole seconds, of any size).
fn time_left(seconds: f64) -> (String, String) {
    let s = if seconds > 0.0 {
        BigInt::from_f64(seconds.trunc()).expect("a finite float")
    } else {
        BigInt::zero()
    };
    let (minute, hour) = (BigInt::from(60), BigInt::from(3600));
    if s < minute {
        (s.to_string(), "s".into())
    } else if s < hour {
        let (m, rest) = s.div_mod_floor(&minute);
        (format!("{m}:{rest:0>2}"), "min".into())
    } else {
        let h = s.div_floor(&hour);
        let m = s.div_floor(&minute).mod_floor(&minute);
        (format!("{h}:{m:0>2}"), "h".into())
    }
}

/// of_total: "9.3 / 24.1 GB": both in the unit of the whole.
fn of_total(loaded: f64, total: f64) -> String {
    let (number, unit) = amount(total);
    let (divisor, digits) = if unit == "GB" {
        (1024.0 * MB, ".1f")
    } else {
        (MB, ".0f")
    };
    format!("{} / {number} {unit}", fixed(loaded / divisor, digits))
}

/// _number: a number of the state as a float; None for what is none (a bool, a text, an
/// infinity); OverflowError for an int past a float's range, as Python's.
fn number(value: Option<&Value>) -> Result<Option<f64>> {
    match value {
        Some(Value::Int(n)) => lp_py::num::int_to_float(n)
            .map(Some)
            .map_err(|e| Failed(e.msg)),
        Some(Value::Float(x)) if x.is_finite() => Ok(Some(*x)),
        _ => Ok(None),
    }
}

/// str(value or ""): a value's text, nothing for one that is not true.
fn text_of(value: Option<&Value>) -> String {
    match value {
        Some(v) if v.truthy() => v.str(),
        _ => String::new(),
    }
}

/// Facts: what the card shows of a state, as text (what its picture is kept by).
#[derive(Clone, Debug, PartialEq)]
pub struct Facts {
    pub phase: String,
    pub source: String,
    pub name: String,
    /// how much is loaded, 0 to 1; None: the size is not known
    pub share: Option<f64>,
    /// (label, number, unit), top to bottom
    pub rows: [(String, String, String); 2],
    /// "9.3 / 24.1 GB", or ""
    pub amount: String,
    /// bytes a second, the oldest first
    pub history: Vec<f64>,
}

impl Facts {
    /// The facts as Python's tuple holds them (compared as == compares them).
    pub fn value(&self) -> Value {
        let s = |t: &str| Value::Str(t.to_string());
        let rows = self
            .rows
            .iter()
            .map(|(a, b, c)| Value::List(vec![s(a), s(b), s(c)]))
            .collect();
        Value::List(vec![
            s(&self.phase),
            s(&self.source),
            s(&self.name),
            self.share.map_or(Value::None, Value::Float),
            Value::List(rows),
            s(&self.amount),
            Value::List(self.history.iter().map(|&v| Value::Float(v)).collect()),
        ])
    }
}

/// read: the card's texts from a state; a field that is missing or no number is left out.
pub fn read(state: &Dict) -> Result<Facts> {
    let phase = match state.get("phase") {
        Some(Value::Str(p)) if PHASES.contains(&p.as_str()) => p.clone(),
        _ => "downloading".to_string(),
    };
    // (`_number(...) or 0.0`: none, nought or minus nought, 0.0)
    let loaded = match number(state.get("bytes"))? {
        Some(x) if x != 0.0 => x,
        _ => 0.0,
    };
    let total = number(state.get("total"))?.filter(|&t| t > 0.0);
    let rate = number(state.get("rate"))?;
    let left = number(state.get("remaining_s"))?;
    let elapsed = number(state.get("elapsed_s"))?;
    let done = phase == "done";
    let share = if done {
        Some(1.0)
    } else {
        total.map(|t| py_max(0.0, py_min(1.0, loaded / t)))
    };
    let row = |label: &str, (number, unit): (String, String)| (lp_i18n::t(label), number, unit);
    let rows = if done || phase == "installing" {
        let first = if done { "Total" } else { "Loaded" };
        [
            row(first, amount(total.unwrap_or(loaded))),
            row("Duration", time_left(elapsed.unwrap_or(0.0))),
        ]
    } else {
        let speed = match rate {
            None => ("--".to_string(), String::new()),
            Some(rate) => {
                let (n, unit) = formatting::rate_parts(&Value::Float(py_max(0.0, rate)))
                    .map_err(|e| Failed(e.msg))?;
                (n, unit.to_string())
            }
        };
        let second = match left {
            Some(left) => row("Remaining", time_left(left)),
            None => row("Loaded", amount(loaded)),
        };
        [row("Rate", speed), second]
    };
    let mut text = String::new();
    if let Some(total) = total {
        if !done && phase != "installing" {
            let near = matches!(state.get("estimated"), Some(Value::Bool(true)));
            text = format!(
                "{}{}",
                if near { "≈ " } else { "" },
                of_total(loaded, total)
            );
        }
    }
    let mut history = Vec::new();
    if let Some(Value::List(items)) = state.get("history") {
        // (the last speeds only: a state of another sender may carry any number)
        for x in &items[items.len().saturating_sub(HISTORY)..] {
            if let Some(v) = number(Some(x))? {
                history.push(py_max(0.0, v));
            }
        }
    }
    let mut name = text_of(state.get("name"));
    if let Some(more) = number(state.get("more"))? {
        if more >= 1.0 {
            let more = BigInt::from_f64(more.trunc()).expect("a finite float");
            name = format!("{name}  +{more}");
        }
    }
    Ok(Facts {
        phase,
        source: lp_py::upper(&text_of(state.get("source"))),
        name,
        share,
        rows,
        amount: text,
        history,
    })
}

// -- the geometry -----------------------------------------------------------------------------

/// Where things are in the reference's pixels: the heading's baseline, the name's (stacked: a
/// line of its own), the progress's top, the readings' baselines, the amount's (stacked: a line
/// of its own), the graph's top and bottom edges.
struct Ref {
    size: (f64, f64),
    head: f64,
    name: f64,
    bar: f64,
    rows: [f64; 2],
    amount: f64,
    graph: (f64, f64),
}

const WIDE: Ref = Ref {
    size: (596.0, 130.0),
    head: 30.0,
    name: f64::NAN, // (on the heading's line)
    bar: 44.0,
    rows: [81.0, 125.0],
    amount: f64::NAN, // (on the second reading's line)
    graph: (60.0, 104.0),
};

const STACKED: Ref = Ref {
    size: (330.0, 254.0),
    head: 30.0,
    name: 58.0,
    bar: 68.0,
    rows: [106.0, 150.0],
    amount: 182.0,
    graph: (196.0, 246.0),
};

/// Layout: the size of things (the reference's, times g), the distance of lines, where the
/// reference's top edge lies, the margins, the graph's box, whether the card is stacked.
struct Layout {
    g: f64,
    gy: f64,
    top: f64,
    left: f64,
    right: f64,
    graph: Option<(f64, f64, f64, f64)>,
    stacked: bool,
    r: &'static Ref,
}

impl Layout {
    /// A line of the reference, in the box.
    fn y(&self, ref_y: f64) -> f64 {
        self.top + ref_y * self.gy
    }
}

/// layout: where things go in a box of w x h: the reference's numbers, as large as the box
/// allows; a taller box spreads its lines a little; one nearly square or tall stacks the card.
fn layout(w: i64, h: i64) -> Layout {
    let (wf, hf) = (w as f64, h as f64);
    let stacked = wf < STACK_ASPECT * hf;
    let r = if stacked { &STACKED } else { &WIDE };
    let (ref_w, ref_h) = r.size;
    let g = py_max(0.15, py_min(wf / ref_w, hf / ref_h));
    let gy = py_min(hf / ref_h, g * SPREAD);
    let top = (hf - ref_h * gy) / 2.0;
    let (left, right) = (MARGIN * g, wf - MARGIN_RIGHT * g);
    let (y0, y1) = (top + r.graph.0 * gy, top + r.graph.1 * gy);
    let graph = if stacked {
        (y1 - y0 >= 10.0).then_some((left, y0, right, y1))
    } else if right - GRAPH_X * g >= GRAPH_LEAST * g {
        Some((GRAPH_X * g, y0, right, y1))
    } else {
        None
    };
    Layout {
        g,
        gy,
        top,
        left,
        right,
        graph,
        stacked,
        r,
    }
}

/// rail: the progress's groove: x0, y0, x1, y1.
fn rail(lay: &Layout) -> (f64, f64, f64, f64) {
    let y = lay.y(lay.r.bar);
    (lay.left, y, lay.right, y + py_max(3.0, BAR_H * lay.g))
}

// -- drawing ----------------------------------------------------------------------------------

/// draw.text with the baseline or the middle at (x, y), as `anchor` says.
fn write(
    image: &mut Image,
    at: (f64, f64),
    text: &str,
    font: &FallbackFont,
    colour: [u8; 4],
    anchor: &str,
) -> Result<()> {
    let text = Text::new(text, font, "RGBA");
    draw(image, at, &text, colour, None, Some(anchor), "left").map_err(failed)
}

/// _tracked: letter-spaced text with its baseline at y, from x (light.draw_tracked: letter by
/// letter, each the font's advance and the tracking on); its width (light.tracked_width).
fn tracked(
    image: &mut Image,
    (x, y): (f64, f64),
    text: &str,
    font: &FallbackFont,
    colour: [u8; 4],
    tracking: f64,
) -> Result<f64> {
    let mut lengths = Vec::new();
    for ch in text.chars() {
        lengths.push(Renderer::advance(font, ch)?);
    }
    let count = lengths.len();
    let width =
        lp_py::sum_floats(lengths.iter().copied()) + tracking * count.saturating_sub(1) as f64;
    let mut at = x;
    let mut buffer = [0u8; 4];
    for (ch, length) in text.chars().zip(lengths) {
        write(
            image,
            (at, y),
            ch.encode_utf8(&mut buffer),
            font,
            colour,
            "ls",
        )?;
        at += length + tracking;
    }
    Ok(width)
}

/// _cut: `text` cut with an ellipsis to fit `room` px.
fn cut(text: &str, font: &FallbackFont, room: f64) -> Result<String> {
    if Renderer::length(font, text)? <= room {
        return Ok(text.to_string());
    }
    let rstrip = |t: &str| t.trim_end_matches(lp_py::isspace).to_string();
    let mut text = text.to_string();
    while text.chars().count() > 1 && Renderer::length(font, &format!("{}…", rstrip(&text)))? > room
    {
        text.pop();
    }
    Ok(format!("{}…", rstrip(&text)))
}

/// _graph: the speed as a curve that grows from the bottom edge, on a scale of its own highest
/// value, with a halo and a fill that fades out below the line (RGBA, `size`).
fn graph(values: &[f64], (w, h): (i64, i64), g: f64, colour: Rgba) -> Result<Image> {
    let (lw, lh) = held(w, h)?;
    let mut layer = Image::blank(Mode::RGBA, lw, lh);
    if values.len() < 2 || w < 4 || h < 4 {
        return Ok(layer);
    }
    // (as many as a state carries fill the width)
    let step = py_max(1.0, w as f64 / (HISTORY - 1) as f64);
    let keep = 2.max((w as f64 / step) as usize + 1);
    let shown = &values[values.len().saturating_sub(keep)..];
    let highest = shown.iter().copied().fold(f64::NEG_INFINITY, py_max) * 1.1;
    let peak = py_max(MB, highest);
    // np.convolve(np.pad(shown, 1, "edge"), [0.25, 0.5, 0.25], "valid"): numpy's
    // small_correlate, the products summed one after another from nought
    let n = shown.len();
    let padded: Vec<f64> = std::iter::once(shown[0])
        .chain(shown.iter().copied())
        .chain(std::iter::once(shown[n - 1]))
        .collect();
    let smooth: Vec<f64> = (0..n)
        .map(|i| {
            let s = 0.0 + padded[i] * 0.25;
            let s = s + padded[i + 1] * 0.5;
            s + padded[i + 2] * 0.25
        })
        .collect();
    let k = 3i64; // drawn at k times the size, then reduced
    let margin = 2 * k;
    let points: Vec<(f64, f64)> = (0..n)
        .map(|i| {
            let x = (w - 1) as f64 - (n - 1 - i) as f64 * step;
            let share = (smooth[i] / peak).clamp(0.0, 1.0);
            let y = (h - 1) as f64 - share * (h - 1) as f64;
            (x * k as f64 + margin as f64, y * k as f64 + margin as f64)
        })
        .collect();
    let big = held(w * k + 2 * margin, h * k + 2 * margin)?;
    let mut line = Image::blank(Mode::L, big.0, big.1);
    let width = 2.max(round(2.0 * g * k as f64)) as i32;
    Draw::new(&mut line).line(&points, Some([255, 0, 0, 0]), width, true)?;
    let mut area = Image::blank(Mode::L, big.0, big.1);
    let floor = big.1 as f64;
    let mut polygon = points.clone();
    polygon.push((points[n - 1].0, floor));
    polygon.push((points[0].0, floor));
    Draw::new(&mut area).polygon(&polygon, Some([255, 0, 0, 0]), None, 1)?;
    let m = margin as f64;
    let source = Some((m, m, m + (w * k) as f64, m + (h * k) as f64));
    let line = line.resize(lw, lh, Filter::Lanczos, source, None)?;
    let area = area.resize(lw, lh, Filter::Lanczos, source, None)?;
    // np.linspace(GRAPH_FILL, 0, h, dtype=np.float32), as uint8: i * step + 105 in doubles,
    // the last row 0, each a float32 cut to its whole part
    let fall = -GRAPH_FILL / (h - 1) as f64;
    let rows: Vec<u8> = (0..lh)
        .map(|row| {
            if row == lh - 1 {
                0
            } else {
                (row as f64 * fall + GRAPH_FILL) as f32 as u8
            }
        })
        .collect();
    let fade: Vec<u8> = rows
        .iter()
        .flat_map(|&v| std::iter::repeat_n(v, lw))
        .collect();
    let fill = chops::multiply(&area, &Image::from_bytes(Mode::L, lw, lh, fade)?)?;
    let radius = py_max(1.0, 2.2 * g) as f32;
    let lut: Vec<i64> = (0..256).map(|v| (v as f64 * 0.55) as i64).collect();
    let halo = line.gaussian_blur(radius, radius)?.point(&lut)?;
    let [r, gr, b, _] = ink(colour);
    for mask in [&fill, &halo, &line] {
        let mut tint = Image::new(Mode::RGBA, lw, lh, &[r, gr, b, 0]);
        tint.putalpha(mask)?;
        layer = lp_pixels::alpha_composite(&layer, &tint)?;
    }
    Ok(layer)
}

/// _bar_flat: a flat progress, a pill in its groove filled to `share`, or (no share) a stretch
/// of it at `run` (0 to 1) along the groove; drawn three times its size and reduced.
fn bar_flat(
    width: i64,
    thick: i64,
    share: Option<f64>,
    colour: Rgba,
    track: Option<Rgba>,
    run: f64,
) -> Result<Image> {
    let u = 3;
    let (lw, lh) = held(width * u, thick * u)?;
    let mut layer = Image::blank(Mode::RGBA, lw, lh);
    let radius = (thick * u) as f64 / 2.0;
    let bottom = (thick * u - 1) as f64;
    let mut draw = Draw::new(&mut layer);
    if let Some(track) = track {
        let whole = (0.0, 0.0, (width * u - 1) as f64, bottom);
        draw.rounded_rectangle(whole, radius, Some(ink(track)), None, 1, ALL_CORNERS)?;
    }
    let (a, b) = match share {
        None => {
            let span = thick.max(round(width as f64 * STRETCH));
            let x0 = round((width - span) as f64 * run);
            (x0 * u, (x0 + span) * u - 1)
        }
        Some(share) => (0, (round(share * width as f64 * u as f64) - 1).max(-1)),
    };
    if b - a >= 1 {
        let span = (a as f64, 0.0, b as f64, bottom);
        draw.rounded_rectangle(span, radius, Some(ink(colour)), None, 1, ALL_CORNERS)?;
    }
    Ok(layer.resize(width as usize, thick as usize, Filter::Lanczos, None, None)?)
}

/// The box an image's whole is laid from.
fn whole(image: &Image) -> (i64, i64, i64, i64) {
    (0, 0, image.width() as i64, image.height() as i64)
}

/// How the progress stands in a frame: its groove's length and thickness and where it lies,
/// the share it shows (rounded to its pixels), whether a stretch of light runs, where.
struct Progress {
    length: i64,
    thick: i64,
    at: (i64, i64),
    frac: Option<f64>,
    running: bool,
    run: f64,
    step: i64,
}

impl Renderer {
    /// _pad: room around the widget for the progress's glow.
    fn download_pad(&self) -> i64 {
        let least = self.theme.width.min(self.theme.height);
        (9.0 * least as f64 / 480.0).ceil() as i64 + 2
    }

    /// accent: the progress's colour: the widget's, the `done` colour for a finished download,
    /// a muted one while it waits.
    fn download_accent(&self, w: &Dict, phase: &str) -> Result<Rgba> {
        let key = match phase {
            "done" => "done",
            "paused" => "muted",
            _ => "color2",
        };
        self.download_colour(w, key)
    }

    /// renderer.color of a colour the card needs (Python fails at none where it takes its
    /// bands).
    fn download_colour(&self, w: &Dict, key: &str) -> Result<Rgba> {
        self.colour_of(w, key)?
            .ok_or_else(|| Failed("'NoneType' object is not subscriptable".into()))
    }

    /// _base: everything that stays while a state does, texts and graph, on a canvas with room
    /// around the widget for the progress's glow.
    fn download_base(&self, w: &Dict, r: &Facts, pad: i64) -> Result<Image> {
        let (ww, wh) = (widget::int(w, "w", 596), widget::int(w, "h", 130));
        let lay = layout(ww, wh);
        let g = lay.g;
        let (cw, ch) = held(ww.saturating_add(2 * pad), wh.saturating_add(2 * pad))?;
        let mut image = Image::blank(Mode::RGBA, cw, ch);
        let o = pad as f64;
        let colour = self.download_colour(w, "color")?;
        let muted = self.download_colour(w, "muted")?;
        let rgb = |c: Rgba| [c[0], c[1], c[2]];
        let [mr, mg, mb] = light::mix(rgb(colour), rgb(muted), 0.45);
        let mid = ink([mr, mg, mb, 255]);
        let tone = self.download_accent(w, &r.phase)?;
        let bold = |size: f64| self.font(widget::text(w, "font"), 6.max(round(size)));
        let plain = |size: f64| self.font(widget::text(w, "label_font"), 6.max(round(size)));

        // the heading: the phase, the source and, to the right (stacked: under it), the name
        let (head, source) = (bold(26.0 * g)?, bold(20.0 * g)?);
        let base = o + lay.y(lay.r.head);
        let word = lp_py::upper(&heading(&r.phase));
        let mut x = o + lay.left;
        x += tracked(&mut image, (x, base), &word, &head, ink(tone), 3.0 * g)?;
        x += 14.0 * g;
        if !r.source.is_empty() && o + lay.right - x > 30.0 * g {
            let width = tracked(
                &mut image,
                (x, base),
                &r.source,
                &source,
                ink(muted),
                2.0 * g,
            )?;
            x += width + 20.0 * g;
        }
        let name_font = plain(22.0 * g)?;
        if lay.stacked {
            if !r.name.is_empty() {
                let text = cut(&r.name, &name_font, lay.right - lay.left)?;
                let at = (o + lay.left, o + lay.y(lay.r.name));
                write(&mut image, at, &text, &name_font, mid, "ls")?;
            }
        } else if !r.name.is_empty() && o + lay.right - x > 40.0 * g {
            let text = cut(&r.name, &name_font, o + lay.right - x)?;
            write(
                &mut image,
                (o + lay.right, base),
                &text,
                &name_font,
                mid,
                "rs",
            )?;
        }

        // the two readings: their names, numbers (ending in one column) and units
        let (label, figure, unit) = (plain(22.0 * g)?, bold(34.0 * g)?, plain(19.0 * g)?);
        for (baseline, (name, value, suffix)) in lay.r.rows.iter().zip(&r.rows) {
            let y = o + lay.y(*baseline);
            let name = lp_py::upper(name);
            tracked(
                &mut image,
                (o + lay.left, y),
                &name,
                &label,
                ink(muted),
                2.0 * g,
            )?;
            write(
                &mut image,
                (o + VALUE_X * g, y),
                value,
                &figure,
                ink(colour),
                "rs",
            )?;
            if !suffix.is_empty() {
                let at = (o + VALUE_X * g + 6.0 * g, y);
                write(&mut image, at, suffix, &unit, ink(muted), "ls")?;
            }
        }
        if !r.amount.is_empty() {
            let small = plain(20.0 * g)?;
            if lay.stacked {
                let at = (o + lay.left, o + lay.y(lay.r.amount));
                write(&mut image, at, &r.amount, &small, mid, "ls")?;
            } else {
                let y = o + lay.y(lay.r.rows[1]);
                let edge = o + VALUE_X * g + 6.0 * g + Renderer::length(&unit, "MB/s")? + 14.0 * g;
                // (not into the unit)
                if o + lay.right - Renderer::length(&small, &r.amount)? > edge {
                    write(&mut image, (o + lay.right, y), &r.amount, &small, mid, "rs")?;
                }
            }
        }

        // the speed of the last minutes, grown from the bottom edge
        if let Some((x0, y0, x1, y1)) = lay.graph {
            let (gw, gh) = (1.max(round(x1 - x0)), 1.max(round(y1 - y0)));
            let rule = ink([muted[0], muted[1], muted[2], 90]);
            let (gx, y) = (pad + round(x0), pad + round(y1));
            let ends = [(gx as f64, y as f64), ((gx + gw - 1) as f64, y as f64)];
            Draw::new(&mut image).line(&ends, Some(rule), 1, false)?;
            if !r.history.is_empty() {
                let curve = graph(&r.history, (gw, gh), g, tone)?;
                image.alpha_composite_at(&curve, (gx, pad + round(y0)), whole(&curve))?;
            }
        }
        Ok(image)
    }

    /// The card's picture: its base and the progress over it, flat or lit.
    fn download_piece(
        &self,
        w: &Dict,
        r: &Facts,
        pad: i64,
        progress: &Progress,
        lit: bool,
    ) -> Result<Piece> {
        let mut canvas = self.download_base(w, r, pad)?;
        let tone = self.download_accent(w, &r.phase)?;
        let track = self.colour_of(w, "background")?;
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let hit = [x, y, widget::int(w, "w", 596), widget::int(w, "h", 130)];
        let Progress {
            length,
            thick,
            at: (bx, by),
            frac,
            running,
            run,
            step,
        } = *progress;
        if !lit {
            let bar = bar_flat(length, thick, frac, tone, track, run)?;
            canvas.alpha_composite_at(&bar, (pad + bx, pad + by), whole(&bar))?;
            let mut piece = Piece::new(canvas, x - pad, y - pad);
            piece.hit = Some(hit);
            return Ok(piece);
        }
        // (the bar lit.bar_piece draws; its groove is the card's background, the stretch's none)
        let mut bar = Dict::new();
        let mut set = |key: &str, value: Value| {
            bar.insert(key.to_string(), value);
        };
        set("x", Value::int(x + bx));
        set("y", Value::int(y + by));
        set("w", Value::int(length));
        set("h", Value::int(thick));
        set("direction", "right".into());
        set("segments", Value::int(0));
        set("segment_gap", Value::int(2));
        set("glow", w.get("glow").cloned().unwrap_or(Value::Float(0.0)));
        set("glow_radius", Value::int(2.max(thick)));
        let unit_px = self.theme.width.min(self.theme.height) as f64 / 480.0;
        let paint: Paint = (tone, None);
        let mut pieces = vec![lit::bar_piece(
            &bar,
            unit_px,
            track,
            frac.unwrap_or(0.0),
            paint,
        )?];
        if frac.is_none() && running {
            // the stretch of light, in the groove
            let span = thick.max(round(length as f64 * STRETCH));
            let start = round(((length - span) * step) as f64 / 64.0);
            let mut stretch = bar.clone();
            stretch.insert("x".into(), Value::int(x + bx + start));
            stretch.insert("w".into(), Value::int(span));
            pieces.push(lit::bar_piece(&stretch, unit_px, None, 1.0, paint)?);
        }
        let mut glow = None;
        for piece in &pieces {
            let at = (piece.x - x + pad, piece.y - y + pad);
            canvas.alpha_composite_at(&piece.layer, at, whole(&piece.layer))?;
            if piece.light.is_some() {
                glow = piece.light.clone();
            }
        }
        let mut piece = Piece::new(canvas, x - pad, y - pad);
        piece.hit = Some(hit);
        piece.light = glow;
        Ok(piece)
    }

    /// _empty: what stands where no download runs: the widget's `empty` text, in the middle.
    fn download_empty(&mut self, w: &Dict) -> Drawn {
        let text = widget::text(w, "empty").to_string();
        if text.is_empty() {
            return Drawn::Nothing;
        }
        let mut plain = w.clone();
        plain.insert("glow".into(), Value::Float(0.0));
        let content = Value::List(vec!["empty".into(), text.clone().into()]);
        let made = self.cached(&plain, content, |r| {
            let (ww, wh) = (widget::int(w, "w", 596), widget::int(w, "h", 130));
            let lay = layout(ww, wh);
            let font = r.font(widget::text(w, "label_font"), 6.max(round(24.0 * lay.g)))?;
            let (lw, lh) = held(ww, wh)?;
            let mut layer = Image::blank(Mode::RGBA, lw, lh);
            let muted = r.download_colour(w, "muted")?;
            let at = (ww as f64 / 2.0, wh as f64 / 2.0);
            write(&mut layer, at, &text, &font, ink(muted), "mm")?;
            Ok(Piece::new(
                layer,
                widget::int(w, "x", 0),
                widget::int(w, "y", 0),
            ))
        });
        match made {
            Ok(piece) => Drawn::Piece(piece),
            Err(e) => Drawn::Failed(e),
        }
    }

    /// download.draw: the download card of the state the widget names (its progress light in
    /// its groove with the lit finish), at the frame's time; where nothing downloads, its
    /// `empty` text or nothing. A moving frame moves on while its light runs to and fro.
    pub(crate) fn download(&mut self, w: &Dict, lit: bool, snapshot: &Snapshot, now: f64) -> Drawn {
        let state = match snapshot.state(widget::text(w, "state")) {
            Ok(Some(Value::Dict(state))) => state,
            Ok(_) => return self.download_empty(w),
            Err(what) => return Drawn::Unported(what),
        };
        let r = match read(&state) {
            Ok(r) => r,
            Err(e) => return Drawn::Failed(e),
        };
        let pad = self.download_pad();
        let lay = layout(widget::int(w, "w", 596), widget::int(w, "h", 130));
        let (x0, y0, x1, y1) = rail(&lay);
        let (length, thick) = (2.max(round(x1 - x0)), 2.max(round(y1 - y0)));
        let running = r.share.is_none() && r.phase != "paused";
        if running && self.options.animate {
            // (a moving frame moves on with its light)
            self.moving = true;
        }
        let run = if running {
            0.5 - 0.5 * (2.0 * PI * now / RUN).cos()
        } else {
            0.0
        };
        let frac = r
            .share
            .map(|share| lp_py::round(share * length as f64) / length as f64);
        // (the stretch's places: a frame that stays has the same picture)
        let step = round(run * 64.0);
        let progress = Progress {
            length,
            thick,
            at: (round(x0), round(y0)),
            frac,
            running,
            run,
            step,
        };
        let content = Value::List(vec![
            r.value(),
            frac.map_or(Value::None, Value::Float),
            Value::int(if frac.is_none() && running { step } else { 0 }),
            (if lit { "lit" } else { "flat" }).into(),
        ]);
        // (its glow is the progress's, drawn with it: the renderer's own halo is not wanted)
        let mut plain = w.clone();
        plain.insert("glow".into(), Value::Float(0.0));
        let made = self.cached(&plain, content, |renderer| {
            renderer.download_piece(w, &r, pad, &progress, lit)
        });
        match made {
            Ok(piece) => Drawn::Piece(piece),
            Err(e) => Drawn::Failed(e),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spans_as_python_words_them() {
        assert_eq!(time_left(-3.0), ("0".into(), "s".into()));
        assert_eq!(time_left(59.9), ("59".into(), "s".into()));
        assert_eq!(time_left(61.0), ("1:01".into(), "min".into()));
        assert_eq!(time_left(3600.0), ("1:00".into(), "h".into()));
        assert_eq!(time_left(86399.0), ("23:59".into(), "h".into()));
        assert_eq!(amount(-1.0), ("0".into(), "MB".into()));
        assert_eq!(amount(1024.0 * MB), ("1.0".into(), "GB".into()));
        assert_eq!(of_total(-1.0, 2.0 * MB), "-0 / 2 MB");
    }
}
