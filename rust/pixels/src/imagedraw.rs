//! ImageDraw.py's own methods (`draw.line(...)`, `draw.rounded_rectangle(...)`), on top of the
//! core in draw.rs and ellipse.rs, as Pillow 13.0 writes them: the inks for fill and outline,
//! the outline left out where it is the fill's own ink, the joints of a curved line, the
//! corners of a rounded rectangle, in Python's arithmetic (its round() half to even, its
//! floor division, its remainder).

use crate::draw::{Canvas, Draw};
use crate::{error, Image, Mode, Result};

/// Python's x % m for floats (the sign of m, never -0).
fn py_mod(x: f64, m: f64) -> f64 {
    let r = x % m;
    if r == 0.0 {
        0.0f64.copysign(m)
    } else if (m < 0.0) != (r < 0.0) {
        r + m
    } else {
        r
    }
}

/// ImageDraw.line's coord_at_angle: `distance` (half the width less one) from the point at
/// the angle less 90 degrees, each step rounded toward the point.
fn at_angle(point: (f64, f64), angle: f64, width: i32) -> (f64, f64) {
    let angle = (angle - 90.0).to_radians();
    let distance = f64::from(width) / 2.0 - 1.0;
    let toward = |d: f64| if d > 0.0 { d.floor() } else { d.ceil() };
    (
        point.0 + toward(distance * angle.cos()),
        point.1 + toward(distance * angle.sin()),
    )
}

/// A corner of a rounded rectangle: the box of its ellipse, the angles it goes from and to.
type Corner = ((f64, f64, f64, f64), f32, f32);

/// The corners a rounded rectangle rounds (ImageDraw's `corners`: top left, top right,
/// bottom right, bottom left).
pub const ALL_CORNERS: [bool; 4] = [true; 4];

impl<C: Canvas> Draw<'_, C> {
    /// ImageDraw._getink: the outline's ink and the fill's; neither given, the outline in the
    /// default ink.
    fn inks(
        &self,
        outline: Option<C::Ink>,
        fill: Option<C::Ink>,
    ) -> (Option<C::Ink>, Option<C::Ink>) {
        if outline.is_none() && fill.is_none() {
            (Some(self.canvas.default_ink()), None)
        } else {
            (outline, fill)
        }
    }

    /// Whether the outline is drawn: given, not the fill's ink, and of a width.
    fn outlined(ink: Option<C::Ink>, fill: Option<C::Ink>, width: i32) -> Option<C::Ink> {
        let ink = ink?;
        let other = fill.is_none_or(|f| !C::same_ink(ink, f));
        (other && width != 0).then_some(ink)
    }

    /// ImageDraw.point.
    pub fn point(&mut self, xy: &[(f64, f64)], fill: Option<C::Ink>) -> Result<()> {
        if let (Some(ink), _) = self.inks(fill, None) {
            self.draw_points(xy, ink)?;
        }
        Ok(())
    }

    /// ImageDraw.line(xy, fill, width, joint): the segments, and with `curve` (joint="curve")
    /// and a width over 4 a slice of a disc at each bend, with small lines over the gaps from
    /// a width over 8.
    pub fn line(
        &mut self,
        xy: &[(f64, f64)],
        fill: Option<C::Ink>,
        width: i32,
        curve: bool,
    ) -> Result<()> {
        let Some(ink) = self.inks(fill, None).0 else {
            return Ok(());
        };
        if width == 0 {
            return Ok(());
        }
        self.draw_lines(xy, ink, width)?;
        if !(curve && width > 4) {
            return Ok(());
        }
        let angle = |start: (f64, f64), end: (f64, f64)| {
            py_mod((end.0 - start.0).atan2(start.1 - end.1).to_degrees(), 360.0)
        };
        for i in 1..xy.len().saturating_sub(1) {
            let point = xy[i];
            let angles = [angle(xy[i - 1], point), angle(point, xy[i + 1])];
            if angles[0] == angles[1] {
                continue; // (a straight line: no joint)
            }
            let flipped = (angles[1] > angles[0] && angles[1] - 180.0 > angles[0])
                || (angles[1] < angles[0] && angles[1] + 180.0 > angles[0]);
            let half = f64::from(width) / 2.0;
            let coords = (
                point.0 - half + 1.0,
                point.1 - half + 1.0,
                point.0 + half - 1.0,
                point.1 + half - 1.0,
            );
            let (start, end) = if flipped {
                (angles[1] + 90.0, angles[0] + 90.0)
            } else {
                (angles[0] - 90.0, angles[1] - 90.0)
            };
            self.pieslice(coords, start - 90.0, end - 90.0, fill, None, 1)?;
            if width > 8 {
                // (covering the gaps between the line and the joint)
                let turn = if flipped { 90.0 } else { -90.0 };
                let gap = [
                    at_angle(point, angles[0] + turn, width),
                    point,
                    at_angle(point, angles[1] + turn, width),
                ];
                self.line(&gap, fill, 3, false)?;
            }
        }
        Ok(())
    }

    /// ImageDraw.rectangle.
    pub fn rectangle(
        &mut self,
        xy: (f64, f64, f64, f64),
        fill: Option<C::Ink>,
        outline: Option<C::Ink>,
        width: i32,
    ) -> Result<()> {
        let (ink, fill_ink) = self.inks(outline, fill);
        if let Some(f) = fill_ink {
            self.draw_rectangle(xy, f, true, 0)?;
        }
        if let Some(ink) = Self::outlined(ink, fill_ink, width) {
            self.draw_rectangle(xy, ink, false, width)?;
        }
        Ok(())
    }

    /// ImageDraw.ellipse.
    pub fn ellipse(
        &mut self,
        xy: (f64, f64, f64, f64),
        fill: Option<C::Ink>,
        outline: Option<C::Ink>,
        width: i32,
    ) -> Result<()> {
        let (ink, fill_ink) = self.inks(outline, fill);
        if let Some(f) = fill_ink {
            self.draw_ellipse(xy, f, true, 0)?;
        }
        if let Some(ink) = Self::outlined(ink, fill_ink, width) {
            self.draw_ellipse(xy, ink, false, width)?;
        }
        Ok(())
    }

    /// ImageDraw.circle: the ellipse in the box `radius` round the middle.
    pub fn circle(
        &mut self,
        middle: (f64, f64),
        radius: f64,
        fill: Option<C::Ink>,
        outline: Option<C::Ink>,
        width: i32,
    ) -> Result<()> {
        let (x, y) = middle;
        self.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill,
            outline,
            width,
        )
    }

    /// ImageDraw.arc (its ink is `fill`).
    pub fn arc(
        &mut self,
        xy: (f64, f64, f64, f64),
        start: f64,
        end: f64,
        fill: Option<C::Ink>,
        width: i32,
    ) -> Result<()> {
        if let (Some(ink), _) = self.inks(fill, None) {
            if width != 0 {
                self.draw_arc(xy, start as f32, end as f32, ink, width)?;
            }
        }
        Ok(())
    }

    /// ImageDraw.chord.
    #[allow(clippy::too_many_arguments)]
    pub fn chord(
        &mut self,
        xy: (f64, f64, f64, f64),
        start: f64,
        end: f64,
        fill: Option<C::Ink>,
        outline: Option<C::Ink>,
        width: i32,
    ) -> Result<()> {
        let (ink, fill_ink) = self.inks(outline, fill);
        let (start, end) = (start as f32, end as f32);
        if let Some(f) = fill_ink {
            self.draw_chord(xy, start, end, f, true, 0)?;
        }
        if let Some(ink) = Self::outlined(ink, fill_ink, width) {
            self.draw_chord(xy, start, end, ink, false, width)?;
        }
        Ok(())
    }

    /// ImageDraw.pieslice.
    #[allow(clippy::too_many_arguments)]
    pub fn pieslice(
        &mut self,
        xy: (f64, f64, f64, f64),
        start: f64,
        end: f64,
        fill: Option<C::Ink>,
        outline: Option<C::Ink>,
        width: i32,
    ) -> Result<()> {
        let (ink, fill_ink) = self.inks(outline, fill);
        let (start, end) = (start as f32, end as f32);
        if let Some(f) = fill_ink {
            self.draw_pieslice(xy, start, end, f, true, 0)?;
        }
        if let Some(ink) = Self::outlined(ink, fill_ink, width) {
            self.draw_pieslice(xy, start, end, ink, false, width)?;
        }
        Ok(())
    }

    /// ImageDraw.polygon: an outline wider than 1 drawn twice as wide, inside the polygon only
    /// (a mask of its fill).
    pub fn polygon(
        &mut self,
        xy: &[(f64, f64)],
        fill: Option<C::Ink>,
        outline: Option<C::Ink>,
        width: i32,
    ) -> Result<()> {
        let (ink, fill_ink) = self.inks(outline, fill);
        if let Some(f) = fill_ink {
            self.draw_polygon(xy, f, true, 0)?;
        }
        if let Some(ink) = Self::outlined(ink, fill_ink, width) {
            if width == 1 {
                self.draw_polygon(xy, ink, false, width)?;
            } else {
                let (w, h) = self.canvas.canvas_size();
                let mut mask = Image::blank(Mode::L, w, h);
                let one = [self.canvas.mask_ink(), 0, 0, 0];
                Draw::new(&mut mask).draw_polygon(xy, one, true, 0)?;
                self.draw_polygon_in(xy, ink, false, width * 2 - 1, Some(&mask))?;
            }
        }
        Ok(())
    }

    /// ImageDraw.rounded_rectangle: quarter discs (or rings) in the corners it rounds and
    /// rectangles between them; a box too small for its corners an ellipse, corners of no
    /// size a rectangle.
    pub fn rounded_rectangle(
        &mut self,
        xy: (f64, f64, f64, f64),
        radius: f64,
        fill: Option<C::Ink>,
        outline: Option<C::Ink>,
        width: i32,
        corners: [bool; 4],
    ) -> Result<()> {
        let (x0, y0, x1, y1) = xy;
        if x1 < x0 {
            return error("x1 must be greater than or equal to x0");
        }
        if y1 < y0 {
            return error("y1 must be greater than or equal to y0");
        }
        let mut d = (x1 - x0).min(y1 - y0).min(radius * 2.0);
        let (x0, y0, x1, y1) = (
            x0.round_ties_even(),
            y0.round_ties_even(),
            x1.round_ties_even(),
            y1.round_ties_even(),
        );
        let (mut full_x, mut full_y) = (false, false);
        if corners.iter().all(|&c| c) {
            full_x = d >= x1 - x0 - 1.0;
            if full_x {
                d = x1 - x0; // (the two left and the two right corners joined)
            }
            full_y = d >= y1 - y0 - 1.0;
            if full_y {
                d = y1 - y0;
            }
            if full_x && full_y {
                return self.ellipse(xy, fill, outline, width); // (all joined: a circle)
            }
        }
        if d == 0.0 || !corners.iter().any(|&c| c) {
            return self.rectangle(xy, fill, outline, width);
        }
        let r = (d / 2.0).floor(); // int(d // 2)
        let (ink, fill_ink) = self.inks(outline, fill);
        let parts: Vec<Corner> = if full_x {
            vec![
                ((x0, y0, x0 + d, y0 + d), 180.0, 360.0),
                ((x0, y1 - d, x0 + d, y1), 0.0, 180.0),
            ]
        } else if full_y {
            vec![
                ((x0, y0, x0 + d, y0 + d), 90.0, 270.0),
                ((x1 - d, y0, x1, y0 + d), 270.0, 90.0),
            ]
        } else {
            [
                ((x0, y0, x0 + d, y0 + d), 180.0, 270.0),
                ((x1 - d, y0, x1, y0 + d), 270.0, 360.0),
                ((x1 - d, y1 - d, x1, y1), 0.0, 90.0),
                ((x0, y1 - d, x0 + d, y1), 90.0, 180.0),
            ]
            .into_iter()
            .zip(corners)
            .filter_map(|(part, on)| on.then_some(part))
            .collect()
        };
        let [c0, c1, c2, c3] = corners;
        let (inset, w) = (r + 1.0, f64::from(width));
        if let Some(f) = fill_ink {
            for &(bounds, start, end) in &parts {
                self.draw_pieslice(bounds, start, end, f, true, 0)?;
            }
            if full_x {
                self.draw_rectangle((x0, y0 + inset, x1, y1 - inset), f, true, 0)?;
            } else if x1 - inset >= x0 + inset {
                self.draw_rectangle((x0 + inset, y0, x1 - inset, y1), f, true, 0)?;
            }
            if !full_x && !full_y {
                let left = (
                    x0,
                    if c0 { y0 + inset } else { y0 },
                    x0 + r,
                    if c3 { y1 - inset } else { y1 },
                );
                self.draw_rectangle(left, f, true, 0)?;
                let right = (
                    x1 - r,
                    if c1 { y0 + inset } else { y0 },
                    x1,
                    if c2 { y1 - inset } else { y1 },
                );
                self.draw_rectangle(right, f, true, 0)?;
            }
        }
        if let Some(ink) = Self::outlined(ink, fill_ink, width) {
            for &(bounds, start, end) in &parts {
                self.draw_arc(bounds, start, end, ink, width)?;
            }
            if !full_x {
                let top = (
                    if c0 { x0 + inset } else { x0 },
                    y0,
                    if c1 { x1 - inset } else { x1 },
                    y0 + w - 1.0,
                );
                self.draw_rectangle(top, ink, true, 0)?;
                let bottom = (
                    if c3 { x0 + inset } else { x0 },
                    y1 - w + 1.0,
                    if c2 { x1 - inset } else { x1 },
                    y1,
                );
                self.draw_rectangle(bottom, ink, true, 0)?;
            }
            if !full_y {
                let left = (
                    x0,
                    if c0 { y0 + inset } else { y0 },
                    x0 + w - 1.0,
                    if c3 { y1 - inset } else { y1 },
                );
                self.draw_rectangle(left, ink, true, 0)?;
                let right = (
                    x1 - w + 1.0,
                    if c1 { y0 + inset } else { y0 },
                    x1,
                    if c2 { y1 - inset } else { y1 },
                );
                self.draw_rectangle(right, ink, true, 0)?;
            }
        }
        Ok(())
    }
}

impl Draw<'_, Image> {
    /// ImageDraw.bitmap: the ink (`fill`, or the default) through the bitmap.
    pub fn bitmap(&mut self, x: f64, y: f64, bitmap: &Image, fill: Option<[u8; 4]>) -> Result<()> {
        let ink = fill.unwrap_or_else(|| self.canvas.default_ink());
        self.draw_bitmap(x, y, bitmap, ink)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_s_remainder_takes_the_divisor_s_sign() {
        assert_eq!(py_mod(-90.0, 360.0), 270.0);
        assert_eq!(py_mod(370.0, 360.0), 10.0);
        assert!(py_mod(-0.0, 360.0).is_sign_positive());
        assert!(py_mod(-360.0, 360.0).is_sign_positive());
    }
}
