//! Python's random.Random as the renderer draws from it (Modules/_randommodule.c and
//! Lib/random.py of CPython 3.12, PSF; the Mersenne Twister of Matsumoto and Nishimura,
//! BSD-3-Clause): seeded with an int, and its random(), getrandbits(), randrange() and
//! uniform(), the same numbers.

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

/// random.Random: the Mersenne Twister and its draws.
#[derive(Clone)]
pub struct Random {
    state: [u32; N],
    index: usize,
}

impl Random {
    /// Random(seed) of an int: init_by_array with its absolute value cut into 32-bit words,
    /// the lowest first (one word for 0).
    pub fn new(seed: i64) -> Random {
        let n = seed.unsigned_abs();
        let mut key = vec![n as u32];
        if n >> 32 != 0 {
            key.push((n >> 32) as u32);
        }
        let mut random = Random {
            state: [0; N],
            index: N,
        };
        random.init_by_array(&key);
        random
    }

    fn init_genrand(&mut self, s: u32) {
        let mt = &mut self.state;
        mt[0] = s;
        for i in 1..N {
            mt[i] = 1_812_433_253u32
                .wrapping_mul(mt[i - 1] ^ (mt[i - 1] >> 30))
                .wrapping_add(i as u32);
        }
        self.index = N;
    }

    fn init_by_array(&mut self, key: &[u32]) {
        self.init_genrand(19_650_218);
        let mt = &mut self.state;
        let (mut i, mut j) = (1usize, 0usize);
        for _ in 0..N.max(key.len()) {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(key[j])
                .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
            if j >= key.len() {
                j = 0;
            }
        }
        for _ in 0..N - 1 {
            mt[i] = (mt[i] ^ (mt[i - 1] ^ (mt[i - 1] >> 30)).wrapping_mul(1_566_083_941))
                .wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                mt[0] = mt[N - 1];
                i = 1;
            }
        }
        mt[0] = 0x8000_0000;
    }

    /// genrand_uint32: the next 32 random bits.
    pub fn next_u32(&mut self) -> u32 {
        if self.index >= N {
            let mt = &mut self.state;
            let mag = |y: u32| if y & 1 == 0 { 0 } else { MATRIX_A };
            for kk in 0..N - M {
                let y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
                mt[kk] = mt[kk + M] ^ (y >> 1) ^ mag(y);
            }
            for kk in N - M..N - 1 {
                let y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
                mt[kk] = mt[kk + M - N] ^ (y >> 1) ^ mag(y);
            }
            let y = (mt[N - 1] & UPPER_MASK) | (mt[0] & LOWER_MASK);
            mt[N - 1] = mt[M - 1] ^ (y >> 1) ^ mag(y);
            self.index = 0;
        }
        let mut y = self.state[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^ (y >> 18)
    }

    /// random(): a double in [0, 1) of 53 random bits.
    pub fn random(&mut self) -> f64 {
        let a = (self.next_u32() >> 5) as f64;
        let b = (self.next_u32() >> 6) as f64;
        (a * 67_108_864.0 + b) * (1.0 / 9_007_199_254_740_992.0)
    }

    /// getrandbits(k) for k up to 64: the words drawn lowest first, as Python fills an int.
    pub fn getrandbits(&mut self, k: u32) -> u64 {
        match k {
            0 => 0,
            1..=32 => (self.next_u32() >> (32 - k)) as u64,
            _ => {
                let low = self.next_u32() as u64;
                let high = (self.next_u32() >> (64 - k.min(64))) as u64;
                low | (high << 32)
            }
        }
    }

    /// _randbelow(n) for n above 0: getrandbits of n's length until one is below n.
    pub fn randbelow(&mut self, n: u64) -> u64 {
        let k = 64 - n.leading_zeros();
        loop {
            let r = self.getrandbits(k);
            if r < n {
                return r;
            }
        }
    }

    /// randrange(stop): None where Python raises ValueError (an empty range).
    pub fn randrange(&mut self, stop: i64) -> Option<i64> {
        (stop > 0).then(|| self.randbelow(stop as u64) as i64)
    }

    /// uniform(a, b): a + (b - a) * random().
    pub fn uniform(&mut self, a: f64, b: f64) -> f64 {
        a + (b - a) * self.random()
    }
}

#[cfg(test)]
mod tests {
    use super::Random;

    #[test]
    fn the_numbers_python_draws() {
        // (Python 3.12: random.Random(0).random(), random.Random(42).getrandbits(32))
        assert_eq!(
            Random::new(0).random().to_bits(),
            0.8444218515250481f64.to_bits()
        );
        assert_eq!(Random::new(42).getrandbits(32), 2_746_317_213);
        assert_eq!(Random::new(-42).getrandbits(32), 2_746_317_213);
        assert_eq!(Random::new((1 << 40) + 7).getrandbits(40), 822_974_591_194);
        assert_eq!(Random::new(0x5EED).randrange(256), Some(72));
        assert_eq!(Random::new(1).randrange(0), None);
    }
}
