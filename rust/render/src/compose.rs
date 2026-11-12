//! The pieces laid on the frame (render/renderer.py's _paste, _composite and _add_light): each
//! over what lies beneath it, a frosted one blurring what lies beneath its box first, light added.

use std::rc::Rc;

use lp_pixels::{chops, Image, Mode};

use crate::piece::{Failed, Piece, Result};

/// A box on the frame: left, top, right, bottom (right and bottom outside it).
pub type Area = (i64, i64, i64, i64);

/// Where on a frame of `size` a piece leaves its pixels: its layer, the light it adds, and the
/// box its frosted backdrop blurs (None where none of it lies on the frame).
fn reach(piece: &Piece, size: (usize, usize)) -> Option<Area> {
    let layer = |p: &Piece| {
        (
            p.x,
            p.y,
            p.x.saturating_add(p.layer.width() as i64),
            p.y.saturating_add(p.layer.height() as i64),
        )
    };
    let mut area = layer(piece);
    if let Some(light) = &piece.light {
        area = union(area, layer(light));
    }
    if let (Some(_), Some([x, y, w, h])) = (&piece.backdrop, piece.hit) {
        area = union(area, (x, y, x.saturating_add(w), y.saturating_add(h)));
    }
    clip(area, size)
}

fn union(a: Area, b: Area) -> Area {
    (a.0.min(b.0), a.1.min(b.1), a.2.max(b.2), a.3.max(b.3))
}

fn clip(a: Area, (w, h): (usize, usize)) -> Option<Area> {
    let a = (a.0.max(0), a.1.max(0), a.2.min(w as i64), a.3.min(h as i64));
    (a.2 > a.0 && a.3 > a.1).then_some(a)
}

fn meet(a: Area, b: Area) -> bool {
    a.0 < b.2 && b.0 < a.2 && a.1 < b.3 && b.1 < a.3
}

fn size_of(a: Area) -> i64 {
    (a.2 - a.0) * (a.3 - a.1)
}

/// The boxes of a frame of `size` to lay again where it had the pieces `before` and has
/// `now`: those of the pieces that changed, before and now, grown over the whole box of each
/// frosted backdrop they touch (its blur takes all of the box under it) and joined where
/// they meet. None where the frame is laid whole: other pieces, or another order, or much of
/// it changed.
pub fn changed(
    before: &[(String, Rc<Piece>)],
    now: &[(String, Rc<Piece>)],
    size: (usize, usize),
) -> Option<(Vec<Area>, Vec<bool>)> {
    if before.len() != now.len() {
        return None;
    }
    let mut areas = Vec::new();
    let mut moved = vec![false; now.len()];
    for (i, ((id_was, was), (id, is))) in before.iter().zip(now).enumerate() {
        if id_was != id {
            return None;
        }
        if Rc::ptr_eq(was, is) {
            continue;
        }
        moved[i] = true;
        areas.extend(reach(was, size));
        areas.extend(reach(is, size));
    }
    let frosted: Vec<Area> = now
        .iter()
        .filter(|(_, p)| p.backdrop.is_some())
        .filter_map(|(_, p)| reach(p, size))
        .collect();
    // grown and joined until no box meets another or leaves a backdrop's box in part
    loop {
        let mut grown = false;
        for area in &mut areas {
            for f in &frosted {
                if meet(*area, *f) && union(*area, *f) != *area {
                    *area = union(*area, *f);
                    grown = true;
                }
            }
        }
        let mut joined: Vec<Area> = Vec::with_capacity(areas.len());
        for area in areas {
            match joined.iter_mut().find(|other| meet(**other, area)) {
                Some(other) => {
                    *other = union(*other, area);
                    grown = true;
                }
                None => joined.push(area),
            }
        }
        areas = joined;
        if !grown {
            break;
        }
    }
    let whole = size.0 as i64 * size.1 as i64;
    let laid: i64 = areas.iter().map(|a| size_of(*a)).sum();
    (laid * 2 <= whole).then_some((areas, moved))
}

/// What lies under the pieces that change, by region (_underlays): the pieces laid there
/// before the first that changed, and the region with them laid. While those stay the same,
/// the next frame starts there (the same steps in the same order: the same pixels).
#[derive(Default)]
pub struct Underlays {
    kept: std::collections::HashMap<Area, (Vec<Rc<Piece>>, Image, u64)>,
    bytes: usize,
    frame: u64,
}

/// What Underlays keeps at most (Python's UNDERLAY_BYTES).
const UNDERLAY_BYTES: usize = 8 << 20;

impl Underlays {
    /// A new frame: what was not used for a while goes first when room is needed.
    pub fn next_frame(&mut self) {
        self.frame += 1;
    }

    fn keep(&mut self, area: Area, under: Vec<Rc<Piece>>, part: &Image) {
        let size = part.as_bytes().len();
        if size > UNDERLAY_BYTES {
            return;
        }
        if let Some((_, old, _)) = self.kept.remove(&area) {
            self.bytes -= old.as_bytes().len();
        }
        while self.bytes + size > UNDERLAY_BYTES {
            let Some(oldest) = self.kept.iter().min_by_key(|(_, v)| v.2).map(|(k, _)| *k) else {
                break;
            };
            if let Some((_, old, _)) = self.kept.remove(&oldest) {
                self.bytes -= old.as_bytes().len();
            }
        }
        self.bytes += size;
        self.kept.insert(area, (under, part.clone(), self.frame));
    }

    pub fn clear(&mut self) {
        self.kept.clear();
        self.bytes = 0;
    }
}

/// The box `area` of `canvas` laid again (_compose_region): the ground there, and over it
/// each piece that reaches into it, in their order; what lies under the first that `moved` is
/// kept in `underlays`, and taken from there while it stays the same (what fails is told in
/// `failed`).
#[allow(clippy::too_many_arguments)]
pub fn lay_again(
    canvas: &mut Image,
    ground: &Image,
    pieces: &[(String, Rc<Piece>)],
    moved: &[bool],
    area: Area,
    opaque: bool,
    underlays: &mut Underlays,
    failed: &mut Vec<(String, String)>,
) {
    let size = canvas.size();
    let (x0, y0, x1, y1) = area;
    crate::prof::add_count("compose px", ((x1 - x0) * (y1 - y0)) as u64);
    let inside: Vec<usize> = (0..pieces.len())
        .filter(|&i| reach(&pieces[i].1, size).is_some_and(|r| meet(r, area)))
        .collect();
    let below = inside
        .iter()
        .position(|&i| moved.get(i).copied().unwrap_or(true))
        .unwrap_or(inside.len());
    let frame = underlays.frame;
    let hit = underlays.kept.get_mut(&area).filter(|(under, ..)| {
        under.len() <= below
            && under
                .iter()
                .zip(&inside)
                .all(|(kept, &i)| Rc::ptr_eq(kept, &pieces[i].1))
    });
    let underlay = crate::prof::Span::new("compose under");
    let (done, mut part) = match hit {
        Some((under, image, used)) => {
            *used = frame;
            (under.len(), image.clone())
        }
        None => (0, ground.crop(x0, y0, x1, y1)),
    };
    drop(underlay);
    let _laying = crate::prof::Span::new("compose lay");
    let reused = done > 0 || underlays.kept.contains_key(&area);
    for (k, &i) in inside.iter().enumerate().skip(done) {
        if k == below && !(reused && done == below) {
            let under = inside[..below]
                .iter()
                .map(|&j| pieces[j].1.clone())
                .collect();
            underlays.keep(area, under, &part);
        }
        let (id, piece) = &pieces[i];
        if let Err(Failed(e)) = composite(&mut part, piece, (x0, y0), opaque) {
            failed.push((id.clone(), e));
        }
    }
    if let Err(e) = canvas.paste(&part, x0, y0, None) {
        failed.push((String::new(), e.0));
    }
}

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
    // (the part on the canvas, read where it lies in the layer: the same as cut out first)
    let part = (x0 - x, y0 - y, x1 - x, y1 - y);
    if opaque && layer.mode() == Mode::RGBA {
        let part = (
            part.0 as usize,
            part.1 as usize,
            part.2 as usize,
            part.3 as usize,
        );
        canvas.paste_part(layer, part, x0, y0)?;
    } else if opaque {
        let cut = layer.crop(part.0, part.1, part.2, part.3);
        canvas.paste(&cut, x0, y0, Some(&cut))?;
    } else {
        canvas.alpha_composite_at(layer, (x0, y0), part)?;
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
            let _frosted = crate::prof::Span::new("compose backdrop");
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

/// The pixels of `from` in `area` (clipped to both) copied into `to`, an image of its mode.
pub fn copy_area(to: &mut Image, from: &Image, area: Area) {
    let (w, h) = (
        to.width().min(from.width()) as i64,
        to.height().min(from.height()) as i64,
    );
    let (x0, y0) = (area.0.clamp(0, w) as usize, area.1.clamp(0, h) as usize);
    let (x1, y1) = (area.2.clamp(0, w) as usize, area.3.clamp(0, h) as usize);
    if x0 >= x1 || y0 >= y1 || to.mode() != from.mode() {
        return;
    }
    let b = from.bands();
    let (tw, fw) = (to.width() * b, from.width() * b);
    let (a, n) = (x0 * b, (x1 - x0) * b);
    let source = from.as_bytes();
    let target = to.as_bytes_mut();
    for y in y0..y1 {
        target[y * tw + a..y * tw + a + n].copy_from_slice(&source[y * fw + a..y * fw + a + n]);
    }
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
