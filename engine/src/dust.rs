//! The dust that falls into the depth behind the panel (render/dust.py), laid by the engine
//! over the frame it composes: Libre Panel moves the grains and works out what they cover (its
//! taps: a pixel, the colour it brings, how much of the pixel it covers), and the engine lays
//! them as Dust._lay does in numpy, to the bit. Each tap blends the pixel towards what it
//! brings by how much it covers, as much of it as shows over the pixel's brightness (a bright
//! element hides the dust, as if it lay behind); all of them read the pixels as they were
//! composed, and where taps meet in a pixel the last one is what it shows (numpy's gather,
//! blend and scatter).

/// The most taps a layer of dust takes (some 24,000 on the 9.2" in a move, 8,000 grains in
/// all at most: each a few hundred taps where it is near).
pub const MOST_TAPS: usize = 1 << 22;

/// One pixel a grain covers: its place in the frame (y * width + x), what it brings to each
/// channel and how much of the pixel it covers (a near grain's disc: its colour comes over it;
/// a fine grain only adds, covering 0).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Tap {
    pub at: u32,
    pub add: [f32; 3],
    pub cover: f32,
}

/// A tap as it is laid: where on the frame (x, y), what it brings, how much it covers.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Laid {
    x: u16,
    y: u16,
    add: [f32; 3],
    cover: f32,
}

/// A frame's dust: its taps by the block they lie in, and how much of a grain shows over a
/// pixel of each brightness (its brightest channel).
#[derive(Clone, Debug)]
pub struct Dust {
    shown: [f32; 256],
    /// The taps, block by block (row of blocks by row, then along it), in the order they came
    /// within each block.
    taps: Vec<Laid>,
    /// Of each block, where its taps begin in `taps` (one more: where the last ones end).
    start: Vec<u32>,
    /// The box round all the taps (x0, y0, x1, y1 on the frame), empty for none.
    pub part: (usize, usize, usize, usize),
}

impl Dust {
    /// The dust of `taps` over a frame of `width` x `height` composed in blocks of `block`.
    pub fn new(
        shown: [f32; 256],
        taps: &[Tap],
        (width, height): (usize, usize),
        block: usize,
    ) -> Result<Dust, String> {
        if taps.len() > MOST_TAPS {
            return Err(format!("dust of {} taps", taps.len()));
        }
        let cells = width * height;
        if let Some(t) = taps.iter().find(|t| t.at as usize >= cells) {
            return Err(format!(
                "dust at {}, off the frame of {width}x{height}",
                t.at
            ));
        }
        if width > usize::from(u16::MAX) || height > usize::from(u16::MAX) {
            return Err(format!("dust over a frame of {width}x{height}"));
        }
        let (cols, rows) = (width.div_ceil(block), height.div_ceil(block));
        // each tap's place (worked out once) and block: counted by block, then laid out block
        // by block in the order they came
        let placed: Vec<(u32, Laid)> = taps
            .iter()
            .map(|t| {
                let (y, x) = (t.at as usize / width, t.at as usize % width);
                let k = (y / block) * cols + x / block;
                let (x, y) = (x as u16, y as u16);
                let laid = Laid {
                    x,
                    y,
                    add: t.add,
                    cover: t.cover,
                };
                (k as u32, laid)
            })
            .collect();
        let mut start = vec![0u32; cols * rows + 1];
        for (k, _) in &placed {
            start[*k as usize + 1] += 1;
        }
        let mut sum = 0;
        for s in start.iter_mut() {
            sum += *s;
            *s = sum;
        }
        let mut next = start.clone();
        let none = Laid {
            x: 0,
            y: 0,
            add: [0.0; 3],
            cover: 0.0,
        };
        let mut sorted = vec![none; taps.len()];
        let (mut x0, mut y0, mut x1, mut y1) = (width, height, 0, 0);
        for (k, laid) in &placed {
            let k = *k as usize;
            sorted[next[k] as usize] = *laid;
            next[k] += 1;
            let (x, y) = (usize::from(laid.x), usize::from(laid.y));
            (x0, y0, x1, y1) = (x0.min(x), y0.min(y), x1.max(x + 1), y1.max(y + 1));
        }
        let part = if taps.is_empty() {
            (0, 0, 0, 0)
        } else {
            (x0, y0, x1, y1)
        };
        Ok(Dust {
            shown,
            taps: sorted,
            start,
            part,
        })
    }

    /// Whether any of its taps lie in block `k`.
    pub fn in_block(&self, k: usize) -> bool {
        self.start[k] < self.start[k + 1]
    }

    /// The blocks its taps lie in.
    pub fn blocks(&self) -> impl Iterator<Item = usize> + '_ {
        (0..self.start.len() - 1).filter(|&k| self.in_block(k))
    }

    /// Its taps in block `k` laid on `pixels` (three bytes each, row by row), the pixels of
    /// the box from (x0, y0), `w` wide: the last tap of a pixel is what it shows, worked out
    /// from the pixel as it lay (the ones before it are left out).
    pub fn lay(&self, k: usize, pixels: &mut [u8], (x0, y0, w): (usize, usize, usize)) {
        let taps = &self.taps[self.start[k] as usize..self.start[k + 1] as usize];
        let h = pixels.len() / (w * 3);
        let mut small = [0u64; 4];
        let mut large = Vec::new();
        if w * h > 256 {
            large = vec![false; w * h];
        }
        for t in taps.iter().rev() {
            let (x, y) = (usize::from(t.x), usize::from(t.y));
            let i = (y - y0) * w + (x - x0);
            if w * h > 256 {
                if large[i] {
                    continue;
                }
                large[i] = true;
            } else {
                let bit = 1u64 << (i % 64);
                if small[i / 64] & bit != 0 {
                    continue;
                }
                small[i / 64] |= bit;
            }
            let p = &mut pixels[i * 3..i * 3 + 3];
            let shown = self.shown[p[0].max(p[1]).max(p[2]) as usize];
            for (c, add) in p.iter_mut().zip(t.add) {
                // (as numpy works it out: body * cover, add minus that, times shown, plus body)
                let body = f32::from(*c);
                let lit = (add - body * t.cover) * shown + body;
                *c = lit as u8;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// dust.py's _SHOWN: a grain shows fully over a pixel 60 bright or less, not at all over
    /// one 150 bright or more.
    fn shown() -> [f32; 256] {
        std::array::from_fn(|i| ((150.0f32 - i as f32) / 90.0f32).clamp(0.0, 1.0))
    }

    fn tap(at: u32, add: [f32; 3], cover: f32) -> Tap {
        Tap { at, add, cover }
    }

    #[test]
    fn a_tap_blends_its_pixel_as_numpy_works_it_out() {
        let (w, h) = (8, 4);
        let dust = Dust::new(
            shown(),
            &[
                tap(9, [30.5, 0.0, 195.0], 0.0),
                tap(10, [10.0, 20.0, 30.0], 0.25),
            ],
            (w, h),
            8,
        )
        .unwrap();
        assert_eq!(dust.part, (1, 1, 3, 2));
        let mut pixels = [40u8, 50, 60].repeat(w * h);
        pixels[10 * 3..10 * 3 + 3].copy_from_slice(&[200, 10, 10]); // too bright: hidden
        dust.lay(0, &mut pixels, (0, 0, w));
        // shown fully over 60 bright: body + add (truncated, as astype(uint8))
        assert_eq!(&pixels[9 * 3..9 * 3 + 3], &[70, 50, 255]);
        assert_eq!(&pixels[10 * 3..10 * 3 + 3], &[200, 10, 10]);
        // in between, by the brightness: (add - body * cover) * shown + body
        let mut one = [100u8, 90, 80];
        let d = Dust::new(shown(), &[tap(0, [10.0, 20.0, 30.0], 0.25)], (1, 1), 8).unwrap();
        d.lay(0, &mut one, (0, 0, 1));
        let s = (150.0f32 - 100.0) / 90.0;
        let want = |body: f32, add: f32| ((add - body * 0.25) * s + body) as u8;
        assert_eq!(one, [want(100.0, 10.0), want(90.0, 20.0), want(80.0, 30.0)]);
    }

    #[test]
    fn where_taps_meet_the_last_shows_worked_out_from_the_pixel_as_it_lay() {
        let first = tap(3, [100.0, 100.0, 100.0], 0.0);
        let last = tap(3, [1.0, 2.0, 3.0], 0.0);
        let dust = Dust::new(shown(), &[first, tap(4, [5.0; 3], 0.0), last], (4, 2), 8).unwrap();
        let mut pixels = vec![10u8; 4 * 2 * 3];
        dust.lay(0, &mut pixels, (0, 0, 4));
        assert_eq!(&pixels[9..12], &[11, 12, 13]); // not 110 + 1
        assert_eq!(&pixels[12..15], &[15, 15, 15]);
    }

    #[test]
    fn taps_go_by_block_and_lie_in_the_box_of_their_block() {
        // a frame of 20 x 10 in blocks of 8: 3 x 2 blocks
        let (w, h) = (20, 10);
        let at = |x: u32, y: u32| y * w as u32 + x;
        let taps = [
            tap(at(19, 9), [9.0; 3], 0.0),
            tap(at(1, 1), [1.0; 3], 0.0),
            tap(at(9, 1), [2.0; 3], 0.0),
            tap(at(2, 1), [3.0; 3], 0.0),
        ];
        let dust = Dust::new(shown(), &taps, (w, h), 8).unwrap();
        assert_eq!(dust.blocks().collect::<Vec<_>>(), vec![0, 1, 5]);
        assert!(!dust.in_block(2) && !dust.in_block(3));
        assert_eq!(dust.part, (1, 1, 20, 10));
        // the last block (16..20, 8..10), as the box of its own pixels
        let mut pixels = vec![0u8; 4 * 2 * 3];
        dust.lay(5, &mut pixels, (16, 8, 4));
        assert_eq!(&pixels[(4 + 3) * 3..(4 + 3) * 3 + 3], &[9, 9, 9]);
        assert_eq!(pixels.iter().filter(|&&v| v != 0).count(), 3);
        // in a block larger than 16 x 16 too (of a frame 40 wide: its box 32 x 10)
        let wide = Dust::new(shown(), &[tap(9 * 40 + 19, [9.0; 3], 0.0)], (40, h), 32).unwrap();
        let mut box_ = vec![0u8; 32 * h * 3];
        wide.lay(0, &mut box_, (0, 0, 32));
        let i = (9 * 32 + 19) * 3;
        assert_eq!(&box_[i..i + 3], &[9, 9, 9]);
        assert_eq!(box_.iter().filter(|&&v| v != 0).count(), 3);
    }

    #[test]
    fn dust_off_the_frame_is_refused() {
        assert!(Dust::new(shown(), &[tap(200, [0.0; 3], 0.0)], (20, 10), 8).is_err());
        let none = Dust::new(shown(), &[], (20, 10), 8).unwrap();
        assert_eq!((none.part, none.blocks().count()), ((0, 0, 0, 0), 0));
    }
}
