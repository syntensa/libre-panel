//! The tray icon's menu (tray.py's Tray): what most people come for first (the app, the
//! theme, the brightness, a pause), then the app's views and the switches set once, the
//! folders, and Quit. The app makes the menu and does what is chosen in it; the icon is shown
//! by a program of its own (`libre-panel-tray`, rust/desktop), which asks for the menu once a
//! second (`GET /api/tray`) and tells what was chosen (`POST /api/tray`).
//!
//! A menu is a list of items, each `{"text", "id", "enabled", "checked", "radio",
//! "default", "items"}` (`checked` null where the item is no switch, `items` null where it
//! opens no menu of its own, `id` null where choosing it does nothing) or `{"separator":
//! true}`: what tray.py hands pystray, with the items that are not visible left out.

use std::path::Path;
use std::sync::Arc;

use lp_config::Config;
use lp_devices::models::{find_model, PanelModel, MODELS};
use lp_i18n::t;
use lp_py::{Dict, Value};

use crate::background::BackgroundApp;

/// Windows: at most 127 characters
const TOOLTIP_CHARS: usize = 120;
/// the brightness steps the menu offers (BackgroundApp.BRIGHTNESS_STEPS)
pub const BRIGHTNESS_STEPS: [i64; 6] = [0, 10, 25, 50, 75, 100];

fn say(message: &str) {
    crate::logs::say("libre_panel.tray", message);
}

fn t_with(text: &str, values: &[(&str, &str)]) -> String {
    let values: Vec<(&str, Value)> = (values.iter())
        .map(|(name, value)| (*name, Value::Str((*value).to_string())))
        .collect();
    lp_i18n::t_format(text, &values).unwrap_or_else(|why| why.msg)
}

fn text_of(state: &Dict, key: &str) -> String {
    match state.get(key) {
        Some(Value::Str(text)) => text.clone(),
        _ => String::new(),
    }
}

/// headline: "Libre Panel: showing on …" and the like, with the mode a service set.
pub fn headline(state: &Dict) -> String {
    let target = Some(text_of(state, "target"))
        .filter(|target| !target.is_empty())
        .unwrap_or_else(|| t("the panel"));
    let now = text_of(state, "state");
    let mut text = match now.as_str() {
        "starting" => t("starting"),
        "showing" => t_with("showing on {target}", &[("target", &target)]),
        "waiting" => t("waiting for the panel"),
        "paused" => t("paused"),
        "error" => t("needs attention"),
        "stopped" => t("stopped"),
        _ => now.clone(),
    };
    let mode = text_of(state, "mode");
    if !mode.is_empty() && now == "showing" {
        text += &format!(" ({})", mode_name(&mode));
    }
    format!("Libre Panel: {text}")
}

/// mode_name: a mode's name as the app's pages call it: "game" is "In game".
pub fn mode_name(mode: &str) -> String {
    match mode {
        "game" => t("In game"),
        "media" => t("Music"),
        "idle" => t("Idle"),
        _ => mode.to_string(),
    }
}

/// tooltip: the headline, and what needs doing (a missing theme, else missing readings), or
/// what went wrong; cut to what Windows shows.
pub fn tooltip(state: &Dict) -> String {
    let mut text = headline(state);
    let notice = text_of(state, "notice");
    let line = match notice.is_empty() {
        true => text_of(state, "hint"),
        false => notice,
    };
    let detail = text_of(state, "detail");
    if !detail.is_empty() {
        text += &format!("\n{detail}");
    } else if !line.is_empty() {
        text += &format!("\n{line}");
        if text.chars().count() > TOOLTIP_CHARS {
            text = format!("Libre Panel\n{line}");
        }
    }
    match text.chars().count() <= TOOLTIP_CHARS {
        true => text,
        false => text.chars().take(TOOLTIP_CHARS - 1).collect::<String>() + "…",
    }
}

// -- the menu's items -----------------------------------------------------------------------------

fn entry(text: &str, id: Option<String>) -> Dict {
    let mut item = Dict::default();
    item.insert("text".into(), Value::Str(text.to_string()));
    item.insert("id".into(), id.map_or(Value::None, Value::Str));
    item.insert("enabled".into(), Value::Bool(true));
    item.insert("checked".into(), Value::None);
    item.insert("radio".into(), Value::Bool(false));
    item.insert("default".into(), Value::Bool(false));
    item.insert("items".into(), Value::None);
    item
}

/// An item that does `id`.
fn action(text: &str, id: impl Into<String>) -> Value {
    Value::Dict(entry(text, Some(id.into())))
}

/// A line that only says something.
fn line(text: &str) -> Value {
    let mut item = entry(text, None);
    item.insert("enabled".into(), Value::Bool(false));
    Value::Dict(item)
}

/// A switch, on or off.
fn switch(text: &str, id: impl Into<String>, on: bool) -> Value {
    let mut item = entry(text, Some(id.into()));
    item.insert("checked".into(), Value::Bool(on));
    Value::Dict(item)
}

/// One of a choice.
fn choice(text: &str, id: impl Into<String>, on: bool) -> Value {
    let mut item = entry(text, Some(id.into()));
    item.insert("checked".into(), Value::Bool(on));
    item.insert("radio".into(), Value::Bool(true));
    Value::Dict(item)
}

/// An item that opens a menu of its own.
fn submenu(text: &str, items: Vec<Value>) -> Value {
    let mut item = entry(text, None);
    item.insert("items".into(), Value::List(items));
    Value::Dict(item)
}

fn separator() -> Value {
    let mut item = Dict::default();
    item.insert("separator".into(), Value::Bool(true));
    Value::Dict(item)
}

// -- the themes ---------------------------------------------------------------------------------

/// _theme_facts: a theme's name, panel, size and collection, from its theme.json.
struct Facts {
    name: String,
    model: Option<String>,
    size: Option<[i64; 2]>,
    collection: String,
}

fn facts(folder: &Path) -> Facts {
    let read = std::fs::read(folder.join("theme.json")).ok();
    let data = read
        .and_then(|bytes| String::from_utf8(bytes).ok())
        .and_then(|text| lp_py::json::loads(&text).ok());
    let data = match data {
        Some(Value::Dict(data)) => data,
        _ => Dict::default(),
    };
    let display = match data.get("display") {
        Some(Value::Dict(display)) => display.clone(),
        _ => Dict::default(),
    };
    let whole = |key: &str| match display.get(key) {
        Some(Value::Int(n)) => n.to_string().parse::<i64>().ok(),
        _ => None,
    };
    let size = match (whole("width"), whole("height")) {
        (Some(w), Some(h)) => Some([w.min(h), w.max(h)]),
        _ => None,
    };
    let name = match data.get("name") {
        Some(Value::Str(name)) if !lp_py::strip(name).is_empty() => name.clone(),
        _ => String::new(),
    };
    let model = match display.get("model") {
        Some(Value::Str(model)) => Some(model.clone()),
        _ => None,
    };
    let collection = match data.get("collection") {
        Some(Value::Str(collection)) => collection.clone(),
        _ => String::new(),
    };
    Facts {
        name,
        model,
        size,
        collection,
    }
}

/// The themes for a panel, as (id, name) by name: those made for `model` (all where it is not
/// known), the others ("More themes"), and each collection's apart (theme_choices).
type Choices = (
    Vec<(String, String)>,
    Vec<(String, String)>,
    Vec<(String, Vec<(String, String)>)>,
);

fn theme_choices(
    places: &lp_theme::versions::Places,
    model: Option<&PanelModel>,
    report: Option<&str>,
) -> Choices {
    let themes = lp_theme::versions::list_themes(places);
    let id_of = |theme: &Dict| match theme.get("id") {
        Some(Value::Str(id)) => id.clone(),
        _ => String::new(),
    };
    let ids: Vec<String> = themes.iter().map(id_of).collect();
    let (mut first, mut more) = (Vec::new(), Vec::new());
    let mut collections: std::collections::BTreeMap<String, Vec<(String, String)>> =
        std::collections::BTreeMap::new();
    let (width, height) = model.map_or((0, 0), |model| model.size("landscape"));
    for theme in &themes {
        let id = id_of(theme);
        if lp_theme::versions::version_base(&id).is_some_and(|base| ids.iter().any(|i| i == base)) {
            continue; // ("lumen-vertical", "lumen-1920x720": chosen as their theme)
        }
        let folder = match theme.get("path") {
            Some(Value::Str(path)) => std::path::PathBuf::from(path),
            _ => continue,
        };
        let facts = facts(&folder);
        let named = match facts.name.is_empty() {
            true => id.clone(),
            false => facts.name.clone(),
        };
        let fits = model.is_none_or(|model| {
            facts.model.as_deref() == Some(model.id)
                || facts.size == Some([width.min(height), width.max(height)])
                || ids.contains(&format!("{id}-{width}x{height}"))
        });
        if !facts.collection.is_empty() {
            collections
                .entry(facts.collection)
                .or_default()
                .push((id, named));
        } else if fits
            && Some(id.as_str()) != report
            && !lp_theme::versions::is_companion(&id)
            && !lp_theme::versions::is_agents_screen(&id)
        {
            first.push((id, named));
        } else {
            more.push((id, named));
        }
    }
    let by_name = |mut entries: Vec<(String, String)>| {
        entries.sort_by_cached_key(|entry| lp_py::casefold(&entry.1));
        entries
    };
    let sets = (collections.into_iter())
        .map(|(name, entries)| (name, by_name(entries)))
        .collect();
    (by_name(first), by_name(more), sets)
}

/// _theme_menu: the themes for `model`, each a choice made `id(theme)`, the one `chosen` on.
fn theme_menu(
    app: &BackgroundApp,
    model: Option<&PanelModel>,
    report: Option<&str>,
    id: &dyn Fn(&str) -> String,
    chosen: Option<&str>,
) -> Vec<Value> {
    let (first, more, collections) = theme_choices(&app.home().places(), model, report);
    let item = |(theme, name): &(String, String)| choice(name, id(theme), chosen == Some(theme));
    let mut items: Vec<Value> = first.iter().map(item).collect();
    if !more.is_empty() || !collections.is_empty() {
        items.push(separator());
    }
    if !more.is_empty() {
        items.push(submenu(&t("More themes"), more.iter().map(item).collect()));
    }
    for (name, entries) in &collections {
        items.push(submenu(name, entries.iter().map(item).collect()));
    }
    items
}

/// _theme_name: a theme's name as its theme.json gives it, else its id.
fn theme_name(app: &BackgroundApp, id: &str) -> String {
    match lp_theme::versions::find_theme(id, &app.home().places()) {
        Ok(folder) => Some(facts(&folder).name)
            .filter(|name| !name.is_empty())
            .unwrap_or_else(|| id.to_string()),
        Err(_) => id.to_string(),
    }
}

/// _panel_model: the panel the themes are offered for (config.toml's model, else the one the
/// running panel says it is) and the round report's theme.
fn panel_model(config: &Config, state: &Dict) -> (Option<&'static PanelModel>, Option<String>) {
    let target = text_of(state, "target");
    let model = find_model(&config.device.model)
        .or_else(|| MODELS.iter().find(|m| target.starts_with(&m.label())));
    let report = match config.services.options.get("memory") {
        Some(memory) => match memory.get("report_theme") {
            Some(lp_py::toml::Toml::Str(theme)) => Some(theme.clone()),
            Some(_) => None,
            None => Some("round-report".to_string()),
        },
        None => Some("round-report".to_string()),
    };
    (model, report)
}

/// _chosen: the theme of the panel's normal page, by the id it is chosen by (Lumen, not its
/// version for a panel on end).
fn chosen(app: &BackgroundApp) -> Option<String> {
    let active = app.active_theme()?;
    let base = lp_theme::versions::version_base(&active).map(str::to_string);
    let places = app.home().places();
    let listed = |id: &str| {
        lp_theme::versions::list_themes(&places)
            .iter()
            .any(|theme| matches!(theme.get("id"), Some(Value::Str(i)) if i == id))
    };
    match base {
        Some(base) if listed(&base) => Some(base),
        _ => Some(active),
    }
}

// -- the menu -----------------------------------------------------------------------------------

/// The panels beside the first ([[panels]]): each with its place there, its settings and how it
/// is doing (None while it does not run).
fn more_panels(
    config: &Config,
    state: &Dict,
) -> Vec<(usize, lp_config::PanelConfig, Option<Dict>)> {
    let running: Vec<Dict> = match state.get("more") {
        Some(Value::List(more)) => (more.iter())
            .filter_map(|item| match item {
                Value::Dict(item) => Some(item.clone()),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    };
    let of = |n: usize| {
        (running.iter())
            .find(|item| item.get("index").and_then(Value::to_f64) == Some(n as f64))
            .cloned()
    };
    (config.panels.iter().enumerate())
        .map(|(n, panel)| (n, panel.clone(), of(n)))
        .collect()
}

fn more_items(app: &BackgroundApp, config: &Config, state: &Dict) -> Vec<Value> {
    let mut items = Vec::new();
    for (n, panel, running) in more_panels(config, state) {
        let label = t_with("Panel {number}", &[("number", &(n + 2).to_string())]);
        let doing = match &running {
            _ if !panel.on => t("Off"),
            Some(running) if text_of(running, "state") == "showing" => {
                let name = theme_name(app, &text_of(running, "theme"));
                t_with("Shows {theme}", &[("theme", &name)])
            }
            _ => t("Waiting for the panel"),
        };
        let told = running
            .as_ref()
            .map(|r| text_of(r, "model"))
            .unwrap_or_default();
        let model = find_model(&panel.device.model)
            .or_else(|| (!told.is_empty()).then(|| find_model(&told)).flatten());
        // (chosen by name: it stays, whatever the first panel shows)
        let themes = theme_menu(
            app,
            model,
            None,
            &|theme| format!("panel-theme:{n}:{theme}"),
            Some(panel.theme.as_str()),
        );
        let follows = panel.follow_brightness;
        let mut brightness = vec![choice(
            &t("As panel 1's"),
            format!("panel-follow:{n}"),
            follows,
        )];
        for step in BRIGHTNESS_STEPS {
            let text = match step {
                0 => t("Off"),
                _ => format!("{step} %"),
            };
            let on = !follows && panel.device.brightness == step;
            brightness.push(choice(&text, format!("panel-brightness:{n}:{step}"), on));
        }
        items.push(submenu(
            &label,
            vec![
                line(&doing),
                switch(
                    &t_with("{panel} on", &[("panel", &label)]),
                    format!("panel-on:{n}"),
                    panel.on,
                ),
                submenu(&t("Theme"), themes),
                submenu(&t("Brightness"), brightness),
            ],
        ));
    }
    items.push(separator());
    items.push(action(&t("Which is which?"), "identify"));
    items
}

/// _timer_line: how the timer stands, while it runs.
fn timer_line(config: &Config, timer: &Dict) -> String {
    let left = timer.get("left").and_then(Value::to_f64).unwrap_or(0.0);
    let left = lp_services::timer::clock_text(left);
    if !matches!(timer.get("running"), Some(Value::Bool(true))) {
        return t_with("Stopped at {time}", &[("time", &left)]);
    }
    let breaks = match (config.services.options.get("timer")).and_then(|o| o.get("break_minutes")) {
        Some(lp_py::toml::Toml::Int(n)) => n.to_string().parse::<f64>().unwrap_or(0.0),
        Some(lp_py::toml::Toml::Float(n)) => *n,
        _ => 0.0,
    };
    if breaks * 60.0 > 0.0 {
        let phase = match text_of(timer, "phase").as_str() {
            "break" => t("Break"),
            _ => t("Focus"),
        };
        return format!("{phase} · {left}");
    }
    t_with("Runs · {time} left", &[("time", &left)])
}

/// build_menu: the menu as it is now.
pub fn menu(app: &BackgroundApp) -> Vec<Value> {
    let state = app.panel.state();
    let config = lp_config::load_config(app.config_file(), lp_config::platform()).ok();
    let snapshot = app.snapshot();
    let on = |key: &str| matches!(snapshot.get(key), Some(Value::Bool(true)));
    let mut items = vec![line(&headline(&state))];
    for key in ["notice", "hint"] {
        let said = text_of(&state, key);
        if !said.is_empty() {
            items.push(line(&said));
        }
    }
    let mut open = entry(&t("Open Libre Panel"), Some("open".into()));
    open.insert("default".into(), Value::Bool(true));
    items.push(Value::Dict(open));
    if app.clip_question() {
        items.push(action(&t("Smooth motion: confirm the clip name…"), "clip"));
    }
    items.push(separator());
    let themes = match &config {
        Some(config) => {
            let (model, report) = panel_model(config, &state);
            let chosen = chosen(app);
            theme_menu(
                app,
                model,
                report.as_deref(),
                &|theme| format!("theme:{theme}"),
                chosen.as_deref(),
            )
        }
        None => theme_menu(app, None, None, &|theme| format!("theme:{theme}"), None),
    };
    items.push(submenu(&t("Theme"), themes));
    let scenes = lp_config::scenes::state(app.config_file(), lp_config::platform()).ok();
    if let Some(scenes) = scenes.filter(|scenes| !scenes.scenes.is_empty()) {
        let names = (scenes.scenes.iter())
            .filter_map(|scene| match scene.get("name") {
                Some(lp_py::toml::Toml::Str(name)) => Some(name.clone()),
                _ => None,
            })
            .map(|name| choice(&name, format!("scene:{name}"), scenes.on == name))
            .collect();
        items.push(submenu(&t("Scenes"), names));
    }
    if let Some(config) = config.as_ref().filter(|config| !config.panels.is_empty()) {
        items.push(submenu(&t("More panels"), more_items(app, config, &state)));
    }
    let level = app.brightness();
    let brightness = (BRIGHTNESS_STEPS.iter())
        .map(|&step| {
            let text = match step {
                0 => t("Off"),
                _ => format!("{step} %"),
            };
            choice(&text, format!("brightness:{step}"), level == Some(step))
        })
        .collect();
    items.push(submenu(&t("Brightness"), brightness));
    if let (Some(Value::Dict(timer)), Some(config)) = (app.timer(), &config) {
        items.push(submenu(
            &t("Timer"),
            vec![
                line(&timer_line(config, &timer)),
                action(&t("Start or pause"), "timer"),
                action(&t("Back to the start"), "timer_reset"),
            ],
        ));
    }
    items.push(switch(&t("Pause panel"), "pause", app.panel.paused()));
    items.push(action(&t("Restart panels"), "restart"));
    items.push(separator());
    items.push(action(&t("Design"), "design"));
    items.push(action(&t("Settings"), "settings"));
    items.push(switch(&t("Upside down"), "upside_down", on("upside_down")));
    items.push(switch(
        &t("Start with system"),
        "autostart",
        on("autostart"),
    ));
    items.push(separator());
    items.push(action(&t("Open settings folder"), "folder"));
    if app.log_file().is_some() {
        items.push(action(&t("Open log"), "log"));
    }
    items.push(separator());
    items.push(action(&t("Quit Libre Panel"), "quit"));
    items
}

/// `GET /api/tray`: the icon's state, its tooltip and the menu.
pub fn state(app: &BackgroundApp) -> Value {
    let panel = app.panel.state();
    let mut told = Dict::default();
    told.insert("state".into(), Value::Str(text_of(&panel, "state")));
    told.insert("title".into(), Value::Str(tooltip(&panel)));
    told.insert("menu".into(), Value::List(menu(app)));
    Value::Dict(told)
}

// -- what is chosen -----------------------------------------------------------------------------

/// _set_panel: the panel at `[[panels]]` `n` changed as Settings → Panels does.
fn set_panel(app: &BackgroundApp, n: usize, changes: &[(&str, Value)]) -> Result<(), String> {
    let restart = crate::settings::Restart::default();
    let settings = crate::settings::Settings {
        home: app.home(),
        config_file: app.config_file(),
        controls: None,
        restart: &restart,
    };
    let read = settings.read().map_err(|why| why.message)?;
    let panels = match &read {
        Value::Dict(read) => match read.get("config") {
            Some(Value::Dict(config)) => match config.get("panels") {
                Some(Value::List(panels)) => panels.clone(),
                _ => Vec::new(),
            },
            _ => Vec::new(),
        },
        _ => Vec::new(),
    };
    let mut panels = panels;
    if let Some(Value::Dict(panel)) = panels.get_mut(n) {
        for (key, value) in changes {
            panel.insert((*key).to_string(), value.clone());
        }
        let mut set = Dict::default();
        set.insert("panels".into(), Value::List(panels));
        let mut payload = Dict::default();
        payload.insert("set".into(), Value::Dict(set));
        settings
            .write(&Value::Dict(payload))
            .map_err(|why| why.message)?;
    }
    Ok(())
}

fn number(text: &str) -> Option<usize> {
    text.parse().ok()
}

fn chose(app: &BackgroundApp, id: &str) -> Result<(), String> {
    let platform = lp_config::platform();
    let file = app.config_file();
    let (head, rest) = id.split_once(':').unwrap_or((id, ""));
    match head {
        "open" => drop(app.open("home")),
        "clip" => drop(app.open("home/clip")),
        "design" => drop(app.open("design")),
        "settings" => drop(app.open("settings")),
        "theme" => lp_config::edit::set_active_theme(rest, file, platform).map_err(|e| e.msg)?,
        "scene" => {
            let on = lp_config::scenes::state(file, platform)
                .map_err(|e| e.msg)?
                .on;
            let name = if on == rest { "" } else { rest };
            lp_config::scenes::put_on(name, file, platform).map_err(|e| e.msg)?;
            app.follow_config();
        }
        "brightness" => {
            let step = rest.parse().map_err(|_| "a brightness step")?;
            app.set_brightness(step)?;
        }
        "panel-on" | "panel-follow" => {
            let n = number(rest).ok_or("a panel")?;
            let config = lp_config::load_config(file, platform).map_err(|e| e.msg)?;
            match head {
                "panel-on" => {
                    let on = config.panels.get(n).is_some_and(|panel| panel.on);
                    set_panel(app, n, &[("on", Value::Bool(!on))])?;
                }
                _ => set_panel(app, n, &[("follow_brightness", Value::Bool(true))])?,
            }
        }
        "panel-brightness" | "panel-theme" => {
            let (n, value) = rest.split_once(':').ok_or("a panel")?;
            let n = number(n).ok_or("a panel")?;
            match head {
                "panel-brightness" => {
                    let step: i64 = value.parse().map_err(|_| "a brightness step")?;
                    set_panel(
                        app,
                        n,
                        &[
                            ("follow_brightness", Value::Bool(false)),
                            ("brightness", Value::int(step)),
                        ],
                    )?;
                }
                _ => set_panel(
                    app,
                    n,
                    &[
                        ("theme", Value::Str(value.to_string())),
                        ("follow", Value::Bool(false)),
                    ],
                )?,
            }
        }
        "identify" => crate::identify::show(),
        "timer" | "timer_reset" => app.shortcut(head),
        "pause" => match app.panel.paused() {
            true => app.panel.resume(),
            false => app.panel.pause(),
        },
        "restart" => app.panel.restart()?,
        "upside_down" => {
            let turned = !app.upside_down().unwrap_or(false);
            app.act("upside_down", &Value::Bool(turned))?;
        }
        "autostart" => {
            let on = matches!(app.snapshot().get("autostart"), Some(Value::Bool(true)));
            app.set_autostart(!on)?;
        }
        "folder" => {
            let folder = &app.home().settings;
            std::fs::create_dir_all(folder).map_err(|e| e.to_string())?;
            crate::settings::open_path(folder).map_err(|e| e.to_string())?;
        }
        "log" => {
            if let Some(log) = app.log_file() {
                crate::settings::open_path(&log).map_err(|e| e.to_string())?;
            }
        }
        "quit" => app.quit(),
        _ => return Err(format!("no such item: {}", lp_py::repr(id))),
    }
    Ok(())
}

/// `POST /api/tray {"id": ...}`: what was chosen in the menu, done in the background so that
/// the menu never waits (tray.py's _later); a failure is logged.
pub fn act(app: Arc<BackgroundApp>, id: String) {
    let thread = std::thread::Builder::new().name("tray-action".into());
    let spawned = thread.spawn(move || {
        if let Err(why) = chose(&app, &id) {
            say(&why);
        }
    });
    if let Err(why) = spawned {
        say(&format!("the menu's choice was not done: {why}"));
    }
}
