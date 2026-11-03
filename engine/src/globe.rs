//! A globe that turns, its pictures made here (render/earth.py's Turning): its map lit as the
//! earth is goes to the engine as a source once a light (every minute or so), the air over it
//! as a source every two degrees of the turn, and for each step of the turn the engine makes
//! the globe's picture as Turning.picture_of makes it: each pixel of the disc the map's pixel
//! at its place on the earth, the rim's pixels their own alpha, the air laid over all of it
//! and your place's mark over that, each as Pillow's alpha_composite lays it.

use crate::ring::alpha_composite;

/// Where each pixel of a globe's disc finds itself on its map (wire.rs's kind 16).
pub struct Tables {
    size: usize,
    width: usize,
    /// the disc's pixels (y * size + x), and for each the map's column at step 0 and its row
    /// times the map's width
    inside: Vec<u32>,
    col: Vec<u32>,
    rowbase: Vec<u32>,
    /// the pixels at the disc's rim, and their alpha
    rim: Vec<u32>,
    rim_alpha: Vec<u8>,
}

impl Tables {
    pub fn new(
        size: usize,
        width: usize,
        (inside, col, rowbase): (Vec<u32>, Vec<u32>, Vec<u32>),
        (rim, rim_alpha): (Vec<u32>, Vec<u8>),
    ) -> Result<Tables, String> {
        if size == 0 || size > crate::scene::MOST_SIDE || width < 2 || !width.is_multiple_of(2) {
            return Err(format!("a globe of {size} px on a map {width} wide"));
        }
        let pixels = (size * size) as u64;
        let rows = (width / 2) as u64;
        let w = width as u64;
        if col.len() != inside.len()
            || rowbase.len() != inside.len()
            || rim_alpha.len() != rim.len()
        {
            return Err("a globe's tables are not of one length".into());
        }
        if inside.iter().chain(&rim).any(|&p| u64::from(p) >= pixels)
            || col.iter().any(|&c| u64::from(c) >= w)
            || rowbase
                .iter()
                .any(|&r| !u64::from(r).is_multiple_of(w) || u64::from(r) / w >= rows)
        {
            return Err("a globe's tables reach beyond it or its map".into());
        }
        Ok(Tables {
            size,
            width,
            inside,
            col,
            rowbase,
            rim,
            rim_alpha,
        })
    }

    pub fn size(&self) -> usize {
        self.size
    }

    /// The globe at `step` of its turn (RGBA, size x size): its disc from `map` (RGBA, width x
    /// width / 2), seen from the longitude of the step, the rim's alpha its own; then `air`
    /// (RGBA, size x size) laid over it, and `mark` (RGBA, w x h) at (x, y) over that.
    pub fn picture(
        &self,
        map: &[u8],
        step: usize,
        air: Option<&[u8]>,
        mark: Option<(&[u8], usize, usize, i64, i64)>,
    ) -> Result<Vec<u8>, String> {
        let (w, size) = (self.width, self.size);
        if map.len() != w * (w / 2) * 4 {
            return Err("a globe's map is not of its width".into());
        }
        let s = step % w;
        let mut out = vec![0u8; size * size * 4];
        for (k, &p) in self.inside.iter().enumerate() {
            // westwards: the ground goes east (Turning.frame's col + width - step)
            let mut c = self.col[k] as usize + w - s;
            if c >= w {
                c -= w;
            }
            let m = (self.rowbase[k] as usize + c) * 4;
            let p = p as usize * 4;
            out[p..p + 4].copy_from_slice(&map[m..m + 4]);
        }
        for (k, &p) in self.rim.iter().enumerate() {
            out[p as usize * 4 + 3] = self.rim_alpha[k];
        }
        if let Some(air) = air {
            if air.len() != out.len() {
                return Err("a globe's air is not of its size".into());
            }
            for (dst, src) in out.chunks_exact_mut(4).zip(air.chunks_exact(4)) {
                alpha_composite(dst, &[src[0], src[1], src[2], src[3]]);
            }
        }
        if let Some((pixels, mw, mh, x, y)) = mark {
            if pixels.len() != mw * mh * 4 {
                return Err("a globe's mark is not of its size".into());
            }
            for v in 0..mh {
                let oy = y + v as i64;
                if !(0..size as i64).contains(&oy) {
                    continue;
                }
                for u in 0..mw {
                    let ox = x + u as i64;
                    if !(0..size as i64).contains(&ox) {
                        continue;
                    }
                    let d = (oy as usize * size + ox as usize) * 4;
                    let m = (v * mw + u) * 4;
                    let src = [pixels[m], pixels[m + 1], pixels[m + 2], pixels[m + 3]];
                    alpha_composite(&mut out[d..d + 4], &src);
                }
            }
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tables(size: usize, width: usize) -> Tables {
        // a disc of the middle pixels, each looking at its own column of row 1
        let inside: Vec<u32> = (0..(size * size) as u32).filter(|p| p % 3 != 0).collect();
        let col = inside.iter().map(|&p| p % width as u32).collect();
        let rowbase = inside.iter().map(|_| width as u32).collect();
        let rim = vec![inside[0]];
        Tables::new(size, width, (inside, col, rowbase), (rim, vec![99])).unwrap()
    }

    #[test]
    fn a_globe_looks_its_map_up_turned_by_its_step_with_its_rim_air_and_mark() {
        let (size, width) = (4, 6);
        let t = tables(size, width);
        // the map: pixel (c, r) is [c, r, 7, 255]
        let map: Vec<u8> = (0..width / 2)
            .flat_map(|r| (0..width).flat_map(move |c| [c as u8, r as u8, 7, 255]))
            .collect();
        let plain = t.picture(&map, 2, None, None).unwrap();
        for p in 0..size * size {
            let got = &plain[p * 4..p * 4 + 4];
            if p % 3 == 0 {
                assert_eq!(got, &[0, 0, 0, 0], "{p}: outside");
                continue;
            }
            let c = (p % width + width - 2) % width;
            let alpha = if p == 1 { 99 } else { 255 };
            assert_eq!(got, &[c as u8, 1, 7, alpha], "{p}");
        }
        assert_eq!(t.picture(&map, 2 + width, None, None).unwrap(), plain); // (a whole turn)
                                                                            // the air over it, and the mark over that, as alpha_composite lays them
        let air = [200u8, 100, 50, 128].repeat(size * size);
        let mark = [0u8, 255, 0, 255].repeat(4);
        let laid = t
            .picture(&map, 2, Some(&air), Some((&mark, 2, 2, 3, 3)))
            .unwrap();
        let mut want = plain.clone();
        for p in want.chunks_exact_mut(4) {
            alpha_composite(p, &[200, 100, 50, 128]);
        }
        let corner = (3 * size + 3) * 4; // (the mark's one pixel on the globe)
        alpha_composite(&mut want[corner..corner + 4], &[0, 255, 0, 255]);
        assert_eq!(laid, want);
        assert!(t.picture(&map[4..], 0, None, None).is_err());
        assert!(t.picture(&map, 0, Some(&air[4..]), None).is_err());
    }

    #[test]
    fn tables_that_reach_beyond_the_globe_or_its_map_are_refused() {
        let ok = |inside: Vec<u32>, col: Vec<u32>, rowbase: Vec<u32>| {
            Tables::new(4, 6, (inside, col, rowbase), (vec![], vec![])).is_ok()
        };
        assert!(ok(vec![15], vec![5], vec![12]));
        assert!(!ok(vec![16], vec![0], vec![0])); // (beyond the globe)
        assert!(!ok(vec![0], vec![6], vec![0])); // (beyond the map's width)
        assert!(!ok(vec![0], vec![0], vec![18])); // (beyond its rows)
        assert!(!ok(vec![0], vec![0], vec![7])); // (not a row's start)
        assert!(!ok(vec![0, 1], vec![0], vec![0])); // (not of one length)
        assert!(Tables::new(4, 5, (vec![], vec![], vec![]), (vec![], vec![])).is_err());
    }
}
