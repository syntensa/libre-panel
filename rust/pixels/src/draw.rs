//! ImageDraw's drawing in C (libImaging/Draw.c and _imaging.c's draw_* methods), as
//! ImageDraw.Draw(image) draws it: without a blend mode, each pixel set to the ink (Libre Panel
//! draws no other way), on pictures of bytes and of floats alike. Coordinates are truncated as
//! _imaging.c casts them. ImageDraw.py's methods on top of these are in imagedraw.rs, the
//! ellipses in ellipse.rs.

use crate::{error, FloatImage, Image, Mode, Result};

/// A picture ImageDraw draws on, and its ink: bytes (L, RGB, RGBA, RGBa) with the four bytes
/// Pillow keeps for an ink (getink: one value and three 0s for L, the alpha 255 where a colour
/// has none), or floats (F) with a float.
pub trait Canvas {
    type Ink: Copy;
    /// Its width and height.
    fn canvas_size(&self) -> (usize, usize);
    /// The pixels x0..=x1 of row y set to the ink (all of them on the picture).
    fn fill_span(&mut self, x0: usize, x1: usize, y: usize, ink: Self::Ink);
    /// ImageDraw's ink where it is given none (self.ink): white, black on L, 1 on F.
    fn default_ink(&self) -> Self::Ink;
    /// Whether ImageDraw takes two inks for the same (it compares Pillow's INT32s of them).
    fn same_ink(a: Self::Ink, b: Self::Ink) -> bool;
    /// The byte a mask of ImageDraw.polygon's is drawn with: the first of the ink it makes of
    /// 1 on this canvas (1, and 0 on F: the lowest byte of 1.0).
    fn mask_ink(&self) -> u8;
}

impl Canvas for Image {
    type Ink = [u8; 4];

    fn canvas_size(&self) -> (usize, usize) {
        (self.width, self.height)
    }

    fn fill_span(&mut self, x0: usize, x1: usize, y: usize, ink: [u8; 4]) {
        let b = self.bands();
        let row = self.row_mut(y);
        if b == 1 {
            row[x0..=x1].fill(ink[0]);
        } else {
            for px in row[x0 * b..(x1 + 1) * b].chunks_exact_mut(b) {
                px.copy_from_slice(&ink[..b]);
            }
        }
    }

    fn default_ink(&self) -> [u8; 4] {
        if self.mode == Mode::L {
            [0; 4]
        } else {
            [255; 4]
        }
    }

    fn same_ink(a: [u8; 4], b: [u8; 4]) -> bool {
        a == b
    }

    fn mask_ink(&self) -> u8 {
        1
    }
}

/// A picture drawn on through another of its mode and size: wherever a drawing sets a pixel,
/// the other's pixel there. As drawing the same in 255 on a mask (L, 0 everywhere) and
/// pasting the other picture through it (Paste.c's BLEND takes a mask of 255 as the other's
/// pixel, one of 0 as its own), without the mask: the ink is the mask's, and 255.
pub struct Through<'a> {
    out: &'a mut Image,
    from: &'a Image,
}

impl<'a> Through<'a> {
    pub fn new(out: &'a mut Image, from: &'a Image) -> Result<Self> {
        if out.mode != from.mode || out.size() != from.size() {
            return error("images do not match");
        }
        Ok(Through { out, from })
    }
}

impl Canvas for Through<'_> {
    type Ink = [u8; 4];

    fn canvas_size(&self) -> (usize, usize) {
        (self.out.width, self.out.height)
    }

    fn fill_span(&mut self, x0: usize, x1: usize, y: usize, _ink: [u8; 4]) {
        debug_assert_eq!(
            _ink[0], 255,
            "a mask's 255 alone is the other picture's pixel"
        );
        let b = self.out.bands();
        let (from, to) = (x0 * b, (x1 + 1) * b);
        self.out.row_mut(y)[from..to].copy_from_slice(&self.from.row(y)[from..to]);
    }

    fn default_ink(&self) -> [u8; 4] {
        [0; 4]
    }

    fn same_ink(a: [u8; 4], b: [u8; 4]) -> bool {
        a == b
    }

    fn mask_ink(&self) -> u8 {
        1
    }
}

impl Canvas for FloatImage {
    type Ink = f32;

    fn canvas_size(&self) -> (usize, usize) {
        (self.width, self.height)
    }

    fn fill_span(&mut self, x0: usize, x1: usize, y: usize, ink: f32) {
        let at = y * self.width;
        self.data[at + x0..=at + x1].fill(ink);
    }

    fn default_ink(&self) -> f32 {
        1.0
    }

    fn same_ink(a: f32, b: f32) -> bool {
        a.to_bits() == b.to_bits()
    }

    fn mask_ink(&self) -> u8 {
        1.0f32.to_le_bytes()[0]
    }
}

impl Mode {
    /// The ink ImageDraw makes of a colour on a picture of this mode (getink): one value on L,
    /// three or four on the others (the alpha 255 where it is left out), as the four bytes
    /// Pillow keeps.
    pub fn ink(self, colour: &[u8]) -> Result<[u8; 4]> {
        match (self.bands(), colour) {
            (1, &[v]) => Ok([v, 0, 0, 0]),
            (b, &[r, g, bl]) if b > 1 => Ok([r, g, bl, 255]),
            (b, &[r, g, bl, a]) if b > 1 => Ok([r, g, bl, a]),
            _ => error("color must be int or tuple"),
        }
    }
}

/// Draw.c's ROUND_UP and ROUND_DOWN on a float (the polygon's crossings): halves away from 0,
/// and toward it.
#[inline(always)]
fn round_up_f(f: f32) -> i32 {
    if f >= 0.0 {
        (f + 0.5).floor() as i32
    } else {
        -((f.abs() + 0.5).floor() as i32)
    }
}

#[inline(always)]
fn round_down_f(f: f32) -> i32 {
    if f >= 0.0 {
        (f - 0.5).ceil() as i32
    } else {
        -((f.abs() - 0.5).ceil() as i32)
    }
}

/// The same on a double (the wide line's corners).
#[inline(always)]
fn round_up_d(f: f64) -> i32 {
    if f >= 0.0 {
        (f + 0.5).floor() as i32
    } else {
        -((f.abs() + 0.5).floor() as i32)
    }
}

#[inline(always)]
fn round_down_d(f: f64) -> i32 {
    if f >= 0.0 {
        (f - 0.5).ceil() as i32
    } else {
        -((f.abs() - 0.5).ceil() as i32)
    }
}

/// A box as _imaging.c takes it: refused where it is the wrong way round, its corners cast to
/// whole numbers (toward 0).
pub(crate) fn corners(bounds: (f64, f64, f64, f64)) -> Result<(i32, i32, i32, i32)> {
    if bounds.2 < bounds.0 {
        return error("x1 must be greater than or equal to x0");
    }
    if bounds.3 < bounds.1 {
        return error("y1 must be greater than or equal to y0");
    }
    Ok((
        bounds.0 as i32,
        bounds.1 as i32,
        bounds.2 as i32,
        bounds.3 as i32,
    ))
}

/// A polygon of more edges than this keeps those each row crosses; a smaller one's rows look
/// at all of them.
const MANY_EDGES: usize = 16;

/// polygon_edges' lists, kept from one polygon to the next on a thread (a wide line is a
/// polygon for each of its segments).
#[derive(Default)]
struct Scratch {
    table: Vec<Edge>,
    xx: Vec<f32>,
    coming: Vec<usize>,
    active: Vec<usize>,
    crossed: Vec<Edge>,
}

thread_local! {
    static SCRATCH: std::cell::RefCell<Scratch> = std::cell::RefCell::new(Scratch::default());
}

/// Draw.c's Edge.
#[derive(Clone, Copy, Debug)]
struct Edge {
    x0: i32,
    y0: i32,
    xmin: i32,
    xmax: i32,
    ymin: i32,
    ymax: i32,
    dx: f32,
}

fn edge(x0: i32, y0: i32, x1: i32, y1: i32) -> Edge {
    Edge {
        x0,
        y0,
        xmin: x0.min(x1),
        xmax: x0.max(x1),
        ymin: y0.min(y1),
        ymax: y0.max(y1),
        dx: if y0 == y1 {
            0.0
        } else {
            (x1 - x0) as f32 / (y1 - y0) as f32
        },
    }
}

/// Where a drawing may set pixels (Draw.c's mask: an L picture of the canvas's size, its
/// pixels that are not 0), or anywhere.
pub(crate) type Mask<'m> = Option<&'m Image>;

/// Drawing on a canvas as ImageDraw.Draw(canvas) draws: here its core, Draw.c's draw_*
/// methods (`draw.draw.draw_lines(...)`); ImageDraw.py's own (`draw.line(...)`) in
/// imagedraw.rs.
pub struct Draw<'a, C: Canvas> {
    pub(crate) canvas: &'a mut C,
}

impl<'a, C: Canvas> Draw<'a, C> {
    pub fn new(canvas: &'a mut C) -> Self {
        Draw { canvas }
    }

    /// point8 / point32: the pixel set where it lies on the picture.
    #[inline(always)]
    pub(crate) fn put(&mut self, x: i32, y: i32, ink: C::Ink) {
        let (w, h) = self.canvas.canvas_size();
        if x >= 0 && y >= 0 && (x as usize) < w && (y as usize) < h {
            self.canvas
                .fill_span(x as usize, x as usize, y as usize, ink);
        }
    }

    /// hline8 / hline32: x0 to x1 (both) on row y, clipped; with a mask only where it is set.
    pub(crate) fn hline(&mut self, x0: i32, y: i32, x1: i32, ink: C::Ink, mask: Mask) {
        let (w, h) = self.canvas.canvas_size();
        if y < 0 || y as usize >= h {
            return;
        }
        let w = w as i32;
        let x0 = if x0 < 0 {
            0
        } else if x0 >= w {
            return;
        } else {
            x0
        };
        if x1 < 0 {
            return;
        }
        let x1 = x1.min(w - 1);
        if x0 > x1 {
            return;
        }
        let (x0, x1, y) = (x0 as usize, x1 as usize, y as usize);
        match mask {
            None => self.canvas.fill_span(x0, x1, y, ink),
            Some(m) => {
                let row = m.row(y);
                let mut x = x0;
                while x <= x1 {
                    if row[x] == 0 {
                        x += 1;
                        continue;
                    }
                    let from = x;
                    while x <= x1 && row[x] != 0 {
                        x += 1;
                    }
                    self.canvas.fill_span(from, x - 1, y, ink);
                }
            }
        }
    }

    /// line8 / line32: Bresenham's line from (x0, y0) toward (x1, y1), its last point not drawn.
    fn thin_line(&mut self, mut x0: i32, mut y0: i32, x1: i32, y1: i32, ink: C::Ink) {
        let (mut dx, xs) = if x1 - x0 < 0 {
            (x0 - x1, -1)
        } else {
            (x1 - x0, 1)
        };
        let (mut dy, ys) = if y1 - y0 < 0 {
            (y0 - y1, -1)
        } else {
            (y1 - y0, 1)
        };
        if dx == 0 {
            for _ in 0..dy {
                self.put(x0, y0, ink);
                y0 += ys;
            }
        } else if dy == 0 {
            for _ in 0..dx {
                self.put(x0, y0, ink);
                x0 += xs;
            }
        } else if dx > dy {
            let n = dx;
            dy += dy;
            let mut e = dy - dx;
            dx += dx;
            for _ in 0..n {
                self.put(x0, y0, ink);
                if e >= 0 {
                    y0 += ys;
                    e -= dx;
                }
                e += dy;
                x0 += xs;
            }
        } else {
            let n = dy;
            dx += dx;
            let mut e = dx - dy;
            dy += dy;
            for _ in 0..n {
                self.put(x0, y0, ink);
                if e >= 0 {
                    x0 += xs;
                    e -= dy;
                }
                e += dx;
                y0 += ys;
            }
        }
    }

    /// polygon_generic, its pixels set (Draw.c: crossings in floats, sorted, spans from
    /// ROUND_UP of one to ROUND_DOWN of the next, horizontal edges drawn whole).
    fn polygon_edges(&mut self, edges: &[Edge], ink: C::Ink, mask: Mask) {
        if edges.is_empty() {
            return;
        }
        SCRATCH.with(|scratch| {
            let mut scratch = scratch.borrow_mut();
            self.polygon_in(edges, ink, mask, &mut scratch);
        });
    }

    fn polygon_in(&mut self, edges: &[Edge], ink: C::Ink, mask: Mask, lists: &mut Scratch) {
        let Scratch {
            table,
            xx,
            coming,
            active,
            crossed,
        } = lists;
        let height = self.canvas.canvas_size().1 as i32;
        let mut ymin = height - 1;
        let mut ymax = 0;
        table.clear();
        for e in edges {
            ymin = ymin.min(e.ymin);
            ymax = ymax.max(e.ymax);
            if e.ymin == e.ymax {
                self.hline(e.xmin, e.ymin, e.xmax, ink, mask);
                continue;
            }
            table.push(*e);
        }
        ymin = ymin.max(0);
        ymax = ymax.min(height);
        let at = |e: &Edge, y: i32| (y - e.y0) as f32 * e.dx + e.x0 as f32;
        // Draw.c looks at every edge on every row; only those the row crosses count (an edge
        // the row is above or below of is passed over, and so in the corners' look back). Of
        // a polygon of many edges (a graph's filled area: one a point) the rows keep the edges
        // they cross, in the table's order, coming in by where they begin and going once they
        // end; a small one looks at all.
        let many = table.len() > MANY_EDGES;
        if many {
            coming.clear();
            coming.extend(0..table.len());
            coming.sort_by_key(|&i| table[i].ymin);
            active.clear();
        }
        let mut next = 0;
        for y in ymin..=ymax {
            xx.clear();
            let row: &[Edge] = if many {
                active.retain(|&i| table[i].ymax >= y);
                while next < coming.len() && table[coming[next]].ymin <= y {
                    let i = coming[next];
                    next += 1;
                    if table[i].ymax >= y {
                        let place = active.partition_point(|&k| k < i);
                        active.insert(place, i);
                    }
                }
                crossed.clear();
                crossed.extend(active.iter().map(|&i| table[i]));
                crossed
            } else {
                table
            };
            for (i, current) in row.iter().enumerate() {
                if y < current.ymin || y > current.ymax {
                    continue;
                }
                xx.push(at(current, y));
                if y == current.ymax && y < ymax {
                    // (Draw.c: needed to draw consistent polygons)
                    let last = *xx.last().unwrap();
                    xx.push(last);
                } else if (y == current.ymin || y == current.ymax) && current.dx != 0.0 {
                    // connect discontiguous corners
                    for other in &row[..i] {
                        if (y != other.ymin && y != other.ymax) || other.dx == 0.0 {
                            continue;
                        }
                        let j = xx.len() - 1;
                        if xx[j].round() == at(other, y).round() {
                            let offset = if y == current.ymax { -1 } else { 1 };
                            let adjacent = at(current, y + offset);
                            if y + offset >= other.ymin && y + offset <= other.ymax {
                                let adjacent_other = at(other, y + offset);
                                if xx[j] > adjacent + 1.0 && xx[j] > adjacent_other + 1.0 {
                                    xx[j] = adjacent.max(adjacent_other).round() + 1.0;
                                } else if xx[j] < adjacent - 1.0 && xx[j] < adjacent_other - 1.0 {
                                    xx[j] = adjacent.min(adjacent_other).round() - 1.0;
                                }
                                break;
                            }
                        }
                    }
                }
            }
            xx.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            let mut i = 1;
            while i < xx.len() {
                self.hline(round_up_f(xx[i - 1]), y, round_down_f(xx[i]), ink, mask);
                i += 2;
            }
        }
    }

    /// ImagingDrawWideLine: a line `width` wide as the polygon of its four corners (a line of
    /// no length a point, whatever the mask).
    #[allow(clippy::too_many_arguments)]
    fn wide_line(
        &mut self,
        x0: i32,
        y0: i32,
        x1: i32,
        y1: i32,
        ink: C::Ink,
        width: i32,
        mask: Mask,
    ) {
        let (dx, dy) = (x1 - x0, y1 - y0);
        if dx == 0 && dy == 0 {
            self.put(x0, y0, ink);
            return;
        }
        let big = f64::from(dx).hypot(f64::from(dy));
        let small = f64::from(width - 1) / 2.0;
        let ratio_max = f64::from(round_up_d(small)) / big;
        let ratio_min = f64::from(round_down_d(small)) / big;
        let dxmin = round_down_d(ratio_min * f64::from(dy));
        let dxmax = round_down_d(ratio_max * f64::from(dy));
        let dymin = round_down_d(ratio_min * f64::from(dx));
        let dymax = round_down_d(ratio_max * f64::from(dx));
        let v = [
            (x0 - dxmin, y0 + dymax),
            (x1 - dxmin, y1 + dymax),
            (x1 + dxmax, y1 - dymin),
            (x0 + dxmax, y0 - dymin),
        ];
        let edges = [
            edge(v[0].0, v[0].1, v[1].0, v[1].1),
            edge(v[1].0, v[1].1, v[2].0, v[2].1),
            edge(v[2].0, v[2].1, v[3].0, v[3].1),
            edge(v[3].0, v[3].1, v[0].0, v[0].1),
        ];
        self.polygon_edges(&edges, ink, mask);
    }

    /// draw_points: each point (truncated) set.
    pub fn draw_points(&mut self, xy: &[(f64, f64)], ink: C::Ink) -> Result<()> {
        for &(x, y) in xy {
            self.put(x as i32, y as i32, ink);
        }
        Ok(())
    }

    /// draw_lines: the segments between the points, thin (and the last point) for a width of
    /// 1 or less, else each as a wide line.
    pub fn draw_lines(&mut self, xy: &[(f64, f64)], ink: C::Ink, width: i32) -> Result<()> {
        let p: Vec<(i32, i32)> = xy.iter().map(|&(x, y)| (x as i32, y as i32)).collect();
        if width <= 1 {
            for s in p.windows(2) {
                self.thin_line(s[0].0, s[0].1, s[1].0, s[1].1, ink);
            }
            if p.len() >= 2 {
                let last = p[p.len() - 1];
                self.put(last.0, last.1, ink);
            }
        } else {
            for s in p.windows(2) {
                self.wide_line(s[0].0, s[0].1, s[1].0, s[1].1, ink, width, None);
            }
        }
        Ok(())
    }

    /// draw_rectangle: filled, or its outline `width` wide inside the box.
    pub fn draw_rectangle(
        &mut self,
        bounds: (f64, f64, f64, f64),
        ink: C::Ink,
        fill: bool,
        width: i32,
    ) -> Result<()> {
        let (x0, mut y0, x1, mut y1) = corners(bounds)?;
        if y0 > y1 {
            std::mem::swap(&mut y0, &mut y1);
        }
        let h = self.canvas.canvas_size().1 as i32;
        if fill {
            if y0 < 0 {
                y0 = 0;
            } else if y0 >= h {
                return Ok(());
            }
            if y1 < 0 {
                return Ok(());
            } else if y1 > h {
                y1 = h;
            }
            for y in y0..=y1 {
                self.hline(x0, y, x1, ink, None);
            }
        } else {
            let width = if width == 0 { 1 } else { width };
            for i in 0..width {
                self.hline(x0, y0 + i, x1, ink, None);
                self.hline(x0, y1 - i, x1, ink, None);
                self.thin_line(x1 - i, y0 + width, x1 - i, y1 - width + 1, ink);
                self.thin_line(x0 + i, y0 + width, x0 + i, y1 - width + 1, ink);
            }
        }
        Ok(())
    }

    /// draw_polygon: filled (Draw.c's edges, a horizontal edge that goes on another merged), or
    /// its outline, thin or of wide lines.
    pub fn draw_polygon(
        &mut self,
        xy: &[(f64, f64)],
        ink: C::Ink,
        fill: bool,
        width: i32,
    ) -> Result<()> {
        self.draw_polygon_in(xy, ink, fill, width, None)
    }

    /// draw_polygon with a mask (ImageDraw.polygon's outline: wide lines only inside).
    pub(crate) fn draw_polygon_in(
        &mut self,
        xy: &[(f64, f64)],
        ink: C::Ink,
        fill: bool,
        width: i32,
        mask: Mask,
    ) -> Result<()> {
        if xy.len() < 2 {
            return error("coordinate list must contain at least 2 coordinates");
        }
        let p: Vec<(i32, i32)> = xy.iter().map(|&(x, y)| (x as i32, y as i32)).collect();
        let count = p.len();
        if fill {
            let mut edges: Vec<Edge> = Vec::with_capacity(count);
            let mut i = 0;
            while i + 1 < count {
                let ((x0, y0), (x1, y1)) = (p[i], p[i + 1]);
                if y0 == y1 && i != 0 && y0 == p[i - 1].1 {
                    // a horizontal line straight after another one
                    let before = p[i - 1].0;
                    let n = edges.len();
                    if x1 > x0 && x0 > before {
                        edges[n - 1].xmax = x1;
                        i += 1;
                        continue;
                    } else if x1 < x0 && x0 < before {
                        edges[n - 1].xmin = x1;
                        i += 1;
                        continue;
                    }
                }
                edges.push(edge(x0, y0, x1, y1));
                i += 1;
            }
            if p[i] != p[0] {
                edges.push(edge(p[i].0, p[i].1, p[0].0, p[0].1));
            }
            self.polygon_edges(&edges, ink, mask);
        } else if width == 1 {
            for s in p.windows(2) {
                self.thin_line(s[0].0, s[0].1, s[1].0, s[1].1, ink);
            }
            let last = p[count - 1];
            self.thin_line(last.0, last.1, p[0].0, p[0].1, ink);
        } else {
            for s in p.windows(2) {
                self.wide_line(s[0].0, s[0].1, s[1].0, s[1].1, ink, width, mask);
            }
            let last = p[count - 1];
            self.wide_line(last.0, last.1, p[0].0, p[0].1, ink, width, mask);
        }
        Ok(())
    }
}

impl Draw<'_, Image> {
    /// draw_bitmap: the ink through `bitmap` (an L mask) with its corner at (x, y)
    /// (ImagingFill2).
    pub fn draw_bitmap(&mut self, x: f64, y: f64, bitmap: &Image, ink: [u8; 4]) -> Result<()> {
        let (x, y) = (i64::from(x as i32), i64::from(y as i32));
        let bounds = (x, y, x + bitmap.width as i64, y + bitmap.height as i64);
        let bands = self.canvas.bands();
        self.canvas
            .paste_colour(&ink[..bands], bounds, Some(bitmap))
    }
}
