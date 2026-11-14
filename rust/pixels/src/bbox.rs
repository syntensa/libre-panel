//! Image.getbbox(): the box round what is not zero (libImaging/GetBBox.c).

use crate::{Image, Mode};

/// Where the first pixel of `bands` bytes with anything in `row` lies, and the last; reading
/// only the byte `at` of each where given (an alpha), else every one.
fn ends(row: &[u8], bands: usize, at: Option<usize>) -> Option<(usize, usize)> {
    match at {
        // (four pixels' alphas at a time where they are all zero)
        Some(3) if bands == 4 => alpha_ends(row),
        Some(at) => {
            let mut pixels = row.chunks_exact(bands);
            let first = pixels.position(|p| p[at] != 0)?;
            let last = row.chunks_exact(bands).rposition(|p| p[at] != 0)?;
            Some((first, last))
        }
        None => {
            let first = row.iter().position(|&v| v != 0)?;
            let last = row.iter().rposition(|&v| v != 0)?;
            Some((first / bands, last / bands))
        }
    }
}

/// ends of RGBA pixels by their alpha: groups of four pixels whose alphas are all zero passed
/// over at once, from either end.
fn alpha_ends(row: &[u8]) -> Option<(usize, usize)> {
    const ALPHAS: u128 = 0xFF00_0000_FF00_0000_FF00_0000_FF00_0000;
    let n = row.len() / 4;
    let groups = n / 4;
    let group = |g: usize| {
        let bytes: [u8; 16] = row[g * 16..g * 16 + 16].try_into().unwrap_or([0; 16]);
        u128::from_le_bytes(bytes) & ALPHAS != 0
    };
    let lit = |i: usize| row[i * 4 + 3] != 0;
    let from = (0..groups)
        .find(|&g| group(g))
        .map_or(groups * 4, |g| g * 4);
    let first = (from..n).find(|&i| lit(i))?;
    let last = match (groups * 4..n).rev().find(|&i| lit(i)) {
        Some(last) => last,
        None => {
            let g = (0..groups).rev().find(|&g| group(g))?;
            (g * 4..g * 4 + 4).rev().find(|&i| lit(i))?
        }
    };
    Some((first, last))
}

impl Image {
    /// The bounds (left, top, right, bottom) of the pixels with anything in them: in RGBA and
    /// RGBa their alpha alone where ``alpha_only`` (Image.py's default), else any band; None
    /// where there is nothing. (Each row read from either end up to its first such pixel.)
    pub fn getbbox(&self, alpha_only: bool) -> Option<(usize, usize, usize, usize)> {
        let bands = self.bands();
        let at = match self.mode {
            Mode::RGBA | Mode::RGBa if alpha_only => Some(3),
            _ => None,
        };
        let (mut x0, mut y0, mut x1, mut y1) = (usize::MAX, usize::MAX, 0, 0);
        for y in 0..self.height {
            let row = &self.row(y)[..self.width * bands];
            if let Some((first, last)) = ends(row, bands, at) {
                x0 = x0.min(first);
                x1 = x1.max(last + 1);
                y0 = y0.min(y);
                y1 = y + 1;
            }
        }
        (y0 != usize::MAX).then_some((x0, y0, x1, y1))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The box as found pixel by pixel.
    fn every_pixel(im: &Image, alpha_only: bool) -> Option<(usize, usize, usize, usize)> {
        let bands = im.bands();
        let (mut x0, mut y0, mut x1, mut y1) = (usize::MAX, usize::MAX, 0, 0);
        for y in 0..im.height {
            for x in 0..im.width {
                let p = &im.row(y)[x * bands..(x + 1) * bands];
                let any = match im.mode {
                    Mode::RGBA | Mode::RGBa if alpha_only => p[3] != 0,
                    _ => p.iter().any(|&v| v != 0),
                };
                if any {
                    x0 = x0.min(x);
                    x1 = x1.max(x + 1);
                    y0 = y0.min(y);
                    y1 = y + 1;
                }
            }
        }
        (y0 != usize::MAX).then_some((x0, y0, x1, y1))
    }

    #[test]
    fn the_box_is_the_one_found_pixel_by_pixel() {
        let mut seed = 0x2545_f491_u64;
        let mut next = |n: u64| {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            seed % n
        };
        for mode in [Mode::RGBA, Mode::RGBa, Mode::RGB, Mode::L] {
            for _ in 0..200 {
                // (wide enough for groups of four pixels and the ones after them)
                let (w, h) = (1 + next(70) as usize, 1 + next(13) as usize);
                let mut data = vec![0u8; w * h * mode.bands()];
                for _ in 0..next(5) {
                    let i = next(data.len() as u64) as usize;
                    data[i] = 1 + next(255) as u8;
                }
                let im = Image::from_bytes(mode, w, h, data).unwrap();
                for alpha_only in [true, false] {
                    assert_eq!(im.getbbox(alpha_only), every_pixel(&im, alpha_only));
                }
            }
        }
    }
}
