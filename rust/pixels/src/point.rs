//! Image.point(lut): each band through its table (libImaging/Point.c; _imaging.c clips each
//! entry to 0..255, Image.py rounds them first, as Python's round does).

use crate::ints::clip8;
use crate::{error, Image, Result};

impl Image {
    /// ``lut`` holds 256 entries a band, band after band (Image.py makes that of a function:
    /// its 256 values once for each band).
    pub fn point(&self, lut: &[i64]) -> Result<Image> {
        let bands = self.bands();
        if lut.len() != 256 * bands {
            return error("wrong number of lut entries");
        }
        let table: Vec<u8> = lut.iter().map(|&v| clip8(v)).collect();
        let mut out = self.clone();
        for p in out.data.chunks_exact_mut(bands) {
            for (b, v) in p.iter_mut().enumerate() {
                *v = table[b * 256 + usize::from(*v)];
            }
        }
        Ok(out)
    }

    /// Image.point(function): the function's value at each of 0..255 (rounded as Python's
    /// round rounds: half to even) for every band.
    pub fn point_with(&self, f: impl Fn(u8) -> f64) -> Result<Image> {
        let once: Vec<i64> = (0..=255u8).map(|i| round_half_even(f(i))).collect();
        let lut: Vec<i64> = once
            .iter()
            .copied()
            .cycle()
            .take(256 * self.bands())
            .collect();
        self.point(&lut)
    }
}

/// Python's round(x) to a whole number: halves go to the even neighbour.
pub fn round_half_even(x: f64) -> i64 {
    lp_py::round(x) as i64
}
