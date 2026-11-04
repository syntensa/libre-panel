//! A lit gauge's ring of one colour, made by the engine for each of its values (stage 2 of
//! docs/ROADMAP.md, element by element): render/lit.py's gauge_piece as _light_ring makes it.
//! What does not change with the value comes once (a [`Base`]): the groove and the groove with
//! the whole sweep lit, both RGBA on the ring's canvas. For a value the engine cuts the lit
//! sweep at the arc's tip and makes the light around the tip, and around the start of a closed
//! ring, anew from the arc's coverage (lit.py's _cover and light), laid on the groove as Pillow
//! lays it (alpha_composite). The coverage and the light are worked out in single precision
//! as there, the blur by its three box passes (dither._rows; the renderer multiplies by their
//! matrices, the same to a thousandth of a level): the same picture to a level.

/// The core runs this far towards white (lit.py's LIGHT_WHITE) ...
const LIGHT_WHITE: f32 = 0.75;
/// ... and the halo stands this strongly beside it (LIGHT_HALO).
const LIGHT_HALO: f32 = 1.45;

pub type Rect = (usize, usize, usize, usize);

/// What a ring's pictures are made of, for every value.
pub struct Base {
    side: usize,
    w: f64,
    thickness: f64,
    start: f64,
    end: f64,
    stroke: f64,
    halo: f64,
    colour: [f32; 3],
    /// The colour's alpha, as a share (lit.py's fade).
    fade: f64,
    groove: Vec<u8>,
    lit: Vec<u8>,
    /// Of each pixel of the canvas: how far its middle is from the ring's (px), and at what
    /// angle from its start, clockwise (0 to 2 pi): lit.py's _field.
    far: Vec<f32>,
    angle: Vec<f32>,
    /// The pixels where the lit sweep is not the groove: all a value's cut can change.
    sweep: Vec<u32>,
    /// The light at the start of a closed ring, the same for every value whose tip is apart.
    start_patch: Option<(Rect, Vec<u8>)>,
    /// What its blur works in, from one picture to the next.
    scratch: Scratch,
}

/// A ring's geometry and colour, as lit.py has them.
pub struct Geometry {
    pub w: f64,
    pub thickness: f64,
    pub start: f64,
    pub end: f64,
    pub stroke: u32,
    pub halo: f64,
    pub colour: [u8; 4],
}

impl Base {
    /// A ring of `side` x `side` pixels (the gauge and its room), its groove and lit sweep RGBA.
    pub fn new(side: usize, g: &Geometry, groove: Vec<u8>, lit: Vec<u8>) -> Result<Base, String> {
        if side == 0 || side > 4096 || groove.len() != side * side * 4 || lit.len() != groove.len()
        {
            return Err("a ring's pictures are not of its canvas".into());
        }
        if !(g.w > g.thickness && g.end > g.start && g.halo > 0.0 && g.stroke > 0) {
            return Err("a ring of no shape known".into());
        }
        // the field: middles at k + 0.5 - side / 2, in single precision (exact: halves)
        let at: Vec<f32> = (0..side)
            .map(|k| k as f32 + (0.5 - side as f64 / 2.0) as f32)
            .collect();
        let start_rad = g.start.to_radians() as f32;
        let turn = (2.0 * std::f64::consts::PI) as f32;
        let sweep = (0..side * side)
            .filter(|i| groove[i * 4..i * 4 + 4] != lit[i * 4..i * 4 + 4])
            .map(|i| i as u32)
            .collect();
        let mut far = Vec::with_capacity(side * side);
        let mut angle = Vec::with_capacity(side * side);
        for &dy in &at {
            for &dx in &at {
                far.push((dx * dx + dy * dy).sqrt());
                angle.push(remainder(dy.atan2(dx) - start_rad, turn));
            }
        }
        Ok(Base {
            side,
            w: g.w,
            thickness: g.thickness,
            start: g.start,
            end: g.end,
            stroke: f64::from(g.stroke),
            halo: g.halo,
            colour: [
                f32::from(g.colour[0]),
                f32::from(g.colour[1]),
                f32::from(g.colour[2]),
            ],
            fade: f64::from(g.colour[3]) / 255.0,
            groove,
            lit,
            far,
            angle,
            sweep,
            start_patch: None,
            scratch: Scratch::default(),
        })
    }

    pub fn side(&self) -> usize {
        self.side
    }

    /// The ring's picture at `frac` of its sweep (RGBA, its canvas).
    pub fn picture(&mut self, frac: f64) -> Vec<u8> {
        let side = self.side;
        let mut out = self.groove.clone();
        if frac <= 0.0 {
            return out;
        }
        let (start, end) = (self.start, self.end);
        let tip = start + (end - start) * frac;
        let gap = 360.0 - (end - start).abs();
        // the lit sweep up to the tip (lit.py's sector, from the middle of the gap): where its
        // edges run, the light is made anew in the windows below, or there is none
        let reach_deg = (tip - start) as f32;
        let back = (360.0 - gap / 2.0) as f32;
        for &i in &self.sweep {
            let i = i as usize;
            let degrees = self.angle[i].to_degrees();
            if degrees <= reach_deg || degrees >= back {
                out[i * 4..i * 4 + 4].copy_from_slice(&self.lit[i * 4..i * 4 + 4]);
            }
        }
        let radius = (self.w - self.thickness) / 2.0;
        let reach = (self.stroke / 2.0 + 3.0 * self.halo).ceil() + 3.0;
        let tip_window = self.window(tip, reach);
        let mut scratch = std::mem::take(&mut self.scratch);
        let mut patches = Vec::new();
        if gap * std::f64::consts::PI / 180.0 * radius < 2.0 * reach + 2.0 {
            // closed: the sweep's light has no start; its start the same at every value but
            // those whose tip is near it
            let (inner, outer) = self.window(start, reach);
            let t = tip_window.1;
            let apart = t.2 <= outer.0 || outer.2 <= t.0 || t.3 <= outer.1 || outer.3 <= t.1;
            if apart {
                if self.start_patch.is_none() {
                    let patch = self.patch(frac, inner, outer, &mut scratch);
                    self.start_patch = Some((inner, patch));
                }
                patches.push(self.start_patch.clone().expect("made above"));
            } else {
                patches.push((inner, self.patch(frac, inner, outer, &mut scratch)));
            }
        }
        patches.push((
            tip_window.0,
            self.patch(frac, tip_window.0, tip_window.1, &mut scratch),
        ));
        self.scratch = scratch;
        for ((x0, y0, x1, y1), patch) in patches {
            let w = x1 - x0;
            for y in y0..y1 {
                let at = (y * side + x0) * 4;
                let row = &mut out[at..at + w * 4];
                row.copy_from_slice(&self.groove[at..at + w * 4]);
                let light = &patch[(y - y0) * w * 4..(y - y0 + 1) * w * 4];
                for (d, s) in row.chunks_exact_mut(4).zip(light.chunks_exact(4)) {
                    let mut s = [s[0], s[1], s[2], s[3]];
                    s[3] = self.faded(s[3]);
                    alpha_composite(d, &s);
                }
            }
        }
        out
    }

    /// The colour's alpha on the light's (lit.py's _alpha_scaled: rounded half to even).
    fn faded(&self, a: u8) -> u8 {
        if self.fade >= 0.999 {
            a
        } else {
            (f64::from(a) * self.fade).round_ties_even().min(255.0) as u8
        }
    }

    /// The window around the point of the ring at `angle` (degrees) where the light is made
    /// anew (inner), and the coverage it is made of around it (outer): lit.py's window().
    fn window(&self, angle: f64, reach: f64) -> (Rect, Rect) {
        let side = self.side as f64;
        let margin = (3.0 * self.halo).ceil() + 4.0;
        let middle = (self.w - self.thickness) / 2.0;
        let x = side / 2.0 + middle * angle.to_radians().cos();
        let y = side / 2.0 + middle * angle.to_radians().sin();
        let clamp = |v: f64| v.clamp(0.0, side) as usize;
        let inner = (
            clamp((x - reach).floor()),
            clamp((y - reach).floor()),
            clamp((x + reach).ceil()),
            clamp((y + reach).ceil()),
        );
        let outer = (
            clamp(inner.0 as f64 - margin),
            clamp(inner.1 as f64 - margin),
            clamp(inner.2 as f64 + margin),
            clamp(inner.3 as f64 + margin),
        );
        (inner, outer)
    }

    /// The light of the arc of `frac` in the `inner` box, worked out from its coverage in
    /// `outer` (lit.py's patch(): light(_cover(...), colour, halo, box)): RGBA, row by row.
    fn patch(&self, frac: f64, inner: Rect, outer: Rect, scratch: &mut Scratch) -> Vec<u8> {
        let (ox0, oy0, ox1, oy1) = outer;
        let (ow, oh) = (ox1 - ox0, oy1 - oy0);
        let mask = self.cover(frac, outer);
        let cut: Vec<f32> = mask.iter().map(|m| (m * 255.0) as u8 as f32).collect();
        let soft = blur_in(&cut, ow, oh, self.halo, scratch);
        let scale = (1.0f64 / 255.0) as f32;
        soft.iter_mut().for_each(|v| *v *= scale);
        let (x0, y0, x1, y1) = inner;
        let mut out = Vec::with_capacity((x1 - x0) * (y1 - y0) * 4);
        let c = self.colour;
        for y in y0..y1 {
            for x in x0..x1 {
                let i = (y - oy0) * ow + (x - ox0);
                let (m, s) = (mask[i], soft[i]);
                // (0 and 1 as they are: powf's, to the bit)
                let core = if m == 0.0 || m == 1.0 { m } else { m.powf(0.7) };
                let white = LIGHT_WHITE * core;
                for c in c {
                    out.push(level(c + (255.0 - c) * white));
                }
                let halo = (s - m).clamp(0.0, 1.0);
                out.push(level((core + LIGHT_HALO * halo).clamp(0.0, 1.0) * 255.0));
            }
        }
        out
    }

    /// How much of each pixel of `box` the arc of `frac` covers (lit.py's _cover).
    fn cover(&self, frac: f64, (x0, y0, x1, y1): Rect) -> Vec<f32> {
        let middle = ((self.w - self.thickness) / 2.0) as f32;
        let half = (self.stroke / 2.0 + 0.5) as f32;
        let sweep64 = ((self.end - self.start) * frac).to_radians();
        let full = sweep64 >= 2.0 * std::f64::consts::PI;
        let sweep = sweep64 as f32;
        let turn = (2.0 * std::f64::consts::PI) as f32;
        let mut out = Vec::with_capacity((x1 - x0) * (y1 - y0));
        for y in y0..y1 {
            for x in x0..x1 {
                let i = y * self.side + x;
                let (far, angle) = (self.far[i], self.angle[i]);
                let cover = (half - (far - middle).abs()).clamp(0.0, 1.0);
                if full {
                    out.push(cover);
                    continue;
                }
                let ends = if angle <= sweep {
                    angle.min(sweep - angle)
                } else {
                    -(angle - sweep).min(turn - angle)
                };
                out.push(cover * (ends * far + 0.5).clamp(0.0, 1.0));
            }
        }
        out
    }
}

/// numpy's remainder of single precision numbers: the sign of the divisor.
fn remainder(a: f32, b: f32) -> f32 {
    let m = a % b;
    if m != 0.0 && ((b < 0.0) != (m < 0.0)) {
        m + b
    } else {
        m
    }
}

/// A light's level as numpy makes it of single precision: clipped after a half, cut to a byte.
fn level(v: f32) -> u8 {
    (v + 0.5).clamp(0.0, 255.0) as u8
}

/// The box passes of dither._box_radius: `side` whole pixels each way and a fraction of the
/// next, three of them make the Gaussian of `radius` (Gwosdek et al., as Pillow).
fn box_radius(radius: f64) -> (usize, f64) {
    let sigma2 = radius * radius / 3.0;
    let big = (12.0 * sigma2 + 1.0).sqrt();
    let side = ((big - 1.0) / 2.0).floor();
    let a = (2.0 * side + 1.0) * (side * (side + 1.0) - 3.0 * sigma2)
        / (6.0 * (sigma2 - (side + 1.0) * (side + 1.0)));
    (side as usize, a)
}

/// dither.blur of `a` (`w` x `h`, single precision): three box passes along the rows, then
/// three down the columns, the edges held. Each pass runs down the lines a row at a time, all
/// lines at once (the rows' passes on the picture turned): every line summed in the same order
/// as dither._rows sums it, the same numbers.
#[cfg(test)]
fn blur(a: &[f32], w: usize, h: usize, radius: f64) -> Vec<f32> {
    let mut scratch = Scratch::default();
    blur_in(a, w, h, radius, &mut scratch).to_vec()
}

/// What a blur works in, kept from one to the next: its buffers grow to the largest picture
/// blurred, and are not zeroed again (every number is written before it is read).
#[derive(Default)]
struct Scratch {
    a: Vec<f32>,
    b: Vec<f32>,
    sums: Vec<f32>,
}

/// [`blur`] in `scratch`'s buffers: the blurred picture, `w` x `h`.
fn blur_in<'a>(
    a: &[f32],
    w: usize,
    h: usize,
    radius: f64,
    scratch: &'a mut Scratch,
) -> &'a mut [f32] {
    let (side, frac) = box_radius(radius);
    let Scratch {
        a: one,
        b: other,
        sums,
    } = scratch;
    transpose_into(a, w, h, one); // (h x w: its rows are the picture's columns ...)
    for _ in 0..3 {
        down_into(one, h, w, side, frac, sums, other); // (... so these are the passes along its rows)
        std::mem::swap(one, other);
    }
    transpose_into(one, h, w, other);
    std::mem::swap(one, other);
    for _ in 0..3 {
        down_into(one, w, h, side, frac, sums, other);
        std::mem::swap(one, other);
    }
    &mut one[..w * h]
}

#[cfg(test)]
fn transpose(a: &[f32], w: usize, h: usize) -> Vec<f32> {
    let mut out = Vec::new();
    transpose_into(a, w, h, &mut out);
    out
}

fn transpose_into(a: &[f32], w: usize, h: usize, out: &mut Vec<f32>) {
    out.resize(w * h, 0.0);
    for y in 0..h {
        for x in 0..w {
            out[x * h + y] = a[y * w + x];
        }
    }
}

/// One pass down each column of `a` (`w` wide, `h` high), as dither._rows makes one along a
/// row: the mean over `side` pixels each way and `frac` of the next, from running sums in
/// single precision, the ends held.
#[cfg(test)]
fn down(a: &[f32], w: usize, h: usize, side: usize, frac: f64) -> Vec<f32> {
    let (mut sums, mut out) = (Vec::new(), Vec::new());
    down_into(a, w, h, side, frac, &mut sums, &mut out);
    out
}

/// [`down`] into `out` (`w` x `h`), its running sums in `c`.
fn down_into(
    a: &[f32],
    w: usize,
    h: usize,
    side: usize,
    frac: f64,
    c: &mut Vec<f32>,
    out: &mut Vec<f32>,
) {
    let n = h + 2 * side + 2; // the column with its ends held, as p in dither._rows
    let at = |k: usize| {
        let y = k.saturating_sub(side + 1).min(h - 1);
        &a[y * w..(y + 1) * w]
    };
    // c[k]: the sum of the first k of the held column (c[0] = 0), row by row
    c.resize((n + 1) * w, 0.0);
    c[..w].fill(0.0);
    for k in 0..n {
        let (before, after) = c.split_at_mut((k + 1) * w);
        let (row, sum) = (&before[k * w..], &mut after[..w]);
        for ((s, r), v) in sum.iter_mut().zip(row).zip(at(k)) {
            *s = r + v;
        }
    }
    let frac32 = frac as f32;
    let scale = (1.0 / (2.0 * side as f64 + 1.0 + 2.0 * frac)) as f32;
    out.resize(w * h, 0.0);
    for i in 0..h {
        let (hi, lo) = (&c[(2 * side + 2 + i) * w..][..w], &c[(1 + i) * w..][..w]);
        let (left, right) = (at(i), at(2 * side + 2 + i));
        let row = &mut out[i * w..(i + 1) * w];
        for x in 0..w {
            let mut v = hi[x] - lo[x];
            if frac != 0.0 {
                v += frac32 * (left[x] + right[x]);
            }
            row[x] = v * scale;
        }
    }
}

/// One pass along each row of `a` (dither._rows), line by line: what [`down`] does to the
/// picture turned (the tests hold the two to the same numbers).
#[cfg(test)]
fn rows(a: &[f32], w: usize, h: usize, side: usize, frac: f64) -> Vec<f32> {
    let n = w + 2 * side + 2;
    let mut p = vec![0f32; n];
    let mut c = vec![0f32; n + 1];
    let mut out = vec![0f32; w * h];
    let frac32 = frac as f32;
    let scale = (1.0 / (2.0 * side as f64 + 1.0 + 2.0 * frac)) as f32;
    for y in 0..h {
        let row = &a[y * w..(y + 1) * w];
        p[..side + 1].fill(row[0]);
        p[side + 1..side + 1 + w].copy_from_slice(row);
        p[side + 1 + w..].fill(row[w - 1]);
        let mut sum = 0f32;
        c[0] = 0.0;
        for (k, v) in p.iter().enumerate() {
            sum += v;
            c[k + 1] = sum;
        }
        for i in 0..w {
            let mut v = c[2 * side + 2 + i] - c[1 + i];
            if frac != 0.0 {
                v += frac32 * (p[i] + p[2 * side + 2 + i]);
            }
            out[y * w + i] = v * scale;
        }
    }
    out
}

/// `src` laid on `dst` (RGBA each) as Pillow's alpha_composite does, to the bit.
pub fn alpha_composite(dst: &mut [u8], src: &[u8; 4]) {
    let sa = u32::from(src[3]);
    if sa == 0 {
        return;
    }
    let blend = u32::from(dst[3]) * (255 - sa);
    let outa255 = sa * 255 + blend;
    let coef1 = sa * 255 * 255 * (1 << 7) / outa255;
    let coef2 = 255 * (1 << 7) - coef1;
    let div = |a: u32| ((a >> 8) + a) >> 8;
    for c in 0..3 {
        let t = u32::from(src[c]) * coef1 + u32::from(dst[c]) * coef2;
        dst[c] = (div(t + (0x80 << 7)) >> 7) as u8;
    }
    dst[3] = div(outa255 + 0x80) as u8;
}

#[cfg(test)]
mod tests {
    use super::*;

    /// How long a ring's picture takes (cargo test --release ring_timing -- --ignored --nocapture).
    #[test]
    #[ignore]
    fn ring_timing() {
        let side = 217;
        let g = Geometry {
            w: 163.0,
            thickness: 10.0,
            start: -90.0,
            end: 270.0,
            stroke: 6,
            halo: 9.0,
            colour: [46, 230, 214, 255],
        };
        let mut base = Base::new(
            side,
            &g,
            vec![30; side * side * 4],
            vec![90; side * side * 4],
        )
        .unwrap();
        let t = std::time::Instant::now();
        let n = 200;
        for k in 0..n {
            std::hint::black_box(base.picture(0.2 + 0.6 * k as f64 / n as f64));
        }
        let each = t.elapsed().as_secs_f64() / n as f64 * 1000.0;
        let (inner, outer) = base.window(-90.0 + 360.0 * 0.4, 36.0);
        let t = std::time::Instant::now();
        for _ in 0..n {
            std::hint::black_box(base.cover(0.4, outer));
        }
        let cover = t.elapsed().as_secs_f64() / n as f64 * 1000.0;
        let mask = base.cover(0.4, outer);
        let (ow, oh) = (outer.2 - outer.0, outer.3 - outer.1);
        let t = std::time::Instant::now();
        for _ in 0..n {
            std::hint::black_box(blur(&mask, ow, oh, 9.0));
        }
        let blurred = t.elapsed().as_secs_f64() / n as f64 * 1000.0;
        let t = std::time::Instant::now();
        let mut scratch = Scratch::default();
        for _ in 0..n {
            std::hint::black_box(base.patch(0.4, inner, outer, &mut scratch));
        }
        let patch = t.elapsed().as_secs_f64() / n as f64 * 1000.0;
        let t = std::time::Instant::now();
        for k in 0..n {
            let mut out = base.groove.clone();
            let lim = (k % 300) as f32;
            for (i, &a) in base.angle.iter().enumerate() {
                if a.to_degrees() <= lim {
                    out[i * 4..i * 4 + 4].copy_from_slice(&base.lit[i * 4..i * 4 + 4]);
                }
            }
            std::hint::black_box(out);
        }
        let sector = t.elapsed().as_secs_f64() / n as f64 * 1000.0;
        println!("picture {each:.3} ms; cover {cover:.3}, blur {blurred:.3}, patch {patch:.3}, sector {sector:.3} ({ow}x{oh})");
    }

    #[test]
    fn the_box_passes_are_dithers() {
        // dither._box_radius(9.0), (6.0)
        let (side, a) = box_radius(9.0);
        assert_eq!(side, 8);
        assert!((a - 0.472_222_222_222_222_2).abs() < 1e-12);
        assert_eq!(box_radius(6.0).0, 5);
        // a level blurred stays where it is; one pixel spreads, its sum kept
        let flat = blur(&vec![7.0; 40 * 30], 40, 30, 6.0);
        assert!(flat.iter().all(|v| (v - 7.0).abs() < 1e-4));
        let mut dot = vec![0.0; 61 * 61];
        dot[30 * 61 + 30] = 255.0;
        let spread = blur(&dot, 61, 61, 6.0);
        let sum: f32 = spread.iter().sum();
        assert!((sum - 255.0).abs() < 0.01 && spread[30 * 61 + 30] < 10.0);
    }

    #[test]
    fn a_pass_down_the_columns_is_dithers_pass_along_the_rows_turned() {
        let (w, h) = (37, 23);
        let a: Vec<f32> = (0..w * h).map(|i| ((i * 7919) % 256) as f32).collect();
        let along = rows(&a, w, h, 5, 0.458_333_333_333_333_3);
        let turned = down(&transpose(&a, w, h), h, w, 5, 0.458_333_333_333_333_3);
        assert_eq!(transpose(&turned, h, w), along); // (to the bit)
    }

    #[test]
    fn alpha_composite_is_pillows() {
        // (dst, src, Pillow's result)
        for (d, s, want) in [
            (
                [10u8, 20, 30, 0],
                [200u8, 100, 50, 128],
                [200u8, 100, 50, 128],
            ),
            ([10, 20, 30, 255], [200, 100, 50, 128], [105, 60, 40, 255]),
            ([0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]),
            ([90, 91, 92, 93], [5, 6, 7, 255], [5, 6, 7, 255]),
        ] {
            let mut out = d;
            alpha_composite(&mut out, &s);
            assert_eq!(out, want, "{d:?} under {s:?}");
        }
    }

    #[test]
    fn numpys_remainder_and_levels() {
        assert_eq!(remainder(-1.0, 6.0), 5.0);
        assert_eq!(remainder(7.0, 6.0), 1.0);
        assert_eq!(level(254.6), 255);
        assert_eq!(level(-3.0), 0);
        assert_eq!(level(10.49), 10);
    }
}
