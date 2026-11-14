//! The colour check on a panel, for the eye (colour_show.py): for SHOW_S the panel chosen
//! shows the check's test picture instead of its theme, and every SWITCH_S it is made the
//! other way, the letter large in a corner: "feed" (the card's conversion against the
//! processor's) or "range" (limited against full range, *Is black too light on the panel?
//! Compare*). Nothing is written into config.toml: each panel's loop asks here on every frame
//! (`now`), so once it ends every panel shows its theme again. While it runs a marker lies
//! beside config.toml; one found at the next start (`recover`) says the app ended during it.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use lp_pixels::{Draw, Image, Mode};

pub const SHOW_S: f64 = 30.0;
pub const SWITCH_S: f64 = 3.0;
/// what each kind compares: the letter on the panel and the value for it
pub const WAYS: [(&str, [(&str, &str); 2]); 2] = [
    ("feed", [("A", "card"), ("B", "processor")]),
    ("range", [("A", "limited"), ("B", "full")]),
];
const MARKER: &str = "colour-check.json";
const FONT: &str = "builtin:Barlow-Medium";

/// The patches of the test picture, in order (devices/colour_check.py's PATCHES).
pub const PATCHES: [(&str, [u8; 3]); 14] = [
    ("black", [0, 0, 0]),
    ("grey-32", [32, 32, 32]),
    ("grey-80", [80, 80, 80]),
    ("grey-128", [128, 128, 128]),
    ("grey-176", [176, 176, 176]),
    ("grey-224", [224, 224, 224]),
    ("white", [255, 255, 255]),
    ("red", [255, 0, 0]),
    ("green", [0, 255, 0]),
    ("blue", [0, 0, 255]),
    ("accent", [0x2E, 0xE6, 0xD6]),
    ("violet", [0xA7, 0x8B, 0xFA]),
    ("orange", [0xFF, 0x8C, 0x1A]),
    ("skin", [0xE0, 0xAC, 0x8A]),
];

/// the check under way: its panel, since, until and kind
static OWN: Mutex<Option<(i64, Instant, Instant, &'static str)>> = Mutex::new(None);

fn say(message: &str) {
    crate::logs::log(crate::logs::Level::Info, "libre_panel.colour_show", message);
}

fn marker() -> std::path::PathBuf {
    lp_config::dirs::config_dir().join(MARKER)
}

fn ways_of(kind: &str) -> Option<(&'static str, [(&'static str, &'static str); 2])> {
    WAYS.iter().find(|(name, _)| *name == kind).copied()
}

/// begin: panel `number` (1 the first) shows the test picture from now on, for `seconds`
/// (SHOW_S), A and B of `kind` in turn.
pub fn begin(number: i64, seconds: Option<f64>, kind: &str) -> Result<(), String> {
    let Some((kind, _)) = ways_of(kind) else {
        return Err("kind: one of feed, range".into());
    };
    let seconds = seconds.unwrap_or(SHOW_S);
    let path = marker();
    if path.parent().is_some_and(std::path::Path::is_dir) {
        let began = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_secs());
        let text = format!("{{\"panel\": {number}, \"kind\": \"{kind}\", \"began\": {began}}}\n");
        std::fs::write(&path, text).map_err(|e| e.to_string())?;
    }
    let now = Instant::now();
    if let Ok(mut own) = OWN.lock() {
        *own = Some((number, now, now + Duration::from_secs_f64(seconds), kind));
    }
    say(&format!(
        "colour check: panel {number} shows the test picture, A and B in turn ({kind})"
    ));
    Ok(())
}

/// end: the test picture goes: every panel shows its theme again at its next frame.
pub fn end() {
    let was = OWN.lock().ok().and_then(|mut own| own.take());
    let _ = std::fs::remove_file(marker());
    if let Some((number, ..)) = was {
        say(&format!(
            "colour check: panel {number} shows its theme again"
        ));
    }
}

fn phase(kind: &str, since: Instant, now: Instant) -> (&'static str, &'static str) {
    let ways = ways_of(kind).map_or(WAYS[0].1, |(_, ways)| ways);
    let step = (now.duration_since(since).as_secs_f64() / SWITCH_S).floor() as usize;
    ways[step % ways.len()]
}

/// now: what panel `number` shows now: (the letter, the kind, the value for it), or None: its
/// theme.
pub fn now(number: i64) -> Option<(&'static str, &'static str, &'static str)> {
    let moment = Instant::now();
    let own = *OWN.lock().ok()?;
    match own {
        Some((panel, since, until, kind)) if panel == number && moment < until => {
            let (letter, value) = phase(kind, since, moment);
            Some((letter, kind, value))
        }
        _ => None,
    }
}

/// The check under way: its panel, kind, letter, value and the seconds left.
pub struct Showing {
    pub panel: i64,
    pub kind: &'static str,
    pub letter: &'static str,
    pub value: &'static str,
    pub left_s: f64,
}

/// state: the check under way here; None for none.
pub fn state() -> Option<Showing> {
    let moment = Instant::now();
    let (panel, since, until, kind) = (*OWN.lock().ok()?)?;
    if moment >= until {
        return None;
    }
    let (letter, value) = phase(kind, since, moment);
    Some(Showing {
        panel,
        kind,
        letter,
        value,
        left_s: until.duration_since(moment).as_secs_f64(),
    })
}

/// grid: columns and rows for `count` patches on a frame of `size`, the cells as square as
/// the frame allows.
fn grid((width, height): (usize, usize), count: usize) -> (usize, usize) {
    let rows = ((count as f64 * height as f64 / width as f64)
        .sqrt()
        .round_ties_even() as usize)
        .clamp(1, count);
    let cols = count.div_ceil(rows);
    (cols, count.div_ceil(cols))
}

/// layout: every patch's colour and box (x0, y0, x1, y1) on a frame of `size`.
pub fn layout(size: (usize, usize)) -> Vec<([u8; 3], [usize; 4])> {
    let (width, height) = size;
    let (cols, rows) = grid(size, PATCHES.len());
    PATCHES
        .iter()
        .enumerate()
        .map(|(i, (_, rgb))| {
            let (col, row) = (i % cols, i / cols);
            let boxed = [
                width * col / cols,
                height * row / rows,
                width * (col + 1) / cols,
                height * (row + 1) / rows,
            ];
            (*rgb, boxed)
        })
        .collect()
}

/// check_picture: the test picture at `size`; `letter` large on it, in the top left corner
/// on black (in the middle of a square frame), white with a black stroke.
pub fn picture(env: &lp_theme::Env, size: (usize, usize), letter: &str) -> Result<Image, String> {
    let (width, height) = size;
    let mut image = Image::new(Mode::RGB, width, height, &[0, 0, 0]);
    let patches = layout(size);
    {
        let mut draw = Draw::new(&mut image);
        for (rgb, b) in &patches {
            let bounds = (
                b[0] as f64,
                b[1] as f64,
                b[2] as f64 - 1.0,
                b[3] as f64 - 1.0,
            );
            let ink = [rgb[0], rgb[1], rgb[2], 255];
            draw.rectangle(bounds, Some(ink), None, 1)
                .map_err(|e| e.0)?;
        }
    }
    if letter.is_empty() {
        return Ok(image);
    }
    let first = patches[0].1;
    let side = ((first[2] - first[0]).min(first[3] - first[1]) as f64 * 0.9) as i64;
    let path = env
        .builtin_font_path(FONT)
        .ok_or_else(|| format!("cannot open resource: {FONT}"))?;
    let font =
        lp_text::truetype(&path, side.max(8) as f64, 0, "", None).map_err(|e| e.to_string())?;
    let stroke = (side / 24).max(2);
    let (at, anchor) = if width == height {
        ((width as f64 / 2.0, height as f64 / 2.0), "mm")
    } else {
        let margin = (side / 8).max(24) as f64;
        ((first[0] as f64 + margin, first[1] as f64 + margin), "lt")
    };
    let mut written = lp_text::text::Text::new(letter, &font, "RGB");
    written.stroke_width = stroke as f64;
    lp_text::text::draw(
        &mut image,
        at,
        &written,
        [255, 255, 255, 255],
        Some([0, 0, 0, 255]),
        Some(anchor),
        "left",
    )
    .map_err(|e| e.to_string())?;
    Ok(image)
}

/// recover: at a start, a marker of a check the app ended during: it goes, and it is said.
pub fn recover() {
    let path = marker();
    let Ok(text) = std::fs::read_to_string(&path) else {
        return;
    };
    let _ = std::fs::remove_file(&path);
    let panel = match lp_py::json::loads(&text) {
        Ok(lp_py::Value::Dict(found)) => found
            .get("panel")
            .map_or("?".to_string(), lp_py::format::str_of),
        _ => "?".to_string(),
    };
    say(&format!(
        "colour check: the app ended while panel {panel} showed the test picture; it shows its \
         theme and feeds ffmpeg as set"
    ));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_patches_lie_as_colour_check_py_lays_them() {
        assert_eq!(grid((1920, 480), 14), (7, 2));
        assert_eq!(grid((480, 1920), 14), (2, 7));
        assert_eq!(grid((480, 480), 14), (4, 4));
        let found = layout((1920, 480));
        assert_eq!(found[0].1, [0, 0, 274, 240]);
        assert_eq!(found[13].1, [1645, 240, 1920, 480]);
    }
}
