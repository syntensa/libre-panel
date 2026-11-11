//! The parts of a frame that changed since the one before (render/renderer.py's changed_region
//! and changed_regions), for a panel that takes a part of its screen: one box around all of
//! them, or a few boxes for a panel on a slow link.

use lp_pixels::{Image, Mode};

use crate::driver::Region;

/// changed_region: the box of the pixels that differ, None where none does; the whole frame
/// where there is no frame before of its size and mode.
pub fn changed_region(previous: Option<&Image>, current: &Image) -> Option<Region> {
    let whole = (0, 0, current.width() as i64, current.height() as i64);
    let Some(previous) = previous else {
        return Some(whole);
    };
    if previous.size() != current.size() || previous.mode() != current.mode() {
        return Some(whole);
    }
    let diff = lp_pixels::chops::difference(previous, current).ok()?;
    diff.getbbox(true)
        .map(|(x0, y0, x1, y1)| (x0 as i64, y0 as i64, x1 as i64, y1 as i64))
}

/// The runs of set flags, those closer than `gap` joined: (first, after the last).
fn runs(flags: &[bool], gap: usize) -> Vec<(usize, usize)> {
    let mut found: Vec<(usize, usize)> = Vec::new();
    for (i, _) in flags.iter().enumerate().filter(|(_, on)| **on) {
        match found.last_mut() {
            Some(last) if i - last.1 <= gap => last.1 = i + 1,
            _ => found.push((i, i + 1)),
        }
    }
    found
}

fn joined(a: Region, b: Region) -> Region {
    (a.0.min(b.0), a.1.min(b.1), a.2.max(b.2), a.3.max(b.3))
}

fn area(b: Region) -> i64 {
    (b.2 - b.0) * (b.3 - b.1)
}

/// changed_regions: the parts that changed as a few boxes, for a panel on a slow link. Rows of
/// changes closer than `gap` px are one band, and so are columns within a band; then the two
/// boxes whose joined box adds the fewest pixels are joined until `most` are left.
pub fn changed_regions(
    previous: Option<&Image>,
    current: &Image,
    gap: usize,
    most: usize,
) -> Vec<Region> {
    let Some(whole) = changed_region(previous, current) else {
        return Vec::new();
    };
    let Some(previous) = previous else {
        return vec![whole];
    };
    if previous.size() != current.size() || previous.mode() != current.mode() {
        return vec![whole];
    }
    let (x0, y0, x1, y1) = whole;
    let diff = lp_pixels::chops::difference(
        &previous.crop(x0, y0, x1, y1),
        &current.crop(x0, y0, x1, y1),
    );
    let grey = match diff.and_then(|diff| {
        if diff.mode() == Mode::L {
            Ok(diff)
        } else {
            diff.convert(Mode::L)
        }
    }) {
        Ok(grey) => grey,
        Err(_) => return vec![whole],
    };
    let (w, h) = (grey.width(), grey.height());
    let changed = grey.as_bytes();
    let rows: Vec<bool> = (0..h)
        .map(|y| changed[y * w..(y + 1) * w].iter().any(|v| *v > 0))
        .collect();
    let mut boxes: Vec<Region> = Vec::new();
    for (top, bottom) in runs(&rows, gap) {
        let columns: Vec<bool> = (0..w)
            .map(|x| (top..bottom).any(|y| changed[y * w + x] > 0))
            .collect();
        for (left, right) in runs(&columns, gap) {
            boxes.push((
                x0 + left as i64,
                y0 + top as i64,
                x0 + right as i64,
                y0 + bottom as i64,
            ));
        }
    }
    if boxes.len() > 8 * most {
        return vec![whole];
    }
    while boxes.len() > most {
        let mut best: Option<(i64, usize, usize)> = None;
        for i in 0..boxes.len() {
            for j in i + 1..boxes.len() {
                let cost = area(joined(boxes[i], boxes[j])) - area(boxes[i]) - area(boxes[j]);
                if best.is_none_or(|(least, ..)| cost < least) {
                    best = Some((cost, i, j));
                }
            }
        }
        let (_, i, j) = best.expect("two boxes at least");
        boxes[i] = joined(boxes[i], boxes[j]);
        boxes.remove(j);
    }
    boxes
}
