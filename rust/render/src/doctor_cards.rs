//! The panel test's cards (doctor.py's test_card and ruler_card): what makes cut-off edges,
//! rotation, mirroring and colour mix-ups obvious, and numbered bars that measure the strip
//! the glass hides at each edge. Drawn as Pillow draws them, in Pillow's default font (Aileron
//! Regular, ImageFont.load_default).

use lp_pixels::{Draw, Image, Mode};

/// Pillow's default font (ImageFont.load_default: Aileron Regular, CC0, as Pillow carries it).
const AILERON: &[u8] = include_bytes!("aileron.ttf");

pub const RULER_EDGES: [&str; 4] = ["top", "bottom", "left", "right"];
const RULER_COLOR: [u8; 4] = [255, 212, 0, 255];
const WHITE: [u8; 4] = [255, 255, 255, 255];
const SOFT: [u8; 4] = [0xcb, 0xd5, 0xe1, 255];
const YELLOW: [u8; 4] = [0xff, 0xd4, 0x00, 255];

fn failed(e: impl std::fmt::Display) -> String {
    e.to_string()
}

/// ImageFont.load_default(max(8, size)).
fn font(size: i64) -> Result<lp_text::Font, String> {
    lp_text::Font::from_bytes(
        AILERON.to_vec(),
        size.max(8) as f64,
        0,
        "",
        Some(lp_text::Layout::Basic),
    )
    .map_err(failed)
}

/// Python's round() of a float to a whole number (half to even).
fn round(v: f64) -> i64 {
    v.round_ties_even() as i64
}

/// ImageDraw.text(xy, text, font=font, fill=fill, anchor=anchor).
fn text(
    image: &mut Image,
    xy: (i64, i64),
    said: &str,
    font: &lp_text::Font,
    fill: [u8; 4],
    anchor: &str,
) -> Result<(), String> {
    let written = lp_text::text::Text::new(said, font, "RGB");
    lp_text::text::draw(
        image,
        (xy.0 as f64, xy.1 as f64),
        &written,
        fill,
        None,
        Some(anchor),
        "left",
    )
    .map_err(failed)
}

fn rect(
    image: &mut Image,
    b: [i64; 4],
    fill: Option<[u8; 4]>,
    outline: Option<[u8; 4]>,
    width: i32,
) -> Result<(), String> {
    let bounds = (b[0] as f64, b[1] as f64, b[2] as f64, b[3] as f64);
    Draw::new(image)
        .rectangle(bounds, fill, outline, width)
        .map_err(|e| e.0)
}

/// The strips the panel's bezel is known to hide (top, right, bottom, left).
pub type Hidden = [i64; 4];

/// test_card: a card that makes cut-off edges, rotation, mirroring and colour mix-ups
/// obvious. `hidden`: the strips the panel's bezel is known to hide; the frame goes inside
/// them, so that a frame not seen means more is hidden than known.
pub fn test_card(
    width: i64,
    height: i64,
    title: &str,
    subtitle: &str,
    round_panel: bool,
    hidden: Hidden,
) -> Result<Image, String> {
    let mut img = Image::new(
        Mode::RGB,
        width.max(0) as usize,
        height.max(0) as usize,
        &[0, 0, 0],
    );
    let unit = 8.max(width.min(height) / 12);
    let [top, right, bottom, left] = hidden;
    let (x0, y0) = (left, top);
    let (x1, y1) = (width - 1 - right, height - 1 - bottom);
    if round_panel {
        Draw::new(&mut img)
            .ellipse(
                (0.0, 0.0, (width - 1) as f64, (height - 1) as f64),
                None,
                Some(WHITE),
                4,
            )
            .map_err(|e| e.0)?;
    } else {
        rect(&mut img, [x0, y0, x1, y1], None, Some(WHITE), 4)?;
        rect(
            &mut img,
            [x0 + 10, y0 + 10, x1 - 10, y1 - 10],
            None,
            Some(YELLOW),
            2,
        )?;
    }
    let small = font(8.max(unit / 2))?;
    let inset = if round_panel {
        (width.min(height) as f64 * 0.2) as i64
    } else {
        unit
    };
    let labels: Vec<(&str, (i64, i64), &str)> = if round_panel {
        // corners do not exist on a round panel: the edges are labelled instead
        vec![
            ("LEFT", (inset / 2, height / 2 - unit), "lm"),
            ("RIGHT", (width - inset / 2, height / 2 - unit), "rm"),
            ("BOTTOM", (width / 2, height - inset / 2), "md"),
        ]
    } else {
        vec![
            ("TOP LEFT", (inset, inset), "la"),
            ("TOP RIGHT", (width - inset, inset), "ra"),
            ("BOTTOM LEFT", (inset, height - inset), "ld"),
            ("BOTTOM RIGHT", (width - inset, height - inset), "rd"),
        ]
    };
    for (label, xy, anchor) in labels {
        text(&mut img, xy, label, &small, WHITE, anchor)?;
    }
    // the arrow to the top edge
    let cx = width / 2;
    let up = inset;
    let half = (unit / 2) as f64;
    Draw::new(&mut img)
        .polygon(
            &[
                (cx as f64, up as f64),
                (cx as f64 - half, up as f64 + half),
                (cx as f64 + half, up as f64 + half),
            ],
            Some(WHITE),
            None,
            1,
        )
        .map_err(|e| e.0)?;
    text(&mut img, (cx, up + unit / 2 + 4), "UP", &small, WHITE, "ma")?;
    text(
        &mut img,
        (cx, height / 2 - unit),
        title,
        &font(unit)?,
        WHITE,
        "mm",
    )?;
    text(&mut img, (cx, height / 2), subtitle, &small, SOFT, "mm")?;
    // the colour boxes with their names
    let names: [(&str, [u8; 4]); 4] = [
        ("RED", [255, 0, 0, 255]),
        ("GREEN", [0, 255, 0, 255]),
        ("BLUE", [0, 0, 255, 255]),
        ("WHITE", WHITE),
    ];
    let boxed = 6.max(unit / 2);
    let gap = boxed * 3;
    let bx0 = cx - (names.len() as i64 * gap).div_euclid(2) + gap / 2;
    let by0 = height / 2 + unit / 2;
    let named = font(8.max(unit / 3))?;
    for (i, (name, colour)) in names.iter().enumerate() {
        let x = bx0 + i as i64 * gap;
        rect(
            &mut img,
            [x - boxed / 2, by0, x + boxed / 2, by0 + boxed],
            Some(*colour),
            None,
            1,
        )?;
        text(&mut img, (x, by0 + boxed + 2), name, &named, WHITE, "ma")?;
    }
    // the grey ramp: its darkest steps show how deep the panel's black goes
    let steps = 16;
    let ramp_w = (width - 2 * inset).min(steps * unit);
    let ramp_y = by0 + boxed + unit;
    if ramp_y + unit / 2 < height - inset {
        for i in 0..steps {
            let v = round(i as f64 * 255.0 / (steps - 1) as f64) as u8;
            let x = cx - ramp_w.div_euclid(2) + (i * ramp_w).div_euclid(steps);
            rect(
                &mut img,
                [
                    x,
                    ramp_y,
                    x + ramp_w.div_euclid(steps) - 1,
                    ramp_y + unit / 2,
                ],
                Some([v, v, v, 255]),
                None,
                1,
            )?;
        }
    }
    Ok(img)
}

/// ruler_card: numbered bars that start at each edge, each as deep as its number: bar `k`
/// covers the `k` pixels next to the edge, so glass that hides `k` pixels or more hides it
/// completely, and the smallest number whose bar shows is one step more than the hidden
/// strip. The numbers sit well inside the card, big enough to read at arm's length.
pub fn ruler_card(width: i64, height: i64, step: i64, depth: i64) -> Result<Image, String> {
    let mut img = Image::new(
        Mode::RGB,
        width.max(0) as usize,
        height.max(0) as usize,
        &[0, 0, 0],
    );
    let size = 12.max(20.min(width.min(height) / 20));
    let face = font(size)?;
    let inset = 45.max(depth + 12); // where the numbers start
    let thick = 4.max(size / 3); // of a bar, along the edge
    let shift_of = |horizontal: bool| {
        if horizontal {
            round(size as f64 * 1.2)
        } else {
            round(size as f64 * 1.8)
        }
    };
    // clear of the numbers of the rulers across (two rows at most)
    let clear_of = |horizontal: bool| {
        if horizontal {
            inset + shift_of(false) + round(size as f64 * 1.6) + size / 2
        } else {
            inset + shift_of(true) + size + size / 2
        }
    };
    for edge in RULER_EDGES {
        let horizontal = edge == "top" || edge == "bottom";
        let along = if horizontal { width } else { height };
        let margin = clear_of(horizontal).min(along.div_euclid(4));
        let need = if horizontal {
            size as f64 * 1.6
        } else {
            size as f64 * 1.1
        };
        let mut marks: Vec<i64> = (step..=depth).step_by(step.max(1) as usize).collect();
        let (room, rows) = loop {
            // one row of numbers, two staggered rows, or fewer bars
            let room = (along - 2 * margin) as f64 / marks.len() as f64;
            let rows = if room >= need { 1 } else { 2 };
            if room * rows as f64 >= need || marks.len() <= 2 {
                break (room, rows);
            }
            marks = marks.iter().skip(1).step_by(2).copied().collect();
        };
        let shift = shift_of(horizontal);
        for (i, k) in marks.iter().enumerate() {
            let k = *k;
            let pos = round(margin as f64 + (i as f64 + 0.5) * room);
            let (a, b) = (pos - thick / 2, pos - thick / 2 + thick - 1);
            let label = inset + (i as i64 % rows) * shift;
            let number = k.to_string();
            match edge {
                "top" => {
                    rect(&mut img, [a, 0, b, k - 1], Some(RULER_COLOR), None, 1)?;
                    text(&mut img, (pos, label), &number, &face, WHITE, "ma")?;
                }
                "bottom" => {
                    rect(
                        &mut img,
                        [a, height - k, b, height - 1],
                        Some(RULER_COLOR),
                        None,
                        1,
                    )?;
                    text(&mut img, (pos, height - label), &number, &face, WHITE, "md")?;
                }
                "left" => {
                    rect(&mut img, [0, a, k - 1, b], Some(RULER_COLOR), None, 1)?;
                    text(&mut img, (label, pos), &number, &face, WHITE, "lm")?;
                }
                _ => {
                    rect(
                        &mut img,
                        [width - k, a, width - 1, b],
                        Some(RULER_COLOR),
                        None,
                        1,
                    )?;
                    text(&mut img, (width - label, pos), &number, &face, WHITE, "rm")?;
                }
            }
        }
    }
    // the title and hint between the side rulers' numbers, where they fit
    let free = width - 2 * (inset + round(size as f64 * 1.8) + 2 * size);
    let (cx, cy) = (width / 2, height / 2);
    let hint = "at each edge: the smallest number whose yellow bar you can see";
    for (said, big, y, fill) in [
        ("RULER", size * 3 / 2, cy - size, WHITE),
        (hint, size, cy + size, SOFT),
    ] {
        for points in (10..=big).rev() {
            let face = font(points)?;
            let length = lp_text::text::Text::new(said, &face, "RGB")
                .get_length()
                .map_err(failed)?;
            if length <= free as f64 {
                text(&mut img, (cx, y), said, &face, fill, "mm")?;
                break;
            }
        }
    }
    Ok(img)
}
