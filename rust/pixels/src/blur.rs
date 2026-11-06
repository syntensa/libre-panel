//! ImageFilter.GaussianBlur and BoxBlur (libImaging/BoxBlur.c): box blurs over the rows, then
//! (BoxBlur.c transposes, blurs the rows and transposes back) over the columns, each band on its
//! own, straight (Image.filter gives a MultibandFilter every band as it is, RGBA too).

use crate::{error, Image, Result};

/// BoxBlur.c's ImagingLineBoxBlur8 on one line of ``n`` values ``stride`` apart from ``at``
/// (a row's band, or a column's), the result in ``out`` (its i-th value at out[i]).
#[allow(clippy::too_many_arguments)]
fn line_blur(
    data: &[u8],
    at: usize,
    stride: usize,
    n: usize,
    radius: usize,
    ww: u32,
    fw: u32,
    out: &mut [u8],
) {
    let px = |i: usize| u32::from(data[at + i * stride]);
    let lastx = n - 1;
    let edge_a = (radius + 1).min(n);
    let edge_b = n.saturating_sub(radius + 1);
    // the sum for pixel -1: the first pixel radius + 1 times, then up to edgeA - 1, then the
    // last one for the rest (where the radius is longer than the line)
    let mut acc: u32 = px(0).wrapping_mul(radius as u32 + 1);
    for x in 0..edge_a.saturating_sub(1) {
        acc = acc.wrapping_add(px(x));
    }
    acc = acc.wrapping_add(px(lastx).wrapping_mul((radius + 1 - edge_a) as u32));
    let mut step = |x: usize, sub: usize, add: usize, left: usize, right: usize, acc: &mut u32| {
        *acc = acc.wrapping_add(px(add)).wrapping_sub(px(sub));
        let bulk = acc
            .wrapping_mul(ww)
            .wrapping_add((px(left) + px(right)).wrapping_mul(fw));
        out[x] = (bulk.wrapping_add(1 << 23) >> 24) as u8;
    };
    if edge_a <= edge_b {
        for x in 0..edge_a {
            step(x, 0, x + radius, 0, x + radius + 1, &mut acc);
        }
        for x in edge_a..edge_b {
            step(
                x,
                x - radius - 1,
                x + radius,
                x - radius - 1,
                x + radius + 1,
                &mut acc,
            );
        }
        for x in edge_b..=lastx {
            step(x, x - radius - 1, lastx, x - radius - 1, lastx, &mut acc);
        }
    } else {
        for x in 0..edge_b {
            step(x, 0, x + radius, 0, x + radius + 1, &mut acc);
        }
        for x in edge_b..edge_a {
            step(x, 0, lastx, 0, lastx, &mut acc);
        }
        for x in edge_a..=lastx {
            step(x, x - radius - 1, lastx, x - radius - 1, lastx, &mut acc);
        }
    }
}

/// The weights of a box of ``float_radius`` (ImagingHorizontalBoxBlur: ww from a float
/// division, fw what is left of 2^24 for the two pixels at the box's ends).
fn weights(float_radius: f32) -> (usize, u32, u32) {
    let radius = float_radius as usize;
    let ww = ((1u32 << 24) as f32 / (float_radius * 2.0 + 1.0)) as u32;
    let fw = ((1u32 << 24).wrapping_sub((radius as u32 * 2 + 1).wrapping_mul(ww))) / 2;
    (radius, ww, fw)
}

/// One pass along the rows (``rows``) or the columns, every band.
fn pass(im: &mut Image, float_radius: f32, rows: bool) {
    let (radius, ww, fw) = weights(float_radius);
    let b = im.bands();
    let (w, h) = (im.width, im.height);
    let (lines, n) = if rows { (h, w) } else { (w, h) };
    let mut out = vec![0u8; n];
    for line in 0..lines {
        for band in 0..b {
            let (at, stride) = if rows {
                (line * w * b + band, b)
            } else {
                (line * b + band, w * b)
            };
            line_blur(&im.data, at, stride, n, radius, ww, fw, &mut out);
            for (i, &v) in out.iter().enumerate() {
                im.data[at + i * stride] = v;
            }
        }
    }
}

/// _gaussian_blur_radius: the box of ``passes`` boxes that make a Gaussian of ``radius``, in
/// floats as BoxBlur.c computes it (the square root in doubles).
fn gaussian_box(radius: f32, passes: u32) -> f32 {
    let sigma2 = radius * radius / passes as f32;
    let big_l = (12.0 * f64::from(sigma2) + 1.0).sqrt() as f32;
    let l = ((f64::from(big_l) - 1.0) / 2.0).floor() as f32;
    let mut a = (2.0 * l + 1.0) * (l * (l + 1.0) - 3.0 * sigma2);
    a /= 6.0 * (sigma2 - (l + 1.0) * (l + 1.0));
    l + a
}

impl Image {
    /// ImageFilter.BoxBlur((xradius, yradius)) with ``passes`` passes (Pillow's: 1).
    pub fn box_blur(&self, xradius: f32, yradius: f32, passes: u32) -> Result<Image> {
        let mut out = self.clone();
        if self.width == 0 || self.height == 0 {
            return Ok(out);
        }
        if passes < 1 {
            return error("number of passes must be greater than zero");
        }
        if xradius < 0.0 || yradius < 0.0 {
            return error("radius must be >= 0");
        }
        if xradius != 0.0 {
            for _ in 0..passes {
                pass(&mut out, xradius, true);
            }
        }
        if yradius != 0.0 {
            for _ in 0..passes {
                pass(&mut out, yradius, false);
            }
        }
        Ok(out)
    }

    /// ImageFilter.GaussianBlur((xradius, yradius)): three boxes as the Gaussian's.
    pub fn gaussian_blur(&self, xradius: f32, yradius: f32) -> Result<Image> {
        if xradius == 0.0 && yradius == 0.0 {
            return Ok(self.clone()); // (ImageFilter returns a copy)
        }
        self.box_blur(gaussian_box(xradius, 3), gaussian_box(yradius, 3), 3)
    }
}
