//! The pictures of saved themes for the app's lists (editor/server.py `_theme_preview`,
//! editor/drawings.py `theme_preview` and `draw_theme`): a theme as the panel shows it with
//! the person's choices, kept until the theme or a choice changes.

use std::collections::VecDeque;
use std::path::Path;
use std::sync::{Condvar, Mutex};

use indexmap::IndexMap;
use lp_config::Config;
use lp_pixels::{Filter, Image, Mode};
use lp_theme::versions;

/// How many pictures are kept (the oldest go first).
const KEPT: usize = 48;
/// How many are drawn at once; the others wait for a turn.
const AT_ONCE: usize = 2;

/// The pictures kept and the turns to draw one.
#[derive(Default)]
pub struct Previews {
    kept: Mutex<VecDeque<(String, Vec<u8>)>>,
    drawing: Mutex<usize>,
    turn: Condvar,
}

/// What a picture is drawn with.
pub struct Asked<'a> {
    pub folder: &'a Path,
    pub size: Option<(i64, i64)>,
    pub width: usize,
    pub accent: Option<String>,
    pub symbols: String,
    pub chosen: Vec<(String, bool)>,
    pub cards: Vec<String>,
    pub card_opacity: i64,
    pub light: bool,
}

/// Why no picture: the theme does not load (404), or it is not drawn (500).
pub enum Refused {
    Theme(String),
    Drawing(String),
}

impl Previews {
    fn kept(&self, key: &str) -> Option<Vec<u8>> {
        let kept = self.kept.lock().unwrap_or_else(|e| e.into_inner());
        kept.iter()
            .find(|(k, _)| k == key)
            .map(|(_, png)| png.clone())
    }

    /// The picture of `key`: the one kept, else drawn by `draw` when a turn comes (None where
    /// `gone` says nobody waits for it any more) and kept.
    pub fn picture(
        &self,
        key: &str,
        gone: impl Fn() -> bool,
        draw: impl FnOnce() -> Result<Vec<u8>, Refused>,
    ) -> Option<Result<Vec<u8>, Refused>> {
        if let Some(png) = self.kept(key) {
            return Some(Ok(png));
        }
        let mut drawing = self.drawing.lock().unwrap_or_else(|e| e.into_inner());
        while *drawing >= AT_ONCE {
            drawing = self.turn.wait(drawing).unwrap_or_else(|e| e.into_inner());
        }
        *drawing += 1;
        drop(drawing);
        let out = (|| {
            if let Some(png) = self.kept(key) {
                return Some(Ok(png)); // (drawn while this one waited)
            }
            if gone() {
                return None;
            }
            let png = match draw() {
                Ok(png) => png,
                Err(refused) => return Some(Err(refused)),
            };
            let mut kept = self.kept.lock().unwrap_or_else(|e| e.into_inner());
            while kept.len() >= KEPT {
                kept.pop_front();
            }
            kept.push_back((key.to_string(), png.clone()));
            Some(Ok(png))
        })();
        *self.drawing.lock().unwrap_or_else(|e| e.into_inner()) -= 1;
        self.turn.notify_one();
        out
    }
}

/// panel_sizes: every size of a panel Libre Panel drives, landscape, and on end for a bar (as
/// the catalogue's models have them): `size`, `shape` ("rect", "round") and the `panels`,
/// from tall to long.
pub fn panel_sizes() -> Vec<((i64, i64), &'static str, Vec<String>)> {
    let mut found: IndexMap<(i64, i64, &'static str), Vec<String>> = IndexMap::new();
    for model in lp_devices::models::MODELS.iter() {
        let ways: &[&str] = match lp_theme::defaults::is_bar(Some(model)) {
            true => &["landscape", "portrait"],
            false => &["landscape"],
        };
        for way in ways {
            let (w, h) = model.size(way);
            found
                .entry((w, h, model.shape))
                .or_default()
                .push(model.label());
        }
    }
    let mut ordered: Vec<_> = found.into_iter().collect();
    ordered.sort_by(|a, b| {
        let ratio = |((w, h, _), _): &((i64, i64, &str), Vec<String>)| *w as f64 / *h as f64;
        ratio(a).total_cmp(&ratio(b))
    });
    ordered
        .into_iter()
        .map(|((w, h, shape), mut names)| {
            names.sort_by(|a, b| a.chars().cmp(b.chars()));
            names.dedup();
            ((w, h), shape, names)
        })
        .collect()
}

/// _theme_sizes: how the theme comes on every panel size (Design → the theme's menu → Every
/// panel size): `version` the one shown there, `how` "made" for it, "laid_out" from `from`
/// (its version for the nearest shape), or "scaled" into it. Refused::Theme where there is
/// no such theme.
pub fn theme_sizes(
    theme_id: &str,
    env: &lp_theme::Env,
    places: &versions::Places,
) -> Result<lp_py::Value, Refused> {
    use lp_py::{Dict, Value};
    versions::find_theme(theme_id, places).map_err(|_| Refused::Theme("not found".into()))?;
    let mut out = Vec::new();
    for ((w, h), shape, panels) in panel_sizes() {
        let way = if h > w { "portrait" } else { "landscape" };
        let version = versions::theme_for(theme_id, way, Some((w, h)), places);
        let loaded = versions::find_theme(&version, places)
            .and_then(|folder| lp_theme::load_theme(&folder, env));
        let Ok(theme) = loaded else {
            continue;
        };
        let fitted =
            lp_theme::adapt::laid_out_anew(&theme, Some((w, h)), "custom", &places.builtin, env)
                .map_err(|e| Refused::Drawing(e.msg))?;
        let (how, source) = match fitted {
            Some(fitted) => {
                let name = fitted.root.as_deref().and_then(Path::file_name);
                let source = name.map_or(version.clone(), |n| n.to_string_lossy().into_owned());
                ("laid_out", source)
            }
            None if (theme.width, theme.height) == (w, h) => ("made", version.clone()),
            None => ("scaled", version.clone()),
        };
        let mut entry = Dict::default();
        entry.insert(
            "size".into(),
            Value::List(vec![Value::int(w), Value::int(h)]),
        );
        entry.insert("shape".into(), Value::Str(shape.into()));
        entry.insert(
            "panels".into(),
            Value::List(panels.into_iter().map(Value::Str).collect()),
        );
        entry.insert("version".into(), Value::Str(version));
        entry.insert("how".into(), Value::Str(how.into()));
        entry.insert("from".into(), Value::Str(source));
        out.push(Value::Dict(entry));
    }
    let mut told = Dict::default();
    told.insert("theme".into(), Value::Str(theme_id.into()));
    told.insert("sizes".into(), Value::List(out));
    Ok(Value::Dict(told))
}

/// _size_of: `"960x320"`: a panel's size (None for anything else).
pub fn size_of(text: &str) -> Option<(i64, i64)> {
    let (w, h) = text.split_once('x')?;
    let number = |part: &str| {
        let fits = (2..=5).contains(&part.len()) && part.bytes().all(|b| b.is_ascii_digit());
        fits.then(|| part.parse::<i64>().ok()).flatten()
    };
    Some((number(w)?, number(h)?))
}

/// chosen_switches: the person's choice of the switches of the theme `theme_id` (those for
/// every theme, then its own), sorted by name.
pub fn chosen_switches(config: &Config, theme_id: &str) -> Vec<(String, bool)> {
    let mut chosen: IndexMap<String, bool> = IndexMap::new();
    let base = versions::version_base(theme_id).unwrap_or(theme_id);
    for name in ["*", base] {
        if let Some(switches) = config.switches.get(name) {
            chosen.extend(switches.iter().map(|(k, v)| (k.clone(), *v)));
        }
    }
    let mut chosen: Vec<(String, bool)> = chosen.into_iter().collect();
    chosen.sort_by(|a, b| a.0.chars().cmp(b.0.chars()));
    chosen
}

/// theme_preview: the PNG of the saved theme in `asked.folder`, laid out on a panel of
/// `asked.size` where one is named, as the panel shows it with the choices: draw_theme.
pub fn theme_preview(
    asked: &Asked,
    env: &lp_theme::Env,
    places: &versions::Places,
) -> Result<Vec<u8>, Refused> {
    let theme = lp_theme::load_theme(asked.folder, env).map_err(|e| Refused::Theme(e.msg))?;
    let theme = match asked.size {
        Some(size) => lp_theme::adapt::laid_out(&theme, Some(size), "custom", &places.builtin, env)
            .map_err(|e| Refused::Theme(e.msg))?,
        None => theme,
    };
    draw_theme(&theme, asked, env).map_err(Refused::Drawing)
}

/// draw_theme: `theme` as a PNG `asked.width` wide at most (on a panel of `asked.size`:
/// scaled into it where it is not laid out on it).
fn draw_theme(
    theme: &lp_theme::Theme,
    asked: &Asked,
    env: &lp_theme::Env,
) -> Result<Vec<u8>, String> {
    let dressed = versions::with_accent(theme, asked.accent.as_deref());
    let dressed = versions::with_light_colours(&dressed, asked.light).map_err(|e| e.msg)?;
    let dressed = versions::with_card_opacity(&dressed, asked.card_opacity);
    let dressed = lp_theme::modules::slots::with_cards(&dressed, &asked.cards, "first", "light")
        .unwrap_or(dressed);
    let options = lp_render::Options {
        preview: true,
        animate: false,
        dither: false,
    };
    let mut renderer = lp_render::Renderer::new(dressed, env, options).map_err(|e| e.0)?;
    crate::plugins::attach(&mut renderer);
    renderer.symbols = asked.symbols.clone();
    renderer.switches = asked.chosen.iter().cloned().collect();
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    let mut snapshot = lp_services::demo::demo_snapshot(
        1_700_000_000.0,
        lp_services::demo::HISTORY_SAMPLES,
        zone,
    )?;
    snapshot.images = demo_pictures(&env.fonts_dir)?;
    let drawn = renderer.render(snapshot, lp_render::video::monotonic());
    renderer.close();
    let (frame, _boxes) = drawn.map_err(|why| format!("not drawn here yet: {}", why.0))?;
    let mut frame = frame.convert(Mode::RGB).map_err(|e| e.to_string())?;
    if let Some((w, h)) = asked.size {
        let size = (w as usize, h as usize);
        if frame.size() != size {
            // (as the panel's loop fits it)
            frame = frame
                .pad(size, Filter::Lanczos, Some(&[0, 0, 0]), (0.5, 0.5))
                .map_err(|e| e.to_string())?;
        }
    }
    if frame.width() > asked.width {
        let height =
            (frame.height() as f64 * asked.width as f64 / frame.width() as f64).round_ties_even();
        frame = frame
            .resize(
                asked.width,
                (height as usize).max(1),
                Filter::Bicubic,
                None,
                None,
            )
            .map_err(|e| e.to_string())?;
    }
    png(&frame)
}

/// The demo's pictures (an album cover, the player's icon, a game's logo), made once.
fn demo_pictures(fonts: &Path) -> Result<IndexMap<String, Image>, String> {
    static MADE: std::sync::OnceLock<Result<IndexMap<String, Image>, String>> =
        std::sync::OnceLock::new();
    MADE.get_or_init(|| lp_services::demo::pictures(fonts))
        .clone()
}

/// The picture as Pillow saves a PNG (compress_level 6).
fn png(frame: &Image) -> Result<Vec<u8>, String> {
    lp_devices::png::encode_with(frame, lp_devices::png::Compression::new(6))
}
