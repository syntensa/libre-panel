//! Analog clocks (the renderer's _draw_analog and _analog_piece) in a still frame: the face, its
//! rim and marks, the hour and minute hands, the second hand and the dot in the middle, drawn
//! three times as large and reduced as _canvas and _down make them.

use lp_pixels::{Draw, Image, Mode};
use lp_py::Dict;

use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Renderer, MOST_PIXELS};
use crate::shapes::{self, ink, SUPERSAMPLE};
use crate::widget;

/// A hand's width as Pillow takes it: round(width), a pixel at least (Pillow refuses one past an
/// int's range as Python's C code does).
fn line_width(width: f64) -> Result<i32> {
    i32::try_from((lp_py::round(width) as i64).max(1))
        .map_err(|_| Failed("signed integer is greater than maximum".into()))
}

impl Renderer {
    /// _analog_piece(widget, hour, minute, second) with part "all": the clock face with its
    /// hands, the second hand at `second` (none below 0).
    pub(crate) fn analog_piece(
        &self,
        w: &Dict,
        hour: i64,
        minute: i64,
        second: i64,
    ) -> Result<Piece> {
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (fw, fh) = (
            widget::int(w, "w", 160).max(8),
            widget::int(w, "h", 160).max(8),
        );
        let s = SUPERSAMPLE as i64;
        let (kw, kh) = (fw.saturating_mul(s), fh.saturating_mul(s));
        if kw.saturating_mul(kh) > MOST_PIXELS {
            return Err(Failed("a clock too large".into()));
        }
        let mut layer = Image::blank(Mode::RGBA, kw as usize, kh as usize);
        // (Python's ints, then true division: exact for any box that fits)
        let r = (fw.min(fh) * s) as f64 / 2.0;
        let (cx, cy) = ((fw * s) as f64 / 2.0, (fh * s) as f64 / 2.0);
        // at(angle, length): the point `length` from the middle at `angle` degrees from twelve
        let at = |angle: f64, length: f64| {
            let a = (angle - 90.0).to_radians(); // (math.radians: times pi / 180, as Rust's)
            (cx + length * a.cos(), cy + length * a.sin())
        };
        let mut draw = Draw::new(&mut layer);
        // hand(angle, length, width, colour, tail): a line from `tail` behind the middle to
        // `length` before it, a disc of its width at each end
        let hand = |draw: &mut Draw<Image>,
                    angle: f64,
                    length: f64,
                    width: f64,
                    colour: [u8; 4],
                    tail: f64|
         -> Result<()> {
            let (start, end) = (at(angle + 180.0, tail), at(angle, length));
            draw.line(&[start, end], Some(colour), line_width(width)?, false)?;
            for (px, py) in [start, end] {
                let half = width / 2.0;
                draw.ellipse(
                    (px - half, py - half, px + half, py + half),
                    Some(colour),
                    None,
                    1,
                )?;
            }
            Ok(())
        };
        if widget::truthy(w, "face") {
            if let Some(face) = self.colour_of(w, "face")? {
                draw.ellipse(
                    (cx - r, cy - r, cx + r - 1.0, cy + r - 1.0),
                    Some(ink(face)),
                    None,
                    1,
                )?;
            }
        }
        if widget::truthy(w, "marks") {
            if let Some(marks) = self.colour_of(w, "marks")? {
                // (a faint rim, as the lit rings have an edge: the face stands off the card)
                let mut rim = marks;
                rim[3] = lp_py::round(marks[3] as f64 * 0.4) as i64;
                draw.ellipse(
                    (cx - r, cy - r, cx + r - 1.0, cy + r - 1.0),
                    None,
                    Some(ink(rim)),
                    line_width(r * 0.012)?,
                )?;
                let fine = fw.min(fh) >= 110;
                for tick in 0..60i64 {
                    let hourly = tick % 5 == 0;
                    if !hourly && !fine {
                        continue;
                    }
                    let quarter = tick % 15 == 0;
                    let inner = r * if quarter {
                        0.8
                    } else if hourly {
                        0.84
                    } else {
                        0.9
                    };
                    let width = r * if quarter {
                        0.055
                    } else if hourly {
                        0.04
                    } else {
                        0.014
                    };
                    hand(
                        &mut draw,
                        (tick * 6) as f64,
                        r * 0.93,
                        width,
                        ink(marks),
                        -inner,
                    )?;
                }
            }
        }
        let colour = self.colour_of(w, "color")?;
        let accent = self.colour_of(w, "color2")?;
        if let Some(colour) = colour {
            let hours = (hour as f64 + minute as f64 / 60.0) * 30.0;
            hand(&mut draw, hours, r * 0.5, r * 0.085, ink(colour), r * 0.08)?;
            let minutes = (minute as f64 + second.max(0) as f64 / 60.0) * 6.0;
            hand(
                &mut draw,
                minutes,
                r * 0.76,
                r * 0.055,
                ink(colour),
                r * 0.08,
            )?;
        }
        if let Some(accent) = accent {
            if second >= 0 {
                let seconds = (second * 6) as f64;
                hand(
                    &mut draw,
                    seconds,
                    r * 0.84,
                    r * 0.022,
                    ink(accent),
                    r * 0.18,
                )?;
            }
            let dot = r * 0.065;
            draw.ellipse(
                (cx - dot, cy - dot, cx + dot, cy + dot),
                Some(ink(accent)),
                None,
                1,
            )?;
        }
        let mut piece = Piece::new(shapes::down(&layer, fw, fh, SUPERSAMPLE)?, x, y);
        piece.hit = Some([x, y, fw, fh]);
        Ok(piece)
    }
}
