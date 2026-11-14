//! The alarm on every panel (render/alarm.py): a red edge for a moment when a sensor reaches
//! its critical limit. The notices service publishes `alarm` (1 while a limit of its warnings
//! is at crit, and a few seconds after); each panel's loop tells its edge the reading on every
//! frame, and the edge pulses round the picture for SECONDS from the moment it turns 1, on all
//! panels at once, as they share the readings. A calm panel shows it still.

use lp_pixels::{Draw, Image, Mode};

use crate::piece::{Failed, Result};

/// How long the edge shows, one fall and rise of it, the strengths it is kept in (made once
/// each), its colour for a theme without "crit", its width (of the shorter side) and its
/// opacity at the rim.
pub const SECONDS: f64 = 3.0;
const PULSE_S: f64 = 1.0;
const LEVELS: i64 = 12;
const COLOUR: &str = "#FF3B5C";
const WIDTH: f64 = 0.035;
const STRONGEST: f64 = 235.0;

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.0)
}

/// A mask's size and strength, which it is kept by.
type Key = ((usize, usize), i64);

/// AlarmEdge: whether the alarm is on, since when the edge shows, and its masks by size and
/// strength.
#[derive(Default)]
pub struct AlarmEdge {
    pub on: bool,
    pub began: Option<f64>,
    made: Vec<(Key, Image)>,
}

impl AlarmEdge {
    pub fn new() -> AlarmEdge {
        AlarmEdge::default()
    }

    /// follow: the alarm reading on this frame; the edge begins when it turns on.
    pub fn follow(&mut self, on: bool, now: f64) {
        if on && !self.on {
            self.began = Some(now);
        }
        self.on = on;
    }

    /// showing: whether the edge shows at `now`.
    pub fn showing(&self, now: f64) -> bool {
        self.began
            .is_some_and(|began| 0.0 <= now - began && now - began < SECONDS)
    }

    /// kept: how many masks the edge keeps (they go when it is over).
    pub fn kept(&self) -> usize {
        self.made.len()
    }

    /// draw: `frame` with the edge while it shows (a new picture; None: the frame as it is), in
    /// the theme's `colour` or the alarm's own; `still` on a calm panel.
    pub fn draw(
        &mut self,
        frame: &Image,
        now: f64,
        colour: Option<&str>,
        still: bool,
    ) -> Result<Option<Image>> {
        let Some(began) = self.began.filter(|_| self.showing(now)) else {
            if self.began.is_some_and(|began| now - began >= SECONDS) {
                self.began = None; // (the masks go with it)
                self.made.clear();
            }
            return Ok(None);
        };
        let strength = if still {
            1.0
        } else {
            // strong at once, then softer and strong again, once a pulse
            0.6 + 0.4 * (2.0 * std::f64::consts::PI * (now - began) / PULSE_S).cos()
        };
        let level = 1.max(lp_py::round(strength * LEVELS as f64) as i64);
        let size = (frame.width(), frame.height());
        let at = match self.made.iter().position(|(key, _)| *key == (size, level)) {
            Some(at) => at,
            None => {
                let mask = edge(size, STRONGEST * level as f64 / LEVELS as f64)?;
                self.made.push(((size, level), mask));
                self.made.len() - 1
            }
        };
        let colour = match colour {
            Some(c) if !c.is_empty() => c,
            _ => COLOUR,
        };
        let ink: Vec<u8> = lp_pixels::colour::getcolor(colour, frame.mode().name())
            .map_err(failed)?
            .iter()
            .map(|&v| v.clamp(0, 255) as u8)
            .collect();
        let mut out = frame.clone();
        out.paste_colour(
            &ink,
            (0, 0, size.0 as i64, size.1 as i64),
            Some(&self.made[at].1),
        )
        .map_err(failed)?;
        Ok(Some(out))
    }
}

/// _edge: a mask that is `strongest` at the rim and fades to nothing inwards.
fn edge((w, h): (usize, usize), strongest: f64) -> Result<Image> {
    let width = 3.max(lp_py::round(w.min(h) as f64 * WIDTH) as i64);
    let mut mask = Image::blank(Mode::L, w, h);
    let mut draw = Draw::new(&mut mask);
    let (wi, hi) = (w as i64, h as i64);
    for inset in 0..width {
        if inset * 2 >= wi.min(hi) {
            break;
        }
        let fade = lp_py::round(strongest * (1.0 - inset as f64 / width as f64).powf(1.6));
        let bounds = (
            inset as f64,
            inset as f64,
            (wi - 1 - inset) as f64,
            (hi - 1 - inset) as f64,
        );
        draw.rectangle(
            bounds,
            None,
            Some([fade.clamp(0.0, 255.0) as u8, 0, 0, 0]),
            1,
        )
        .map_err(failed)?;
    }
    Ok(mask)
}
