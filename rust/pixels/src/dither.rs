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

/// dither.TILE: the noise's tile, laid side by side.
pub const TILE: usize = 128;

/// dither.PRODUCT_MOST, PRODUCT_STEP and PRODUCT_SHORT: where the renderer blurs by products.
const PRODUCT_MOST: usize = 640;
const PRODUCT_STEP: usize = 32;
const PRODUCT_SHORT: usize = 160;

/// numpy's SeedSequence (bit_generator.pyx): the pool its entropy is mixed into.
fn seed_sequence(entropy: &[u32], pool_size: usize) -> Vec<u32> {
    const INIT_A: u32 = 0x43b0_d7e5;
    const MULT_A: u32 = 0x931e_8875;
    const MIX_MULT_L: u32 = 0xca01_f9dd;
    const MIX_MULT_R: u32 = 0x4973_f715;
    let mut hash_const = INIT_A;
    let mut hashmix = |value: u32| -> u32 {
        let mut value = value ^ hash_const;
        hash_const = hash_const.wrapping_mul(MULT_A);
        value = value.wrapping_mul(hash_const);
        value ^ (value >> 16)
    };
    let mix = |x: u32, y: u32| -> u32 {
        let result = MIX_MULT_L
            .wrapping_mul(x)
            .wrapping_sub(MIX_MULT_R.wrapping_mul(y));
        result ^ (result >> 16)
    };
    let mut pool: Vec<u32> = (0..pool_size)
        .map(|i| hashmix(entropy.get(i).copied().unwrap_or(0)))
        .collect();
    for src in 0..pool_size {
        for dst in 0..pool_size {
            if src != dst {
                let h = hashmix(pool[src]);
                pool[dst] = mix(pool[dst], h);
            }
        }
    }
    for &e in entropy.iter().skip(pool_size) {
        for slot in pool.iter_mut() {
            let h = hashmix(e);
            *slot = mix(*slot, h);
        }
    }
    pool
}

/// SeedSequence.generate_state(n, np.uint64): 2 n words of 32 bits from the pool, in pairs
/// (the low one first).
fn generate_state(pool: &[u32], n: usize) -> Vec<u64> {
    const INIT_B: u32 = 0x8b51_f9dd;
    const MULT_B: u32 = 0x58f3_8ded;
    let mut hash_const = INIT_B;
    let words: Vec<u32> = (0..2 * n)
        .map(|i| {
            let mut v = pool[i % pool.len()] ^ hash_const;
            hash_const = hash_const.wrapping_mul(MULT_B);
            v = v.wrapping_mul(hash_const);
            v ^ (v >> 16)
        })
        .collect();
    words
        .chunks_exact(2)
        .map(|w| u64::from(w[0]) | u64::from(w[1]) << 32)
        .collect()
}

/// numpy's PCG64 (XSL-RR on a 128-bit LCG), and its next_uint32 (each 64 bits giving two, the
/// low half first).
struct Pcg64 {
    state: u128,
    inc: u128,
    half: Option<u32>,
}

impl Pcg64 {
    const MULT: u128 = 0x2360_ed05_1fc6_5da4_4385_df64_9fcc_f645;

    /// PCG64(SeedSequence(seed)): pcg64_set_seed with the state's four words.
    fn new(seed: u32) -> Pcg64 {
        let pool = seed_sequence(&[seed], 4);
        let v = generate_state(&pool, 4);
        let initstate = u128::from(v[0]) << 64 | u128::from(v[1]);
        let initseq = u128::from(v[2]) << 64 | u128::from(v[3]);
        let mut rng = Pcg64 {
            state: 0,
            inc: initseq << 1 | 1,
            half: None,
        };
        rng.step();
        rng.state = rng.state.wrapping_add(initstate);
        rng.step();
        rng
    }

    fn step(&mut self) {
        self.state = self.state.wrapping_mul(Pcg64::MULT).wrapping_add(self.inc);
    }

    fn next_u64(&mut self) -> u64 {
        self.step();
        let s = self.state;
        (((s >> 64) as u64) ^ (s as u64)).rotate_right((s >> 122) as u32)
    }

    fn next_u32(&mut self) -> u32 {
        if let Some(h) = self.half.take() {
            return h;
        }
        let next = self.next_u64();
        self.half = Some((next >> 32) as u32);
        next as u32
    }

    /// next_float: 24 bits of a 32-bit draw, as a float in [0, 1).
    fn next_f32(&mut self) -> f32 {
        (self.next_u32() >> 8) as f32 * (1.0 / 16_777_216.0)
    }
}

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
/// and `frac` of the next, the edges held, through a running sum.
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

/// The `h` x `w` floats turned (w rows of h).
fn turned<T: Level>(a: &[T], h: usize, w: usize) -> Vec<T> {
    let mut out = vec![T::ZERO; h * w];
    for r in 0..h {
        for k in 0..w {
            out[k * h + r] = a[r * w + k];
        }
    }
    out
}

/// The three passes along the rows, then the three down.
fn passes<T: Level>(a: Vec<T>, h: usize, w: usize, side: usize, frac: f64) -> Vec<T> {
    let mut out = a;
    for _ in 0..3 {
        out = rows(&out, h, w, side, frac);
    }
    let mut out = turned(&out, h, w);
    for _ in 0..3 {
        out = rows(&out, w, h, side, frac);
    }
    turned(&out, w, h)
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
    fn numpys_first_draws() {
        // default_rng(0x11B7E).random(2, np.float32), and the tile's first noise
        let mut rng = Pcg64::new(0x11B7E);
        let first = [rng.next_f32(), rng.next_f32()];
        assert!(first.iter().all(|v| (0.0..1.0).contains(v)));
        assert_eq!(tile().len(), TILE * TILE);
    }
}
