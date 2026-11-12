//! ImageDraw's ellipses and their parts in C (libImaging/Draw.c's ellipseNew, arcNew, chordNew
//! and pieNew, as Pillow draws them since 8.2): the spans between two quarter ellipses on a grid
//! of half pixels round the middle, cut by trees of half planes where only a part is drawn, each
//! pixel set to the ink.

use crate::draw::{corners, Canvas, Draw};
use crate::Result;

/// Draw.c's quarter_state: the top right quarter of an ellipse of semi-axes a and b, on the
/// points (2x + x0, 2y + y0) of the grid where (a, b) lies (x0, y0 each 0 or 1).
struct Quarter {
    cx: i32,
    cy: i32,
    ex: i32,
    ey: i32,
    a2: i64,
    b2: i64,
    a2b2: i64,
    finished: bool,
}

impl Quarter {
    fn new(a: i32, b: i32) -> Quarter {
        let (a2, b2) = (i64::from(a) * i64::from(a), i64::from(b) * i64::from(b));
        Quarter {
            cx: a,
            cy: b % 2,
            ex: a % 2,
            ey: b,
            a2,
            b2,
            a2b2: a2 * b2,
            finished: a < 0 || b < 0,
        }
    }

    /// How far a point lies off the curve (quarter_delta).
    fn delta(&self, x: i32, y: i32) -> i64 {
        let (x, y) = (i64::from(x), i64::from(y));
        (self.a2 * y * y + self.b2 * x * x - self.a2b2).abs()
    }

    /// The point it is at, and on to the next (quarter_next, Bresenham's way); None once done.
    fn next(&mut self) -> Option<(i32, i32)> {
        if self.finished {
            return None;
        }
        let at = (self.cx, self.cy);
        if self.cx == self.ex && self.cy == self.ey {
            self.finished = true;
        } else {
            let (mut nx, mut ny) = (self.cx, self.cy + 2);
            let mut ndelta = self.delta(nx, ny);
            if nx > 1 {
                let newdelta = self.delta(self.cx - 2, self.cy + 2);
                if ndelta > newdelta {
                    nx = self.cx - 2;
                    ny = self.cy + 2;
                    ndelta = newdelta;
                }
                let newdelta = self.delta(self.cx - 2, self.cy);
                if ndelta > newdelta {
                    nx = self.cx - 2;
                    ny = self.cy;
                }
            }
            self.cx = nx;
            self.cy = ny;
        }
        Some(at)
    }
}

/// Draw.c's ellipse_state: the spans (x0, y, x1, on the grid of half pixels round the middle)
/// of an ellipse `w` wide, between its outer quarter and the inner one, all four quarters.
struct Ellipse {
    outer: Quarter,
    inner: Quarter,
    py: i32,
    pl: i32,
    pr: i32,
    spans: Vec<(i32, i32, i32)>,
    finished: bool,
    leftmost: i32,
}

impl Ellipse {
    fn new(a: i32, b: i32, w: i32) -> Ellipse {
        let mut e = Ellipse {
            outer: Quarter::new(a, b),
            inner: Quarter::new(-1, -1),
            py: 0,
            pl: 0,
            pr: 0,
            spans: Vec::with_capacity(4),
            finished: true,
            leftmost: a % 2,
        };
        if w >= 1 {
            if let Some((x, y)) = e.outer.next() {
                e.pr = x;
                e.py = y;
                e.finished = false;
                e.inner = Quarter::new(a - 2 * (w - 1), b - 2 * (w - 1));
                e.pl = e.leftmost;
            }
        }
        e
    }

    /// The next span (ellipse_next): four at a time from each step down, given out last first.
    fn next(&mut self) -> Option<(i32, i32, i32)> {
        if self.spans.is_empty() {
            if self.finished {
                return None;
            }
            let (y, mut l, r) = (self.py, self.pl, self.pr);
            // (the outer quarter on to the first point below this row)
            let mut below = None;
            while let Some((cx, cy)) = self.outer.next() {
                if cy > y {
                    below = Some((cx, cy));
                    break;
                }
            }
            match below {
                None => self.finished = true,
                Some((cx, cy)) => {
                    self.pr = cx;
                    self.py = cy;
                }
            }
            // (and the inner one: the last point on this row is where the span begins)
            let mut next_left = None;
            while let Some((cx, cy)) = self.inner.next() {
                if cy > y {
                    next_left = Some(cx);
                    break;
                }
                l = cx;
            }
            self.pl = next_left.unwrap_or(self.leftmost);
            if (l > 0 || l < r) && y > 0 {
                self.spans.push((if l == 0 { 2 } else { l }, y, r));
            }
            if y > 0 {
                self.spans.push((-r, y, -l));
            }
            if l > 0 || l < r {
                self.spans.push((if l == 0 { 2 } else { l }, -y, r));
            }
            self.spans.push((-r, -y, -l));
        }
        self.spans.pop()
    }
}

/// Draw.c's clipping tree: half planes (a x + b y + c > 0) joined by and and or.
enum Clip {
    Half(f64, f64, f64),
    And(Box<Clip>, Box<Clip>),
    Or(Box<Clip>, Box<Clip>),
}

fn and(l: Clip, r: Clip) -> Clip {
    Clip::And(Box::new(l), Box::new(r))
}

fn or(l: Clip, r: Clip) -> Clip {
    Clip::Or(Box::new(l), Box::new(r))
}

impl Clip {
    /// Mirrored about the line y = x (clip_tree_transpose).
    fn transposed(self) -> Clip {
        match self {
            Clip::Half(a, b, c) => Clip::Half(b, a, c),
            Clip::And(l, r) => and(l.transposed(), r.transposed()),
            Clip::Or(l, r) => or(l.transposed(), r.transposed()),
        }
    }
}

/// What a half plane leaves of the span x0..=x1 of row y: its ends, None for nothing.
fn half_span((a, b, c): (f64, f64, f64), x0: i32, y: i32, x1: i32) -> Option<(i32, i32)> {
    let eps = 1e-9;
    let (mut x0, mut x1) = (x0, x1);
    let y = f64::from(y);
    if a.abs() < eps {
        if b * y + c < -eps {
            x0 = 1;
            x1 = 0;
        }
    } else {
        // where it crosses the row
        let ix = -(b * y + c) / a;
        if a * f64::from(x0) + b * y + c < eps {
            x0 = f64::from(x0).max(ix).round() as i32;
        }
        if a * f64::from(x1) + b * y + c < eps {
            x1 = f64::from(x1).min(ix).round() as i32;
        }
    }
    (x0 <= x1).then_some((x0, x1))
}

/// What is left of the span x0..=x1 of row y in `clip` (clip_tree_do_clip): its pieces' ends in
/// order, each with 1 where one begins and -1 where it ends.
fn clipped(clip: Option<&Clip>, x0: i32, y: i32, x1: i32) -> Vec<(i32, i8)> {
    match clip {
        None => vec![(x0, 1), (x1, -1)],
        Some(&Clip::Half(a, b, c)) => match half_span((a, b, c), x0, y, x1) {
            Some((x0, x1)) => vec![(x0, 1), (x1, -1)],
            None => Vec::new(),
        },
        Some(Clip::And(l, r)) => joined(
            true,
            &clipped(Some(l), x0, y, x1),
            &clipped(Some(r), x0, y, x1),
        ),
        Some(Clip::Or(l, r)) => joined(
            false,
            &clipped(Some(l), x0, y, x1),
            &clipped(Some(r), x0, y, x1),
        ),
    }
}

/// What `clipped` gives, held in place: at most `MOST` ends (a tree of more half planes than
/// half that can give more: None then, and `clipped` is asked).
const MOST: usize = 32;

#[derive(Clone, Copy)]
struct Ends {
    len: usize,
    at: [(i32, i8); MOST],
}

impl Ends {
    fn new() -> Ends {
        Ends {
            len: 0,
            at: [(0, 0); MOST],
        }
    }

    fn of(pairs: &[(i32, i8)]) -> Ends {
        let mut ends = Ends::new();
        ends.at[..pairs.len()].copy_from_slice(pairs);
        ends.len = pairs.len();
        ends
    }

    fn as_slice(&self) -> &[(i32, i8)] {
        &self.at[..self.len]
    }
}

/// clipped, without a list made for each node: None where more ends come of it than fit.
fn clipped_in_place(clip: Option<&Clip>, x0: i32, y: i32, x1: i32) -> Option<Ends> {
    match clip {
        None => Some(Ends::of(&[(x0, 1), (x1, -1)])),
        Some(&Clip::Half(a, b, c)) => Some(match half_span((a, b, c), x0, y, x1) {
            Some((x0, x1)) => Ends::of(&[(x0, 1), (x1, -1)]),
            None => Ends::new(),
        }),
        Some(Clip::And(l, r)) => joined_in_place(
            true,
            clipped_in_place(Some(l), x0, y, x1)?.as_slice(),
            clipped_in_place(Some(r), x0, y, x1)?.as_slice(),
        ),
        Some(Clip::Or(l, r)) => joined_in_place(
            false,
            clipped_in_place(Some(l), x0, y, x1)?.as_slice(),
            clipped_in_place(Some(r), x0, y, x1)?.as_slice(),
        ),
    }
}

/// joined, held in place (None where its ends do not fit).
fn joined_in_place(and: bool, l1: &[(i32, i8)], l2: &[(i32, i8)]) -> Option<Ends> {
    if l1.len() + l2.len() > MOST {
        return None;
    }
    let mut out = Ends::new();
    let (mut i, mut j) = (0, 0);
    let (mut k1, mut k2) = (0i32, 0i32);
    while i < l1.len() || j < l2.len() {
        let first = j >= l2.len()
            || (i < l1.len() && (l1[i].0 < l2[j].0 || (l1[i].0 == l2[j].0 && l1[i].1 > l2[j].1)));
        let t = if first {
            let t = l1[i];
            k1 += i32::from(t.1);
            i += 1;
            t
        } else {
            let t = l2[j];
            k2 += i32::from(t.1);
            j += 1;
            t
        };
        let tail = out.as_slice().last().map(|e| e.1);
        let opens = t.1 == 1 && tail.is_none_or(|kind| kind == -1);
        let keep = if and {
            (opens && k1 > 0 && k2 > 0) || (t.1 == -1 && tail == Some(1) && (k1 == 0 || k2 == 0))
        } else {
            opens || (t.1 == -1 && k1 == 0 && k2 == 0)
        };
        if keep {
            out.at[out.len] = t;
            out.len += 1;
        }
    }
    Some(out)
}

/// Two lists of ends joined as Draw.c's combining nodes join them (and: where both are, or:
/// where either is), taken in turn by where they lie, a beginning before an end.
fn joined(and: bool, l1: &[(i32, i8)], l2: &[(i32, i8)]) -> Vec<(i32, i8)> {
    let (mut i, mut j) = (0, 0);
    let (mut k1, mut k2) = (0i32, 0i32);
    let mut out: Vec<(i32, i8)> = Vec::with_capacity(l1.len() + l2.len());
    while i < l1.len() || j < l2.len() {
        let first = j >= l2.len()
            || (i < l1.len() && (l1[i].0 < l2[j].0 || (l1[i].0 == l2[j].0 && l1[i].1 > l2[j].1)));
        let t = if first {
            let t = l1[i];
            k1 += i32::from(t.1);
            i += 1;
            t
        } else {
            let t = l2[j];
            k2 += i32::from(t.1);
            j += 1;
            t
        };
        let tail = out.last().map(|e| e.1);
        let opens = t.1 == 1 && tail.is_none_or(|kind| kind == -1);
        let keep = if and {
            (opens && k1 > 0 && k2 > 0) || (t.1 == -1 && tail == Some(1) && (k1 == 0 || k2 == 0))
        } else {
            opens || (t.1 == -1 && k1 == 0 && k2 == 0)
        };
        if keep {
            out.push(t);
        }
    }
    out
}

/// Draw.c's normalize_angles, in its mix of single and double precision: 0 <= al < 360 and
/// al <= ar <= al + 360.
fn normalized(al: f32, ar: f32) -> (f32, f32) {
    if ar - al >= 360.0 {
        return (0.0, 360.0);
    }
    let start = if al < 0.0 {
        360.0 - f64::from(-al) % 360.0
    } else {
        f64::from(al)
    };
    let al = (start % 360.0) as f32;
    let turn = if ar < al {
        360.0 - f64::from(al - ar) % 360.0
    } else {
        f64::from(ar - al)
    };
    (al, (f64::from(al) + turn % 360.0) as f32)
}

/// An angle in degrees as Draw.c turns it into radians (the float made a double first).
fn radians(angle: f32, per_half_turn: f64) -> f64 {
    f64::from(angle) * std::f64::consts::PI / per_half_turn
}

/// The clipping tree of an arc from `al` to `ar` (degrees, normalized) of an ellipse of semi
/// axes a >= b (arc_init): caps across the curve at both ends.
fn arc_tree(a: i32, b: i32, al: f32, ar: f32) -> Option<Clip> {
    if ar == al + 360.0 {
        return None;
    }
    let lc = Clip::Half(
        f64::from(-a) * radians(al, 180.0).sin(),
        f64::from(b) * radians(al, 180.0).cos(),
        f64::from(a * a - b * b) * radians(al, 90.0).sin() / 2.0,
    );
    let rc = Clip::Half(
        f64::from(a) * radians(ar, 180.0).sin(),
        f64::from(-b) * radians(ar, 180.0).cos(),
        f64::from(b * b - a * a) * radians(ar, 90.0).sin() / 2.0,
    );
    let both = |l: Clip, r: Clip| {
        if ar - al < 180.0 {
            and(l, r)
        } else {
            or(l, r)
        }
    };
    let side = |angle: f32| {
        if (angle / 180.0) as i32 % 2 == 0 {
            1.0
        } else {
            -1.0
        }
    };
    Some(
        if f64::from(al) % 180.0 == 0.0 || f64::from(ar) % 180.0 == 0.0 {
            both(lc, rc)
        } else if ((al / 180.0) as i32 + (ar / 180.0) as i32) % 2 == 1 {
            or(
                and(Clip::Half(0.0, side(al), 0.0), lc),
                and(Clip::Half(0.0, side(ar), 0.0), rc),
            )
        } else {
            #[allow(clippy::manual_range_contains)] // (as Draw.c has it, for any angle)
            let below = if ar < 180.0 || ar > 540.0 { 1.0 } else { -1.0 };
            both(both(lc, rc), Clip::Half(0.0, below, 0.0))
        },
    )
}

/// The clipping tree of an arc of an ellipse of semi axes a and b, either way round (arc_init:
/// a tall one as a wide one, mirrored).
fn arc_clip(a: i32, b: i32, al: f32, ar: f32) -> Option<Clip> {
    if a < b {
        let (al, ar) = normalized(90.0 - ar, 90.0 - al);
        return arc_tree(b, a, al, ar).map(Clip::transposed);
    }
    let (al, ar) = normalized(al, ar);
    arc_tree(a, b, al, ar)
}

/// The points of the ellipse at the angles al and ar (xl, yl, xr, yr), as chord_init and
/// pie_init compute them.
fn ends(a: i32, b: i32, al: f32, ar: f32) -> (f64, f64, f64, f64) {
    let (l, r) = (radians(al, 180.0), radians(ar, 180.0));
    (
        f64::from(a) * l.cos(),
        f64::from(b) * l.sin(),
        f64::from(a) * r.cos(),
        f64::from(b) * r.sin(),
    )
}

/// chord_init's half plane: the side of the line through both ends away from the middle.
fn chord_tree(a: i32, b: i32, al: f32, ar: f32) -> Clip {
    let (xl, yl, xr, yr) = ends(a, b, al, ar);
    let (ca, cb) = (yr - yl, xl - xr);
    Clip::Half(ca, cb, -(ca * xl + cb * yl))
}

/// chord_line_init's strip: from the line through both ends 2w (half pixels) toward the middle.
fn chord_line_tree(a: i32, b: i32, w: i32, al: f32, ar: f32) -> Clip {
    let (xl, yl, xr, yr) = ends(a, b, al, ar);
    let (ca, cb) = (yr - yl, xl - xr);
    let cc = -(ca * xl + cb * yl);
    and(
        Clip::Half(ca, cb, cc),
        Clip::Half(-ca, -cb, f64::from(2 * w) * (ca * ca + cb * cb).sqrt() - cc),
    )
}

/// pie_side_init's band: w (half pixels) on either side of the line from the middle to the
/// angle's point, on that point's side of the middle.
fn pie_side_tree(a: i32, b: i32, w: i32, al: f32) -> Clip {
    let l = radians(al, 180.0);
    let (xl, yl) = (f64::from(a) * l.cos(), f64::from(b) * l.sin());
    let (a1, b1) = (-yl, xl);
    let c1 = f64::from(w) * (a1 * a1 + b1 * b1).sqrt();
    and(
        and(Clip::Half(a1, b1, c1), Clip::Half(-a1, -b1, c1)),
        Clip::Half(b1, -a1, 0.0),
    )
}

/// pie_init's tree: between the lines from the middle to both ends, and for a slice narrower
/// than a quarter one more half plane against the spike beyond the middle.
fn pie_tree(a: i32, b: i32, al: f32, ar: f32) -> Clip {
    let (xl, yl, xr, yr) = ends(a, b, al, ar);
    let (lc, rc) = (Clip::Half(-yl, xl, 0.0), Clip::Half(yr, -xr, 0.0));
    let root = if ar - al < 180.0 {
        and(lc, rc)
    } else {
        or(lc, rc)
    };
    if ar - al < 90.0 {
        and(root, Clip::Half((xl + xr) / 2.0, (yl + yr) / 2.0, 0.0))
    } else {
        root
    }
}

impl<C: Canvas> Draw<'_, C> {
    /// ellipseNew: the ellipse in the box, a ring `width` wide or (`fill`) all of it.
    fn ellipse_new(
        &mut self,
        (x0, y0, x1, y1): (i32, i32, i32, i32),
        ink: C::Ink,
        fill: bool,
        width: i32,
    ) {
        let (a, b) = (x1 - x0, y1 - y0);
        if a < 0 || b < 0 {
            return;
        }
        let mut e = Ellipse::new(a, b, if fill { a + b } else { width });
        while let Some((sx0, sy, sx1)) = e.next() {
            self.hline(
                x0 + (sx0 + a) / 2,
                y0 + (sy + b) / 2,
                x0 + (sx1 + a) / 2,
                ink,
                None,
            );
        }
    }

    /// clipEllipseNew: the spans of the ellipse that `init` makes of the semi axes, what its
    /// clipping tree leaves of them.
    fn clip_ellipse_new(
        &mut self,
        (x0, y0, x1, y1): (i32, i32, i32, i32),
        ink: C::Ink,
        init: impl FnOnce(i32, i32) -> (Ellipse, Option<Clip>),
    ) {
        let (a, b) = (x1 - x0, y1 - y0);
        if a < 0 || b < 0 {
            return;
        }
        let (mut e, clip) = init(a, b);
        let (w, h) = self.canvas.canvas_size();
        let (w, h) = (w as i32, h as i32);
        while let Some((sx0, sy, sx1)) = e.next() {
            // (a span off the picture leaves nothing, whatever the clipping makes of it: the
            // spans it leaves lie within it)
            let y = y0 + (sy + b) / 2;
            if y < 0 || y >= h || x0 + (sx1 + a) / 2 < 0 || x0 + (sx0 + a) / 2 >= w {
                continue;
            }
            let kept = clipped_in_place(clip.as_ref(), sx0, sy, sx1);
            let listed;
            let spans = match &kept {
                Some(ends) => ends.as_slice(),
                None => {
                    listed = clipped(clip.as_ref(), sx0, sy, sx1);
                    &listed[..]
                }
            };
            for span in spans.chunks_exact(2) {
                self.hline(
                    x0 + (span[0].0 + a) / 2,
                    y0 + (sy + b) / 2,
                    x0 + (span[1].0 + a) / 2,
                    ink,
                    None,
                );
            }
        }
    }

    /// draw_ellipse: the ellipse in the box, filled or a ring `width` wide.
    pub fn draw_ellipse(
        &mut self,
        bounds: (f64, f64, f64, f64),
        ink: C::Ink,
        fill: bool,
        width: i32,
    ) -> Result<()> {
        let corners = corners(bounds)?;
        self.ellipse_new(corners, ink, fill, width);
        Ok(())
    }

    /// draw_arc: the ring `width` wide from `start` to `end` (degrees, clockwise from three
    /// o'clock), all of it where they are a turn apart.
    pub fn draw_arc(
        &mut self,
        bounds: (f64, f64, f64, f64),
        start: f32,
        end: f32,
        ink: C::Ink,
        width: i32,
    ) -> Result<()> {
        let corners = corners(bounds)?;
        let (start, end) = normalized(start, end);
        if start + 360.0 == end {
            self.ellipse_new(corners, ink, false, width);
        } else if start != end {
            self.clip_ellipse_new(corners, ink, |a, b| {
                (Ellipse::new(a, b, width), arc_clip(a, b, start, end))
            });
        }
        Ok(())
    }

    /// draw_chord: the part of the ellipse beyond the line between the angles' points, filled,
    /// or its outline `width` wide (the arc's ring and a strip along the line).
    pub fn draw_chord(
        &mut self,
        bounds: (f64, f64, f64, f64),
        start: f32,
        end: f32,
        ink: C::Ink,
        fill: bool,
        width: i32,
    ) -> Result<()> {
        let corners = corners(bounds)?;
        let (start, end) = normalized(start, end);
        if start + 360.0 == end {
            self.ellipse_new(corners, ink, fill, width);
        } else if start != end {
            if fill {
                self.clip_ellipse_new(corners, ink, |a, b| {
                    (
                        Ellipse::new(a, b, a + b + 1),
                        Some(chord_tree(a, b, start, end)),
                    )
                });
            } else {
                self.clip_ellipse_new(corners, ink, |a, b| {
                    let tree = chord_line_tree(a, b, width, start, end);
                    (Ellipse::new(a, b, a + b + 1), Some(tree))
                });
                self.clip_ellipse_new(corners, ink, |a, b| {
                    (
                        Ellipse::new(a, b, width),
                        Some(chord_tree(a, b, start, end)),
                    )
                });
            }
        }
        Ok(())
    }

    /// draw_pieslice: the slice of the ellipse from `start` to `end`, filled, or its outline
    /// `width` wide (both sides, a dot `width` across in the middle, the ring between).
    pub fn draw_pieslice(
        &mut self,
        bounds: (f64, f64, f64, f64),
        start: f32,
        end: f32,
        ink: C::Ink,
        fill: bool,
        width: i32,
    ) -> Result<()> {
        let corners = corners(bounds)?;
        let (start, end) = normalized(start, end);
        if start + 360.0 == end {
            self.ellipse_new(corners, ink, fill, width);
        } else if start != end {
            if fill {
                self.clip_ellipse_new(corners, ink, |a, b| {
                    (Ellipse::new(a, b, a + b), Some(pie_tree(a, b, start, end)))
                });
            } else {
                for side in [start, end] {
                    self.clip_ellipse_new(corners, ink, |a, b| {
                        let tree = pie_side_tree(a, b, width, side);
                        (Ellipse::new(a, b, a + b + 1), Some(tree))
                    });
                }
                let (x0, y0, x1, y1) = corners;
                let xc = (f64::from(x0 + x1 - width) / 2.0).round() as i32;
                let yc = (f64::from(y0 + y1 - width) / 2.0).round() as i32;
                self.ellipse_new((xc, yc, xc + width - 1, yc + width - 1), ink, true, 0);
                self.clip_ellipse_new(corners, ink, |a, b| {
                    (Ellipse::new(a, b, width), Some(pie_tree(a, b, start, end)))
                });
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn angles_are_normalized_as_pillow_does() {
        assert_eq!(normalized(-90.0, 90.0), (270.0, 450.0));
        assert_eq!(normalized(-90.0, 270.0), (0.0, 360.0));
        assert_eq!(normalized(30.0, 20.0), (30.0, 380.0));
        assert_eq!(normalized(400.0, 410.0), (40.0, 50.0));
    }
}
