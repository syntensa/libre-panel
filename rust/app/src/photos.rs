//! Pictures to keep or share (server.py's _panels_photo and _round_picture): all panels where
//! they stand, each with what it shows now; and the report of a round of History as the panel
//! shows it, in a picture of the panel, kept in the person's Pictures folder.

use std::path::{Path, PathBuf};

use lp_devices::models::find_model;
use lp_py::{Dict, Value};
use lp_render::photo::{photo, Placed};

use crate::arrangement::{self, Told};
use crate::background::BackgroundApp;

/// What a route answers where it makes no picture: its status and the error.
pub type Refused = (u16, String);

fn conflict(why: String) -> Refused {
    (409, why)
}

fn failed(why: impl std::fmt::Display) -> Refused {
    (500, why.to_string())
}

/// The model of a panel: as set, else as its loop said it is.
fn model_of(
    device: &lp_config::DeviceConfig,
    told: Option<&Told>,
) -> Option<&'static lp_devices::models::PanelModel> {
    let told = told.map_or("", |told| told.model.as_str());
    find_model(&device.model).or_else(|| (!told.is_empty()).then(|| find_model(told)).flatten())
}

/// _panels_photo: one picture of all panels that are on, where they stand (Settings →
/// Panels), each with what it shows now, as a PNG, and the name to keep it by.
pub fn panels(
    app: Option<&BackgroundApp>,
    config_file: &Path,
    env: &lp_theme::Env,
) -> Result<(Vec<u8>, String), Refused> {
    let config = lp_config::load_config(config_file, lp_config::platform())
        .map_err(|why| conflict(why.msg))?;
    let nothing = || (404, lp_i18n::t("the panels show no picture now"));
    let app = app.ok_or_else(nothing)?;
    let statuses = app.panel.told(config.panels.len());
    let devices: Vec<&lp_config::DeviceConfig> = std::iter::once(&config.device)
        .chain(config.panels.iter().map(|panel| &panel.device))
        .collect();
    let on: Vec<bool> = std::iter::once(true)
        .chain(config.panels.iter().map(|panel| panel.on))
        .collect();
    let mut placed = Vec::new();
    for (n, rect) in arrangement::laid_out(&config, &statuses)
        .into_iter()
        .enumerate()
    {
        let Some(rect) = rect.filter(|_| on[n]) else {
            continue;
        };
        let model = model_of(devices[n], statuses[n].as_ref());
        let frame = app.panel.shown_frame(n as i64 + 1);
        placed.push(Placed {
            rect,
            frame: frame.map(|frame| (*frame).clone()),
            round: model.is_some_and(|model| model.shape == "round"),
            number: n + 1,
        });
    }
    if !placed.iter().any(|panel| panel.frame.is_some()) {
        return Err(nothing());
    }
    let picture = photo(env, &placed).map_err(|why| failed(why.0))?;
    let picture = picture.ok_or_else(nothing)?;
    let png = lp_devices::png::encode_optimized(&picture).map_err(failed)?;
    let [year, month, day, ..] = crate::share::local_time(crate::share::now());
    Ok((
        png,
        format!("libre-panel-{year:04}-{month:02}-{day:02}.png"),
    ))
}

/// what no file name may hold (Windows): history.py's NOT_IN_NAMES
fn not_in_names(c: char) -> bool {
    matches!(c, '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|') || (c as u32) < 0x20
}

/// picture_name: a file name for the picture of a round: the game's name, without what a
/// file name cannot hold, and when the round ended ("Starfall 2026-10-24 21-48.png").
pub fn picture_name(game: &Value, ended: f64) -> String {
    let game = match game {
        Value::Str(game) => game.as_str(),
        _ => "",
    };
    let cleaned: String = game
        .chars()
        .map(|c| if not_in_names(c) { ' ' } else { c })
        .collect();
    let words: Vec<&str> = cleaned.split_whitespace().collect();
    let joined = words.join(" ");
    let trimmed: String = joined.trim_matches([' ', '.']).chars().take(60).collect();
    let name = lp_py::strip(&trimmed);
    let name = if name.is_empty() { "Libre Panel" } else { name };
    let [year, month, day, hour, minute, _] = crate::share::local_time(ended);
    format!("{name} {year:04}-{month:02}-{day:02} {hour:02}-{minute:02}.png")
}

/// free_name: `name` in `folder`, or "name (2).png" and so on where a file has it already.
pub fn free_name(folder: &Path, name: &str) -> PathBuf {
    let (stem, suffix) = match name.rfind('.') {
        Some(at) if at > 0 => (&name[..at], &name[at..]),
        _ => (name, ""),
    };
    let mut path = folder.join(name);
    let mut n = 2;
    while path.exists() {
        path = folder.join(format!("{stem} ({n}){suffix}"));
        n += 1;
    }
    path
}

/// round_report: the report of the round `ident` against the last `compare` rounds of its
/// program before it, and whether it is the last round kept. None for a round that is not
/// kept or had no frame rates.
fn round_report(path: &Path, ident: i64, compare: i64) -> Option<(Dict, bool)> {
    if !path.is_file() {
        return None;
    }
    let con = lp_services::memory_store::read_only(path).ok()?;
    let found = lp_services::round_report::build(&con, Some(ident), 0.0, compare).ok()??;
    let last: Option<i64> = con
        .query_row(
            "SELECT id FROM sessions ORDER BY started DESC LIMIT 1",
            [],
            |row| row.get(0),
        )
        .ok();
    Some((found, last == Some(ident)))
}

/// The person's Pictures folder.
fn pictures_folder() -> PathBuf {
    use lp_services::downloads::{home, pictures_folder, system_folder, Known};
    let places = lp_services::steam::Places::here();
    PathBuf::from(pictures_folder(system_folder(Known::Pictures), &home(&places)).text())
}

/// A value of `[services.memory]` as Python's truth and str() take it: "" for none.
fn text_of(value: Option<&lp_py::toml::Toml>) -> String {
    use lp_py::toml::Toml;
    match value {
        Some(Toml::Str(text)) => text.clone(),
        Some(Toml::Bool(true)) => "True".into(),
        Some(Toml::Int(n)) if *n != 0.into() => n.to_string(),
        Some(Toml::Float(f)) if *f != 0.0 => lp_py::format::str_of(&Value::Float(*f)),
        _ => String::new(),
    }
}

/// _round_picture: the report of the round `ident` (Status → History) as the panel shows it,
/// in a picture of the panel, kept in the person's Pictures folder (in `Libre Panel`), which
/// then opens: `{"file", "folder", "opened"}`.
pub fn round_picture(
    app: Option<&BackgroundApp>,
    config_file: &Path,
    places: &lp_theme::versions::Places,
    env: &lp_theme::Env,
    ident: i64,
) -> Result<Dict, Refused> {
    let config = lp_config::load_config(config_file, lp_config::platform())
        .map_err(|why| conflict(why.msg))?;
    let none = lp_py::toml::Table::new();
    let memory = config.services.options.get("memory").unwrap_or(&none);
    let compare = match memory.get("report_compare") {
        Some(lp_py::toml::Toml::Int(n)) if *n > 0.into() => i64::try_from(n).unwrap_or(i64::MAX),
        Some(_) => 10,
        None => 10,
    };
    let Some((report, last)) = round_report(&crate::history::database(&config), ident, compare)
    else {
        return Err((
            404,
            lp_i18n::t("This round has no report: it is no longer kept, or it had no frame rates."),
        ));
    };
    // the theme of the round report's page ("" there: no theme of its own, only the data)
    let name = Some(text_of(memory.get("report_theme")))
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| "round-report".into());
    let statuses = app.map(|app| app.panel.told(0)).unwrap_or_default();
    let model = model_of(&config.device, statuses.first().and_then(Option::as_ref));
    let way = if config.device.orientation == "portrait" {
        "portrait"
    } else {
        "landscape"
    };
    let name = lp_theme::versions::theme_for(
        &name,
        &config.device.orientation,
        model.map(|model| model.size(way)),
        places,
    );
    let theme = lp_theme::versions::find_theme(&name, places)
        .and_then(|folder| lp_theme::model::load_theme(&folder, env))
        .map_err(|why| (404, why.to_string()))?;
    let readings = lp_services::round_report::readings(&report).map_err(failed)?;
    let readings = readings
        .into_iter()
        .map(|(key, (value, unit, label))| {
            let reading = lp_render::Reading {
                unit,
                label,
                ..lp_render::Reading::of(value)
            };
            (key, reading)
        })
        .collect();
    let mut images = indexmap::IndexMap::new();
    if last {
        // the game service keeps the last game's logo: this round's
        if let Some(logo) = app.and_then(|app| app.images().swap_remove("game.logo")) {
            images.insert("game.logo".to_string(), logo);
        }
    }
    let session = report.get("session").and_then(Value::as_dict);
    let ended = session
        .and_then(|session| session.get("ended"))
        .ok_or_else(|| failed("KeyError: 'ended'"))
        .and_then(|ended| lp_py::format::float_of(ended).map_err(|why| failed(why.msg)))?;
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    let (now, fold) = lp_py::zones::fromtimestamp(zone, ended)
        .ok_or_else(|| failed("OverflowError: timestamp out of range"))?;
    let snapshot = lp_render::Snapshot {
        readings,
        now,
        fold,
        images,
        ..lp_render::Snapshot::default()
    };
    let dressed = lp_theme::versions::with_accent(
        &theme,
        crate::mainloop::accent_of(&config.accent).as_deref(),
    );
    let dressed = lp_theme::versions::with_light_colours(&dressed, config.light_colours)
        .map_err(|why| failed(why.msg))?;
    let dressed = lp_theme::versions::with_card_opacity(&dressed, config.card_opacity);
    let options = lp_render::Options {
        preview: true,
        animate: false,
        dither: false,
    };
    let mut renderer = lp_render::Renderer::new(dressed, env, options).map_err(|e| failed(e.0))?;
    crate::plugins::attach(&mut renderer);
    renderer.symbols = config.symbols.clone();
    renderer.switches = crate::previews::chosen_switches(&config, &name)
        .into_iter()
        .collect();
    let drawn = renderer.render(snapshot, lp_render::video::monotonic());
    renderer.close();
    let (frame, _boxes) = drawn.map_err(|why| failed(why.0))?;
    let size = (frame.width() as i64, frame.height() as i64);
    let mm = find_model(&theme.model)
        .or(model)
        .and_then(|model| arrangement::millimetres(model, Some(size)));
    let (width, height) = mm.unwrap_or((
        size.0 as f64 * arrangement::GUESS_MM_PER_PX,
        size.1 as f64 * arrangement::GUESS_MM_PER_PX,
    ));
    let one = Placed {
        rect: (0.0, 0.0, width, height),
        frame: Some(frame),
        round: false,
        number: 1,
    };
    let picture = photo(env, &[one])
        .map_err(|why| failed(why.0))?
        .ok_or_else(|| failed("no picture"))?;
    let folder = pictures_folder().join("Libre Panel");
    let game = session
        .and_then(|session| session.get("game"))
        .cloned()
        .unwrap_or(Value::None);
    let kept = || -> Result<PathBuf, String> {
        std::fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
        let path = free_name(&folder, &picture_name(&game, ended));
        let png = lp_devices::png::encode_optimized(&picture)?;
        std::fs::write(&path, png).map_err(|e| e.to_string())?;
        Ok(path)
    };
    let path = kept().map_err(conflict)?;
    // (kept all the same where it does not open: the answer says where)
    let opened = crate::settings::open_path(&folder).is_ok();
    let mut told = Dict::new();
    told.insert(
        "file".into(),
        Value::Str(path.to_string_lossy().into_owned()),
    );
    told.insert(
        "folder".into(),
        Value::Str(folder.to_string_lossy().into_owned()),
    );
    told.insert("opened".into(), Value::Bool(opened));
    Ok(told)
}
