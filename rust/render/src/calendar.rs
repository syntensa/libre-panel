//! The month as a grid of days (render/renderer.py's _draw_calendar and _calendar_piece): the
//! days of the week over seven columns, named in the panel's language, the days of the month
//! below them, today on a smooth dot.

use lp_pixels::{Draw, Filter, Image, Mode};
use lp_py::datetime::{days_in_month, DateTime};
use lp_py::Dict;
use lp_text::text::{draw, Text};

use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Renderer, MOST_PIXELS};
use crate::shapes::ink;
use crate::widget;

fn failed(e: lp_text::Error) -> Failed {
    Failed(e.to_string())
}

/// A picture's side that Rust holds (Python's fails far past any panel's).
fn held(w: i64, h: i64) -> Result<(usize, usize)> {
    if w < 0 || h < 0 || w.saturating_mul(h) > MOST_PIXELS {
        return Err(Failed("a calendar too large".into()));
    }
    Ok((w as usize, h as usize))
}

impl Renderer {
    /// _calendar_piece: the month `today` is in.
    pub(crate) fn calendar_piece(&self, w: &Dict, today: &DateTime) -> Result<Piece> {
        let (width, height) = (
            widget::int(w, "w", 280).max(14),
            widget::int(w, "h", 200).max(14),
        );
        let first = DateTime::date(today.year, today.month, 1);
        // (the weekday() of the first column)
        let start: i64 = if widget::text(w, "first_day") == "monday" {
            0
        } else {
            6
        };
        let lead = (i64::from(first.weekday()) - start).rem_euclid(7);
        let days = i64::from(days_in_month(today.year, today.month));
        let weeks = (lead + days + 6).div_euclid(7);
        let (cell_w, cell_h) = (width as f64 / 7.0, height as f64 / (weeks + 1) as f64);
        let size = widget::int(w, "font_size", 16)
            .min((cell_h * 0.62) as i64)
            .min((cell_w * 0.5) as i64)
            .max(6);
        let font = self.font(widget::text(w, "font"), size)?;
        let (lw, lh) = held(width, height)?;
        let mut layer = Image::blank(Mode::RGBA, lw, lh);
        let colour = |key: &str| -> Result<[u8; 4]> {
            Ok(ink(self.colour_of(w, key)?.unwrap_or([0, 0, 0, 0])))
        };
        let (fill, mark, muted) = (colour("color")?, colour("color2")?, colour("muted")?);
        let write = |layer: &mut Image, at: (f64, f64), text: &str, ink: [u8; 4]| {
            let text = Text::new(text, font.as_ref(), "RGBA");
            draw(layer, at, &text, ink, None, Some("mm"), "left").map_err(failed)
        };
        let monday = today.toordinal() - i64::from(today.weekday());
        for column in 0..7 {
            let day = DateTime::from_ordinal(monday + (start + column) % 7)
                .ok_or_else(|| Failed("date value out of range".into()))?;
            let name: String = lp_i18n::format_date(&day, "%a")
                .map_err(|e| Failed(format!("{e:?}")))?
                .chars()
                .take(2)
                .collect();
            let at = ((column as f64 + 0.5) * cell_w, cell_h / 2.0);
            write(&mut layer, at, &name, muted)?;
        }
        let (r, g, b) = (f64::from(mark[0]), f64::from(mark[1]), f64::from(mark[2]));
        let light = 0.299 * r + 0.587 * g + 0.114 * b > 150.0;
        for number in 1..=days {
            let slot = lead + number - 1;
            let cx = ((slot % 7) as f64 + 0.5) * cell_w;
            let cy = ((slot / 7) as f64 + 1.5) * cell_h;
            let mut ink = fill;
            if number == i64::from(today.day) {
                // (a smooth dot: drawn four times larger, then reduced)
                let radius = cell_w.min(cell_h) * 0.46;
                let side = (lp_py::round(radius * 2.0) as i64).max(2);
                let (big, _) = held(side.saturating_mul(4), side.saturating_mul(4))?;
                let mut dot = Image::blank(Mode::L, big, big);
                let last = (big - 1) as f64;
                Draw::new(&mut dot).ellipse(
                    (0.0, 0.0, last, last),
                    Some([255, 0, 0, 0]),
                    None,
                    1,
                )?;
                let dot = dot.resize(side as usize, side as usize, Filter::Lanczos, None, None)?;
                let half = side as f64 / 2.0;
                let corner = (
                    lp_py::round(cx - half) as i64,
                    lp_py::round(cy - half) as i64,
                );
                let disc = Image::new(Mode::RGBA, side as usize, side as usize, &mark);
                layer.paste(&disc, corner.0, corner.1, Some(&dot))?;
                ink = if light {
                    [12, 16, 22, 255]
                } else {
                    [255, 255, 255, 255]
                };
            }
            write(&mut layer, (cx, cy), &number.to_string(), ink)?;
        }
        Ok(Piece::new(
            layer,
            widget::int(w, "x", 0),
            widget::int(w, "y", 0),
        ))
    }
}
