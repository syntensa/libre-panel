//! Turned widgets (render/renderer.py's Turn, _turned_point, _turned_box, _turned_image and
//! _turned): a widget's piece turned about a point of the panel, a quarter turn pixel for
//! pixel, any other smoothly (Pillow's bicubic rotate, all of the layer kept), with its box,
//! what blurs beneath it and its light.

use lp_pixels::{Filter, Image, Mode, Transpose};

use crate::piece::{Piece, Result};

/// How far a widget is turned, degrees clockwise, and about which point on the panel.
pub type Turn = (f64, (f64, f64));

/// round() of a float as a whole number.
fn round_int(x: f64) -> i64 {
    lp_py::round(x) as i64
}

/// Python's float `%`: the result has the divisor's sign (never -0 where it is 0).
pub fn py_mod(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r == 0.0 {
        0.0f64.copysign(m)
    } else if (m < 0.0) != (r < 0.0) {
        r + m
    } else {
        r
    }
}

/// _turned_point: `point` turned as `turn` says (y grows downwards: clockwise as seen).
pub fn point(point: (f64, f64), turn: Turn) -> (f64, f64) {
    let (angle, (cx, cy)) = turn;
    let radians = angle * (std::f64::consts::PI / 180.0); // (math.radians)
    let (cos, sin) = (radians.cos(), radians.sin());
    let (dx, dy) = (point.0 - cx, point.1 - cy);
    (cx + dx * cos - dy * sin, cy + dx * sin + dy * cos)
}

/// _turned_box: the box around `bounds` ([x, y, w, h]) turned.
pub fn bounds(bounds: [i64; 4], turn: Turn) -> [i64; 4] {
    let [x, y, w, h] = bounds.map(|v| v as f64);
    let corners = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)].map(|c| point(c, turn));
    // (a quarter turn's sines are not quite 1 and 0)
    let xs = corners.map(|c| lp_py::round_digits(c.0, 6));
    let ys = corners.map(|c| lp_py::round_digits(c.1, 6));
    let min = |v: [f64; 4]| v.into_iter().fold(f64::INFINITY, f64::min);
    let max = |v: [f64; 4]| v.into_iter().fold(f64::NEG_INFINITY, f64::max);
    let (left, top) = (min(xs).floor() as i64, min(ys).floor() as i64);
    [
        left,
        top,
        max(xs).ceil() as i64 - left,
        max(ys).ceil() as i64 - top,
    ]
}

/// _turned_image: all of `image` turned `angle` degrees clockwise: a quarter turn and its
/// multiples pixel for pixel (Pillow's turns are anticlockwise), any other smoothly.
fn image(image: &Image, angle: f64) -> Result<Image> {
    if angle == angle.trunc() {
        let quarter = match angle as i64 {
            90 => Some(Transpose::Rotate270),
            180 => Some(Transpose::Rotate180),
            270 => Some(Transpose::Rotate90),
            _ => None,
        };
        if let Some(quarter) = quarter {
            return Ok(image.transpose(quarter));
        }
    }
    Ok(image.rotate(-angle, Filter::Bicubic, true, None, None, None)?)
}

/// _turned: `piece` turned: its layer (in RGBA), its box, what blurs beneath it and its light.
pub(crate) fn turned(piece: &Piece, turn: Turn) -> Result<Piece> {
    let piece = &*piece.filled();
    let converted;
    let layer = if piece.layer.mode() == Mode::RGBA {
        &piece.layer
    } else {
        converted = piece.layer.convert(Mode::RGBA)?;
        &converted
    };
    let shown = image(layer, turn.0)?;
    let (w, h) = (layer.width() as f64, layer.height() as f64);
    let middle = point((piece.x as f64 + w / 2.0, piece.y as f64 + h / 2.0), turn);
    let was = piece.hit.unwrap_or([
        piece.x,
        piece.y,
        layer.width() as i64,
        layer.height() as i64,
    ]);
    let (backdrop, hit) = match &piece.backdrop {
        // (it lies on the box: the box goes where it now lies)
        Some(mask) => {
            let mask = image(mask, turn.0)?;
            let (mx, my) = point(
                (
                    was[0] as f64 + was[2] as f64 / 2.0,
                    was[1] as f64 + was[3] as f64 / 2.0,
                ),
                turn,
            );
            let (mw, mh) = (mask.width() as i64, mask.height() as i64);
            let hit = [
                round_int(mx - mask.width() as f64 / 2.0),
                round_int(my - mask.height() as f64 / 2.0),
                mw,
                mh,
            ];
            (Some(mask), hit)
        }
        None => (None, bounds(was, turn)),
    };
    let light = match &piece.light {
        Some(light) => Some(Box::new(turned(light, turn)?)),
        None => None,
    };
    Ok(Piece {
        x: round_int(middle.0 - shown.width() as f64 / 2.0),
        y: round_int(middle.1 - shown.height() as f64 / 2.0),
        layer: shown,
        hit: Some(hit),
        backdrop,
        backdrop_radius: piece.backdrop_radius,
        add: piece.add,
        light,
        cut: None,
        left_out: None,
        made: None,
        ring: None,
        bar: None,
        laid: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_s_float_remainder() {
        assert_eq!(py_mod(-90.0, 360.0), 270.0);
        assert_eq!(py_mod(450.0, 360.0), 90.0);
        assert!(py_mod(-360.0, 360.0).is_sign_positive());
        // (a hair below 0 is a whole turn: Python's -1e-20 % 360 is 360.0)
        assert_eq!(py_mod(-1e-20, 360.0), 360.0);
    }

    #[test]
    fn a_quarter_turn_s_box_is_whole() {
        // a 40 x 10 box turned a quarter about its middle (20, 5): 10 x 40 about it
        assert_eq!(
            bounds([0, 0, 40, 10], (90.0, (20.0, 5.0))),
            [15, -15, 10, 40]
        );
        assert_eq!(bounds([0, 0, 40, 10], (180.0, (20.0, 5.0))), [0, 0, 40, 10]);
    }
}
