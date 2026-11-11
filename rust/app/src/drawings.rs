//! The editor's pictures (editor/drawings.py): Design's and Look's picture of a theme with a
//! test message on it, each module kind drawn for the library, the starting layouts of a
//! grid, a module's parts as plain widgets, and a theme adapted to another panel.

use std::path::Path;
use std::rc::Rc;

use indexmap::IndexMap;
use lp_config::Config;
use lp_py::{Dict, Value};
use lp_render::overlays::{Toast, ToastLayer};
use lp_render::{Renderer, Snapshot};
use lp_theme::{Env, Kind, Theme};
use num_bigint::BigInt;

/// how far into its life a preview shows a test message: drawn in full
const NOTICE_AGE_S: f64 = 1.2;

/// What a drawing came to where it is no picture: said to the page (400), or what Python's
/// server does not catch (left unanswered, logged).
pub enum Refused {
    Said(String),
    Failed(String),
}

type Answer<T> = Result<T, Refused>;

fn theme_error(e: lp_theme::Error) -> Refused {
    match e.kind {
        Kind::Theme => Refused::Said(e.msg),
        kind => Refused::Failed(format!("{}: {}", kind.name(), e.msg)),
    }
}

fn unported(e: impl std::fmt::Debug) -> Refused {
    Refused::Failed(format!("not drawn by this build yet: {e:?}"))
}

pub(crate) fn base64(bytes: &[u8]) -> String {
    const LETTERS: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let n = chunk
            .iter()
            .enumerate()
            .fold(0u32, |n, (i, b)| n | u32::from(*b) << (16 - 8 * i));
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(LETTERS[(n >> (18 - 6 * i) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

/// _png: a frame as Pillow saves a PNG.
fn png(frame: &lp_pixels::Image) -> Answer<Vec<u8>> {
    let rgb = frame
        .convert(lp_pixels::Mode::RGB)
        .map_err(|e| Refused::Failed(e.to_string()))?;
    lp_devices::png::encode_with(&rgb, lp_devices::png::Compression::new(6))
        .map_err(Refused::Failed)
}

/// The demo's readings at `t` (now where None), with its pictures.
fn demo(t: Option<f64>, fonts: &Path) -> Answer<Snapshot> {
    let t = t.unwrap_or_else(|| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0.0, |d| d.as_secs_f64())
    });
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    let mut snapshot =
        lp_services::demo::demo_snapshot(t, lp_services::demo::HISTORY_SAMPLES, zone)
            .map_err(Refused::Failed)?;
    snapshot.images = demo_pictures(fonts)?;
    Ok(snapshot)
}

fn demo_pictures(fonts: &Path) -> Answer<IndexMap<String, lp_pixels::Image>> {
    static MADE: std::sync::OnceLock<Result<IndexMap<String, lp_pixels::Image>, String>> =
        std::sync::OnceLock::new();
    MADE.get_or_init(|| lp_services::demo::pictures(fonts))
        .clone()
        .map_err(Refused::Failed)
}

/// The person's choice of the switches of `theme`, as config.toml has them.
fn switches(theme: &Theme, config: &Config) -> IndexMap<String, bool> {
    let chosen: Dict = config
        .switches
        .iter()
        .map(|(name, switches)| {
            let table = switches
                .iter()
                .map(|(k, on)| (k.clone(), Value::Bool(*on)))
                .collect();
            (name.clone(), Value::Dict(table))
        })
        .collect();
    lp_theme::versions::switches_for(theme, &chosen)
        .into_iter()
        .filter_map(|(name, on)| match on {
            Value::Bool(on) => Some((name, on)),
            _ => None,
        })
        .collect()
}

fn fan_choices(config: &Config) -> lp_render::fan::Choices {
    let hardware = &config.hardware;
    lp_render::fan::Choices {
        look: hardware.fan_look.clone(),
        paint: hardware.fan_paint.clone(),
        looks: hardware.fan_looks.clone().into_iter().collect(),
        paints: hardware.fan_paints.clone().into_iter().collect(),
    }
}

fn said(text: &str) -> String {
    lp_i18n::t(text)
}

/// control.probes: a test message of each kind, as the services make theirs: the name the page
/// gives it and the toast.
pub fn probes(look: &str) -> IndexMap<&'static str, (String, Toast)> {
    let test = said("Test message");
    let probe = |kind: &str,
                 name: String,
                 text: String,
                 label: String,
                 rank: i64,
                 seconds: Option<f64>,
                 icon: Option<&str>,
                 level: &str,
                 more: Dict| {
        let mut payload = Dict::new();
        payload.insert(
            "label".into(),
            Value::Str(if label.is_empty() {
                name.clone()
            } else {
                label
            }),
        );
        payload.extend(more);
        let toast = Toast {
            text,
            icon: icon.map(str::to_string),
            level: level.into(),
            seconds,
            kind: kind.into(),
            rank,
            payload,
            service: "editor".into(),
            key: String::new(),
        };
        (name, toast)
    };
    let progress = |p: f64| -> Dict {
        let mut d = Dict::new();
        d.insert("progress".into(), Value::Float(p));
        d
    };
    let mut volume = progress(0.42);
    if !look.is_empty() {
        volume.insert("look".into(), Value::Str(look.into()));
    }
    let (now, _) = moment_now();
    let time = format!("{:02}:{:02}", now.hour, now.minute);
    let panel_text = lp_i18n::t_format("Panel restarted {time}", &[("time", Value::Str(time))])
        .unwrap_or_else(|why| why.msg);
    let mut out = IndexMap::new();
    out.insert(
        "volume",
        probe(
            "volume",
            said("Volume"),
            "42".into(),
            String::new(),
            0,
            None,
            None,
            "info",
            volume,
        ),
    );
    out.insert(
        "music",
        probe(
            "music",
            said("Music"),
            test.clone(),
            said("Now playing"),
            1,
            Some(5.0),
            Some("music"),
            "info",
            progress(0.4),
        ),
    );
    out.insert(
        "warning",
        probe(
            "warning",
            said("Warning"),
            test.clone(),
            String::new(),
            2,
            Some(5.0),
            Some("temperature"),
            "warning",
            progress(0.9),
        ),
    );
    out.insert(
        "panel",
        probe(
            "panel",
            said("Panel"),
            panel_text,
            String::new(),
            2,
            Some(8.0),
            None,
            "info",
            Dict::new(),
        ),
    );
    out.insert(
        "device",
        probe(
            "device",
            said("Device"),
            test.clone(),
            String::new(),
            1,
            Some(4.0),
            Some("usb"),
            "info",
            Dict::new(),
        ),
    );
    out.insert(
        "game",
        probe(
            "game",
            said("Game"),
            test,
            said("Game mode"),
            1,
            Some(3.0),
            Some("gamepad"),
            "info",
            Dict::new(),
        ),
    );
    out
}

fn moment_now() -> (lp_py::datetime::DateTime, bool) {
    let since = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64());
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    lp_py::zones::fromtimestamp(zone, since)
        .unwrap_or((lp_py::datetime::DateTime::date(1970, 1, 1), false))
}

/// with_notice: `frame` with the test message of `kind` drawn on it in the theme's toast style,
/// a moment after it began; None for an unknown kind.
fn with_notice(
    renderer: &Renderer,
    frame: lp_pixels::Image,
    kind: &str,
    look: &str,
) -> Answer<Option<lp_pixels::Image>> {
    let Some((_name, toast)) = probes(look).swap_remove(kind) else {
        return Ok(None);
    };
    let mut layer = ToastLayer::new(renderer).map_err(unported)?;
    layer.add([Rc::new(toast)]);
    layer.advance(0.0, false).map_err(unported)?;
    let drawn = layer
        .draw(&frame, NOTICE_AGE_S, false, renderer)
        .map_err(unported)?;
    Ok(Some(drawn.unwrap_or(frame)))
}

fn int_value(n: i64) -> Value {
    Value::int(n)
}

pub fn grid_settings(theme: &Theme) -> lp_theme::grid::Settings {
    let int = |g: &Dict, k: &str| match g.get(k) {
        Some(Value::Int(n)) => i64::try_from(n).unwrap_or(0),
        Some(Value::Bool(b)) => i64::from(*b),
        _ => 0,
    };
    theme
        .grid
        .as_ref()
        .map_or(lp_theme::grid::Settings::default(), |g| {
            lp_theme::grid::Settings {
                columns: int(g, "columns"),
                rows: int(g, "rows"),
                gap: int(g, "gap"),
                margin: int(g, "margin"),
            }
        })
}

/// Grid.to_dict.
fn grid_dict(grid: &lp_theme::grid::Grid) -> Value {
    let mut d = Dict::new();
    d.insert("x".into(), int_value(grid.x));
    d.insert("y".into(), int_value(grid.y));
    d.insert("cell_w".into(), Value::Float(grid.cell_w));
    d.insert("cell_h".into(), Value::Float(grid.cell_h));
    d.insert("gap".into(), int_value(grid.gap));
    d.insert("columns".into(), int_value(grid.columns));
    d.insert("rows".into(), int_value(grid.rows));
    Value::Dict(d)
}

fn boxes_value(boxes: &IndexMap<String, [i64; 4]>) -> Value {
    Value::Dict(
        boxes
            .iter()
            .map(|(k, b)| {
                (
                    k.clone(),
                    Value::List(b.iter().map(|n| int_value(*n)).collect()),
                )
            })
            .collect(),
    )
}

/// The readings and their history, live, laid over the demo's.
pub type Live = (
    IndexMap<String, lp_render::Reading>,
    IndexMap<String, Vec<f64>>,
);

/// render: Design's and Look's picture of the theme `raw` (its JSON; `root`: the folder its
/// assets come from), with the demo values and, `live`, the readings and their history over
/// them; with the test message `notice` on it: the PNG (base64), the boxes, the warnings, the
/// grid and the modules' boxes.
pub fn render(
    raw: &Value,
    root: Option<&Path>,
    config: &Config,
    live: Option<Live>,
    notice: &Value,
    look: &Value,
    env: &Env,
) -> Answer<Value> {
    let theme = lp_theme::parse_theme(raw, root, env).map_err(theme_error)?;
    let mut snapshot = demo(None, &env.fonts_dir)?;
    if let Some((readings, history)) = live {
        snapshot.readings.extend(readings);
        snapshot.history.extend(history);
    }
    let dressed = lp_theme::modules::slots::with_cards(&theme, &config.cards, "first", "light")
        .unwrap_or_else(|| theme.clone());
    let options = lp_render::Options {
        preview: true,
        animate: false,
        dither: false,
    };
    let mut renderer = Renderer::new(dressed, env, options).map_err(unported)?;
    renderer.fan_choices = fan_choices(config);
    renderer.symbols = config.symbols.clone();
    renderer.switches = switches(&theme, config);
    let drawn = renderer.render(snapshot, lp_render::video::monotonic());
    let shown = drawn.map_err(unported).and_then(|(frame, boxes)| {
        if !notice.truthy() {
            return Ok((frame, boxes));
        }
        // (a test message on it, as the theme's toast style shows it)
        let style = match look {
            Value::Str(s) => s.as_str(),
            _ => "",
        };
        let shown = match notice {
            Value::Str(kind) => with_notice(&renderer, frame, kind, style)?,
            _ => None,
        };
        match shown {
            Some(frame) => Ok((frame, boxes)),
            None => Err(Refused::Said(
                "notice must be the kind of a test message".into(),
            )),
        }
    });
    renderer.close();
    let (frame, boxes) = shown?;
    let has_modules = theme
        .widgets
        .iter()
        .any(|w| w.get("type").and_then(Value::as_str) == Some("module"));
    let grid = lp_theme::grid::make_grid(
        theme.width,
        theme.height,
        grid_settings(&theme),
        &theme.model,
    );
    let warnings: Vec<Value> = theme
        .warnings
        .iter()
        .cloned()
        .chain(renderer.warnings())
        .map(Value::Str)
        .collect();
    let mut told = Dict::new();
    told.insert("png".into(), Value::Str(base64(&png(&frame)?)));
    told.insert("boxes".into(), boxes_value(&boxes));
    told.insert("width".into(), int_value(theme.width));
    told.insert("height".into(), int_value(theme.height));
    told.insert("warnings".into(), Value::List(warnings));
    told.insert("grid".into(), grid_dict(&grid));
    told.insert(
        "grid_active".into(),
        Value::Bool(has_modules || theme.grid.is_some()),
    );
    told.insert("module_boxes".into(), boxes_value(&renderer.module_boxes));
    Ok(Value::Dict(told))
}

/// module_previews: each module kind in a 2x1 box with the given look, for the library
/// (PNGs, base64).
pub fn module_previews(palette: &Value, style: &Value, symbols: &str, env: &Env) -> Answer<Value> {
    let (arctic_palette, arctic_style) =
        lp_theme::modules::info::look_theme_parts("arctic").unwrap_or_default();
    let palette = match palette.truthy() {
        true => palette.clone(),
        false => Value::Dict(arctic_palette),
    };
    let style = match style.truthy() {
        true => style.clone(),
        false => Value::Dict(arctic_style),
    };
    let snapshot = demo(Some(1_700_000_000.0), &env.fonts_dir)?;
    let mut previews = Dict::new();
    for kind in lp_theme::tables::MODULE_KINDS {
        let source = match *kind {
            "ring" => "cpu",
            "stat" => "gpu",
            "graph" => "cpu",
            _ => "cpu",
        };
        let text = format!(
            r##"{{"format": "libre-panel-theme/1", "display": {{"width": 460, "height": 220}},
            "background": {{"color": "#000000"}},
            "grid": {{"columns": 2, "rows": 1, "gap": 10, "margin": 10}},
            "widgets": [{{"type": "module", "id": "m", "module": "{kind}", "cols": 2,
            "source": "{source}"}}]}}"##
        );
        let mut data = lp_py::json::loads(&text).map_err(|e| Refused::Failed(e.to_string()))?;
        if let Value::Dict(d) = &mut data {
            d.insert("palette".into(), palette.clone());
            d.insert("style".into(), style.clone());
        }
        let theme = lp_theme::parse_theme(&data, None, env).map_err(theme_error)?;
        let options = lp_render::Options {
            preview: true,
            animate: false,
            dither: false,
        };
        let mut renderer = Renderer::new(theme, env, options).map_err(unported)?;
        renderer.symbols = symbols.to_string();
        let drawn = renderer.render(snapshot.clone(), lp_render::video::monotonic());
        let at = renderer.module_boxes.get("m").copied();
        renderer.close();
        let (frame, _boxes) = drawn.map_err(unported)?;
        let [x, y, w, h] = at.ok_or_else(|| Refused::Failed("KeyError: 'm'".into()))?;
        let part = frame.crop(x, y, x + w, y + h);
        previews.insert((*kind).to_string(), Value::Str(base64(&png(&part)?)));
    }
    Ok(Value::Dict(previews))
}

/// A theme drawn still for the library, the demo's readings at a fixed moment: its PNG in
/// base64.
pub fn still_picture(theme: Theme, symbols: &str, env: &Env) -> Answer<Value> {
    let snapshot = demo(Some(1_700_000_000.0), &env.fonts_dir)?;
    let options = lp_render::Options {
        preview: true,
        animate: false,
        dither: false,
    };
    let mut renderer = Renderer::new(theme, env, options).map_err(unported)?;
    renderer.symbols = symbols.to_string();
    let drawn = renderer.render(snapshot, lp_render::video::monotonic());
    renderer.close();
    let (frame, _boxes) = drawn.map_err(unported)?;
    Ok(Value::Str(base64(&png(&frame)?)))
}

/// fan_look: a fan of that look as a picture (PNG), in its square frame and the app's accent:
/// the tiles of Settings show them. None for a look or colours there are not.
pub fn fan_look(look: &str, paint: &str, fonts: &Path) -> Answer<Option<Vec<u8>>> {
    let known = lp_theme::tables::FAN_LOOKS.contains(&look)
        && lp_theme::tables::FAN_PAINTS.contains(&paint);
    if !known {
        return Ok(None);
    }
    // (a fan that runs: lit, drawn standing, its blades sharp)
    let mut rotor = lp_render::fan::Rotor::settled("preview", Some(1800.0), 3000.0);
    rotor.speed = 0.0;
    let image = lp_render::fan::picture(
        &lp_render::fan::pictures_dir(fonts),
        [34, 211, 238],
        112,
        &rotor,
        lp_render::fan::Frame::Square,
        lp_render::fan_looks::look(look),
        paint == "own",
    )
    .map_err(|e| Refused::Failed(format!("{e:?}")))?;
    lp_devices::png::encode_with(&image, lp_devices::png::Compression::new(6))
        .map(Some)
        .map_err(Refused::Failed)
}

/// symbols_look: the weather symbols and the moon in that look as a picture (PNG), on a
/// panel's dark ground: the sun behind a cloud and snow, lit, by day, and the moon waxing.
/// None for a look there is not.
pub fn symbols_look(pack: &str, env: &Env) -> Answer<Option<Vec<u8>>> {
    if !lp_theme::tables::SYMBOL_PACKS.contains(&pack) {
        return Ok(None);
    }
    let (palette, _) = lp_theme::modules::info::look_theme_parts("arctic").unwrap_or_default();
    let text = r##"{"format": "libre-panel-theme/1", "display": {"width": 360, "height": 120},
        "background": {"color": "#0b1016"}, "widgets": [
        {"type": "symbol", "id": "sun", "x": 0, "y": 0, "size": 120, "finish": "lit",
         "sensor": "weather.code"},
        {"type": "symbol", "id": "snow", "x": 120, "y": 0, "size": 120, "finish": "lit",
         "sensor": "weather.hour.1.code"},
        {"type": "moon", "id": "moon", "x": 250, "y": 10, "size": 100}]}"##;
    let mut data = lp_py::json::loads(text).map_err(|e| Refused::Failed(e.to_string()))?;
    if let Value::Dict(d) = &mut data {
        d.insert("palette".into(), Value::Dict(palette));
    }
    let theme = lp_theme::parse_theme(&data, None, env).map_err(theme_error)?;
    let mut snapshot = demo(Some(1_700_000_000.0), &env.fonts_dir)?;
    for (key, value) in [
        ("weather.code", Value::int(2)),
        ("weather.is_day", Value::int(1)),
        ("moon.phase", Value::Float(0.36)),
        ("weather.hour.1.code", Value::int(73)),
        ("weather.hour.1.day", Value::int(1)),
    ] {
        snapshot
            .readings
            .insert(key.into(), lp_render::Reading::of(value));
    }
    let options = lp_render::Options {
        preview: true,
        animate: false,
        dither: false,
    };
    let mut renderer = Renderer::new(theme, env, options).map_err(unported)?;
    renderer.symbols = pack.to_string();
    let drawn = renderer.render(snapshot, lp_render::video::monotonic());
    renderer.close();
    let (frame, _boxes) = drawn.map_err(unported)?;
    png(&frame).map(Some)
}

/// int(value) as Python takes it: its exception and message where it raises.
pub fn int_of(value: Option<&Value>) -> Result<BigInt, (&'static str, String)> {
    let not = |name: &str| {
        (
            "TypeError",
            format!(
                "int() argument must be a string, a bytes-like object or a real number, not \
                 '{name}'"
            ),
        )
    };
    match value {
        None | Some(Value::None) => Err(not("NoneType")),
        Some(Value::Int(n)) => Ok(n.clone()),
        Some(Value::Bool(b)) => Ok(BigInt::from(u8::from(*b))),
        Some(Value::Float(f)) if f.is_nan() => {
            Err(("ValueError", "cannot convert float NaN to integer".into()))
        }
        Some(Value::Float(f)) if f.is_infinite() => Err((
            "OverflowError",
            "cannot convert float infinity to integer".into(),
        )),
        Some(Value::Float(f)) => {
            Ok(num_bigint::ToBigInt::to_bigint(&f.trunc()).unwrap_or_default())
        }
        Some(Value::Str(s)) => lp_py::int_of_str(s).ok_or_else(|| {
            (
                "ValueError",
                format!("invalid literal for int() with base 10: {}", lp_py::repr(s)),
            )
        }),
        Some(other) => Err(not(other.type_name())),
    }
}

/// _templates: `{"width", "height", "model"}`: the grid of that panel and the starting layouts
/// of modules on it.
pub fn templates(payload: &Value) -> Answer<Value> {
    // (payload["width"] of what is no dict: a TypeError, caught)
    let Value::Dict(d) = payload else {
        let why = match payload {
            Value::List(_) => "list indices must be integers or slices, not str".to_string(),
            Value::Str(_) => "string indices must be integers, not 'str'".to_string(),
            other => format!("'{}' object is not subscriptable", other.type_name()),
        };
        return Err(Refused::Said(format!("bad size: {why}")));
    };
    let size = |key: &str| -> Answer<i64> {
        let Some(value) = d.get(key) else {
            return Err(Refused::Said(format!("bad size: {}", lp_py::repr(key))));
        };
        match int_of(Some(value)) {
            Ok(n) => Ok(i64::try_from(&n).unwrap_or(i64::MAX)),
            Err(("OverflowError", msg)) => Err(Refused::Failed(format!("OverflowError: {msg}"))),
            Err((_, msg)) => Err(Refused::Said(format!("bad size: {msg}"))),
        }
    };
    let (width, height) = (size("width")?, size("height")?);
    let model = d.get("model").map_or("custom".to_string(), Value::str);
    let grid =
        lp_theme::grid::make_grid(width, height, lp_theme::grid::Settings::default(), &model);
    let mut told = Dict::new();
    told.insert("grid".into(), grid_dict(&grid));
    told.insert(
        "templates".into(),
        lp_theme::modules::info::templates(grid.columns, grid.rows),
    );
    Ok(Value::Dict(told))
}

fn get<'a>(payload: &'a Value, key: &str) -> Answer<Option<&'a Value>> {
    match payload {
        Value::Dict(d) => Ok(d.get(key)),
        other => Err(Refused::Failed(format!(
            "AttributeError: '{}' object has no attribute 'get'",
            other.type_name()
        ))),
    }
}

/// _detach: a module's parts as plain widgets.
pub fn detach(payload: &Value, root: Option<&Path>, env: &Env) -> Answer<Value> {
    let raw = get(payload, "theme")?.cloned().unwrap_or(Value::None);
    let theme = lp_theme::parse_theme(&raw, root, env).map_err(theme_error)?;
    let id = get(payload, "id")?.map_or(String::new(), Value::str);
    let widgets = lp_theme::modules::info::detach(&theme, env, &id)
        .map_err(|e| Refused::Failed(format!("{}: {}", e.kind.name(), e.msg)))?;
    let mut told = Dict::new();
    told.insert(
        "widgets".into(),
        Value::List(widgets.into_iter().map(Value::Dict).collect()),
    );
    Ok(Value::Dict(told))
}

/// _adapt: a theme made for another panel: `{"theme", "model", "orientation", "mode",
/// "width", "height"}`.
pub fn adapt(payload: &Value, env: &Env) -> Answer<Value> {
    let raw = get(payload, "theme")?.cloned().unwrap_or(Value::None);
    let text = |key: &str, default: &str| -> Answer<String> {
        Ok(get(payload, key)?.map_or(default.to_string(), Value::str))
    };
    let (model, orientation, mode) = (
        text("model", "custom")?,
        text("orientation", "landscape")?,
        text("mode", "scale")?,
    );
    let size = |key: &str| -> Answer<BigInt> {
        let given = get(payload, key)?.filter(|v| v.truthy());
        match given {
            None => Ok(BigInt::from(0)),
            Some(value) => match int_of(Some(value)) {
                Ok(n) => Ok(n),
                Err(("OverflowError", msg)) => {
                    Err(Refused::Failed(format!("OverflowError: {msg}")))
                }
                Err((_, msg)) => Err(Refused::Said(msg)),
            },
        }
    };
    let (width, height) = (size("width")?, size("height")?);
    let adapted =
        lp_theme::adapt::adapt_theme(&raw, &model, &orientation, &mode, &width, &height, env)
            .map_err(|e| match e.kind {
                Kind::Theme | Kind::Key | Kind::Value | Kind::Type => Refused::Said(e.msg),
                kind => Refused::Failed(format!("{}: {}", kind.name(), e.msg)),
            })?;
    let mut told = Dict::new();
    told.insert("theme".into(), adapted);
    Ok(Value::Dict(told))
}

#[cfg(test)]
mod tests {
    #[test]
    fn base64_is_the_standard_alphabet_with_padding() {
        assert_eq!(super::base64(b""), "");
        assert_eq!(super::base64(b"f"), "Zg==");
        assert_eq!(super::base64(b"fo"), "Zm8=");
        assert_eq!(super::base64(b"foobar"), "Zm9vYmFy");
    }
}
