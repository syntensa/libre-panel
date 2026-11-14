//! Which panel is which (identify.py): for a few seconds each panel shows its number, large,
//! instead of its theme (Settings → Panels, the tray's "Which is which?"). The first panel
//! ([device]) is 1, those of `[[panels]]` 2, 3 and 4 in their order there.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use lp_pixels::{Draw, Image, Mode};

/// how long the numbers show
const SHOW_S: f64 = 6.0;
const FONT: &str = "builtin:Barlow-Medium";
/// the numbers' colour where the theme names no accent
pub const ACCENT: &str = "#2EE6D6";

static UNTIL: Mutex<Option<Instant>> = Mutex::new(None);

/// show: every panel shows its number from now on, for SHOW_S.
pub fn show() {
    if let Ok(mut until) = UNTIL.lock() {
        *until = Some(Instant::now() + Duration::from_secs_f64(SHOW_S));
    }
}

/// showing: whether the panels show their numbers.
pub fn showing() -> bool {
    UNTIL
        .lock()
        .ok()
        .and_then(|until| *until)
        .is_some_and(|until| Instant::now() < until)
}

/// picture: the panel's number on black, as large as it fits, in `colour` (a colour as a theme
/// writes it), framed.
pub fn picture(
    env: &lp_theme::Env,
    number: usize,
    (width, height): (usize, usize),
    colour: &str,
) -> Result<Image, String> {
    let rgb = lp_pixels::colour::getrgb(colour).map_err(|e| e.0)?;
    let ink = [rgb[0] as u8, rgb[1] as u8, rgb[2] as u8, 255];
    let mut image = Image::new(Mode::RGB, width, height, &[0, 0, 0]);
    let text = number.to_string();
    let side = (width.min(height) as f64 * 0.8) as i64;
    let path = env
        .builtin_font_path(FONT)
        .ok_or_else(|| format!("cannot open resource: {FONT}"))?;
    let font = lp_text::truetype(&path, side as f64, 0, "", None).map_err(|e| e.to_string())?;
    let written = lp_text::text::Text::new(&text, &font, "L");
    let bbox = written
        .get_bbox((0.0, 0.0), None, "left")
        .map_err(|e| e.to_string())?;
    let x = (width as f64 - (bbox.2 - bbox.0)) / 2.0 - bbox.0;
    let y = (height as f64 - (bbox.3 - bbox.1)) / 2.0 - bbox.1;
    lp_text::text::draw(&mut image, (x, y), &written, ink, None, None, "left")
        .map_err(|e| e.to_string())?;
    let margin = 2.max(width.min(height) / 30);
    let bounds = (
        margin as f64,
        margin as f64,
        (width - 1 - margin) as f64,
        (height - 1 - margin) as f64,
    );
    Draw::new(&mut image)
        .rectangle(bounds, None, Some(ink), 2.max(margin / 2) as i32)
        .map_err(|e| e.0)?;
    Ok(image)
}
