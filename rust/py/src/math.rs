//! What Python's math module computes otherwise than the C library: hypot (CPython's own
//! vector_norm, since 3.8: the squares summed without loss, the root corrected once).

/// The product of two floats as the rounded product and what rounding lost (dl_mul).
fn dl_mul(x: f64, y: f64) -> (f64, f64) {
    let z = x * y;
    (z, x.mul_add(y, -z))
}

/// A sum as the rounded sum and what rounding lost, |a| >= |b| (dl_fast_sum).
fn dl_fast_sum(a: f64, b: f64) -> (f64, f64) {
    let x = a + b;
    (x, (a - x) + b)
}

/// frexp's exponent: x = m * 2^e with 0.5 <= m < 1 (x finite, not 0).
fn frexp_exponent(x: f64) -> i32 {
    let bits = x.to_bits();
    let biased = ((bits >> 52) & 0x7ff) as i32;
    if biased == 0 {
        // subnormal: as many more places as its leading zeros
        let fraction = bits & ((1 << 52) - 1);
        -1022 - (fraction.leading_zeros() as i32 - 12)
    } else {
        biased - 1022
    }
}

/// ldexp(1.0, k): 2 to the power of k exactly, the subnormal ones too (powi(-1024) would take
/// 1 / 2^1024, which is infinite, and give 0).
fn pow2(k: i32) -> f64 {
    if k >= -1022 {
        f64::from_bits(((k + 1023) as u64) << 52)
    } else {
        f64::from_bits(1u64 << (k + 1074))
    }
}

/// CPython's vector_norm of the coordinates' absolute values, `max` the largest.
fn vector_norm(vec: &mut [f64], max: f64, found_nan: bool) -> f64 {
    if max.is_infinite() {
        return max;
    }
    if found_nan {
        return f64::NAN;
    }
    if max == 0.0 || vec.len() <= 1 {
        return max;
    }
    let max_e = frexp_exponent(max);
    if max_e < -1023 {
        // (2^-max_e would overflow: subnormals made normal first)
        for v in vec.iter_mut() {
            *v /= f64::MIN_POSITIVE;
        }
        return f64::MIN_POSITIVE * vector_norm(vec, max / f64::MIN_POSITIVE, found_nan);
    }
    let scale = pow2(-max_e);
    let (mut csum, mut frac1, mut frac2) = (1.0f64, 0.0f64, 0.0f64);
    for &v in vec.iter() {
        let x = v * scale; // lossless
        let (hi, lo) = dl_mul(x, x); // lossless
        let (sum, lost) = dl_fast_sum(csum, hi); // lossless
        csum = sum;
        frac1 += lo;
        frac2 += lost;
    }
    let mut h = (csum - 1.0 + (frac1 + frac2)).sqrt();
    let (hi, lo) = dl_mul(-h, h);
    let (sum, lost) = dl_fast_sum(csum, hi);
    csum = sum;
    frac1 += lo;
    frac2 += lost;
    let x = csum - 1.0 + (frac1 + frac2);
    h += x / (2.0 * h); // the differential correction
    h / scale
}

/// math.hypot(x, y) as Python computes it.
pub fn hypot(x: f64, y: f64) -> f64 {
    let mut vec = [x.abs(), y.abs()];
    let found_nan = vec.iter().any(|v| v.is_nan());
    let mut max = 0.0f64;
    for &v in &vec {
        if v > max {
            max = v;
        }
    }
    vector_norm(&mut vec, max, found_nan)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hypot_of_python_s() {
        assert_eq!(hypot(3.0, 4.0), 5.0);
        assert_eq!(hypot(-0.0, 0.0), 0.0);
        assert!(hypot(f64::NAN, 1.0).is_nan() && hypot(f64::NAN, f64::INFINITY).is_infinite());
        assert_eq!(hypot(5e-324, 5e-324), 5e-324 * std::f64::consts::SQRT_2);
        assert_eq!(frexp_exponent(1.0), 1);
        assert_eq!(frexp_exponent(0.75), 0);
        assert_eq!(frexp_exponent(5e-324), -1073);
        assert_eq!(hypot(0.0, 1e308), 1e308);
        assert_eq!(pow2(-1024), 2f64.powi(-1000) * 2f64.powi(-24));
        assert_eq!(pow2(-1074), 5e-324);
    }
}
