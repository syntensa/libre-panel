//! ImageDraw's drawing in C (libImaging/Draw.c and _imaging.c's draw_* methods), as
//! ImageDraw.Draw(image) draws it: without a blend mode, each pixel set to the ink (Libre Panel
//! draws no other way). Coordinates are truncated as _imaging.c casts them.

use crate::{error, Image, Result};

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

impl Image {
    /// point8 / point32: the pixel set where it lies on the image.
    #[inline(always)]
    fn put(&mut self, x: i32, y: i32, ink: &[u8]) {
        if x >= 0 && y >= 0 && (x as usize) < self.width && (y as usize) < self.height {
            let b = self.bands();
            let at = (y as usize * self.width + x as usize) * b;
            self.data[at..at + b].copy_from_slice(ink);
        }
    }

    /// hline8 / hline32: x0 to x1 (both) on row y, clipped.
    pub(crate) fn hline(&mut self, x0: i32, y: i32, x1: i32, ink: &[u8]) {
        if y < 0 || y as usize >= self.height {
            return;
        }
        let w = self.width as i32;
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
        for x in x0..=x1 {
            self.put(x, y, ink);
        }
    }

    /// line8 / line32: Bresenham's line from (x0, y0) toward (x1, y1), its last point not drawn.
    fn thin_line(&mut self, mut x0: i32, mut y0: i32, x1: i32, y1: i32, ink: &[u8]) {
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

    /// polygon_generic without a mask, its pixels set (Draw.c: crossings in floats, sorted,
    /// spans from ROUND_UP of one to ROUND_DOWN of the next, horizontal edges drawn whole).
    fn polygon_edges(&mut self, edges: &[Edge], ink: &[u8]) {
        if edges.is_empty() {
            return;
        }
        let mut ymin = self.height as i32 - 1;
        let mut ymax = 0;
        let mut table: Vec<&Edge> = Vec::with_capacity(edges.len());
        for e in edges {
            ymin = ymin.min(e.ymin);
            ymax = ymax.max(e.ymax);
            if e.ymin == e.ymax {
                self.hline(e.xmin, e.ymin, e.xmax, ink);
                continue;
            }
            table.push(e);
        }
        ymin = ymin.max(0);
        ymax = ymax.min(self.height as i32);
        let mut xx: Vec<f32> = Vec::with_capacity(table.len() * 2);
        let at = |e: &Edge, y: i32| (y - e.y0) as f32 * e.dx + e.x0 as f32;
        for y in ymin..=ymax {
            xx.clear();
            for (i, current) in table.iter().enumerate() {
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
                    for other in &table[..i] {
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
                self.hline(round_up_f(xx[i - 1]), y, round_down_f(xx[i]), ink);
                i += 2;
            }
        }
    }

    /// ImagingDrawWideLine: a line ``width`` wide as the polygon of its four corners.
    fn wide_line(&mut self, x0: i32, y0: i32, x1: i32, y1: i32, ink: &[u8], width: i32) {
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
        self.polygon_edges(&edges, ink);
    }

    pub(crate) fn check_ink(&self, ink: &[u8]) -> Result<()> {
        if ink.len() != self.bands() {
            return error("an ink of another number of bands");
        }
        Ok(())
    }

    /// draw_points: each point (truncated) set.
    pub fn draw_points(&mut self, xy: &[(f64, f64)], ink: &[u8]) -> Result<()> {
        self.check_ink(ink)?;
        for &(x, y) in xy {
            self.put(x as i32, y as i32, ink);
        }
        Ok(())
    }

    /// draw_lines: the segments between the points, thin (and the last point) for a width of
    /// 1 or less, else each as a wide line.
    pub fn draw_lines(&mut self, xy: &[(f64, f64)], ink: &[u8], width: i32) -> Result<()> {
        self.check_ink(ink)?;
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
                self.wide_line(s[0].0, s[0].1, s[1].0, s[1].1, ink, width);
            }
        }
        Ok(())
    }

    /// draw_rectangle: filled, or its outline ``width`` wide inside the box.
    pub fn draw_rectangle(
        &mut self,
        bounds: (f64, f64, f64, f64),
        ink: &[u8],
        fill: bool,
        width: i32,
    ) -> Result<()> {
        self.check_ink(ink)?;
        let (x0, mut y0, x1, mut y1) = corners(bounds)?;
        if y0 > y1 {
            std::mem::swap(&mut y0, &mut y1);
        }
        let h = self.height as i32;
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
                self.hline(x0, y, x1, ink);
            }
        } else {
            let width = if width == 0 { 1 } else { width };
            for i in 0..width {
                self.hline(x0, y0 + i, x1, ink);
                self.hline(x0, y1 - i, x1, ink);
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
        ink: &[u8],
        fill: bool,
        width: i32,
    ) -> Result<()> {
        self.check_ink(ink)?;
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
            self.polygon_edges(&edges, ink);
        } else if width == 1 {
            for s in p.windows(2) {
                self.thin_line(s[0].0, s[0].1, s[1].0, s[1].1, ink);
            }
            let last = p[count - 1];
            self.thin_line(last.0, last.1, p[0].0, p[0].1, ink);
        } else {
            for s in p.windows(2) {
                self.wide_line(s[0].0, s[0].1, s[1].0, s[1].1, ink, width);
            }
            let last = p[count - 1];
            self.wide_line(last.0, last.1, p[0].0, p[0].1, ink, width);
        }
        Ok(())
    }

    /// draw_bitmap: ``ink`` through ``bitmap`` (an L mask) with its corner at (x, y)
    /// (ImagingFill2).
    pub fn draw_bitmap(&mut self, x: f64, y: f64, bitmap: &Image, ink: &[u8]) -> Result<()> {
        let (x, y) = (i64::from(x as i32), i64::from(y as i32));
        let bounds = (x, y, x + bitmap.width as i64, y + bitmap.height as i64);
        self.paste_colour(ink, bounds, Some(bitmap))
    }
}
