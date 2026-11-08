//! What numpy computes otherwise than the C library, where the renderer's numbers go through it.

/// numpy's float32 exp (loops_exponent_log's simd_exp_FLOAT): every x86-64 numpy with AVX2 and
/// FMA3 or with AVX512F takes it, for a single number too, never the C library's expf. The
/// exponent's whole part rounded off by Cody-Waite in two steps (fused), what is left a ratio
/// of two Remez polynomials (fused, Horner), scaled by the power of two: a few units in the last
/// place off the true value at most, exactly as numpy is.
pub fn expf(x: f32) -> f32 {
    // (numpy's constants as the float32s they are, written as short as they read back)
    const XMAX: f32 = 88.722_84;
    const XMIN: f32 = -103.972_084;
    const LOG2E: f32 = std::f32::consts::LOG2_E;
    // (0x1.8p+23: added and taken away, a float32 rounds to its nearest whole number)
    const MAGIC: f32 = 12_582_912.0;
    const C1: f32 = -6.931_457_5e-1;
    const C2: f32 = -1.428_606_8e-6;
    const P: [f32; 6] = [
        1.0,
        7.257_665e-1,
        2.473_615_4e-1,
        5.114_512e-2,
        6.757_897e-3,
        5.082_763e-4,
    ];
    const Q: [f32; 3] = [1.0, -2.742_335_5e-1, 2.159_509_4e-2];
    if x.is_nan() {
        return f32::NAN;
    }
    if x >= XMAX {
        return f32::INFINITY;
    }
    if x <= XMIN {
        return 0.0;
    }
    let quadrant = (x * LOG2E + MAGIC) - MAGIC;
    let mut r = quadrant.mul_add(C1, x);
    r = quadrant.mul_add(C2, r);
    r = quadrant.mul_add(0.0, r);
    let mut num = P[5].mul_add(r, P[4]);
    for p in [P[3], P[2], P[1], P[0]] {
        num = num.mul_add(r, p);
    }
    let den = Q[2].mul_add(r, Q[1]).mul_add(r, Q[0]);
    let poly = num / den;
    // (scalef: poly times 2 to the quadrant, rounded once; the power of two exact in a double)
    let two = f64::from_bits(((quadrant as i64 + 1023) as u64) << 52);
    (f64::from(poly) * two) as f32
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exp_as_numpy_gives_it() {
        // (np.exp of these float32s, their bits: the last two where its first step, fused,
        // gives another number than unfused)
        for (x, bits) in [
            (1.0, 0x402D_F855),
            (-1.0, 0x3EBC_5AB1),
            (0.5, 0x3FD3_094C),
            (10.0, 0x46AC_14EF),
            (-50.0, 0x1B69_2BEB),
            (-87.0, 0x00B3_3687),
            (f32::from_bits(0x3EA0_9998), 0x3FAF_2910),
            (f32::from_bits(0x4071_E1D1), 0x422F_2910),
        ] {
            assert_eq!(expf(x).to_bits(), bits, "{x}");
        }
        assert_eq!(expf(0.0), 1.0);
        assert_eq!(expf(-0.0), 1.0);
        assert_eq!(expf(f32::INFINITY), f32::INFINITY);
        assert_eq!(expf(f32::NEG_INFINITY), 0.0);
        assert!(expf(f32::NAN).is_nan());
        assert_eq!(expf(88.722_84), f32::INFINITY);
        assert_eq!(expf(-103.972_084), 0.0);
    }
}
