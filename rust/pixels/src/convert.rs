//! Image.convert(mode) between L, RGB, RGBA and RGBa, as libImaging/Convert.c converts (its
//! table's functions named below), with Image.py's way round where Convert.c has none: through
//! the mode's base (RGBa to L goes through RGB).

use crate::ints::{muldiv255, unmultiplied};
use crate::{error, Image, Mode, Result};

/// L24 >> 16: the grey of a colour, ITU-R 601-2 (rgb2l).
#[inline(always)]
fn grey(r: u8, g: u8, b: u8) -> u8 {
    ((u32::from(r) * 19595 + u32::from(g) * 38470 + u32::from(b) * 7471 + 0x8000) >> 16) as u8
}

impl Image {
    pub fn convert(&self, mode: Mode) -> Result<Image> {
        use Mode::*;
        let n = self.width * self.height;
        let src = &self.data;
        let mut out = Vec::with_capacity(n * mode.bands());
        match (self.mode, mode) {
            (a, b) if a == b => return Ok(self.clone()),
            (L, RGB) => {
                for &v in src {
                    out.extend_from_slice(&[v, v, v]);
                }
            }
            (L, RGBA) => {
                for &v in src {
                    out.extend_from_slice(&[v, v, v, 255]);
                }
            }
            (RGB, L) => {
                for p in src.chunks_exact(3) {
                    out.push(grey(p[0], p[1], p[2]));
                }
            }
            (RGB, RGBA) | (RGB, RGBa) => {
                for p in src.chunks_exact(3) {
                    out.extend_from_slice(&[p[0], p[1], p[2], 255]);
                }
            }
            (RGBA, L) => {
                for p in src.chunks_exact(4) {
                    out.push(grey(p[0], p[1], p[2]));
                }
            }
            (RGBA, RGB) => {
                for p in src.chunks_exact(4) {
                    out.extend_from_slice(&[p[0], p[1], p[2]]);
                }
            }
            (RGBA, RGBa) => {
                // rgbA2rgba (sic): each colour band times its alpha, rounded
                for p in src.chunks_exact(4) {
                    let a = u32::from(p[3]);
                    out.extend_from_slice(&[
                        muldiv255(u32::from(p[0]), a) as u8,
                        muldiv255(u32::from(p[1]), a) as u8,
                        muldiv255(u32::from(p[2]), a) as u8,
                        p[3],
                    ]);
                }
            }
            (RGBa, RGBA) => {
                // rgba2rgbA (sic): each premultiplied colour band back to the plain one
                out.resize(n * 4, 0);
                for (o, p) in out.chunks_exact_mut(4).zip(src.chunks_exact(4)) {
                    let a = p[3];
                    o[0] = unmultiplied(p[0], a);
                    o[1] = unmultiplied(p[1], a);
                    o[2] = unmultiplied(p[2], a);
                    o[3] = a;
                }
            }
            (RGBa, RGB) => {
                // rgba2rgb_: back to plain colours, the alpha dropped
                for p in src.chunks_exact(4) {
                    let a = p[3];
                    out.extend_from_slice(&[
                        unmultiplied(p[0], a),
                        unmultiplied(p[1], a),
                        unmultiplied(p[2], a),
                    ]);
                }
            }
            // Convert.c has none: Image.convert goes through the base mode (RGB)
            (RGBa, L) => return self.convert(RGB)?.convert(L),
            (from, to) => return error(format!("conversion from {from} to {to} not supported")),
        }
        Ok(Image {
            mode,
            width: self.width,
            height: self.height,
            data: out,
        })
    }
}
