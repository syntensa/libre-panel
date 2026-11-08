//! What numpy computes otherwise than the C library, where the renderer's numbers go through it.

/// numpy's float32 exp (loops_exponent_log's simd_exp_FLOAT): every x86-64 numpy with AVX2 and
/// FMA3 or with AVX512F takes it, for a single number too, never the C library's expf. The
/// exponent's whole part rounded off by Cody-Waite in two steps (fused), what is left a ratio
/// of two Remez polynomials (fused, Horner), scaled by the power of two: a few units in the last
/// place off the true value at most, exactly as numpy is. The whole part's product and sum as
/// numpy's builds have them: apart on Linux (GCC builds numpy's C as ISO C11, contracting
/// nothing), one fused step on Windows (MSVC with /fp:contract): 84 float32s come out apart.
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
    #[cfg(not(windows))]
    let quadrant = (x * LOG2E + MAGIC) - MAGIC;
    #[cfg(windows)]
    let quadrant = x.mul_add(LOG2E, MAGIC) - MAGIC;
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

/// numpy's float32 tanh (loops_hyperbolic's simd_tanh_f32, SVML's converted): every x86-64
/// numpy with FMA3 takes it, for a single number too. |x| falls in one of 32 intervals (by
/// its exponent and first mantissa bits), each with a polynomial of degree six in |x| less
/// the interval's start (fused, Horner); past the last, 1; the sign of x put back.
pub fn tanhf(x: f32) -> f32 {
    // (per interval: c6, c5, c4, c3, c2, c1, c0 and its start b, as numpy's lut8x32 has them)
    const LUT: [[u32; 8]; 32] = [
        [
            0xbc0e2f66, 0x3e0910e9, 0xb76dd6b9, 0xbeaaaaa5, 0xb0343c7b, 0x3f800000, 0x00000000,
            0x00000000,
        ],
        [
            0x460bda12, 0x43761143, 0xbe1c276d, 0xbeab0612, 0xbd6ee69d, 0x3f7f1f84, 0x3d6fb9c9,
            0x3d700000,
        ],
        [
            0x43d638ef, 0x4165ecdc, 0x3c1dcf2f, 0xbea7f01f, 0xbd8f0da7, 0x3f7ebd11, 0x3d8fc35f,
            0x3d900000,
        ],
        [
            0xc3e11c3e, 0xc190f756, 0x3dc1a78d, 0xbea4e120, 0xbdae477d, 0x3f7e1e5f, 0x3daf9169,
            0x3db00000,
        ],
        [
            0xc2baa4e9, 0xc08c097d, 0x3d96f985, 0xbea387b7, 0xbdcd2a1f, 0x3f7d609f, 0x3dcf49ab,
            0x3dd00000,
        ],
        [
            0xc249da2d, 0xc02ba813, 0x3da2b61b, 0xbea15962, 0xbdeba80d, 0x3f7c842d, 0x3deee849,
            0x3df00000,
        ],
        [
            0xc1859b82, 0xbf7f6bda, 0x3dc13397, 0xbe9d57f7, 0xbe0c443b, 0x3f7b00e5, 0x3e0f0ee8,
            0x3e100000,
        ],
        [
            0x40dd5b57, 0x3f2b1dc0, 0x3dd2f670, 0xbe976b5a, 0xbe293cf3, 0x3f789580, 0x3e2e4984,
            0x3e300000,
        ],
        [
            0x40494640, 0x3ece105d, 0x3df48a0a, 0xbe90230d, 0xbe44f282, 0x3f75b8ad, 0x3e4d2f8e,
            0x3e500000,
        ],
        [
            0x40c730a8, 0x3f426a94, 0x3e06c5a8, 0xbe880dff, 0xbe5f3651, 0x3f726fd9, 0x3e6bb32e,
            0x3e700000,
        ],
        [
            0xbf0f160e, 0xbadb0dc4, 0x3e1a3aba, 0xbe7479b3, 0xbe81c7c0, 0x3f6cc59b, 0x3e8c51cd,
            0x3e900000,
        ],
        [
            0x3e30e76f, 0x3da43b17, 0x3e27c405, 0xbe4c3d88, 0xbe96d7ca, 0x3f63fb92, 0x3ea96163,
            0x3eb00000,
        ],
        [
            0xbea81387, 0xbd51ab88, 0x3e2e78d0, 0xbe212482, 0xbea7fb8e, 0x3f59ff97, 0x3ec543f1,
            0x3ed00000,
        ],
        [
            0xbdb26a1c, 0xbcaea23d, 0x3e2c3e44, 0xbdeb8cba, 0xbeb50e9e, 0x3f4f11d7, 0x3edfd735,
            0x3ef00000,
        ],
        [
            0xbd351e57, 0xbd3b6d8d, 0x3e1d3097, 0xbd5e78ad, 0xbec12efe, 0x3f3d7573, 0x3f028438,
            0x3f100000,
        ],
        [
            0xbb4c01a0, 0xbd6caaad, 0x3df4a8f4, 0x3c6b5e6e, 0xbec4be92, 0x3f24f360, 0x3f18abf0,
            0x3f300000,
        ],
        [
            0x3c1d7bfb, 0xbd795bed, 0x3da38508, 0x3d839143, 0xbebce070, 0x3f0cbfe7, 0x3f2bc480,
            0x3f500000,
        ],
        [
            0x3c722cd1, 0xbd5fddda, 0x3d31416a, 0x3dc21ee1, 0xbead510e, 0x3eec1a69, 0x3f3bec1c,
            0x3f700000,
        ],
        [
            0x3c973f1c, 0xbd038f3b, 0x3b562657, 0x3de347af, 0xbe8ef7d6, 0x3eb0a801, 0x3f4f2e5b,
            0x3f900000,
        ],
        [
            0x3c33a31b, 0xbc1cad63, 0xbcaeeac9, 0x3dcbec96, 0xbe4b8704, 0x3e6753a2, 0x3f613c53,
            0x3fb00000,
        ],
        [
            0x3b862ef4, 0x3abb4766, 0xbcce9419, 0x3d99ef2d, 0xbe083237, 0x3e132f1a, 0x3f6ce37d,
            0x3fd00000,
        ],
        [
            0x3a27b3d0, 0x3b95f10b, 0xbcaaeac4, 0x3d542ea1, 0xbdaf7449, 0x3db7e7d3, 0x3f743c4f,
            0x3ff00000,
        ],
        [
            0xba3b5907, 0x3b825873, 0xbc49e7d0, 0x3cdde701, 0xbd2e1ec4, 0x3d320845, 0x3f7a5feb,
            0x40100000,
        ],
        [
            0xba0efc22, 0x3afaea66, 0xbba71ddd, 0x3c2cca67, 0xbc83bf06, 0x3c84d3d4, 0x3f7dea85,
            0x40300000,
        ],
        [
            0xb97f9f0f, 0x3a49f878, 0xbb003b0e, 0x3b81cb27, 0xbbc3e0b5, 0x3bc477b7, 0x3f7f3b3d,
            0x40500000,
        ],
        [
            0xb8c8af50, 0x39996bf3, 0xba3f9a05, 0x3ac073a1, 0xbb10aadc, 0x3b10d3da, 0x3f7fb78c,
            0x40700000,
        ],
        [
            0xb7bdddfb, 0x388f3e6c, 0xb92c08a7, 0x39ac3032, 0xba0157db, 0x3a01601e, 0x3f7fefd4,
            0x40900000,
        ],
        [
            0xb64f2950, 0x371bb0e3, 0xb7ba9232, 0x383a94d9, 0xb88c18f2, 0x388c1a3b, 0x3f7ffdd0,
            0x40b00000,
        ],
        [
            0xb4e085b1, 0x35a8a5e6, 0xb64a0b0f, 0x36ca081d, 0xb717b096, 0x3717b0da, 0x3f7fffb4,
            0x40d00000,
        ],
        [
            0xb3731dfa, 0x34369b17, 0xb4dac169, 0x355abd4c, 0xb5a43bae, 0x35a43bce, 0x3f7ffff6,
            0x40f00000,
        ],
        [
            0xb15a1f04, 0x322487b0, 0xb2ab78ac, 0x332b3cb6, 0xb383012c, 0x338306c6, 0x3f7fffff,
            0x41100000,
        ],
        [
            0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x00000000, 0x3f800000,
            0x00000000,
        ],
    ];
    if x.is_nan() {
        return f32::NAN;
    }
    let bits = x.to_bits();
    let ndnan = (bits & 0x7fe0_0000) as i32;
    let idx = ((ndnan - 0x3d40_0000).clamp(0, 0x03e0_0000) as u32 >> 21) as usize;
    let c = |i: usize| f32::from_bits(LUT[idx][i]);
    let y = x.abs() - c(7);
    let mut r = c(0).mul_add(y, c(1));
    for i in 2..7 {
        r = r.mul_add(y, c(i));
    }
    if ndnan > 0x7f00_0000 {
        r = 1.0; // (|x| past the last interval, or infinite)
    }
    f32::from_bits(r.to_bits() | (bits & 0x8000_0000))
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
