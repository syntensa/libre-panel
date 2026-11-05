//! A fan's pictures, made by the engine for each position, speed and load (stage 2 of
//! docs/ROADMAP.md, element by element): render/fan.py's rotor_image, disc_image, blades_image
//! and ring_image, to the bit. What a rotor is made of comes once for a look and a size (a
//! [`Rotor`]: its grey layers at each of its positions, as fan.py's rotor_layers has them); a
//! position in its colours, blurred into its disc and dimmed ([`Blades`]), and the ring with its
//! glowing arc ([`RingSpec`]) are made from that here, as they are asked for. So neither Libre
//! Panel nor the engine keeps a picture of every position: Lumen's fans held 7.5 MB of them in
//! Python, and the engine 64 positions of each beside.
//!
//! The numbers are worked out in single precision in the order numpy works them out there (each
//! of its steps rounded to single precision, none fused), a mean over the positions summed from
//! the first on, as numpy sums along an axis that is not the last; Pillow's steps (its blend,
//! its arc and ellipse, its reduce, its GaussianBlur, its alpha_composite) as Pillow takes them.

use crate::bar::{gaussian_one, reduce_one};
use crate::draw::Mask;
use crate::ring::alpha_composite;

/// How a rotor's grey layers make a colour (fan.py's AMBIENT, DIFFUSE, 1 - CORE, CORE and
/// GLOSS, in single precision as numpy takes them): the colour times (ambient + diffuse *
/// shape), darker at the blades' edges by the core layer (flat + core * core layer), the gloss
/// added white.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Shading {
    pub ambient: f32,
    pub diffuse: f32,
    pub flat: f32,
    pub core: f32,
    pub gloss: f32,
}

/// A rotor's colour and its second colour.
type Colours = ([u8; 3], [u8; 3]);

/// The most positions a rotor comes in, and the largest rotor (px).
const MOST_POSITIONS: usize = 256;
const MOST_SIDE: usize = 1024;

/// A rotor of a look at a size: `k` x `k` pixels, its grey layers (shape, core and gloss
/// times alpha, alpha, and where its second colour goes) at each of its positions, position
/// after position, layer after layer, row after row (fan.py's rotor_layers). The disc it blurs
/// into is made once a pair of colours, when a blurred position first asks for it.
pub struct Rotor {
    k: usize,
    positions: usize,
    layers: usize,
    planes: Vec<f32>,
    shading: Shading,
    discs: Vec<(Colours, Vec<u8>)>,
}

/// A rotor at a position in its colours (fan.py's blades_image): blurred into its disc by
/// `blend` (Image.blend's alpha, 0 for none), its alpha mapped through `dim` where it is
/// dimmed (the table Image.point takes).
pub struct Blades {
    pub phase: usize,
    pub colour: [u8; 3],
    pub second: [u8; 3],
    pub blend: f32,
    pub dim: Option<Vec<u8>>,
}

impl Rotor {
    pub fn new(
        k: usize,
        positions: usize,
        layers: usize,
        planes: Vec<f32>,
        shading: Shading,
    ) -> Result<Rotor, String> {
        if k == 0
            || k > MOST_SIDE
            || positions == 0
            || positions > MOST_POSITIONS
            || !(4..=5).contains(&layers)
            || planes.len() != positions * layers * k * k
        {
            return Err(format!(
                "a rotor of {k} px, {positions} positions of {layers} layers in {} numbers",
                planes.len()
            ));
        }
        Ok(Rotor {
            k,
            positions,
            layers,
            planes,
            shading,
            discs: Vec::new(),
        })
    }

    /// Its side (px).
    pub fn side(&self) -> usize {
        self.k
    }

    /// fan.py's _coloured of position `phase`: its grey layers as RGBA in `colour`, and in
    /// `second` where its fifth layer says so (where there is one, and the two differ).
    pub fn coloured(&self, phase: usize, colour: [u8; 3], second: [u8; 3]) -> Vec<u8> {
        let kk = self.k * self.k;
        let base = (phase % self.positions) * self.layers * kk;
        let plane = |i: usize| &self.planes[base + i * kk..base + (i + 1) * kk];
        let (shape, core, gloss, alpha) = (plane(0), plane(1), plane(2), plane(3));
        let part = (second != colour && self.layers > 4).then(|| plane(4));
        let main = colour.map(|v| f32::from(v) / 255.0);
        let other = second.map(|v| f32::from(v) / 255.0);
        let Shading {
            ambient,
            diffuse,
            flat,
            core: edge,
            gloss: shine,
        } = self.shading;
        let mut out = vec![0u8; kk * 4];
        for (i, px) in out.chunks_exact_mut(4).enumerate() {
            let a = alpha[i];
            let safe = a.max(1e-6);
            let shade = (shape[i] / safe).clamp(0.0, 1.0);
            let dark = (core[i] / safe).clamp(0.0, 1.0);
            let lit = (gloss[i] / safe).clamp(0.0, 1.0);
            let c = match part {
                Some(part) => {
                    let p = (part[i] / safe).clamp(0.0, 1.0);
                    [0, 1, 2].map(|j| main[j] * (1.0 - p) + other[j] * p)
                }
                None => main,
            };
            let (light, depth) = (ambient + diffuse * shade, flat + edge * dark);
            let rgb = c.map(|v| v * light * depth);
            // (the hue kept: a pastel does not wash out)
            let top = 1.0f32.max(rgb[0].max(rgb[1]).max(rgb[2]));
            for (o, v) in px.iter_mut().zip(rgb) {
                let v = (v / top + shine * lit).clamp(0.0, 1.0);
                *o = (v * 255.0 + 0.5).clamp(0.0, 255.0) as u8;
            }
            px[3] = (a.clamp(0.0, 1.0) * 255.0 + 0.5).clamp(0.0, 255.0) as u8;
        }
        out
    }

    /// fan.py's disc_image: the rotor blurred into a disc, the mean over all its positions,
    /// each coloured first; made once a pair of colours.
    fn disc(&mut self, colour: [u8; 3], second: [u8; 3]) -> &[u8] {
        let key = (colour, second);
        if let Some(at) = self.discs.iter().position(|(k, _)| *k == key) {
            return &self.discs[at].1;
        }
        let kk = self.k * self.k;
        let mut alpha = vec![0f32; kk];
        let mut sums = vec![0f32; kk * 3];
        for p in 0..self.positions {
            let rgba = self.coloured(p, colour, second);
            for (i, px) in rgba.chunks_exact(4).enumerate() {
                let a = f32::from(px[3]) / 255.0;
                // (numpy's mean along the positions: summed from the first one on)
                if p == 0 {
                    alpha[i] = a;
                } else {
                    alpha[i] += a;
                }
                for c in 0..3 {
                    let v = f32::from(px[c]) * a;
                    if p == 0 {
                        sums[i * 3 + c] = v;
                    } else {
                        sums[i * 3 + c] += v;
                    }
                }
            }
        }
        let n = self.positions as f32;
        let mut out = vec![0u8; kk * 4];
        for (i, px) in out.chunks_exact_mut(4).enumerate() {
            let mean = alpha[i] / n;
            for c in 0..3 {
                let v = (sums[i * 3 + c] / n) / mean.max(1e-6);
                px[c] = v.round_ties_even().clamp(0.0, 255.0) as u8;
            }
            px[3] = (mean * 255.0).round_ties_even().clamp(0.0, 255.0) as u8;
        }
        if self.discs.len() >= 8 {
            self.discs.remove(0);
        }
        self.discs.push((key, out));
        &self.discs.last().expect("the disc just made").1
    }

    /// fan.py's blades_image: the rotor at its position in its colours, blurred into its disc
    /// as Image.blend blends (single precision, cut to a whole level) and dimmed.
    pub fn blades(&mut self, b: &Blades) -> Vec<u8> {
        let mut out = self.coloured(b.phase, b.colour, b.second);
        if b.blend != 0.0 {
            let alpha = b.blend;
            let disc = self.disc(b.colour, b.second);
            for (o, &d) in out.iter_mut().zip(disc) {
                let from = i32::from(*o);
                *o = (from as f32 + alpha * (i32::from(d) - from) as f32) as u8;
            }
        }
        if let Some(table) = b.dim.as_ref().filter(|t| t.len() == 256) {
            for px in out.chunks_exact_mut(4) {
                px[3] = table[usize::from(px[3])];
            }
        }
        out
    }
}

/// How far a fan's glowing arc goes round: not at all, all the way (an ellipse), or from one
/// angle to another (degrees, as Pillow's arc takes them).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Arc {
    None,
    Whole,
    Part(f32, f32),
}

/// A fan's ring (fan.py's ring_image): on a canvas of `canvas` x `canvas`, its groove all round
/// in `colour` (`groove` of its coverage opaque), and its arc glowing over it: the coverage of a
/// ring `width` wide in `bounds`, drawn three times larger and reduced (fan.py's _ring_mask),
/// its core burning towards white (`white`), its halo blurred by `halo` and standing beside it
/// (`share`); `core`: the coverage's 256 levels to the power fan.py takes (worked out there, as
/// numpy works them out).
pub struct RingSpec {
    pub canvas: usize,
    pub colour: [u8; 3],
    pub bounds: (i32, i32, i32, i32),
    pub width: i32,
    pub arc: Arc,
    pub halo: f32,
    pub groove: f32,
    pub white: f32,
    pub share: f32,
    pub core: Vec<f32>,
}

/// How much larger a ring's coverage is drawn before it is reduced (fan.py's _ring_mask).
const LARGER: usize = 3;

/// The coverage of a ring (or of its arc), drawn larger and reduced as fan.py's _ring_mask
/// draws it.
fn coverage(spec: &RingSpec, arc: Arc) -> Vec<u8> {
    let g = spec.canvas * LARGER;
    let mut pixels = vec![0u8; g * g];
    let mut mask = Mask {
        pixels: &mut pixels,
        w: g,
        h: g,
    };
    match arc {
        Arc::None => {}
        Arc::Whole => mask.ellipse(spec.bounds, spec.width, 255),
        Arc::Part(start, end) => mask.arc(spec.bounds, start, end, spec.width, 255),
    }
    reduce_one(&pixels, g, g, LARGER).0
}

/// A fan's ring as fan.py's ring_image makes it (RGBA, `canvas` x `canvas`).
pub fn ring(spec: &RingSpec) -> Result<Vec<u8>, String> {
    if spec.canvas == 0 || spec.canvas > MOST_SIDE || spec.core.len() != 256 {
        return Err(format!(
            "a fan's ring on a canvas of {} px, {} levels",
            spec.canvas,
            spec.core.len()
        ));
    }
    let groove = coverage(spec, Arc::Whole);
    let mut out = vec![0u8; spec.canvas * spec.canvas * 4];
    for (px, &g) in out.chunks_exact_mut(4).zip(&groove) {
        px[..3].copy_from_slice(&spec.colour);
        let a = f32::from(g) / 255.0 * spec.groove * 255.0 + 0.5;
        px[3] = a.clamp(0.0, 255.0) as u8;
    }
    if spec.arc == Arc::None {
        return Ok(out);
    }
    let lit = coverage(spec, spec.arc);
    let mut soft = lit.clone();
    gaussian_one(&mut soft, spec.canvas, spec.canvas, spec.halo);
    let c = spec.colour.map(f32::from);
    for ((px, &l), &s) in out.chunks_exact_mut(4).zip(&lit).zip(&soft) {
        // fan.py's _glow: a core that burns towards white, in a halo
        let m = f32::from(l) / 255.0;
        let soft = f32::from(s) / 255.0;
        let core = spec.core[usize::from(l)];
        let towards = spec.white * core;
        let mut glow = [0u8; 4];
        for j in 0..3 {
            glow[j] = (c[j] + (255.0 - c[j]) * towards + 0.5).clamp(0.0, 255.0) as u8;
        }
        let alpha = (core + spec.share * (soft - m).clamp(0.0, 1.0)).clamp(0.0, 1.0);
        glow[3] = (alpha * 255.0 + 0.5).clamp(0.0, 255.0) as u8;
        alpha_composite(px, &glow);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHADING: Shading = Shading {
        ambient: 0.35,
        diffuse: 0.65,
        flat: 0.7,
        core: 0.3,
        gloss: 0.55,
    };

    fn rotor(k: usize, positions: usize) -> Rotor {
        let kk = k * k;
        let mut planes = Vec::with_capacity(positions * 5 * kk);
        for p in 0..positions {
            for layer in 0..5 {
                for i in 0..kk {
                    let alpha = if (i + p) % 7 == 0 {
                        0.0
                    } else {
                        ((i % k) as f32 / k as f32).min(1.0)
                    };
                    planes.push(match layer {
                        3 => alpha,
                        4 => {
                            if i % 3 == 0 {
                                alpha * 0.5
                            } else {
                                0.0
                            }
                        }
                        _ => alpha * ((i * (layer + 1) + p) % 11) as f32 / 10.0,
                    });
                }
            }
        }
        Rotor::new(k, positions, 5, planes, SHADING).unwrap()
    }

    #[test]
    fn a_rotor_must_hold_what_it_says() {
        assert!(Rotor::new(4, 2, 5, vec![0.0; 4 * 4 * 5 * 2 - 1], SHADING).is_err());
        assert!(Rotor::new(4, 2, 3, vec![0.0; 4 * 4 * 3 * 2], SHADING).is_err());
        assert_eq!(
            Rotor::new(4, 2, 5, vec![0.0; 4 * 4 * 5 * 2], SHADING)
                .unwrap()
                .side(),
            4
        );
    }

    #[test]
    fn where_nothing_of_the_rotor_is_its_pixels_are_clear_and_a_blur_lies_between() {
        let mut r = rotor(6, 4);
        let plain = r.coloured(1, [200, 40, 30], [255, 200, 180]);
        for (i, px) in plain.chunks_exact(4).enumerate() {
            if (i + 1) % 7 == 0 {
                assert_eq!(px[3], 0);
            }
        }
        let blurred = r.blades(&Blades {
            phase: 1,
            colour: [200, 40, 30],
            second: [255, 200, 180],
            blend: 0.4,
            dim: None,
        });
        let disc = r.disc([200, 40, 30], [255, 200, 180]).to_vec();
        for ((&b, &p), &d) in blurred.iter().zip(&plain).zip(&disc) {
            assert!(b >= p.min(d) && b <= p.max(d));
        }
        let table: Vec<u8> = (0..=255u8).map(|v| v / 2).collect();
        let dimmed = r.blades(&Blades {
            phase: 1,
            colour: [200, 40, 30],
            second: [255, 200, 180],
            blend: 0.0,
            dim: Some(table),
        });
        for (d, p) in dimmed.chunks_exact(4).zip(plain.chunks_exact(4)) {
            assert_eq!(d[..3], p[..3]);
            assert_eq!(d[3], p[3] / 2);
        }
    }

    fn spec(arc: Arc) -> RingSpec {
        RingSpec {
            canvas: 30,
            colour: [46, 230, 214],
            bounds: (10, 10, 79, 79),
            width: 7,
            arc,
            halo: 2.0,
            groove: 0.22,
            white: 0.75,
            share: 1.45,
            core: (0..256).map(|v| (v as f32 / 255.0).powf(0.7)).collect(),
        }
    }

    #[test]
    fn a_ring_without_its_arc_is_its_groove_in_its_colour() {
        let groove = ring(&spec(Arc::None)).unwrap();
        assert!(groove.chunks_exact(4).all(|px| px[..3] == [46, 230, 214]));
        assert!(groove.chunks_exact(4).any(|px| px[3] > 50));
        let half = ring(&spec(Arc::Part(-90.0, 90.0))).unwrap();
        let whole = ring(&spec(Arc::Whole)).unwrap();
        assert_ne!(half, groove);
        assert_ne!(half, whole);
        // (the glow lies over the groove: no pixel of the lit ring is more see-through)
        for (l, g) in whole.chunks_exact(4).zip(groove.chunks_exact(4)) {
            assert!(l[3] >= g[3]);
        }
        assert!(ring(&RingSpec {
            core: vec![0.0; 10],
            ..spec(Arc::Whole)
        })
        .is_err());
    }
}
