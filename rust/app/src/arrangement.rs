//! Where the panels stand (Settings → Panels), and one picture across all of them
//! (arrangement.py): each panel's picture has a size in millimetres, from its model's diagonal
//! and pixels and the way it is mounted, and a place (`at = [x, y]`, its top left corner in
//! millimetres). A picture across the panels (`across`) covers the panels that are placed as
//! one: each shows its own part of it, under its theme, instead of the theme's background
//! picture. The picture is kept in the settings folder (across/picture.jpg), made again from
//! what was given: at most MAX_SIDE pixels on its long side, without the file's own metadata.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use lp_devices::models::{find_model, PanelModel};
use lp_pixels::{Filter, Image, Mode};

const MM_PER_INCH: f64 = 25.4;
pub const ACROSS_FILE: &str = "across/picture.jpg";
const MAX_SIDE: f64 = 4096.0;
pub const MAX_BYTES: usize = 40 * 1024 * 1024;
const MAX_PIXELS: usize = 80_000_000;
/// between two panels not placed yet, as the app lays them out
pub const GAP_MM: f64 = 30.0;
/// a panel of a model not known: as dense as the 9.2" bar
pub const GUESS_MM_PER_PX: f64 = 0.118;

/// x, y, width, height in millimetres
pub type Rect = (f64, f64, f64, f64);

/// What a panel's loop says of it: its model and the size of its frames.
#[derive(Clone, Debug)]
pub struct Told {
    pub model: String,
    pub size: Option<(i64, i64)>,
}

/// millimetres: the size of `model`'s picture in millimetres, as mounted: `size` (its frames
/// in pixels, turned as the panel is) or its landscape size. None for a model not known.
pub fn millimetres(model: &PanelModel, size: Option<(i64, i64)>) -> Option<(f64, f64)> {
    let inch = model.size_inch.get();
    if inch == 0.0 {
        return None;
    }
    let per = inch * MM_PER_INCH / (model.native_width as f64).hypot(model.native_height as f64);
    let (width, height) = size.unwrap_or_else(|| model.size("landscape"));
    Some((width as f64 * per, height as f64 * per))
}

/// panel_rect: where the panel of `device` stands, in the size of its model as set, else as
/// the panel said it is; None while it is not placed, or of a size not known. `guess`: a panel
/// of a model not known that says the size of its frames is taken as dense as the 9.2" bar,
/// and one not placed stands at 0, 0 (`laid_out` moves it).
pub fn panel_rect(
    device: &lp_config::DeviceConfig,
    status: Option<&Told>,
    guess: bool,
) -> Option<Rect> {
    let told = status.map_or("", |s| s.model.as_str());
    let model = find_model(&device.model)
        .or_else(|| (!told.is_empty()).then(|| find_model(told)).flatten());
    let way = if device.orientation == "portrait" {
        "portrait"
    } else {
        "landscape"
    };
    let size = status
        .and_then(|s| s.size)
        .or_else(|| model.map(|m| m.size(way)));
    let (x, y) = match device.at {
        Some(at) => at,
        None if guess => (0, 0),
        None => return None,
    };
    match model.and_then(|model| millimetres(model, size)) {
        Some((w, h)) => Some((x as f64, y as f64, w, h)),
        None if guess => size.map(|(w, h)| {
            let per = GUESS_MM_PER_PX;
            (x as f64, y as f64, w as f64 * per, h as f64 * per)
        }),
        None => None,
    }
}

/// places: where every panel stands: the first panel's first, then those of `[[panels]]`;
/// `statuses` what the loop of each says, in that order (None: it does not run).
pub fn places(config: &lp_config::Config, statuses: &[Option<Told>]) -> Vec<Option<Rect>> {
    let devices = std::iter::once(&config.device).chain(config.panels.iter().map(|p| &p.device));
    devices
        .enumerate()
        .map(|(i, device)| panel_rect(device, statuses.get(i).and_then(Option::as_ref), false))
        .collect()
}

/// laid_out: every panel where it is shown to stand: its place, or, not placed, in a row with
/// the others not placed, beside those that are (as Settings → Panels lays them out); None
/// for one whose size is not known at all.
pub fn laid_out(config: &lp_config::Config, statuses: &[Option<Told>]) -> Vec<Option<Rect>> {
    let devices: Vec<&lp_config::DeviceConfig> = std::iter::once(&config.device)
        .chain(config.panels.iter().map(|p| &p.device))
        .collect();
    let sized: Vec<Option<Rect>> = devices
        .iter()
        .enumerate()
        .map(|(i, device)| panel_rect(device, statuses.get(i).and_then(Option::as_ref), true))
        .collect();
    let placed: Vec<Rect> = sized
        .iter()
        .zip(&devices)
        .filter_map(|(r, d)| r.filter(|_| d.at.is_some()))
        .collect();
    let loose: Vec<Rect> = sized
        .iter()
        .zip(&devices)
        .filter_map(|(r, d)| r.filter(|_| d.at.is_none()))
        .collect();
    let row =
        loose.iter().map(|r| r.2).sum::<f64>() + GAP_MM * loose.len().saturating_sub(1) as f64;
    let mut x = if placed.is_empty() {
        -(round(row / 2.0) as f64)
    } else {
        placed
            .iter()
            .map(|r| r.0 + r.2)
            .fold(f64::NEG_INFINITY, f64::max)
            + GAP_MM
    };
    let y = if placed.is_empty() {
        0.0
    } else {
        placed.iter().map(|r| r.1).fold(f64::INFINITY, f64::min)
    };
    sized
        .iter()
        .zip(&devices)
        .map(|(found, device)| match found {
            Some((_, _, w, h)) if device.at.is_none() => {
                let shown = (x, y, *w, *h);
                x += w + GAP_MM;
                Some(shown)
            }
            other => *other,
        })
        .collect()
}

/// Python's round() of a float to a whole number (half to even).
fn round(v: f64) -> i64 {
    v.round_ties_even() as i64
}

/// part: the part of `picture` the panel `rects[index]` shows: the picture covers all the
/// panels that are placed (as large as it must be, its middle at theirs), and each shows what
/// lies where it stands. None for a panel that is not placed.
pub fn part(picture: &Image, rects: &[Option<Rect>], index: usize) -> Option<Image> {
    let (x, y, w, h) = (*rects.get(index)?)?;
    let placed: Vec<Rect> = rects.iter().flatten().copied().collect();
    let left = placed.iter().map(|r| r.0).fold(f64::INFINITY, f64::min);
    let top = placed.iter().map(|r| r.1).fold(f64::INFINITY, f64::min);
    let width = placed
        .iter()
        .map(|r| r.0 + r.2)
        .fold(f64::NEG_INFINITY, f64::max)
        - left;
    let height = placed
        .iter()
        .map(|r| r.1 + r.3)
        .fold(f64::NEG_INFINITY, f64::max)
        - top;
    let (pw, ph) = (picture.width() as f64, picture.height() as f64);
    let per = (pw / width).min(ph / height);
    let shift_x = (pw / per - width) / 2.0;
    let shift_y = (ph / per - height) / 2.0;
    let boxed = (
        round((x - left + shift_x) * per),
        round((y - top + shift_y) * per),
        round((x - left + shift_x + w) * per),
        round((y - top + shift_y + h) * per),
    );
    Some(picture.crop(boxed.0, boxed.1, boxed.2, boxed.3))
}

/// across_path: the file `across` names: a path of its own, or one in the settings folder.
pub fn across_path(across: &str, config_dir: &Path) -> Option<PathBuf> {
    if across.is_empty() {
        return None;
    }
    let path = expand_user(across);
    Some(if path.is_absolute() {
        path
    } else {
        config_dir.join(path)
    })
}

fn expand_user(path: &str) -> PathBuf {
    match path.strip_prefix('~') {
        Some(rest) if rest.is_empty() || rest.starts_with(['/', '\\']) => {
            match Some(crate::autostart::user_home()).filter(|h| !h.as_os_str().is_empty()) {
                Some(home) => home.join(rest.trim_start_matches(['/', '\\'])),
                None => PathBuf::from(path),
            }
        }
        _ => PathBuf::from(path),
    }
}

/// The pictures read (two, by their file and when it was written: _load's lru_cache).
/// A picture read: its file and when it was written, and the picture (None: not readable).
type Read = ((PathBuf, i128), Option<Image>);
static LOADED: Mutex<Vec<Read>> = Mutex::new(Vec::new());

/// When a file was last written, in nanoseconds (None: it is not there).
pub fn stamp_of(path: &Path) -> Option<i128> {
    let modified = std::fs::metadata(path).ok()?.modified().ok()?;
    let since = modified.duration_since(std::time::UNIX_EPOCH).ok()?;
    Some(since.as_nanos() as i128)
}

/// The picture of a file as it is kept: turned as its EXIF says, RGB, at most MAX_SIDE on its
/// long side; None for one that cannot be read.
fn read(data: &[u8]) -> Option<Image> {
    let image = lp_decode::open(data, Mode::RGB).ok()?;
    image
        .thumbnail((MAX_SIDE, MAX_SIDE), Filter::Bicubic, Some(2.0))
        .ok()
}

/// load: the picture across the panels and when its file was last written; None without one,
/// or with one that cannot be read.
pub fn load(path: Option<&Path>) -> Option<(Image, i128)> {
    let path = path?;
    let stamp = stamp_of(path)?;
    let key = (path.to_path_buf(), stamp);
    let mut loaded = LOADED.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(at) = loaded.iter().position(|(k, _)| *k == key) {
        let found = loaded.remove(at);
        let picture = found.1.clone();
        loaded.push(found);
        return picture.map(|p| (p, stamp));
    }
    let picture = std::fs::read(path).ok().and_then(|data| read(&data));
    loaded.push((key, picture.clone()));
    while loaded.len() > 2 {
        loaded.remove(0);
    }
    picture.map(|p| (p, stamp))
}

/// save_picture: `data` (a picture's file) kept as the picture across the panels: made again,
/// at most MAX_SIDE on its long side, without its metadata. What config.toml names it by; the
/// error a person reads where it is no picture.
pub fn save_picture(data: &[u8], config_dir: &Path) -> Result<Result<String, String>, String> {
    if data.len() > MAX_BYTES {
        let said = lp_i18n::t("the picture is larger than {size} MB")
            .replace("{size}", &(MAX_BYTES / 1_048_576).to_string());
        return Ok(Err(said));
    }
    let Ok(image) = lp_decode::open(data, Mode::RGB) else {
        return Ok(Err(lp_i18n::t("this is not a picture that can be read")));
    };
    if image.width() * image.height() > MAX_PIXELS {
        return Ok(Err(lp_i18n::t("the picture has too many pixels")));
    }
    let image = image
        .thumbnail((MAX_SIDE, MAX_SIDE), Filter::Bicubic, Some(2.0))
        .map_err(|e| e.0)?;
    let target = config_dir.join(ACROSS_FILE);
    if let Some(folder) = target.parent() {
        std::fs::create_dir_all(folder).map_err(|e| e.to_string())?;
    }
    let jpeg = lp_decode::save_jpeg_options(&image, 92, false, true).map_err(|e| e.to_string())?;
    let kept = target.with_extension("tmp");
    std::fs::write(&kept, jpeg).map_err(|e| e.to_string())?;
    std::fs::rename(&kept, &target).map_err(|e| e.to_string())?;
    Ok(Ok(ACROSS_FILE.to_string()))
}

/// The part of the picture across the panels a panel's loop shows: the picture's file, when
/// it was written, where every panel stands (rounded to a tenth of a millimetre, as the key
/// the renderer is made again by) and which of them this is.
#[derive(Clone, Debug, PartialEq)]
pub struct Across {
    pub path: PathBuf,
    pub stamp: i128,
    pub places: Vec<Option<(i64, i64, i64, i64)>>,
    pub index: usize,
}

/// The places rounded to a tenth of a millimetre (in tenths).
fn tenths(places: &[Option<Rect>]) -> Vec<Option<(i64, i64, i64, i64)>> {
    let tenth = |v: f64| (v * 10.0).round_ties_even() as i64;
    places
        .iter()
        .map(|r| r.map(|(x, y, w, h)| (tenth(x), tenth(y), tenth(w), tenth(h))))
        .collect()
}

/// _across: this panel's part of the picture across the panels: None without a picture, or
/// while this panel is not placed.
pub fn across_of(
    config: &lp_config::Config,
    config_dir: &Path,
    places: &[Option<Rect>],
    index: usize,
) -> Option<Across> {
    if config.across.is_empty() || places.get(index).copied().flatten().is_none() {
        return None;
    }
    let path = across_path(&config.across, config_dir)?;
    let stamp = stamp_of(&path)?;
    Some(Across {
        path,
        stamp,
        places: tenths(places),
        index,
    })
}

/// _backdrop: the part of the picture across the panels, cut out.
pub fn backdrop(across: Option<&Across>) -> Option<Image> {
    let across = across?;
    let (picture, _) = load(Some(&across.path))?;
    let rects: Vec<Option<Rect>> = across
        .places
        .iter()
        .map(|r| {
            r.map(|(x, y, w, h)| {
                (
                    x as f64 / 10.0,
                    y as f64 / 10.0,
                    w as f64 / 10.0,
                    h as f64 / 10.0,
                )
            })
        })
        .collect();
    part(&picture, &rects, across.index)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_panel_shows_its_part_of_the_picture_as_arrangement_py_cuts_it() {
        let picture = Image::new(Mode::RGB, 400, 100, &[0, 0, 0]);
        let rects = [
            Some((0.0, 0.0, 100.0, 50.0)),
            None,
            Some((100.0, 0.0, 100.0, 50.0)),
        ];
        let left = part(&picture, &rects, 0).expect("placed");
        let right = part(&picture, &rects, 2).expect("placed");
        assert_eq!((left.width(), left.height()), (200, 100));
        assert_eq!((right.width(), right.height()), (200, 100));
        assert!(part(&picture, &rects, 1).is_none());
    }
}
