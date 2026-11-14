//! numpy's random Generator as the renderer draws from it (numpy/random: _generator.pyx,
//! bit_generator.pyx, src/pcg64 and src/distributions, NumPy's BSD-3-Clause; the ziggurat
//! methods derived from Julia's, MIT): default_rng(seed)'s SeedSequence and PCG64, and the
//! doubles, normals (the ziggurat), exponentials (the ziggurat), powers and uniforms it draws,
//! the same numbers. The C library's functions it calls (exp, expm1, log1p, pow) are the
//! system's, as numpy's are.

use crate::ziggurat::{
    FE_DOUBLE, FI_DOUBLE, KE_DOUBLE, KI_DOUBLE, WE_DOUBLE, WI_DOUBLE, ZIGGURAT_EXP_R,
    ZIGGURAT_NOR_INV_R, ZIGGURAT_NOR_R,
};

/// numpy's SeedSequence (bit_generator.pyx): the pool its entropy is mixed into.
pub(crate) fn seed_sequence(entropy: &[u32], pool_size: usize) -> Vec<u32> {
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
pub(crate) fn generate_state(pool: &[u32], n: usize) -> Vec<u64> {
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
pub struct Pcg64 {
    state: u128,
    inc: u128,
    half: Option<u32>,
}

impl Pcg64 {
    const MULT: u128 = 0x2360_ed05_1fc6_5da4_4385_df64_9fcc_f645;

    /// PCG64(SeedSequence(seed)): pcg64_set_seed with the state's four words.
    pub fn new(seed: u32) -> Pcg64 {
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

    pub fn next_u64(&mut self) -> u64 {
        self.step();
        let s = self.state;
        (((s >> 64) as u64) ^ (s as u64)).rotate_right((s >> 122) as u32)
    }

    pub fn next_u32(&mut self) -> u32 {
        if let Some(h) = self.half.take() {
            return h;
        }
        let next = self.next_u64();
        self.half = Some((next >> 32) as u32);
        next as u32
    }

    /// next_float: 24 bits of a 32-bit draw, as a float in [0, 1).
    pub fn next_f32(&mut self) -> f32 {
        (self.next_u32() >> 8) as f32 * (1.0 / 16_777_216.0)
    }
}

impl Pcg64 {
    /// next_double: 53 bits of a 64-bit draw, as a double in [0, 1).
    pub fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / 9_007_199_254_740_992.0)
    }
}

/// Generator(PCG64(seed)): numpy.random.default_rng(seed) of a seed of 32 bits.
pub struct Generator {
    bits: Pcg64,
}

fn table(bits: &[u64; 256], i: usize) -> f64 {
    f64::from_bits(bits[i])
}

impl Generator {
    pub fn new(seed: u32) -> Generator {
        Generator {
            bits: Pcg64::new(seed),
        }
    }

    /// random(): a double in [0, 1).
    pub fn random(&mut self) -> f64 {
        self.bits.next_f64()
    }

    /// random_standard_normal: the ziggurat of 256 layers, its tail and its wedges as numpy
    /// draws them.
    pub fn standard_normal(&mut self) -> f64 {
        loop {
            let mut r = self.bits.next_u64();
            let idx = (r & 0xff) as usize;
            r >>= 8;
            let sign = r & 0x1;
            let rabs = (r >> 1) & 0x000f_ffff_ffff_ffff;
            let mut x = rabs as f64 * table(&WI_DOUBLE, idx);
            if sign & 0x1 != 0 {
                x = -x;
            }
            if rabs < KI_DOUBLE[idx] {
                return x;
            }
            if idx == 0 {
                let (nor_r, inv_r) = (
                    f64::from_bits(ZIGGURAT_NOR_R),
                    f64::from_bits(ZIGGURAT_NOR_INV_R),
                );
                loop {
                    let xx = -inv_r * (-self.bits.next_f64()).ln_1p();
                    let yy = -(-self.bits.next_f64()).ln_1p();
                    if yy + yy > xx * xx {
                        return if (rabs >> 8) & 0x1 != 0 {
                            -(nor_r + xx)
                        } else {
                            nor_r + xx
                        };
                    }
                }
            }
            let f0 = table(&FI_DOUBLE, idx - 1);
            let f1 = table(&FI_DOUBLE, idx);
            if (f0 - f1) * self.bits.next_f64() + f1 < (-0.5 * x * x).exp() {
                return x;
            }
        }
    }

    /// random_standard_exponential: the ziggurat of 256 layers, its tail and its wedges.
    pub fn standard_exponential(&mut self) -> f64 {
        loop {
            let mut ri = self.bits.next_u64();
            ri >>= 3;
            let idx = (ri & 0xff) as usize;
            ri >>= 8;
            let x = ri as f64 * table(&WE_DOUBLE, idx);
            if ri < KE_DOUBLE[idx] {
                return x;
            }
            // standard_exponential_unlikely
            if idx == 0 {
                return f64::from_bits(ZIGGURAT_EXP_R) - (-self.bits.next_f64()).ln_1p();
            }
            let f0 = table(&FE_DOUBLE, idx - 1);
            let f1 = table(&FE_DOUBLE, idx);
            if (f0 - f1) * self.bits.next_f64() + f1 < (-x).exp() {
                return x;
            }
            // (else a draw anew, as numpy's recursion has it)
        }
    }

    /// random_power: pow(-expm1(-standard_exponential), 1 / a).
    pub fn power(&mut self, a: f64) -> f64 {
        (-(-self.standard_exponential()).exp_m1()).powf(1.0 / a)
    }

    /// uniform(low, high): low + (high - low) * a double in [0, 1).
    pub fn uniform(&mut self, low: f64, high: f64) -> f64 {
        low + (high - low) * self.bits.next_f64()
    }

    /// random_pareto: expm1 of a standard exponential over `a` (the C library's expm1).
    pub fn pareto(&mut self, a: f64) -> f64 {
        (self.standard_exponential() / a).exp_m1()
    }

    /// random(dtype=np.float32): 24 bits of a 32-bit draw, a float in [0, 1).
    pub fn random_f32(&mut self) -> f32 {
        self.bits.next_f32()
    }

    /// integers(low, high) of int64, `high` left out (random_bounded_uint64_fill): `low`
    /// and a draw of 0 to high - low - 1, by Lemire's method on 32 bits for a range of up to
    /// 2^32 (each 32-bit draw half of a 64-bit one, as next_uint32 keeps them); None for a
    /// wider range (not in Rust yet) and an empty one (numpy's ValueError).
    pub fn integers(&mut self, low: i64, high: i64) -> Option<i64> {
        let closed = high.checked_sub(1)?.checked_sub(low)?;
        if closed < 0 {
            return None;
        }
        let rng = closed as u64;
        if rng == 0 {
            return Some(low);
        }
        if rng > 0xFFFF_FFFF {
            return None;
        }
        if rng == 0xFFFF_FFFF {
            return Some(low.wrapping_add(i64::from(self.bits.next_u32())));
        }
        let rng = rng as u32;
        let excl = rng + 1;
        let mut m = u64::from(self.bits.next_u32()) * u64::from(excl);
        let mut leftover = m as u32;
        if leftover < excl {
            let threshold = (u32::MAX - rng) % excl;
            while leftover < threshold {
                m = u64::from(self.bits.next_u32()) * u64::from(excl);
                leftover = m as u32;
            }
        }
        Some(low.wrapping_add((m >> 32) as i64))
    }
}
