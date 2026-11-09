//! The looks of the volume message (render/volume_looks.py), chosen for every screen at once
//! (`[services.notices]` `volume_look`) and named in the message's payload ("look"): a bar
//! along the bottom edge, levels as on a mixer, a ring, and a large number in the middle. Each
//! fades and slides in, changes in place while the volume turns (its level glides to the new
//! one) and keeps clear of what the panel's frame hides. Sizes are for a panel 480 px high and
//! scale with the shorter side of the frame.
//!
//! Python makes a look's picture anew only where it changed since the one before, and its tests
//! hold each to the picture made whole, pixel for pixel (test_volume_looks.py): here each is
//! made whole. What a look keeps between frames that decides what shows is kept as Python keeps
//! it: the picture of what it shows (made again only where the text, muted, the level's steps,
//! the size or the theme change) and the level gliding.

use std::rc::Rc;

use lp_pixels::{Draw, Filter, Image, Mode, ALL_CORNERS};
use lp_py::Value;
use lp_text::fallback::FallbackFont;
use lp_text::text::{draw as draw_text, Text};

use crate::icons;
use crate::light::{self, ease_out, mix, py_max, py_min};
use crate::overlays::{accent_colour, Context, Toast};
use crate::piece::{Failed, Result};
use crate::renderer::Renderer;

/// The looks by name (VOLUME_LOOKS).
pub(crate) const NAMES: [&str; 4] = ["bar", "levels", "ring", "big"];
/// drawn this much larger, then made smaller: smooth edges
const SS: i64 = 3;
const NUMBER_FACE: &str = "Barlow-Bold";
/// the level's time to reach a new value
const GLIDE_S: f64 = 0.16;
const FADE_IN_S: f64 = 0.18;
/// leave_s: the time a look takes to leave after its hold time
pub(crate) const LEAVE_S: f64 = 0.3;
/// LevelsLook.BARS
const BARS: i64 = 16;

/// Python's round(x) of a float, as an int.
fn round_int(x: f64) -> i64 {
    lp_py::round(x) as i64
}

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.to_string())
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Bar,
    Levels,
    Ring,
    Big,
}

/// What of the level a picture shows (steps): how many levels are lit, or the level to three
/// places.
#[derive(PartialEq)]
enum Steps {
    Lit(i64),
    Level(f64),
}

/// What a picture shows (_laid's key): made again where it changes.
#[derive(PartialEq)]
struct Shows {
    text: String,
    muted: bool,
    steps: Steps,
    size: (i64, i64),
    context: u64,
}

/// colors: the look's colours of the theme's roles.
struct Colours {
    accent: [i64; 3],
    card: [i64; 3],
    text: [i64; 3],
    muted: [i64; 3],
    track: [i64; 3],
}

fn ink(c: [i64; 3]) -> [u8; 4] {
    [c[0] as u8, c[1] as u8, c[2] as u8, 255]
}

/// A blank RGBA picture of `w` x `h`, as Image.new makes one (a negative size fails).
fn blank(w: i64, h: i64) -> Result<Image> {
    if w < 0 || h < 0 {
        return Err(Failed("Width and height must be >= 0".into()));
    }
    if w.saturating_mul(h) > crate::renderer::MOST_PIXELS {
        return Err(Failed("a look too large".into()));
    }
    Ok(Image::blank(Mode::RGBA, w as usize, h as usize))
}

/// A look's picture as it shows now: the picture, how far it shows (0 to 1) and where it goes.
type Laid = (Rc<Image>, f64, i64, i64);

/// One look of the volume (VolumeLook and its kinds): what it keeps from frame to frame.
pub(crate) struct Look {
    kind: Kind,
    pub(crate) anchor: String,
    /// the share shown, gliding to the message's (None: none shown yet)
    level: Option<f64>,
    from: f64,
    to: f64,
    since: f64,
    /// (what it shows, its picture)
    made: Option<(Shows, Rc<Image>)>,
}

impl Look {
    pub(crate) fn new(name: &str, anchor: &str) -> Option<Look> {
        let kind = match name {
            "bar" => Kind::Bar,
            "levels" => Kind::Levels,
            "ring" => Kind::Ring,
            "big" => Kind::Big,
            _ => return None,
        };
        Some(Look {
            kind,
            anchor: anchor.to_string(),
            level: None,
            from: 0.0,
            to: 0.0,
            since: 0.0,
            made: None,
        })
    }

    /// forget: the message is gone, and what was kept to make its next picture quickly.
    pub(crate) fn forget(&mut self) {
        self.made = None;
    }

    // -- what a message says ------------------------------------------------------------

    /// share: the payload's progress as float() takes it, held to 0..1; 0 where float()
    /// refuses it (TypeError, ValueError), a failure where it raises otherwise (an int too
    /// large for a float).
    fn share(toast: &Toast) -> Result<f64> {
        let x = match toast.payload.get("progress") {
            None => 0.0,
            Some(Value::Bool(b)) => f64::from(u8::from(*b)),
            Some(Value::Int(n)) => lp_py::num::int_to_float(n)
                .map_err(|_| Failed("int too large to convert to float".into()))?,
            Some(Value::Float(x)) => *x,
            Some(Value::Str(s)) => match lp_py::float_of_str(s) {
                Some(x) => x,
                None => return Ok(0.0),
            },
            Some(_) => return Ok(0.0),
        };
        Ok(py_max(0.0, py_min(1.0, x)))
    }

    fn muted(toast: &Toast) -> bool {
        toast.payload.get("muted").is_some_and(Value::truthy)
    }

    fn unit(size: (i64, i64)) -> f64 {
        py_max(0.5, size.0.min(size.1) as f64 / 480.0)
    }

    fn colours(toast: &Toast, context: &Context, renderer: &Renderer) -> Colours {
        let accent = accent_colour(renderer, &context.palette, toast);
        let role = |name: &str| light::role_colour(&context.palette, name);
        Colours {
            accent: if Self::muted(toast) {
                role("muted")
            } else {
                accent
            },
            card: role("card"),
            text: role("text"),
            muted: role("muted"),
            track: role("track"),
        }
    }

    // -- drawing ---------------------------------------------------------------------------

    /// draw: `frame` with the look laid on it, and where it drew; None while it does not show
    /// (the frame as it is).
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn draw(
        &mut self,
        frame: &Image,
        toast: &Rc<Toast>,
        age: f64,
        previous: bool,
        clock: f64,
        context: &Context,
        renderer: &Renderer,
    ) -> Result<Option<(Image, [i64; 4])>> {
        let size = (frame.width() as i64, frame.height() as i64);
        let Some((picture, shown, x, y)) =
            self.laid(size, toast, age, previous, clock, context, renderer)?
        else {
            return Ok(None);
        };
        let alpha = crate::overlays::through(&picture, shown)?;
        let mut out = frame.clone();
        // (picture.convert(frame.mode): an RGB frame takes its colours as they are)
        out.paste(&picture, x, y, Some(&alpha)).map_err(failed)?;
        let bounds = [
            x,
            y,
            x + picture.width() as i64,
            y + picture.height() as i64,
        ];
        Ok(Some((out, bounds)))
    }

    /// layer: the look as draw lays it over a frame of `size`, one picture (RGBA) at (x, y)
    /// laid through its alpha; None while it does not show.
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn layer(
        &mut self,
        size: (i64, i64),
        toast: &Rc<Toast>,
        age: f64,
        previous: bool,
        clock: f64,
        context: &Context,
        renderer: &Renderer,
    ) -> Result<Option<(Rc<Image>, i64, i64)>> {
        let Some((picture, shown, x, y)) =
            self.laid(size, toast, age, previous, clock, context, renderer)?
        else {
            return Ok(None);
        };
        Ok(Some((crate::overlays::faded(&picture, shown)?, x, y)))
    }

    /// _laid: its picture, how far it shows (0 to 1) and where it goes, at `age`; None while it
    /// does not show. `clock` is time.monotonic() for the level's glide.
    #[allow(clippy::too_many_arguments)]
    fn laid(
        &mut self,
        size: (i64, i64),
        toast: &Rc<Toast>,
        age: f64,
        previous: bool,
        clock: f64,
        context: &Context,
        renderer: &Renderer,
    ) -> Result<Option<Laid>> {
        let level = self.glide(toast, previous, clock, context.preview)?;
        let Some(seconds) = toast.seconds else {
            return Err(Failed(
                "unsupported operand type(s) for +: 'NoneType' and 'float'".into(),
            ));
        };
        let leaving = (seconds + LEAVE_S - age) / LEAVE_S;
        let arriving = if previous { 1.0 } else { age / FADE_IN_S }; // (replaced in place)
        let shown = py_max(0.0, py_min(py_min(1.0, arriving), leaving));
        if shown <= 0.0 {
            return Ok(None);
        }
        let shows = Shows {
            text: toast.text.clone(),
            muted: Self::muted(toast),
            steps: self.steps(level),
            size,
            context: context.id,
        };
        let picture = match &self.made {
            Some((made, picture)) if *made == shows => picture.clone(),
            _ => {
                let picture = Rc::new(self.picture(toast, level, size, context, renderer)?);
                self.made = Some((shows, picture.clone()));
                picture
            }
        };
        let (w, h) = (picture.width() as i64, picture.height() as i64);
        let (x, y) = self.place(size, (w, h), 1.0 - ease_out(shown), context);
        Ok(Some((picture, shown, x, y)))
    }

    /// _glide: the level to show now, the message's, reached in GLIDE_S from where it was; a
    /// still picture (a preview) shows where it ends.
    fn glide(&mut self, toast: &Toast, previous: bool, now: f64, preview: bool) -> Result<f64> {
        let target = if Self::muted(toast) {
            0.0
        } else {
            Self::share(toast)?
        };
        if preview {
            self.level = Some(target);
            return Ok(target);
        }
        match self.level {
            Some(level) if previous => {
                if target != self.to {
                    (self.from, self.to, self.since) = (level, target, now);
                }
            }
            _ => (self.from, self.to, self.since) = (target, target, now),
        }
        let u = ease_out((now - self.since) / GLIDE_S);
        let level = self.from + (self.to - self.from) * u;
        self.level = Some(level);
        Ok(level)
    }

    /// steps: what of `level` the picture shows; a new picture where that changes.
    fn steps(&self, level: f64) -> Steps {
        match self.kind {
            Kind::Levels => Steps::Lit((0..BARS).filter(|&i| lit(level, i)).count() as i64),
            _ => Steps::Level(lp_py::round_digits(level, 3)),
        }
    }

    fn hidden(size: (i64, i64), context: &Context) -> [i64; 4] {
        let orientation = if size.0 > size.1 {
            "landscape"
        } else {
            "portrait"
        };
        context
            .model
            .map_or([0; 4], |model| model.hidden_edges(orientation))
    }

    /// place: where the picture goes, `slide` (0 to 1) of a short way from it while it comes
    /// and goes.
    fn place(
        &self,
        frame: (i64, i64),
        size: (i64, i64),
        slide: f64,
        context: &Context,
    ) -> (i64, i64) {
        let ((fw, fh), (w, h)) = (frame, size);
        let [top, right, bottom, left] = Self::hidden(frame, context);
        match self.kind {
            Kind::Bar => {
                let margin = 8.max(fw.min(fh).div_euclid(40));
                let x = left + (fw - left - right - w).div_euclid(2);
                (x, fh - bottom - margin - h + round_int(slide * h as f64))
            }
            Kind::Big => {
                let x = left + (fw - left - right - w).div_euclid(2);
                let y = top + (fh - top - bottom - h).div_euclid(2);
                (x, y + round_int(slide * fw.min(fh) as f64 / 24.0))
            }
            Kind::Levels | Kind::Ring => {
                let margin = 10.max(fw.min(fh).div_euclid(24));
                let shift = round_int(slide * margin as f64);
                let y = if self.anchor.starts_with("top") {
                    top + margin - shift
                } else {
                    fh - bottom - margin - h + shift
                };
                let x = if self.anchor.ends_with("right") {
                    fw - right - margin - w
                } else if self.anchor.ends_with("left") {
                    left + margin
                } else {
                    (fw - w).div_euclid(2)
                };
                (x, y)
            }
        }
    }

    fn picture(
        &self,
        toast: &Toast,
        level: f64,
        size: (i64, i64),
        context: &Context,
        renderer: &Renderer,
    ) -> Result<Image> {
        let c = Self::colours(toast, context, renderer);
        let u = Self::unit(size) * SS as f64;
        let face = |px: i64| light::face(&renderer.env, NUMBER_FACE, px);
        match self.kind {
            Kind::Bar => {
                let [_, right, _, left] = Self::hidden(size, context);
                let room = size.0 - left - right - 2 * 8.max(size.0.min(size.1).div_euclid(40));
                let (w, h) = (room * SS, round_int(56.0 * u));
                let icon = round_int(28.0 * u);
                let font = face(round_int(30.0 * u))?;
                let text = &toast.text;
                let text_w = round_int(light::length(&font, text)?);
                let x0 = round_int(18.0 * u) + icon + round_int(16.0 * u);
                let x1 = w - round_int(22.0 * u) - text_w - round_int(18.0 * u);
                let mut card = panel(w, h, &c, round_int(h as f64 / 2.0))?;
                speaker_at(&mut card, toast, icon, round_int(18.0 * u), c.accent)?;
                write(
                    &mut card,
                    (w - round_int(22.0 * u), h.div_euclid(2)),
                    text,
                    &font,
                    c.text,
                    "rm",
                )?;
                let thick = round_int(8.0 * u);
                groove(&mut card, (x0, x1), h.div_euclid(2), thick, &c)?;
                fill(
                    &mut card,
                    (x0, x1),
                    h.div_euclid(2),
                    thick,
                    level,
                    &c,
                    6.0 * u,
                )?;
                small(&card)
            }
            Kind::Levels => {
                let w = round_int(py_min(430.0 * u, (size.0 * SS) as f64 * 0.8));
                let h = round_int(118.0 * u);
                let mut card = panel(w, h, &c, round_int(18.0 * u))?;
                let icon = round_int(30.0 * u);
                speaker_at(&mut card, toast, icon, round_int(22.0 * u), c.accent)?;
                let font = face(round_int(38.0 * u))?;
                let text = &toast.text;
                let right = w - round_int(22.0 * u);
                write(
                    &mut card,
                    (right, h.div_euclid(2)),
                    text,
                    &font,
                    c.text,
                    "rm",
                )?;
                let x0 = round_int(22.0 * u) + icon + round_int(20.0 * u);
                let x1 = right - round_int(light::length(&font, text)?) - round_int(20.0 * u);
                let (bottom, top) = (h - round_int(30.0 * u), round_int(30.0 * u));
                let step = (x1 - x0) as f64 / BARS as f64;
                let bar_w = SS.max(round_int(step * 0.62));
                let warn = if Self::muted(toast) {
                    c.accent
                } else {
                    light::role_colour(&context.palette, "warning")
                };
                let mut lit_boxes: Vec<([i64; 4], [i64; 3])> = Vec::new();
                for i in 0..BARS {
                    let share = (i + 1) as f64 / BARS as f64;
                    let bar_h = round_int((bottom - top) as f64 * (0.28 + 0.72 * share));
                    let x = round_int(x0 as f64 + i as f64 * step);
                    let bounds = [x, bottom - bar_h, x + bar_w, bottom];
                    if lit(level, i) {
                        let colour = mix(warn, c.accent, py_max(0.0, (share - 0.75) / 0.25));
                        lit_boxes.push((bounds, colour));
                    } else {
                        rounded(&mut card, bounds, bar_w.div_euclid(3), c.track)?;
                    }
                }
                if !lit_boxes.is_empty() {
                    let bounds = [
                        lit_boxes.iter().map(|(b, _)| b[0]).min().unwrap_or(0),
                        lit_boxes.iter().map(|(b, _)| b[1]).min().unwrap_or(0),
                        lit_boxes.iter().map(|(b, _)| b[2]).max().unwrap_or(0),
                        lit_boxes.iter().map(|(b, _)| b[3]).max().unwrap_or(0),
                    ];
                    glowing(&mut card, bounds, 5.0 * u, |layer, dx, dy| {
                        for (b, colour) in &lit_boxes {
                            let at = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy];
                            rounded(layer, at, bar_w.div_euclid(3), *colour)?;
                        }
                        Ok(())
                    })?;
                }
                small(&card)
            }
            Kind::Ring => {
                let d = round_int(py_min(170.0 * u, (size.0.min(size.1) * SS) as f64 * 0.8));
                let mut card = ringed(d, level, &c, 6.0 * u)?;
                let text = &toast.text;
                let share = if text.chars().count() <= 3 { 0.3 } else { 0.16 };
                let font = face(round_int(d as f64 * share))?;
                write(
                    &mut card,
                    (d.div_euclid(2), round_int(d as f64 * 0.47)),
                    text,
                    &font,
                    c.text,
                    "mm",
                )?;
                let icon = round_int(d as f64 * 0.16);
                let speaker = speaker(toast, icon, c.muted)?;
                composite(
                    &mut card,
                    &speaker,
                    ((d - icon).div_euclid(2), round_int(d as f64 * 0.66)),
                )?;
                small(&card)
            }
            Kind::Big => {
                let w = round_int(py_min(560.0 * u, (size.0 * SS) as f64 * 0.9));
                let h = round_int(py_min(236.0 * u, (size.1 * SS) as f64 * 0.8));
                let span = (round_int(w as f64 * 0.1), round_int(w as f64 * 0.9));
                let (y, thick) = (round_int(h as f64 * 0.78), SS.max(round_int(12.0 * u)));
                let radius = round_int(28.0 * u);
                let mut card = panel(w, h, &c, radius)?;
                let text = &toast.text;
                let share = if text.chars().count() <= 3 {
                    0.46
                } else {
                    0.24
                };
                let font = face(round_int(h as f64 * share))?;
                let icon = round_int(h as f64 * 0.26);
                let text_w = round_int(light::length(&font, text)?);
                let gap = round_int(18.0 * u);
                let left = (w - icon - gap - text_w).div_euclid(2);
                let mid = round_int(h as f64 * 0.42);
                let speaker = speaker(toast, icon, c.accent)?;
                let top = mid - icon.div_euclid(2);
                composite(&mut card, &speaker, (left, top))?;
                write(
                    &mut card,
                    (left + icon + gap, mid),
                    text,
                    &font,
                    c.text,
                    "lm",
                )?;
                groove(&mut card, span, y, thick, &c)?;
                fill(&mut card, span, y, thick, level, &c, 8.0 * u)?;
                small(&card)
            }
        }
    }
}

/// LevelsLook.lit: whether level `i` (of BARS) is lit at `level`.
fn lit(level: f64, i: i64) -> bool {
    level >= (i + 1) as f64 / BARS as f64 - 0.5 / BARS as f64 && level > 0.0
}

/// panel: a card of `w` x `h` (drawn sizes) with a fine light edge.
fn panel(w: i64, h: i64, c: &Colours, radius: i64) -> Result<Image> {
    let mut card = blank(w, h)?;
    let whole = (0.0, 0.0, (w - 1) as f64, (h - 1) as f64);
    let [r, g, b, _] = ink(c.card);
    let mut pen = Draw::new(&mut card);
    pen.rounded_rectangle(
        whole,
        radius as f64,
        Some([r, g, b, 238]),
        None,
        1,
        ALL_CORNERS,
    )
    .map_err(failed)?;
    let edge = [255, 255, 255, 30];
    pen.rounded_rectangle(
        whole,
        radius as f64,
        None,
        Some(edge),
        SS as i32,
        ALL_CORNERS,
    )
    .map_err(failed)?;
    Ok(card)
}

fn rounded(card: &mut Image, b: [i64; 4], radius: i64, colour: [i64; 3]) -> Result<()> {
    let xy = (b[0] as f64, b[1] as f64, b[2] as f64, b[3] as f64);
    Draw::new(card)
        .rounded_rectangle(xy, radius as f64, Some(ink(colour)), None, 1, ALL_CORNERS)
        .map_err(failed)
}

/// alpha_composite of `top` over `card` at `at`.
fn composite(card: &mut Image, top: &Image, at: (i64, i64)) -> Result<()> {
    let whole = (0, 0, top.width() as i64, top.height() as i64);
    card.alpha_composite_at(top, at, whole).map_err(failed)
}

/// ImageDraw.text on the card: `text` in `colour` at `at` by `anchor`.
fn write(
    card: &mut Image,
    at: (i64, i64),
    text: &str,
    font: &FallbackFont,
    colour: [i64; 3],
    anchor: &str,
) -> Result<()> {
    let written = Text::new(text, font, "RGBA");
    draw_text(
        card,
        (at.0 as f64, at.1 as f64),
        &written,
        ink(colour),
        None,
        Some(anchor),
        "left",
    )
    .map_err(crate::text::failed)
}

/// speaker: the speaker (muted or not) `size` wide in `colour`.
fn speaker(toast: &Toast, size: i64, colour: [i64; 3]) -> Result<Image> {
    let name = if Look::muted(toast) { "mute" } else { "volume" };
    icons::draw_icon(name, size, ink(colour), 2.0).map_err(failed)
}

/// speaker_at: the speaker at `x`, in the middle of the card's height.
fn speaker_at(card: &mut Image, toast: &Toast, size: i64, x: i64, colour: [i64; 3]) -> Result<()> {
    let icon = speaker(toast, size, colour)?;
    let y = (card.height() as i64 - size).div_euclid(2);
    composite(card, &icon, (x, y))
}

/// groove: the bar's groove from `span.0` to `span.1` at `y`.
fn groove(card: &mut Image, span: (i64, i64), y: i64, thick: i64, c: &Colours) -> Result<()> {
    let half = thick.div_euclid(2);
    rounded(card, [span.0, y - half, span.1, y + half], half, c.track)
}

/// fill: the groove filled to `level`, with its glow.
#[allow(clippy::too_many_arguments)]
fn fill(
    card: &mut Image,
    span: (i64, i64),
    y: i64,
    thick: i64,
    level: f64,
    c: &Colours,
    glow: f64,
) -> Result<()> {
    if level <= 0.0 {
        return Ok(());
    }
    let (x0, x1) = span;
    let half = thick.div_euclid(2);
    let bounds = [
        x0,
        y - half,
        x0 + thick.max(round_int((x1 - x0) as f64 * level)),
        y + half,
    ];
    let accent = c.accent;
    glowing(card, bounds, glow, |layer, dx, dy| {
        let at = [
            bounds[0] + dx,
            bounds[1] + dy,
            bounds[2] + dx,
            bounds[3] + dy,
        ];
        rounded(layer, at, half, accent)
    })
}

/// reach: how far the glow of `radius` reaches: Pillow's three passes of a box blur, each its
/// radius and a pixel wide.
fn reach(radius: f64) -> i64 {
    3 * (py_max(1.0, radius).ceil() as i64 + 2)
}

/// glowing: what `paint(layer, dx, dy)` draws within `bounds` (x0, y0, x1, y1 on `card`,
/// shifted by dx, dy onto the part it gets), with its glow, laid onto `card`: worked out in
/// the part of the card the glow can reach alone.
fn glowing(
    card: &mut Image,
    bounds: [i64; 4],
    radius: f64,
    paint: impl Fn(&mut Image, i64, i64) -> Result<()>,
) -> Result<()> {
    let reach = reach(radius);
    let (x0, y0) = ((bounds[0] - reach).max(0), (bounds[1] - reach).max(0));
    let x1 = (card.width() as i64).min(bounds[2] + 1 + reach);
    let y1 = (card.height() as i64).min(bounds[3] + 1 + reach);
    if x1 <= x0 || y1 <= y0 {
        return Ok(());
    }
    let mut layer = blank(x1 - x0, y1 - y0)?;
    paint(&mut layer, -x0, -y0)?;
    let lit = glow(&layer, radius)?;
    composite(card, &lit, (x0, y0))
}

/// glow: `layer` with a soft glow of itself beneath it.
fn glow(layer: &Image, radius: f64) -> Result<Image> {
    let r = py_max(1.0, radius) as f32;
    let soft = layer.gaussian_blur(r, r).map_err(failed)?;
    let mut out = Image::blank(Mode::RGBA, layer.width(), layer.height());
    composite(&mut out, &soft, (0, 0))?;
    composite(&mut out, layer, (0, 0))?;
    Ok(out)
}

/// ringed: the ring `d` wide, drawn large, without its number: the card, the groove and the
/// sweep lit to `level` with its glow.
fn ringed(d: i64, level: f64, c: &Colours, glow_r: f64) -> Result<Image> {
    let (pad, width) = (
        round_int(d as f64 * 0.11),
        SS.max(round_int(d as f64 * 0.07)),
    );
    let xy = (
        pad as f64,
        pad as f64,
        (d - 1 - pad) as f64,
        (d - 1 - pad) as f64,
    );
    let mut sweep = blank(d, d)?;
    if level > 0.0 {
        Draw::new(&mut sweep)
            .arc(
                xy,
                135.0,
                135.0 + 270.0 * level,
                Some(ink(c.accent)),
                width as i32,
            )
            .map_err(failed)?;
    }
    let mut card = blank(d, d)?;
    let [r, g, b, _] = ink(c.card);
    let mut pen = Draw::new(&mut card);
    let whole = (0.0, 0.0, (d - 1) as f64, (d - 1) as f64);
    pen.ellipse(
        whole,
        Some([r, g, b, 238]),
        Some([255, 255, 255, 30]),
        SS as i32,
    )
    .map_err(failed)?;
    // three quarters, open below
    pen.arc(xy, 135.0, 405.0, Some(ink(c.track)), width as i32)
        .map_err(failed)?;
    if level > 0.0 {
        let lit = glow(&sweep, glow_r)?;
        composite(&mut card, &lit, (0, 0))?;
    }
    Ok(card)
}

/// small: the picture drawn large made SS times smaller (made_smaller).
fn small(picture: &Image) -> Result<Image> {
    let (w, h) = (picture.width(), picture.height());
    made_smaller(picture, (1.max(w / SS as usize), 1.max(h / SS as usize)))
}

/// made_smaller: `picture` (RGBA) made `size` with LANCZOS as Pillow up to 12 makes it in one:
/// its colours premultiplied by their alpha, first across, then down.
fn made_smaller(picture: &Image, size: (usize, usize)) -> Result<Image> {
    let mut out = picture.convert(Mode::RGBa).map_err(failed)?;
    if out.width() != size.0 {
        out = out
            .resize(size.0, out.height(), Filter::Lanczos, None, None)
            .map_err(failed)?;
    }
    if out.height() != size.1 {
        out = out
            .resize(size.0, size.1, Filter::Lanczos, None, None)
            .map_err(failed)?;
    }
    out.convert(Mode::RGBA).map_err(failed)
}
