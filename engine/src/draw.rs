//! Pillow's ellipse of a width and its arc (ImageDraw.ellipse with an outline, ImageDraw.arc),
//! drawn on one channel as Pillow draws them (Draw.c's ellipseNew and arcNew, Pillow 8.2 on):
//! the spans of two ellipses' quarters on a grid of half pixels, an arc's caps cut by half
//! planes through its middle, to the pixel. A fan's ring and its glowing arc are drawn so,
//! three times larger and then reduced (fan.rs).

use std::collections::VecDeque;

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
        if a < 0 || b < 0 {
            return Quarter {
                cx: 0,
                cy: 0,
                ex: 0,
                ey: 0,
                a2: 0,
                b2: 0,
                a2b2: 0,
                finished: true,
            };
        }
        let (a2, b2) = (i64::from(a) * i64::from(a), i64::from(b) * i64::from(b));
        Quarter {
            cx: a,
            cy: b % 2,
            ex: a % 2,
            ey: b,
            a2,
            b2,
            a2b2: a2 * b2,
            finished: false,
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

impl Clip {
    /// Mirrored about the line y = x (clip_tree_transpose).
    fn transposed(self) -> Clip {
        match self {
            Clip::Half(a, b, c) => Clip::Half(b, a, c),
            Clip::And(l, r) => Clip::And(Box::new(l.transposed()), Box::new(r.transposed())),
            Clip::Or(l, r) => Clip::Or(Box::new(l.transposed()), Box::new(r.transposed())),
        }
    }
}

/// What is left of the span x0..=x1 of row y in `clip` (clip_tree_do_clip): its pieces' ends in
/// order, each with 1 where one begins and -1 where it ends.
fn clipped(clip: Option<&Clip>, x0: i32, y: i32, x1: i32) -> Vec<(i32, i8)> {
    match clip {
        None => vec![(x0, 1), (x1, -1)],
        Some(Clip::Half(a, b, c)) => {
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
            if x0 <= x1 {
                vec![(x0, 1), (x1, -1)]
            } else {
                Vec::new()
            }
        }
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

/// The clipping tree of an arc from `al` to `ar` (degrees, normalized) of an ellipse of semi
/// axes a >= b (arc_init): caps across the curve at both ends.
fn arc_tree(a: i32, b: i32, al: f32, ar: f32) -> Option<Clip> {
    if ar == al + 360.0 {
        return None;
    }
    let (fa, fb) = (f64::from(a), f64::from(b));
    let (l, r) = (f64::from(al), f64::from(ar));
    let pi = std::f64::consts::PI;
    let lc = Clip::Half(
        f64::from(-a) * (l * pi / 180.0).sin(),
        fb * (l * pi / 180.0).cos(),
        f64::from(a * a - b * b) * (l * pi / 90.0).sin() / 2.0,
    );
    let rc = Clip::Half(
        fa * (r * pi / 180.0).sin(),
        f64::from(-b) * (r * pi / 180.0).cos(),
        f64::from(b * b - a * a) * (r * pi / 90.0).sin() / 2.0,
    );
    let both = |l: Clip, r: Clip| {
        if ar - al < 180.0 {
            Clip::And(Box::new(l), Box::new(r))
        } else {
            Clip::Or(Box::new(l), Box::new(r))
        }
    };
    let side = |angle: f32| {
        if (angle / 180.0) as i32 % 2 == 0 {
            1.0
        } else {
            -1.0
        }
    };
    Some(if l % 180.0 == 0.0 || r % 180.0 == 0.0 {
        both(lc, rc)
    } else if ((al / 180.0) as i32 + (ar / 180.0) as i32) % 2 == 1 {
        Clip::Or(
            Box::new(Clip::And(
                Box::new(Clip::Half(0.0, side(al), 0.0)),
                Box::new(lc),
            )),
            Box::new(Clip::And(
                Box::new(Clip::Half(0.0, side(ar), 0.0)),
                Box::new(rc),
            )),
        )
    } else {
        let half = Clip::Half(
            0.0,
            if (180.0..=540.0).contains(&ar) {
                -1.0
            } else {
                1.0
            },
            0.0,
        );
        both(both(lc, rc), half)
    })
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

/// A picture of one channel, `w` x `h`, a row after the other.
pub struct Mask<'a> {
    pub pixels: &'a mut [u8],
    pub w: usize,
    pub h: usize,
}

impl Mask<'_> {
    /// Draw.c's hline8: x0 to x1 of row y (both in), cut to the picture.
    fn line(&mut self, x0: i32, y: i32, x1: i32, ink: u8) {
        if y < 0 || y as usize >= self.h || x1 < 0 || x0 >= self.w as i32 {
            return;
        }
        let (x0, x1) = (x0.max(0) as usize, (x1 as usize).min(self.w - 1));
        if x0 <= x1 {
            self.pixels[y as usize * self.w + x0..=y as usize * self.w + x1].fill(ink);
        }
    }

    /// ImageDraw.ellipse(box, outline=ink, width=width) (ImagingDrawEllipse without a fill): the
    /// box's corners as Pillow takes them, cut to whole pixels.
    pub fn ellipse(&mut self, (x0, y0, x1, y1): (i32, i32, i32, i32), width: i32, ink: u8) {
        let (a, b) = (x1 - x0, y1 - y0);
        if a < 0 || b < 0 {
            return;
        }
        let mut e = Ellipse::new(a, b, width);
        while let Some((sx0, sy, sx1)) = e.next() {
            self.line(
                x0 + (sx0 + a) / 2,
                y0 + (sy + b) / 2,
                x0 + (sx1 + a) / 2,
                ink,
            );
        }
    }

    /// ImageDraw.arc(box, start, end, fill=ink, width=width) (ImagingDrawArc): the whole ring
    /// where it goes all the way round, nothing where it has no length.
    pub fn arc(&mut self, bounds: (i32, i32, i32, i32), start: f32, end: f32, width: i32, ink: u8) {
        let (start, end) = normalized(start, end);
        if start + 360.0 == end {
            return self.ellipse(bounds, width, ink);
        }
        if start == end {
            return;
        }
        let (x0, y0, x1, y1) = bounds;
        let (a, b) = (x1 - x0, y1 - y0);
        if a < 0 || b < 0 {
            return;
        }
        let clip = arc_clip(a, b, start, end);
        let mut e = Ellipse::new(a, b, width);
        let mut ends: VecDeque<(i32, i8)> = VecDeque::new();
        let mut row = 0;
        loop {
            while ends.is_empty() {
                let Some((sx0, sy, sx1)) = e.next() else {
                    return;
                };
                ends.extend(clipped(clip.as_ref(), sx0, sy, sx1));
                row = sy;
            }
            let (Some((from, _)), Some((to, _))) = (ends.pop_front(), ends.pop_front()) else {
                return;
            };
            self.line(
                x0 + (from + a) / 2,
                y0 + (row + b) / 2,
                x0 + (to + a) / 2,
                ink,
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn drawn(w: usize, h: usize, draw: impl FnOnce(&mut Mask)) -> Vec<u8> {
        let mut pixels = vec![0u8; w * h];
        draw(&mut Mask {
            pixels: &mut pixels,
            w,
            h,
        });
        pixels
    }

    #[test]
    fn a_ring_is_round_and_its_arc_part_of_it() {
        let ring = drawn(40, 40, |m| m.ellipse((4, 4, 35, 35), 3, 255));
        let half = drawn(40, 40, |m| m.arc((4, 4, 35, 35), -90.0, 90.0, 3, 255));
        let full = drawn(40, 40, |m| m.arc((4, 4, 35, 35), -90.0, 270.0, 3, 255));
        assert_eq!(full, ring);
        // (the arc lies on the ring, on its right half: from twelve o'clock clockwise to six)
        for (i, &v) in half.iter().enumerate() {
            if v == 255 {
                assert_eq!(ring[i], 255);
                assert!(
                    i % 40 >= 19,
                    "a pixel of the right half's arc at x {}",
                    i % 40
                );
            }
        }
        assert!(drawn(40, 40, |m| m.arc((4, 4, 35, 35), 10.0, 10.0, 3, 255))
            .iter()
            .all(|&v| v == 0));
    }

    /// How many pixels are drawn, the sum of their places and of their squares (mod 1000003),
    /// as of Pillow 12.3's ImageDraw on a 40 x 40 "L" picture.
    fn signature(pixels: &[u8]) -> (usize, usize, usize) {
        let on: Vec<usize> = (0..pixels.len()).filter(|&i| pixels[i] == 255).collect();
        (
            on.len(),
            on.iter().sum(),
            on.iter().map(|i| i * i).sum::<usize>() % 1_000_003,
        )
    }

    #[test]
    fn ellipses_and_arcs_are_pillows_to_the_pixel() {
        let cases: [(
            &str,
            (i32, i32, i32, i32),
            Option<(f32, f32)>,
            i32,
            (usize, usize, usize),
        ); 6] = [
            ("ellipse", (4, 4, 35, 35), None, 3, (264, 211068, 963669)),
            (
                "half",
                (4, 4, 35, 35),
                Some((-90.0, 90.0)),
                3,
                (132, 106758, 939006),
            ),
            (
                "third",
                (4, 4, 35, 35),
                Some((-90.0, 30.0)),
                3,
                (90, 52220, 822355),
            ),
            (
                "tall",
                (8, 2, 31, 37),
                Some((10.0, 250.0)),
                4,
                (221, 214841, 994417),
            ),
            (
                "wide",
                (1, 9, 38, 30),
                Some((100.0, 170.0)),
                2,
                (31, 33593, 694811),
            ),
            // (a box of fractions: Pillow takes its whole pixels)
            (
                "odd",
                (3, 4, 36, 35),
                Some((-90.0, 211.5)),
                5,
                (362, 322131, 959612),
            ),
        ];
        for (name, bounds, arc, width, pillow) in cases {
            let pixels = drawn(40, 40, |m| match arc {
                None => m.ellipse(bounds, width, 255),
                Some((start, end)) => m.arc(bounds, start, end, width, 255),
            });
            assert_eq!(signature(&pixels), pillow, "{name}");
        }
    }

    #[test]
    fn angles_are_normalized_as_pillow_does() {
        assert_eq!(normalized(-90.0, 90.0), (270.0, 450.0));
        assert_eq!(normalized(-90.0, 270.0), (0.0, 360.0));
        assert_eq!(normalized(30.0, 20.0), (30.0, 380.0));
        assert_eq!(normalized(400.0, 410.0), (40.0, 50.0));
    }
}
