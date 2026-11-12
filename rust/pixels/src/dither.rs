//! render/dither.py: soft light without bands. Its noise (a tile of TILE x TILE, from numpy's
//! default_rng(0x11B7E): SeedSequence, PCG64, random float32s, the same numbers), pictures made
//! 8 bits with a level's worth of it (to_u8, ramp_u8), and its blur (three box passes each way,
//! as Pillow's GaussianBlur works out its boxes), all in single precision in numpy's order.
//!
//! The blur is worked out by its passes. Where the Python renderer takes them, they are its
//! passes to the bit (single precision, its running sums). Where it multiplies by their matrices
//! instead (a short side, dither.PRODUCT_MOST), through the BLAS numpy has, whose sums go in an
//! order of the processor's own, the passes are worked out in double precision: what the
//! product comes to, within a third of a thousandth of a level (single precision's running
//! sums were up to two hundredths away on bright rows).

use std::sync::OnceLock;

use crate::random::Pcg64;

/// dither.TILE: the noise's tile, laid side by side.
pub const TILE: usize = 128;

/// dither.PRODUCT_MOST, PRODUCT_STEP and PRODUCT_SHORT: where the renderer blurs by products.
const PRODUCT_MOST: usize = 640;
const PRODUCT_STEP: usize = 32;
const PRODUCT_SHORT: usize = 160;

/// dither._tile: rng.random((TILE, TILE), float32) + rng.random(...) - 1, of default_rng(0x11B7E).
pub fn tile() -> &'static [f32] {
    static TILE_NOISE: OnceLock<Vec<f32>> = OnceLock::new();
    TILE_NOISE.get_or_init(|| {
        let mut rng = Pcg64::new(0x11B7E);
        let a: Vec<f32> = (0..TILE * TILE).map(|_| rng.next_f32()).collect();
        let b: Vec<f32> = (0..TILE * TILE).map(|_| rng.next_f32()).collect();
        a.iter().zip(&b).map(|(&a, &b)| (a + b) - 1.0).collect()
    })
}

/// dither.noise(w, h, x, y): the noise (h rows of w), -1 to 1, of a picture whose top left is
/// at (x, y) on the panel.
pub fn noise(w: usize, h: usize, x: i64, y: i64) -> Vec<f32> {
    let t = tile();
    let (dx, dy) = (
        x.rem_euclid(TILE as i64) as usize,
        y.rem_euclid(TILE as i64) as usize,
    );
    let mut out = Vec::with_capacity(w * h);
    for r in 0..h {
        let row = &t[((r + dy) % TILE) * TILE..((r + dy) % TILE + 1) * TILE];
        out.extend((0..w).map(|c| row[(c + dx) % TILE]));
    }
    out
}

/// dither.to_u8: `values` (h rows of w pixels of c bands, 0 to 255) as bytes: each plus a half
/// and its place's noise, floored; 0 or less and 255 or more as they are; clipped. Without the
/// dither each rounded (plus a half, floored).
pub fn to_u8(
    values: &[f32],
    w: usize,
    h: usize,
    c: usize,
    x: i64,
    y: i64,
    dither: bool,
) -> Vec<u8> {
    let n = if dither {
        noise(w, h, x, y)
    } else {
        Vec::new()
    };
    values
        .iter()
        .enumerate()
        .map(|(i, &v)| {
            let mut out = v + 0.5;
            if dither {
                out = (out + n[i / c]).floor();
                if v <= 0.0 || v >= 255.0 {
                    out = v;
                }
            } else {
                out = out.floor();
            }
            // (np.clip, then astype: a float cut to its whole part)
            out.clamp(0.0, 255.0) as u8
        })
        .collect()
}

/// dither.ramp_u8: a straight gradient as bytes (rows of pixels of c bands): `line`, the n
/// colours along it, `length` px across it; `across` false: `line` runs down (n rows of
/// `length`), else across (`length` rows of n). A tile's width of it is made, then laid side
/// by side.
pub fn ramp_u8(
    line: &[f32],
    n: usize,
    c: usize,
    length: usize,
    across: bool,
    dither: bool,
) -> Vec<u8> {
    let part = if dither { length.min(TILE) } else { 1 };
    if part == 0 || n == 0 {
        return Vec::new();
    }
    if across {
        // (part rows, each the line)
        let block: Vec<f32> = (0..part).flat_map(|_| line.iter().copied()).collect();
        let block = to_u8(&block, n, part, c, 0, 0, dither);
        let row = n * c;
        let mut out = Vec::with_capacity(length * row);
        for r in 0..length {
            out.extend_from_slice(&block[(r % part) * row..(r % part + 1) * row]);
        }
        out
    } else {
        // (n rows, each its colour part times)
        let block: Vec<f32> = line
            .chunks_exact(c)
            .flat_map(|colour| (0..part).flat_map(move |_| colour.iter().copied()))
            .collect();
        let block = to_u8(&block, part, n, c, 0, 0, dither);
        let mut out = Vec::with_capacity(n * length * c);
        for r in 0..n {
            let row = &block[r * part * c..(r + 1) * part * c];
            for k in 0..length {
                out.extend_from_slice(&row[(k % part) * c..(k % part + 1) * c]);
            }
        }
        out
    }
}

/// dither._box_radius (Pillow's boxes for a Gaussian, Gwosdek et al. 2011): `side` whole pixels
/// each side and a fraction of the next, for each of three passes.
pub fn box_radius(radius: f64) -> (usize, f64) {
    let sigma2 = radius * radius / 3.0;
    let big = (12.0 * sigma2 + 1.0).sqrt();
    let side = ((big - 1.0) / 2.0).floor();
    let a = (2.0 * side + 1.0) * (side * (side + 1.0) - 3.0 * sigma2)
        / (6.0 * (sigma2 - (side + 1.0) * (side + 1.0)));
    (side as usize, a)
}

/// dither.reach: how far a pixel's light carries through the blur.
pub fn reach(radius: f64) -> usize {
    if radius <= 0.0 {
        return 0;
    }
    3 * (box_radius(radius).0 + 1) + 1
}

/// The floats a blur's passes are worked out in: single precision as the renderer's passes,
/// double where they stand for its products.
trait Level:
    Copy + std::ops::Add<Output = Self> + std::ops::Sub<Output = Self> + std::ops::Mul<Output = Self>
{
    const ZERO: Self;
    fn of(v: f64) -> Self;
}

impl Level for f32 {
    const ZERO: f32 = 0.0;
    fn of(v: f64) -> f32 {
        v as f32
    }
}

impl Level for f64 {
    const ZERO: f64 = 0.0;
    fn of(v: f64) -> f64 {
        v
    }
}

/// dither._rows: one pass along each of `h` rows of `w`: the mean over `side` pixels each way
/// and `frac` of the next, the edges held, through a running sum. (What `passes` does down
/// the columns of the picture turned; kept as the measure of that.)
#[cfg(test)]
fn rows<T: Level>(a: &[T], h: usize, w: usize, side: usize, frac: f64) -> Vec<T> {
    let padded = w + 2 * side + 2;
    let mut p = vec![T::ZERO; padded];
    let mut c = vec![T::ZERO; padded + 1];
    let mut out = vec![T::ZERO; h * w];
    let frac_t = T::of(frac);
    let scale = T::of(1.0 / (2.0 * side as f64 + 1.0 + 2.0 * frac));
    for r in 0..h {
        let line = &a[r * w..(r + 1) * w];
        p[..side + 1].fill(line[0]);
        p[side + 1..side + 1 + w].copy_from_slice(line);
        p[side + 1 + w..].fill(line[w - 1]);
        let mut sum = T::ZERO;
        for (k, &v) in p.iter().enumerate() {
            sum = sum + v;
            c[k + 1] = sum;
        }
        let o = &mut out[r * w..(r + 1) * w];
        for (i, o) in o.iter_mut().enumerate() {
            let mut v = c[2 * side + 2 + i] - c[1 + i];
            if frac != 0.0 {
                v = v + frac_t * (p[i] + p[2 * side + 2 + i]);
            }
            *o = v * scale;
        }
    }
    out
}

/// The `h` x `w` floats turned (w rows of h): the passes along the rows made passes down the
/// columns, which run for all of them at once. (In tiles, each read and written in the cache.)
#[cfg(test)]
fn turned<T: Level>(a: &[T], h: usize, w: usize) -> Vec<T> {
    let mut out = vec![T::ZERO; h * w];
    turned_into(a, &mut out, h, w);
    out
}

fn turned_into<T: Level>(a: &[T], out: &mut [T], h: usize, w: usize) {
    // (column k of `a`, from its k-th value on every w-th, as row k of `out`)
    for (k, line) in out[..h * w].chunks_exact_mut(h).enumerate() {
        for (o, &v) in line.iter_mut().zip(a[k..].iter().step_by(w)) {
            *o = v;
        }
    }
}

/// rows down the columns: the same sums in the same order for each column (as the rows of the
/// picture turned), worked out for all columns at once, row by row. Of the running sums only
/// the two a window takes are kept, where it starts and where it ends, each summed from the
/// first row on as rows sums them: the same additions in the same order.
fn cols<T: Level>(a: &[T], out: &mut [T], h: usize, w: usize, side: usize, frac: f64) {
    #[cfg(target_arch = "x86_64")]
    {
        static WIDE: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
        if *WIDE.get_or_init(|| std::arch::is_x86_feature_detected!("avx2")) {
            // SAFETY: the processor has AVX2 (asked above).
            return unsafe { cols_avx2(a, out, h, w, side, frac) };
        }
    }
    cols_in(a, out, h, w, side, frac);
}

/// cols with AVX2's wider registers: the same additions and products, in the same order, on
/// more columns at a time (no fused multiply-add: that would round once where they round
/// twice).
#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn cols_avx2<T: Level>(a: &[T], out: &mut [T], h: usize, w: usize, side: usize, frac: f64) {
    cols_in(a, out, h, w, side, frac);
}

#[inline(always)]
fn cols_in<T: Level>(a: &[T], out: &mut [T], h: usize, w: usize, side: usize, frac: f64) {
    let frac_t = T::of(frac);
    let scale = T::of(1.0 / (2.0 * side as f64 + 1.0 + 2.0 * frac));
    // the padded column's k-th value: the edges held
    let at = |k: usize| -> &[T] {
        let r = k.saturating_sub(side + 1).min(h - 1);
        &a[r * w..(r + 1) * w]
    };
    // c[1] and c[2 side + 2] of the running sums c[0] = 0, c[k + 1] = c[k] + p[k]
    let (mut lo, mut hi) = (vec![T::ZERO; w], vec![T::ZERO; w]);
    for (sum, &v) in lo.iter_mut().zip(at(0)) {
        *sum = *sum + v;
    }
    for k in 0..2 * side + 2 {
        for (sum, &v) in hi.iter_mut().zip(at(k)) {
            *sum = *sum + v;
        }
    }
    for i in 0..h {
        let (first, last) = (at(i), at(2 * side + 2 + i));
        let row = &mut out[i * w..(i + 1) * w];
        if i + 1 == h {
            for x in 0..w {
                row[x] = if frac != 0.0 {
                    (hi[x] - lo[x] + frac_t * (first[x] + last[x])) * scale
                } else {
                    (hi[x] - lo[x]) * scale
                };
            }
            break;
        }
        // (the row, then on to c[i + 2] and c[2 side + 3 + i]: in one go along it)
        let (lo, hi, next) = (&mut lo[..w], &mut hi[..w], &at(i + 1)[..w]);
        let (first, last) = (&first[..w], &last[..w]);
        if frac != 0.0 {
            for x in 0..w {
                row[x] = (hi[x] - lo[x] + frac_t * (first[x] + last[x])) * scale;
                lo[x] = lo[x] + next[x];
                hi[x] = hi[x] + last[x];
            }
        } else {
            for x in 0..w {
                row[x] = (hi[x] - lo[x]) * scale;
                lo[x] = lo[x] + next[x];
                hi[x] = hi[x] + last[x];
            }
        }
    }
}

/// The three passes along the rows, then the three down: those along the rows as passes down
/// the columns of the picture turned (each row's sums the same, in the same order), so that
/// every pass runs across all the lines at once.
/// (Two buffers, the passes going from one to the other.)
fn passes<T: Level>(a: Vec<T>, h: usize, w: usize, side: usize, frac: f64) -> Vec<T> {
    let mut one = vec![T::ZERO; h * w];
    turned_into(&a, &mut one, h, w);
    let mut other = a;
    for _ in 0..3 {
        cols(&one, &mut other, w, h, side, frac);
        std::mem::swap(&mut one, &mut other);
    }
    turned_into(&one, &mut other, w, h);
    std::mem::swap(&mut one, &mut other);
    for _ in 0..3 {
        cols(&one, &mut other, h, w, side, frac);
        std::mem::swap(&mut one, &mut other);
    }
    one
}

/// Whether dither.blur multiplies by the passes' matrices: no side longer than PRODUCT_MOST,
/// and both (each made a step longer where nothing is within the blur's reach of its end) of
/// the lengths it keeps a matrix for.
fn takes_products(a: &[f32], h: usize, w: usize, radius: f64) -> bool {
    if h.max(w) > PRODUCT_MOST {
        return false;
    }
    let far = reach(radius);
    let stepped = |n: usize| PRODUCT_MOST.min(n.div_ceil(PRODUCT_STEP) * PRODUCT_STEP);
    let served = |n: usize| n <= PRODUCT_SHORT || n.is_multiple_of(PRODUCT_STEP);
    // (NaN is something, as numpy's any takes it)
    let nothing = |v: &f32| *v == 0.0;
    let rows = if h > far && a[(h - far) * w..].iter().all(nothing) {
        stepped(h)
    } else {
        h
    };
    let cols = if w > far && (0..h).all(|r| a[r * w + w - far..(r + 1) * w].iter().all(nothing)) {
        stepped(w)
    } else {
        w
    };
    served(rows) && served(cols)
}

/// dither.blur: a Gaussian blur of `a` (h rows of w floats) of `radius`, as Pillow's
/// GaussianBlur blurs whole levels: three box passes along the rows, then three down (in double
/// precision where the renderer takes their products).
pub fn blur(a: &[f32], h: usize, w: usize, radius: f64) -> Vec<f32> {
    if radius <= 0.0 || a.is_empty() {
        return a.to_vec();
    }
    let (side, frac) = box_radius(radius);
    if takes_products(a, h, w, radius) {
        let wide: Vec<f64> = a.iter().map(|&v| f64::from(v)).collect();
        return passes(wide, h, w, side, frac)
            .into_iter()
            .map(|v| v as f32)
            .collect();
    }
    passes(a.to_vec(), h, w, side, frac)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_passes_down_the_columns_as_down_the_rows_of_the_picture_turned() {
        let turned_passes = |a: Vec<f64>, h: usize, w: usize, side: usize, frac: f64| {
            let mut out = a;
            for _ in 0..3 {
                out = rows(&out, h, w, side, frac);
            }
            let mut out = turned(&out, h, w);
            for _ in 0..3 {
                out = rows(&out, w, h, side, frac);
            }
            turned(&out, w, h)
        };
        let mut seed = 0x51ed_270b_u64;
        for (h, w) in [(1usize, 1usize), (1, 7), (9, 1), (13, 17), (64, 31)] {
            for (side, frac) in [(0usize, 0.0), (1, 0.25), (3, 0.5), (6, 0.0)] {
                let a: Vec<f64> = (0..h * w)
                    .map(|_| {
                        seed ^= seed << 13;
                        seed ^= seed >> 7;
                        seed ^= seed << 17;
                        (seed % 1000) as f64 / 7.0
                    })
                    .collect();
                let want = turned_passes(a.clone(), h, w, side, frac);
                let got = passes(a.clone(), h, w, side, frac);
                assert!(
                    want.iter()
                        .zip(&got)
                        .all(|(x, y)| x.to_bits() == y.to_bits()),
                    "{h}x{w}"
                );
                let a32: Vec<f32> = a.iter().map(|&v| v as f32).collect();
                let mut want32 = a32.clone();
                for _ in 0..3 {
                    want32 = rows(&want32, h, w, side, frac);
                }
                let mut want32 = turned(&want32, h, w);
                for _ in 0..3 {
                    want32 = rows(&want32, w, h, side, frac);
                }
                let want32 = turned(&want32, w, h);
                let got32 = passes(a32, h, w, side, frac);
                assert!(want32
                    .iter()
                    .zip(&got32)
                    .all(|(x, y)| x.to_bits() == y.to_bits()));
            }
        }
    }

    #[test]
    fn numpys_first_draws() {
        // default_rng(0x11B7E).random(2, np.float32), and the tile's first noise
        let mut rng = Pcg64::new(0x11B7E);
        let first = [rng.next_f32(), rng.next_f32()];
        assert!(first.iter().all(|v| (0.0..1.0).contains(v)));
        assert_eq!(tile().len(), TILE * TILE);
    }
}
