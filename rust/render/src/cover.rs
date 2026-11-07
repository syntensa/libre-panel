//! The colour a cover carries (render/cover.py): what a card's frame takes on while a track
//! plays. The picture reduced to a few colours, and the one that joins how much of it there is
//! with how colourful it is wins; too dark and too pale ones are left out, a cover of greys
//! carries none, and a winner too dark for a thin line on a dark panel is brightened.

use lp_pixels::{Filter, Image, Mode};

/// The cover is looked at this small.
const SIDE: usize = 48;
const COLOURS: usize = 8;
/// A candidate whose brightest channel is below this carries nothing ...
const DARKEST: u8 = 60;
/// ... and one less colourful than this is grey.
const GREYEST: f64 = 0.18;
/// The winner is brightened to this, as a line on dark ground.
const BRIGHT: u8 = 180;

/// dominant: the colour that carries `image`, or None (only greys, or a picture that cannot
/// be read so).
pub fn dominant(image: &Image) -> Option<[u8; 3]> {
    let rgb = image.convert(Mode::RGB).ok()?;
    let small = rgb.resize(SIDE, SIDE, Filter::Bilinear, None, None).ok()?;
    let (indices, palette) = small.quantize(COLOURS).ok()?;
    let pixels: Vec<u8> = indices
        .iter()
        .flat_map(|&i| palette[usize::from(i)])
        .collect();
    let few = Image::from_bytes(Mode::RGB, SIDE, SIDE, pixels).ok()?;
    let (mut best, mut score) = (None, 0.0f64);
    for (count, colour) in few.getcolors(SIDE * SIDE).ok()?.unwrap_or_default() {
        let (r, g, b) = (colour[0], colour[1], colour[2]);
        let top = r.max(g).max(b);
        if top < DARKEST {
            continue;
        }
        let colourful = f64::from(top - r.min(g).min(b)) / f64::from(top.max(1));
        if colourful < GREYEST {
            continue;
        }
        let value = (f64::from(count) / (SIDE * SIDE) as f64).powf(0.5) * colourful;
        if value > score {
            best = Some([r, g, b]);
            score = value;
        }
    }
    let mut best = best?;
    let top = best.into_iter().max().unwrap_or(0);
    if top < BRIGHT {
        let k = f64::from(BRIGHT) / f64::from(top);
        best = best.map(|c| (f64::from(c) * k).trunc().min(255.0) as u8);
    }
    Some(best)
}
