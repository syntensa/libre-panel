//! Image.getbbox(): the box round what is not zero (libImaging/GetBBox.c).

use crate::{Image, Mode};

impl Image {
    /// The bounds (left, top, right, bottom) of the pixels with anything in them: in RGBA and
    /// RGBa their alpha alone where ``alpha_only`` (Image.py's default), else any band; None
    /// where there is nothing.
    pub fn getbbox(&self, alpha_only: bool) -> Option<(usize, usize, usize, usize)> {
        let bands = self.bands();
        let any = |p: &[u8]| -> bool {
            match self.mode {
                Mode::RGBA | Mode::RGBa if alpha_only => p[3] != 0,
                _ => p.iter().any(|&v| v != 0),
            }
        };
        let (mut x0, mut y0, mut x1, mut y1) = (usize::MAX, usize::MAX, 0, 0);
        for y in 0..self.height {
            let row = self.row(y);
            for x in 0..self.width {
                if any(&row[x * bands..(x + 1) * bands]) {
                    x0 = x0.min(x);
                    x1 = x1.max(x + 1);
                    y0 = y0.min(y);
                    y1 = y + 1;
                }
            }
        }
        (y0 != usize::MAX).then_some((x0, y0, x1, y1))
    }
}
