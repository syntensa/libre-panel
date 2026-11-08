//! Analog clocks (the renderer's _draw_analog and _analog_piece): the face, its rim and marks,
//! the hour and minute hands, the second hand and the dot in the middle, drawn three times as
//! large and reduced as _canvas and _down make them. In a moving frame a second hand that
//! glides or goes round (_analog_part, _second_hand, _hand_effects): the face with the other
//! hands kept, the second hand drawn in its own small box and laid onto it each frame.

use std::rc::Rc;

use lp_pixels::{Draw, Image, Mode};
use lp_py::{Dict, Value};

use crate::effects;
use crate::piece::{Failed, Piece, Result};
use crate::renderer::{Renderer, MOST_PIXELS};
use crate::roll;
use crate::shapes::{self, ink, SUPERSAMPLE};
use crate::widget;

/// What _analog_part keeps (a face) and _hand_effects (its shadow and glow) by: the widget's
/// id, the widget (json.dumps of it, its keys sorted) and the hands shown.
type FaceKey = (String, String, [i64; 3]);

/// The faces and halos the clocks keep: the newest eight, the first kept the first to go (as
/// a dict that drops its first key when it holds more).
pub(crate) struct Faces<V> {
    items: Vec<(FaceKey, V)>,
}

impl<V: Clone> Default for Faces<V> {
    fn default() -> Faces<V> {
        Faces { items: Vec::new() }
    }
}

impl<V: Clone> Faces<V> {
    fn get(&self, key: &FaceKey) -> Option<V> {
        self.items
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.clone())
    }

    fn put(&mut self, key: FaceKey, value: V) {
        self.items.push((key, value));
        while self.items.len() > 8 {
            self.items.remove(0);
        }
    }
}

/// A clock's key: its id, the widget as JSON (its keys sorted) and the hands shown.
fn face_key(w: &Dict, hands: [i64; 3]) -> FaceKey {
    let how = lp_py::json::Dumps {
        sort_keys: true,
        ..lp_py::json::Dumps::default()
    };
    let json = lp_py::json::dumps(&Value::Dict(w.clone()), &how);
    (widget::text(w, "id").to_string(), json, hands)
}

/// Python's max(a, b) of floats: the first unless the second is larger.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python's min(a, b, c) of floats: the first of the smallest.
fn py_min3(a: f64, b: f64, c: f64) -> f64 {
    let ab = if b < a { b } else { a };
    if c < ab {
        c
    } else {
        ab
    }
}

/// Python's max(a, b, c) of floats: the first of the largest.
fn py_max3(a: f64, b: f64, c: f64) -> f64 {
    py_max(py_max(a, b), c)
}

/// A hand's width as Pillow takes it: round(width), a pixel at least (Pillow refuses one past an
/// int's range as Python's C code does).
fn line_width(width: f64) -> Result<i32> {
    i32::try_from((lp_py::round(width) as i64).max(1))
        .map_err(|_| Failed("signed integer is greater than maximum".into()))
}

impl Renderer {
    /// _analog_piece(widget, hour, minute, second, part): the clock face with its hands, the
    /// second hand at `second` (none below 0); `face`: the part "face", without the second
    /// hand and the dot in the middle.
    pub(crate) fn analog_piece(
        &self,
        w: &Dict,
        hour: i64,
        minute: i64,
        second: i64,
        face: bool,
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
        if face {
            let mut piece = Piece::new(shapes::down(&layer, fw, fh, SUPERSAMPLE)?, x, y);
            piece.hit = Some([x, y, fw, fh]);
            return Ok(piece);
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

    /// _draw_analog where the second hand moves (`kind` "glide": to the second in
    /// digits_seconds, as rolling digits do; "sweep": round without stopping), in a moving
    /// frame at `now`: the face with the hour and minute hands kept (the minute hand moving on
    /// every five seconds), the second hand laid onto it; kept while it rests for the rest of
    /// the second (made here: a hand must not step back), else the frame moves on.
    pub(crate) fn analog_moving(
        &mut self,
        w: &Dict,
        now: &crate::snapshot::Moment,
        kind: &str,
    ) -> Result<Rc<Piece>> {
        let part = f64::from(now.microsecond) / 1e6;
        let seconds = widget::number(w, "digits_seconds", roll::DIGITS_S);
        let second = f64::from(now.second);
        let at = if kind == "sweep" {
            second + part
        } else if 0.0 < seconds && seconds <= 1.0 && part < seconds {
            // (as the digits roll)
            second - (1.0 - part / seconds).powf(3.0)
        } else {
            second
        };
        let shown = [
            i64::from(now.hour % 12),
            i64::from(now.minute),
            i64::from(now.second / 5 * 5),
        ];
        let content = Value::List(vec![
            Value::from("hand"),
            Value::List(shown.iter().map(|&v| Value::int(v)).collect()),
            Value::Float(at),
        ]);
        let id = widget::text(w, "id").to_string();
        if let Some((kept, piece)) = self.cache.get(&id) {
            if *kept == content {
                return Ok(piece.clone());
            }
        }
        let face = self.analog_face(w, shown)?;
        let mut layer = (*face).clone();
        let (hand, hx, hy) = self.second_hand(w, at)?;
        let (hw, hh) = (hand.width() as i64, hand.height() as i64);
        layer.alpha_composite_at(&hand, (hx, hy), (0, 0, hw, hh))?;
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let (lw, lh) = (layer.width() as i64, layer.height() as i64);
        let mut piece = Piece::new(layer, x, y);
        piece.hit = Some([x, y, lw, lh]);
        let piece = Rc::new(self.hand_effects(w, piece, &face, shown)?);
        if kind == "sweep" || at != second {
            self.moving = true;
        } else {
            self.cache.insert(id, (content, piece.clone()));
        }
        Ok(piece)
    }

    /// _analog_part: the face with its hour and minute hands at `hands` and no second hand,
    /// kept while the widget stays the same.
    fn analog_face(&mut self, w: &Dict, hands: [i64; 3]) -> Result<Rc<Image>> {
        let key = face_key(w, hands);
        if let Some(found) = self.analog_parts.get(&key) {
            return Ok(found);
        }
        let found = Rc::new(
            self.analog_piece(w, hands[0], hands[1], hands[2], true)?
                .layer,
        );
        self.analog_parts.put(key, found.clone());
        Ok(found)
    }

    /// _second_hand: the second hand at `at` seconds (a fraction too) with the dot in the
    /// middle, drawn in its own small box (where it is on the face); the box and where it is.
    fn second_hand(&self, w: &Dict, at: f64) -> Result<(Image, i64, i64)> {
        let (fw, fh) = (
            widget::int(w, "w", 160).max(8),
            widget::int(w, "h", 160).max(8),
        );
        // (Python's ints, then true division)
        let r = fw.min(fh) as f64 / 2.0;
        let (cx, cy) = (fw as f64 / 2.0, fh as f64 / 2.0);
        let a = (at * 6.0 - 90.0).to_radians();
        let tip = (cx + r * 0.84 * a.cos(), cy + r * 0.84 * a.sin());
        let tail = (cx - r * 0.18 * a.cos(), cy - r * 0.18 * a.sin());
        let (width, dot) = (r * 0.022, r * 0.065);
        let pad = py_max(width / 2.0, dot) + 2.0;
        let x0 = ((py_min3(tip.0, tail.0, cx) - pad).floor() as i64).max(0);
        let y0 = ((py_min3(tip.1, tail.1, cy) - pad).floor() as i64).max(0);
        let x1 = ((py_max3(tip.0, tail.0, cx) + pad).ceil() as i64).min(fw);
        let y1 = ((py_max3(tip.1, tail.1, cy) + pad).ceil() as i64).min(fh);
        let s = SUPERSAMPLE as i64;
        let (kw, kh) = ((x1 - x0).max(0) * s, (y1 - y0).max(0) * s);
        if kw.saturating_mul(kh) > MOST_PIXELS {
            return Err(Failed("a clock too large".into()));
        }
        let mut layer = Image::blank(Mode::RGBA, kw as usize, kh as usize);
        if let Some(accent) = self.colour_of(w, "color2")? {
            let s = SUPERSAMPLE as f64;
            let at_s = |(px, py): (f64, f64)| ((px - x0 as f64) * s, (py - y0 as f64) * s);
            let mut draw = Draw::new(&mut layer);
            let ends = [at_s(tail), at_s(tip)];
            draw.line(&ends, Some(ink(accent)), line_width(width * s)?, false)?;
            for (px, py) in ends {
                let half = width * s / 2.0;
                draw.ellipse(
                    (px - half, py - half, px + half, py + half),
                    Some(ink(accent)),
                    None,
                    1,
                )?;
            }
            let (mx, my) = at_s((cx, cy));
            let d = dot * s;
            draw.ellipse((mx - d, my - d, mx + d, my + d), Some(ink(accent)), None, 1)?;
        }
        Ok((shapes::down(&layer, x1 - x0, y1 - y0, SUPERSAMPLE)?, x0, y0))
    }

    /// _hand_effects: _effects for a face whose second hand moves (`piece`: the face with the
    /// hand on it): on a face that is not see-through the hand adds nothing to the shape its
    /// shadow and glow are of (it lies on the face), so those of `face` (with its hands
    /// `shown`) are kept as long as it is; a see-through face's are made whole.
    fn hand_effects(
        &mut self,
        w: &Dict,
        mut piece: Piece,
        face: &Image,
        shown: [i64; 3],
    ) -> Result<Piece> {
        let solid =
            widget::truthy(w, "face") && self.colour_of(w, "face")?.is_some_and(|c| c[3] == 255);
        if !solid || (!widget::truthy(w, "glow") && !widget::truthy(w, "shadow")) {
            return self.effects(w, piece);
        }
        let opacity = widget::number(w, "opacity", 1.0);
        let key = face_key(w, shown);
        let kept = match self.analog_halos.get(&key) {
            Some(kept) => kept,
            None => {
                let scaled = effects::scale_alpha(face, opacity)?;
                let tint = self.shadow_tint(w)?;
                let made = effects::halo(w, scaled.as_ref().unwrap_or(face), tint, self.dither)?;
                let kept = made.map(|(halo, pad)| (Rc::new(halo), pad));
                self.analog_halos.put(key, kept.clone());
                kept
            }
        };
        let Some((halo, pad)) = kept else {
            // (no effects after all)
            return self.effects(w, piece);
        };
        let mut out = (*halo).clone();
        let scaled = effects::scale_alpha(&piece.layer, opacity)?;
        let layer = scaled.as_ref().unwrap_or(&piece.layer);
        let (lw, lh) = (layer.width() as i64, layer.height() as i64);
        out.alpha_composite_at(layer, (pad, pad), (0, 0, lw, lh))?;
        piece.layer = out;
        piece.x -= pad;
        piece.y -= pad;
        piece.light = effects::dimmed(&piece.light, opacity)?;
        Ok(if widget::truthy(w, "clip") {
            effects::clip_sides(piece)
        } else {
            piece
        })
    }
}
