//! One picture of the panels where they stand, to share (arrangement.py's photo): each panel
//! in a dark frame with its shadow on a dark ground, showing its frame (or its number), as
//! large as the densest of them shows its frame, at most PHOTO_MAX_SIDE; "Libre Panel" in the
//! corner.

use lp_pixels::{Draw, Filter, Image, Mode};

use crate::piece::{Failed, Result};

/// a picture of all panels is at most this large
pub const PHOTO_MAX_SIDE: f64 = 4000.0;
const PHOTO_MARK: &str = "Libre Panel";
const FONT: &str = "builtin:Barlow-Medium";

/// A panel in the picture: its place (x, y, width, height in millimetres), the frame it shows
/// (None: nothing now), whether it is round, and its number.
pub struct Placed {
    pub rect: (f64, f64, f64, f64),
    pub frame: Option<Image>,
    pub round: bool,
    pub number: usize,
}

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.0)
}

/// Python's round() of a float to a whole number (half to even).
fn round(v: f64) -> i64 {
    v.round_ties_even() as i64
}

fn rgb(c: [u8; 3]) -> [u8; 4] {
    [c[0], c[1], c[2], 255]
}

/// photo: the picture of `panels`; None without a panel.
pub fn photo(env: &lp_theme::Env, panels: &[Placed]) -> Result<Option<Image>> {
    if panels.is_empty() {
        return Ok(None);
    }
    let mut per = panels
        .iter()
        .filter_map(|p| p.frame.as_ref().map(|f| f.width() as f64 / p.rect.2))
        .fold(None, |most: Option<f64>, v| {
            Some(most.map_or(v, |m| if v > m { v } else { m }))
        })
        .unwrap_or(4.0);
    let min = |f: &dyn Fn(&Placed) -> f64| panels.iter().map(f).fold(f64::INFINITY, f64::min);
    let max = |f: &dyn Fn(&Placed) -> f64| panels.iter().map(f).fold(f64::NEG_INFINITY, f64::max);
    let left = min(&|p| p.rect.0);
    let top = min(&|p| p.rect.1);
    let width = max(&|p| p.rect.0 + p.rect.2) - left;
    let height = max(&|p| p.rect.1 + p.rect.3) - top;
    let margin = width.max(height) * 0.08 + 12.0;
    let longest = (width.max(height) + 2.0 * margin) * per;
    if longest > PHOTO_MAX_SIDE {
        per *= PHOTO_MAX_SIDE / longest;
    }
    let size = (
        round((width + 2.0 * margin) * per).max(0) as usize,
        round((height + 2.0 * margin) * per).max(0) as usize,
    );
    let mut canvas = Image::new(Mode::RGB, size.0, size.1, &[11, 14, 19]);
    {
        let mut ground = Draw::new(&mut canvas);
        for y in 0..size.1 {
            // (a little lighter towards the bottom)
            let share = y as f64 / (size.1.max(2) - 1) as f64;
            let fill = [
                11 + round(9.0 * share) as u8,
                14 + round(11.0 * share) as u8,
                19 + round(15.0 * share) as u8,
            ];
            ground
                .line(
                    &[(0.0, y as f64), (size.0 as f64, y as f64)],
                    Some(rgb(fill)),
                    1,
                    false,
                )
                .map_err(failed)?;
        }
    }
    let path = env.builtin_font_path(FONT);
    for placed in panels {
        let (x_mm, y_mm, w_mm, h_mm) = placed.rect;
        let (x, y) = (
            round((x_mm - left + margin) * per),
            round((y_mm - top + margin) * per),
        );
        let (w, h) = (round(w_mm * per).max(1), round(h_mm * per).max(1));
        let bezel = round(w.min(h) as f64 * 0.035).max(6);
        let outer = (x - bezel, y - bezel, x + w + bezel, y + h + bezel);
        let mut shadow = Image::new(Mode::L, size.0, size.1, &[0]);
        let shifted = (
            outer.0 as f64,
            (outer.1 + bezel) as f64,
            outer.2 as f64,
            (outer.3 + bezel) as f64,
        );
        {
            let mut drawn = Draw::new(&mut shadow);
            if placed.round {
                drawn.ellipse(shifted, Some([150, 0, 0, 0]), None, 1)
            } else {
                drawn.rounded_rectangle(
                    shifted,
                    (bezel * 2) as f64,
                    Some([150, 0, 0, 0]),
                    None,
                    1,
                    [true; 4],
                )
            }
            .map_err(failed)?;
        }
        let blur = (bezel as f64 * 1.5) as f32;
        let shadow = shadow.gaussian_blur(blur, blur).map_err(failed)?;
        canvas
            .paste_colour(
                &[0, 0, 0],
                (0, 0, size.0 as i64, size.1 as i64),
                Some(&shadow),
            )
            .map_err(failed)?;
        {
            let mut body = Draw::new(&mut canvas);
            let outer = (
                outer.0 as f64,
                outer.1 as f64,
                outer.2 as f64,
                outer.3 as f64,
            );
            let edge = (bezel / 4).max(1) as i32;
            if placed.round {
                body.ellipse(outer, Some(rgb([6, 8, 11])), Some(rgb([42, 49, 60])), edge)
            } else {
                body.rounded_rectangle(
                    outer,
                    (bezel * 2) as f64,
                    Some(rgb([6, 8, 11])),
                    Some(rgb([42, 49, 60])),
                    edge,
                    [true; 4],
                )
            }
            .map_err(failed)?;
        }
        let (wu, hu) = (w as usize, h as usize);
        let screen = match &placed.frame {
            Some(frame) => frame
                .convert(Mode::RGB)
                .and_then(|f| f.resize(wu, hu, Filter::Lanczos, None, None))
                .map_err(failed)?,
            None => {
                // it shows nothing now: its number
                let mut screen = Image::new(Mode::RGB, wu, hu, &[14, 17, 22]);
                if let Some(path) = &path {
                    let side = round(w.min(h) as f64 * 0.3).max(10) as f64;
                    let font = lp_text::truetype(path, side, 0, "", None)
                        .map_err(|e| Failed(e.to_string()))?;
                    let number = placed.number.to_string();
                    let written = lp_text::text::Text::new(&number, &font, "RGB");
                    lp_text::text::draw(
                        &mut screen,
                        (w as f64 / 2.0, h as f64 / 2.0),
                        &written,
                        rgb([70, 80, 95]),
                        None,
                        Some("mm"),
                        "left",
                    )
                    .map_err(|e| Failed(e.to_string()))?;
                }
                screen
            }
        };
        let mask = if placed.round {
            let mut mask = Image::new(Mode::L, wu, hu, &[0]);
            Draw::new(&mut mask)
                .ellipse(
                    (0.0, 0.0, (w - 1) as f64, (h - 1) as f64),
                    Some([255, 0, 0, 0]),
                    None,
                    1,
                )
                .map_err(failed)?;
            Some(mask)
        } else {
            None
        };
        canvas.paste(&screen, x, y, mask.as_ref()).map_err(failed)?;
    }
    if let Some(path) = &path {
        let side = round(size.1 as f64 * 0.028).max(12) as f64;
        let font = lp_text::truetype(path, side, 0, "", None).map_err(|e| Failed(e.to_string()))?;
        let gap = round(margin * per * 0.35);
        let written = lp_text::text::Text::new(PHOTO_MARK, &font, "RGB");
        lp_text::text::draw(
            &mut canvas,
            ((size.0 as i64 - gap) as f64, (size.1 as i64 - gap) as f64),
            &written,
            rgb([120, 130, 146]),
            None,
            Some("rs"),
            "left",
        )
        .map_err(|e| Failed(e.to_string()))?;
    }
    Ok(Some(canvas))
}
