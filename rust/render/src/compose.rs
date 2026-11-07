//! The pieces laid on the frame (render/renderer.py's _paste, _composite and _add_light): each
//! over what lies beneath it, a frosted one blurring what lies beneath its box first, light added.

use lp_pixels::{chops, Image, Mode};

use crate::piece::{Piece, Result};

/// The part of a layer at (x, y) that lies on a canvas of `size`: its corners on the canvas,
/// None where none of it does.
fn on_canvas(size: (usize, usize), layer: &Image, x: i64, y: i64) -> Option<(i64, i64, i64, i64)> {
    let (x0, y0) = (x.max(0), y.max(0));
    let x1 = x.saturating_add(layer.width() as i64).min(size.0 as i64);
    let y1 = y.saturating_add(layer.height() as i64).min(size.1 as i64);
    (x1 > x0 && y1 > y0).then_some((x0, y0, x1, y1))
}

/// _paste: alpha_composite that takes a layer hanging off the canvas. Onto an `opaque` canvas
/// (RGB), a paste through the layer's alpha gives the same colours in place.
pub fn paste(canvas: &mut Image, layer: &Image, x: i64, y: i64, opaque: bool) -> Result<()> {
    let Some((x0, y0, x1, y1)) = on_canvas(canvas.size(), layer, x, y) else {
        return Ok(());
    };
    let whole = (x0, y0, x1, y1) == (x, y, x + layer.width() as i64, y + layer.height() as i64);
    let cut;
    let layer = if whole {
        layer
    } else {
        cut = layer.crop(x0 - x, y0 - y, x1 - x, y1 - y);
        &cut
    };
    if opaque {
        canvas.paste(layer, x0, y0, Some(layer))?;
    } else {
        let (w, h) = (layer.width() as i64, layer.height() as i64);
        canvas.alpha_composite_at(layer, (x0, y0), (0, 0, w, h))?;
    }
    Ok(())
}

/// _add_light: light (a layer whose colour is the light) added onto the canvas, as light adds
/// up; the part on the canvas only.
pub fn add_light(canvas: &mut Image, layer: &Image, x: i64, y: i64) -> Result<()> {
    let Some((x0, y0, x1, y1)) = on_canvas(canvas.size(), layer, x, y) else {
        return Ok(());
    };
    let mut part = layer.crop(x0 - x, y0 - y, x1 - x, y1 - y);
    if part.mode() != canvas.mode() {
        part = part.convert(canvas.mode())?;
    }
    let under = canvas.crop(x0, y0, x1, y1);
    let sum = chops::add(&under, &part, 1.0, 0)?;
    canvas.paste(&sum, x0, y0, None)?;
    Ok(())
}

/// _composite: `piece` onto `canvas`, whose top left corner is `origin` on the panel.
pub fn composite(
    canvas: &mut Image,
    piece: &Piece,
    origin: (i64, i64),
    opaque: bool,
) -> Result<()> {
    let (ox, oy) = origin;
    if let (Some(mask), Some([bx, by, bw, bh])) = (&piece.backdrop, piece.hit) {
        let (bx, by) = (bx - ox, by - oy);
        let region = (
            bx.max(0),
            by.max(0),
            bx.saturating_add(bw).min(canvas.width() as i64),
            by.saturating_add(bh).min(canvas.height() as i64),
        );
        if region.2 > region.0 && region.3 > region.1 {
            let under = canvas.crop(region.0, region.1, region.2, region.3);
            let r = piece.backdrop_radius;
            let blurred = under.gaussian_blur(r, r)?;
            let cut = mask.crop(region.0 - bx, region.1 - by, region.2 - bx, region.3 - by);
            canvas.paste(&blurred, region.0, region.1, Some(&cut))?;
        }
    }
    if piece.add {
        return add_light(canvas, &piece.layer, piece.x - ox, piece.y - oy);
    }
    paste(canvas, &piece.layer, piece.x - ox, piece.y - oy, opaque)?;
    if let Some(light) = &piece.light {
        add_light(canvas, &light.layer, light.x - ox, light.y - oy)?;
    }
    Ok(())
}

/// _as_rgb: the frame of `canvas` in RGB, an RGBA canvas's colours as they are (Paste.c takes
/// them so: its alpha is left behind).
pub fn as_rgb(canvas: &Image) -> Result<Image> {
    if canvas.mode() == Mode::RGB {
        return Ok(canvas.clone());
    }
    let mut rgb = Image::blank(Mode::RGB, canvas.width(), canvas.height());
    rgb.paste(canvas, 0, 0, None)?;
    Ok(rgb)
}
