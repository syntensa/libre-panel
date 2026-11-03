//! The panel's video frames as its encoder takes them: YUV 4:2:0, turned into the panel's
//! framebuffer, kept from frame to frame and made anew only in the blocks that changed, byte
//! for byte as devices/yuv.py makes them (Pillow's YCbCr, BT.601; the colours of each two by two
//! pixels their average; the panel's range).

use crate::tables::{CB_B, CB_G, CB_R, CR_B, CR_G, CR_R, LIMITED_C, LIMITED_Y, Y_B, Y_G, Y_R};

/// How the drawn frame is turned into the panel's framebuffer (yuv.py's ``rotate``).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Turn {
    /// As it is.
    None,
    /// Half a turn (180).
    Half,
    /// A quarter turn clockwise (270, ffmpeg's transpose=1): (x, y) -> (height - 1 - y, x).
    Quarter,
}

impl Turn {
    pub fn of(degrees: u32) -> Option<Turn> {
        match degrees {
            0 => Some(Turn::None),
            180 => Some(Turn::Half),
            270 => Some(Turn::Quarter),
            _ => None,
        }
    }
}

/// The blocks a frame of `width` x `height` is compared and converted in: 16 pixels, or 8
/// where the frame is not made of whole blocks of 16 (yuv.block_of).
pub fn block_of(width: usize, height: usize) -> Option<usize> {
    [16, 8]
        .into_iter()
        .find(|b| width.is_multiple_of(*b) && height.is_multiple_of(*b))
}

/// x264's macroblock flag for a block that stayed the same since the frame before.
pub const CONSTANT: u8 = 1;

pub struct Yuv {
    pub width: usize,
    pub height: usize,
    pub turn: Turn,
    pub full: bool,
    pub block: usize,
    /// The turned frame's size, as the encoder takes it.
    pub turned: (usize, usize),
    pub y: Vec<u8>,
    pub u: Vec<u8>,
    pub v: Vec<u8>,
    rgb: Vec<u8>,
    rows: usize,
    cols: usize,
    changed: Vec<bool>,
    whole: bool,
}

impl Yuv {
    pub fn new(width: usize, height: usize, turn: Turn, full: bool) -> Result<Yuv, String> {
        let block = block_of(width, height)
            .ok_or_else(|| format!("no blocks of 8 or 16 pixels make {width}x{height}"))?;
        let turned = if turn == Turn::Quarter {
            (height, width)
        } else {
            (width, height)
        };
        let (rows, cols) = (height / block, width / block);
        Ok(Yuv {
            width,
            height,
            turn,
            full,
            block,
            turned,
            y: vec![if full { 0 } else { 16 }; turned.0 * turned.1],
            u: vec![128; (turned.0 / 2) * (turned.1 / 2)],
            v: vec![128; (turned.0 / 2) * (turned.1 / 2)],
            rgb: vec![0; width * height * 3],
            rows,
            cols,
            changed: vec![false; rows * cols],
            whole: true,
        })
    }

    /// The frame as it was put, three bytes a pixel, row by row (before it is turned).
    pub fn rgb(&self) -> &[u8] {
        &self.rgb
    }

    /// The encoder's macroblocks across and down (the turned frame, in blocks of 16).
    pub fn macroblocks(&self) -> (usize, usize) {
        (self.turned.0.div_ceil(16), self.turned.1.div_ceil(16))
    }

    /// A part of the new frame that may have changed: the box (x0, y0)-(x1, y1) and its pixels,
    /// three bytes each, row by row. Where it differs from the frame before, its blocks are
    /// marked for :meth:`finish`.
    pub fn put(
        &mut self,
        x0: usize,
        y0: usize,
        x1: usize,
        y1: usize,
        pixels: &[u8],
    ) -> Result<(), String> {
        if x0 >= x1 || y0 >= y1 || x1 > self.width || y1 > self.height {
            return Err(format!("the box {x0},{y0},{x1},{y1} is not in the frame"));
        }
        let wide = x1 - x0;
        if pixels.len() != wide * (y1 - y0) * 3 {
            return Err("the box's pixels are not as many as it holds".into());
        }
        let block = self.block;
        for row in y0..y1 {
            let src = &pixels[(row - y0) * wide * 3..(row - y0 + 1) * wide * 3];
            let at = (row * self.width + x0) * 3;
            let dst = &mut self.rgb[at..at + wide * 3];
            if !self.whole {
                let line = (row / block) * self.cols;
                let mut x = x0;
                while x < x1 {
                    let end = ((x / block + 1) * block).min(x1);
                    let (a, b) = ((x - x0) * 3, (end - x0) * 3);
                    if src[a..b] != dst[a..b] {
                        self.changed[line + x / block] = true;
                    }
                    x = end;
                }
            }
            dst.copy_from_slice(src);
        }
        Ok(())
    }

    /// Make the YUV anew in the blocks that changed and say which of the encoder's macroblocks
    /// stayed the same (`mb`: CONSTANT, else 0). The number of blocks made anew.
    pub fn finish(&mut self, mb: &mut [u8]) -> usize {
        if self.whole {
            self.changed.iter_mut().for_each(|c| *c = true);
            self.whole = false;
        }
        mb.iter_mut().for_each(|m| *m = CONSTANT);
        let (mb_cols, _) = self.macroblocks();
        let mut count = 0;
        for r in 0..self.rows {
            for c in 0..self.cols {
                let index = r * self.cols + c;
                if !self.changed[index] {
                    continue;
                }
                self.changed[index] = false;
                count += 1;
                let (tr, tc) = self.convert(r, c);
                let n = self.block;
                for mr in (tr * n) / 16..=((tr + 1) * n - 1) / 16 {
                    for mc in (tc * n) / 16..=((tc + 1) * n - 1) / 16 {
                        mb[mr * mb_cols + mc] = 0;
                    }
                }
            }
        }
        count
    }

    /// Convert the block at (r, c) of the drawn frame and lay it, turned, into the planes:
    /// where it lies once turned (block row and column).
    fn convert(&mut self, r: usize, c: usize) -> (usize, usize) {
        let n = self.block;
        let half = n / 2;
        let mut light = [0u8; 256];
        let mut blue = [0i32; 256];
        let mut red = [0i32; 256];
        for i in 0..n {
            let at = ((r * n + i) * self.width + c * n) * 3;
            for j in 0..n {
                let (pr, pg, pb) = (
                    self.rgb[at + j * 3] as usize,
                    self.rgb[at + j * 3 + 1] as usize,
                    self.rgb[at + j * 3 + 2] as usize,
                );
                let y = (Y_R[pr] + Y_G[pg] + Y_B[pb]) >> 6;
                let cb = (((CB_R[pr] + CB_G[pg] + CB_B[pb]) >> 6) + 128) & 255;
                let cr = (((CR_R[pr] + CR_G[pg] + CR_B[pb]) >> 6) + 128) & 255;
                let y = (y & 255) as u8;
                light[i * n + j] = if self.full { y } else { LIMITED_Y[y as usize] };
                blue[i * n + j] = cb;
                red[i * n + j] = cr;
            }
        }
        let mut cb = [0u8; 64];
        let mut cr = [0u8; 64];
        for i in 0..half {
            for j in 0..half {
                let k = (2 * i) * n + 2 * j;
                let sum_b = blue[k] + blue[k + 1] + blue[k + n] + blue[k + n + 1];
                let sum_r = red[k] + red[k + 1] + red[k + n] + red[k + n + 1];
                let (b, r) = (((sum_b + 2) >> 2) as usize, ((sum_r + 2) >> 2) as usize);
                cb[i * half + j] = if self.full { b as u8 } else { LIMITED_C[b] };
                cr[i * half + j] = if self.full { r as u8 } else { LIMITED_C[r] };
            }
        }
        let (tr, tc) = match self.turn {
            Turn::None => (r, c),
            Turn::Half => (self.rows - 1 - r, self.cols - 1 - c),
            Turn::Quarter => (c, self.rows - 1 - r),
        };
        let turn = self.turn;
        let stride = self.turned.0;
        lay(&mut self.y, stride, tr * n, tc * n, n, &light, turn);
        lay(
            &mut self.u,
            stride / 2,
            tr * half,
            tc * half,
            half,
            &cb,
            turn,
        );
        lay(
            &mut self.v,
            stride / 2,
            tr * half,
            tc * half,
            half,
            &cr,
            turn,
        );
        (tr, tc)
    }
}

/// The n x n `block` (row by row) into `plane` at (`top`, `left`), turned as `turn` says.
fn lay(
    plane: &mut [u8],
    stride: usize,
    top: usize,
    left: usize,
    n: usize,
    block: &[u8],
    turn: Turn,
) {
    for a in 0..n {
        let row = &mut plane[(top + a) * stride + left..(top + a) * stride + left + n];
        for (b, out) in row.iter_mut().enumerate() {
            *out = match turn {
                Turn::None => block[a * n + b],
                Turn::Half => block[(n - 1 - a) * n + (n - 1 - b)],
                Turn::Quarter => block[(n - 1 - b) * n + a],
            };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_and_turned_size() {
        assert_eq!(block_of(1920, 720), Some(16));
        assert_eq!(block_of(1024, 600), Some(8));
        let yuv = Yuv::new(1920, 720, Turn::Quarter, false).unwrap();
        assert_eq!(yuv.turned, (720, 1920));
        assert_eq!(yuv.macroblocks(), (45, 120));
    }

    #[test]
    fn white_and_black_in_the_panels_range() {
        let mut yuv = Yuv::new(32, 32, Turn::None, false).unwrap();
        let white = vec![255u8; 32 * 32 * 3];
        yuv.put(0, 0, 32, 32, &white).unwrap();
        let mut mb = vec![0u8; 4];
        assert_eq!(yuv.finish(&mut mb), 4);
        assert!(yuv.y.iter().all(|&v| v == 235) && yuv.u.iter().all(|&v| v == 128));
        assert!(mb.iter().all(|&m| m == 0));
        // the same again: nothing changed, every macroblock constant
        yuv.put(0, 0, 32, 32, &white).unwrap();
        assert_eq!(yuv.finish(&mut mb), 0);
        assert!(mb.iter().all(|&m| m == CONSTANT));
    }
}
