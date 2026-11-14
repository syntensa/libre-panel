//! The pages of the panel for the app (server.py's _panel_pages, _set_panel_page and their
//! like for a panel beside the first): the theme it shows normally, the theme of each mode
//! and the round report's, read from config.toml and written to it.

use lp_config::edit::Change;
use lp_config::Config;
use lp_py::toml::Toml;
use lp_py::{Dict, Value};
use lp_theme::versions;

use crate::modes;
use crate::settings::{Refusal, Settings};

const GAME: &str = "game";
const REPORT: &str = "report";
const MEMORY: &str = "memory";
/// The pages of a panel beside the first, and the key of each in its `[[panels]]` table.
const BESIDE_PAGES: [(&str, &str); 4] = [
    ("normal", "theme"),
    ("game", "game_theme"),
    ("media", "media_theme"),
    ("alarm", "alarm_theme"),
];

type Answer = Result<Value, Refusal>;

fn refusal(message: impl Into<String>, status: u16) -> Refusal {
    Refusal {
        message: message.into(),
        status,
    }
}

fn dict<const N: usize>(pairs: [(&str, Value); N]) -> Dict {
    pairs
        .into_iter()
        .map(|(key, value)| (key.to_string(), value))
        .collect()
}

fn text(value: &str) -> Value {
    Value::Str(value.to_string())
}

fn texts(values: &[&str]) -> Value {
    Value::List(values.iter().map(|value| text(value)).collect())
}

fn or_none(value: Option<String>) -> Value {
    value
        .filter(|text| !text.is_empty())
        .map_or(Value::None, Value::Str)
}

/// The screens made for a page, offered first where its theme is chosen.
fn suggested(page: &str) -> &'static [&'static str] {
    match page {
        GAME => &["arena"],
        modes::MEDIA => &["encore"],
        modes::IDLE => &["almanac", "almanac-analog"],
        REPORT => &["round-report"],
        _ => &[],
    }
}

/// `?panel=2`: the panel's number (1 the first), None for one that is not there.
fn panel_number(asked: Option<&str>, config: &Config) -> Option<usize> {
    let asked = match asked {
        None | Some("") => "1",
        Some(asked) => asked,
    };
    let number = lp_py::int_of_str(lp_py::strip(asked))?;
    let number = usize::try_from(number).ok()?;
    (1..=config.panels.len() + 1)
        .contains(&number)
        .then_some(number)
}

/// theme_chosen: whether config.toml names a theme itself: a first start has no file, or
/// none with a `theme` line yet, and the app asks which look the panel should have.
pub fn theme_chosen(path: &std::path::Path) -> bool {
    std::fs::read(path)
        .ok()
        .and_then(|bytes| String::from_utf8(bytes).ok())
        .and_then(|text| lp_py::toml::loads(&text).ok())
        .is_some_and(|document| document.contains_key("theme"))
}

impl Settings<'_> {
    fn loaded(&self) -> Result<Config, Refusal> {
        lp_config::load_config(self.config_file, lp_config::platform())
            .map_err(|why| refusal(why.msg, 409))
    }

    /// _panel_state: what the running app says about its panel; empty without one.
    fn panel_state(&self) -> Dict {
        (self.controls.as_ref()).map_or_else(Dict::default, |app| app.panel.state())
    }

    /// _panel_pages: the pages of the panel: the theme it shows normally (config.toml's,
    /// else the panel's own default), the theme of each mode (game mode's among them, with
    /// its frame rate; None: the normal one) and the round report's, and which page is on
    /// show now, whether config.toml names a theme at all (`chosen`) and the panel's own
    /// `default`. `panel`: those of a panel beside the first.
    pub fn pages(&self, panel: Option<&str>) -> Answer {
        let config = self.loaded()?;
        let Some(number) = panel_number(panel, &config) else {
            return Err(refusal("no such panel", 404));
        };
        if number > 1 {
            return Ok(Value::Dict(self.beside_pages(&config, number - 2)));
        }
        let panel = self.panel_state();
        // the panel's own default, worked out from the config
        let model = crate::settings::panel_model(&config);
        let default = lp_theme::defaults::default_theme(model, &self.home.places());
        let normal = match config.theme_given {
            true => config.theme.as_str(),
            false => default,
        };
        let sequence = config.sequence.on && !config.sequence.steps.is_empty();
        let mut pages = vec![Value::Dict(dict([
            ("id", text("normal")),
            ("theme", text(normal)),
            ("sequence", Value::Bool(sequence)),
        ]))];
        let own = [GAME, modes::MEDIA, modes::IDLE];
        let mut others: Vec<&str> = (config.modes.keys())
            .map(String::as_str)
            .filter(|name| !own.contains(name))
            .collect();
        others.sort_unstable();
        let enabled = |service: &str| config.services.enabled.iter().any(|s| s == service);
        let option = |service: &str, option: &str| {
            let table = config.services.options.get(service);
            table.and_then(|table| table.get(option))
        };
        for name in own.into_iter().chain(others) {
            let mode = config.modes.get(name);
            let mut page = dict([
                ("id", text(name)),
                ("mode", text(name)),
                ("theme", or_none(mode.and_then(|mode| mode.theme.clone()))),
                (
                    "fps",
                    mode.and_then(|mode| mode.fps)
                        .map_or(Value::None, Value::int),
                ),
                ("on", Value::Bool(true)),
                ("suggested", texts(suggested(name))),
            ]);
            if name == GAME {
                // game mode's page is the game service switching by itself
                let automatic = !matches!(option(GAME, "automatic"), Some(Toml::Bool(false)));
                page.insert("on".into(), Value::Bool(enabled(GAME) && automatic));
                page.insert("service".into(), text(GAME));
            } else if name == modes::MEDIA || name == modes::IDLE {
                // the pages Libre Panel switches to itself
                page.insert("on".into(), Value::Bool(modes::page_on(&config, name)));
                let theme = modes::page_theme(&config, Some(name));
                page.insert("theme".into(), theme.map_or(Value::None, Value::Str));
                page.insert("own".into(), Value::Bool(true));
                if name == modes::IDLE {
                    let minutes = modes::idle_minutes(&config);
                    page.insert("minutes".into(), Value::Float(minutes));
                }
            }
            pages.push(Value::Dict(page));
        }
        let report = match option(MEMORY, "report_theme") {
            Some(Toml::Str(theme)) => Some(theme.clone()),
            Some(_) => None,
            None => {
                let declared = crate::services::parts().get(MEMORY).map(|p| (p.options)());
                let found = declared
                    .and_then(|options| options.into_iter().find(|o| o.0 == "report_theme"));
                found.and_then(|(_, _, default)| default.as_str().map(str::to_string))
            }
        };
        let reports = match option(MEMORY, "report") {
            None | Some(Toml::Bool(true)) => enabled(MEMORY),
            Some(_) => false,
        };
        pages.push(Value::Dict(dict([
            ("id", text(REPORT)),
            ("service", text(MEMORY)),
            ("theme", or_none(report)),
            ("on", Value::Bool(reports)),
            ("suggested", texts(suggested(REPORT))),
        ])));
        // false on a first start: Start offers the themes
        let chosen = theme_chosen(self.config_file);
        let of = |key: &str| panel.get(key).cloned().unwrap_or(Value::None);
        Ok(Value::Dict(dict([
            ("pages", Value::List(pages)),
            ("shown", of("theme")),
            ("mode", of("mode")),
            ("chosen", Value::Bool(chosen)),
            ("default", text(default)),
        ])))
    }

    /// _beside_pages: the pages of the panel beside the first at `[[panels]]` `n`: its
    /// theme, and the theme it shows while the first panel is in a game, plays music or is
    /// at rest, and during an alarm. While it follows the first panel it shows the
    /// companion of the first panel's theme, and so does a page without a theme (None);
    /// else its own theme.
    fn beside_pages(&self, config: &Config, n: usize) -> Dict {
        let panel = &config.panels[n];
        let follows = versions::follows_first(panel.follow, &panel.theme);
        let mut pages = vec![Value::Dict(dict([
            ("id", text("normal")),
            ("theme", or_none(Some(panel.theme.clone()))),
            ("beside", Value::Bool(true)),
            ("companion", Value::Bool(follows)),
            ("follows", Value::Bool(follows)),
        ]))];
        let themes = [
            ("game", &panel.game_theme),
            ("media", &panel.media_theme),
            ("alarm", &panel.alarm_theme),
        ];
        for (page, theme) in themes {
            let follows = follows && theme.is_empty() && page != "alarm";
            pages.push(Value::Dict(dict([
                ("id", text(page)),
                ("theme", or_none(Some(theme.clone()))),
                ("on", Value::Bool(true)),
                ("beside", Value::Bool(true)),
                ("follows", Value::Bool(follows)),
            ])));
        }
        dict([
            ("pages", Value::List(pages)),
            ("shown", Value::None),
            ("mode", Value::None),
            ("chosen", Value::Bool(true)),
            ("default", text(&panel.theme)),
            ("panel", Value::int(n as i64 + 2)),
        ])
    }

    /// _page_switch: what switching a page on or off writes: game mode's page is the game
    /// service switching by itself (switched on, the service runs), the round report's is
    /// the memory service's report (likewise), and any other is its mode's `on`.
    fn page_switch(&self, page: &str, on: bool) -> Result<Vec<Change>, Refusal> {
        let (service, option) = match page {
            GAME => (GAME, "automatic"),
            REPORT => (MEMORY, "report"),
            _ => {
                return Ok(vec![Change::Value(
                    format!("modes.{page}.on"),
                    Toml::Bool(on),
                )])
            }
        };
        let name = format!("services.{service}.{option}");
        let mut changes = vec![Change::Value(name, Toml::Bool(on))];
        let mut enabled = self.loaded()?.services.enabled;
        if on && !enabled.iter().any(|name| name == service) {
            enabled.push(service.to_string());
            let enabled = enabled.into_iter().map(Toml::Str).collect();
            changes.push(Change::Value(
                "services.enabled".into(),
                Toml::Array(enabled),
            ));
        }
        Ok(changes)
    }

    /// _set_panel_page: `{"page": ..., "theme": ...}` and, for a mode, `{"fps": ...}`: what
    /// a page of the panel shows, written to config.toml. `page` is "normal", "report" (the
    /// round report) or a mode's name; a mode's empty theme is the normal one's.
    pub fn set_page(&self, payload: &Value) -> Answer {
        let bad = |message: &str| Err(refusal(message, 400));
        let fits = |page: &str| {
            let bytes = page.as_bytes();
            let rest = |b: &u8| b.is_ascii_lowercase() || b.is_ascii_digit() || b"_-".contains(b);
            (1..=32).contains(&bytes.len())
                && bytes[0].is_ascii_lowercase()
                && bytes[1..].iter().all(rest)
        };
        let asked = match payload {
            Value::Dict(asked) => Some(asked),
            _ => None,
        };
        let page = asked
            .and_then(|asked| asked.get("page"))
            .and_then(Value::as_str);
        let (Some(asked), Some(page)) = (asked, page.filter(|page| fits(page))) else {
            return bad("expected {\"page\": \"normal\", \"report\" or a mode, ...}");
        };
        let of = |key: &str| asked.get(key).cloned().unwrap_or(Value::None);
        if asked
            .get("panel")
            .is_some_and(|panel| *panel != Value::int(1))
        {
            return self.set_beside_page(asked, page);
        }
        let (theme, fps, on, minutes) = (of("theme"), of("fps"), of("on"), of("minutes"));
        let none = |value: &Value| matches!(value, Value::None);
        if !none(&theme) && theme.as_str().is_none() {
            return bad("theme must be a theme name");
        }
        if !none(&fps) && !matches!(fps, Value::Int(_)) {
            return bad("fps must be a whole number");
        }
        if !none(&on) && (!matches!(on, Value::Bool(_)) || page == "normal") {
            return bad("on is true or false, for every page but the normal one");
        }
        if !none(&minutes) {
            let given = match &minutes {
                Value::Int(_) | Value::Float(_) => minutes.to_f64(),
                _ => None,
            };
            let fits = given.is_some_and(|given| (0.5..=1440.0).contains(&given));
            if page != modes::IDLE || !fits {
                return bad("minutes (0.5 to 1440) are the idle page's");
            }
        }
        let fixed = page == "normal" || page == REPORT;
        let mut theme = theme.as_str().map(str::to_string);
        if fixed && (!none(&fps) || theme.as_deref() == Some("")) {
            return bad(&format!(
                "the page {page} needs a theme and has no frame rate of its own"
            ));
        }
        if fixed && theme.is_none() && none(&on) {
            return bad(&format!("the page {page} needs a theme"));
        }
        let places = self.home.places();
        if page == "normal" {
            // (the pair version, put on it while a companion ran)
            if let Some(shown) = theme.as_deref().filter(|theme| !theme.is_empty()) {
                theme = Some(versions::unpaired_version(shown, &places).unwrap_or(shown.into()));
            }
        }
        let key = match page {
            "normal" => "theme".to_string(),
            REPORT => format!("services.{MEMORY}.report_theme"),
            _ => format!("modes.{page}.theme"),
        };
        let mut changes = Vec::new();
        if let Some(theme) = &theme {
            changes.push(Change::Value(key, Toml::Str(theme.clone())));
        }
        if let Value::Int(fps) = &fps {
            changes.push(Change::Value(
                format!("modes.{page}.fps"),
                Toml::Int(fps.clone()),
            ));
        }
        match &minutes {
            Value::Int(given) => {
                changes.push(Change::Value(
                    "modes.idle.minutes".into(),
                    Toml::Int(given.clone()),
                ));
            }
            Value::Float(given) => {
                changes.push(Change::Value(
                    "modes.idle.minutes".into(),
                    Toml::Float(*given),
                ));
            }
            _ => {}
        }
        if let Value::Bool(on) = on {
            changes.extend(self.page_switch(page, on)?);
        }
        if let Some(theme) = theme.as_deref().filter(|theme| !theme.is_empty()) {
            versions::find_theme(theme, &places).map_err(|why| refusal(why.msg, 400))?;
        }
        if page == "normal" {
            let sequence = self.loaded()?.sequence;
            if sequence.on && !sequence.steps.is_empty() {
                // one theme chosen: the screens in turn stop (they are kept, to go on later)
                changes.push(Change::Value("sequence.on".into(), Toml::Bool(false)));
            }
        }
        if !changes.is_empty() {
            lp_config::edit::edit_config(&changes, self.config_file, lp_config::platform())
                .map_err(|why| refusal(why.msg, 409))?;
        }
        self.pages(None)
    }

    /// _set_beside_page: `{"panel": 2, "page": "media", "theme": ...}`: the theme a page of
    /// a panel beside the first shows (an empty one: the companion of the first panel's
    /// theme, else its own), written to its `[[panels]]` table. The normal page's theme is
    /// the panel's own from then on, whatever the first panel shows; `{"page": "normal",
    /// "follow": true}` has it show the companion of the first panel's theme again.
    fn set_beside_page(&self, asked: &Dict, page: &str) -> Answer {
        let config = self.loaded()?;
        let said = asked.get("panel").map(lp_py::format::str_of);
        let number = panel_number(said.as_deref(), &config).filter(|number| *number >= 2);
        let follow = matches!(asked.get("follow"), Some(Value::Bool(true))) && page == "normal";
        let Some(number) = number else {
            return Err(refusal("no such panel", 404));
        };
        let places = self.home.places();
        let mut theme = asked.get("theme").cloned().unwrap_or(Value::None);
        if follow && matches!(theme, Value::None) {
            // (its theme kept where it is a companion)
            let own = &config.panels[number - 2].theme;
            theme = Value::Str(match versions::is_companion(own) {
                true => own.clone(),
                false => versions::companion_for(&config.theme, &places)
                    .unwrap_or(lp_config::ALARM_THEME.to_string()),
            });
        }
        let key = BESIDE_PAGES.iter().find(|(name, _)| *name == page);
        let theme = theme
            .as_str()
            .filter(|theme| page != "normal" || !theme.is_empty());
        let (Some((_, key)), Some(theme)) = (key, theme) else {
            return Err(refusal(
                "a page of a panel beside the first: normal, game, media or alarm, with a theme \
                 (none: the companion's, for all but normal), or normal with follow: true",
                400,
            ));
        };
        if !theme.is_empty() {
            versions::find_theme(theme, &places).map_err(|why| refusal(why.msg, 400))?;
        }
        let read = self.read()?;
        let within = |value: &Value, key: &str| match value {
            Value::Dict(table) => table.get(key).cloned(),
            _ => None,
        };
        let panels = within(&read, "config").and_then(|config| within(&config, "panels"));
        let mut tables = match panels {
            Some(Value::List(tables)) => tables,
            _ => Vec::new(),
        };
        if let Some(Value::Dict(table)) = tables.get_mut(number - 2) {
            table.insert((*key).to_string(), text(theme));
            if page == "normal" {
                // chosen: its own, or the companion of the first's again
                table.insert("follow".into(), Value::Bool(follow));
            }
        }
        let set = dict([("panels", Value::List(tables))]);
        self.write(&Value::Dict(dict([("set", Value::Dict(set))])))?;
        Ok(Value::Dict(self.beside_pages(&self.loaded()?, number - 2)))
    }

    /// _sequence: the screens in turn: their steps (a theme and its seconds each), whether
    /// they show, and where the running panel stands in them (`now`: the step's index and
    /// theme and the seconds left of it; None while it shows no sequence).
    pub fn sequence(&self) -> Answer {
        let config = self.loaded()?;
        let steps = (config.sequence.steps.iter())
            .map(|step| {
                Value::Dict(dict([
                    ("theme", text(&step.theme)),
                    ("seconds", Value::int(step.seconds)),
                ]))
            })
            .collect();
        let active = config.sequence.on && !config.sequence.steps.is_empty();
        let now = match active {
            true => self
                .panel_state()
                .get("sequence")
                .cloned()
                .unwrap_or(Value::None),
            false => Value::None,
        };
        Ok(Value::Dict(dict([
            ("on", Value::Bool(config.sequence.on)),
            ("steps", Value::List(steps)),
            ("now", now),
            ("shortest_s", Value::int(lp_config::sequence::SHORTEST_S)),
            ("longest_s", Value::int(lp_config::sequence::LONGEST_S)),
        ])))
    }

    /// _set_sequence: `{"on": true, "steps": [{"theme": "almanac", "seconds": 300}, ...]}`:
    /// the screens in turn, written to config.toml ([sequence]); without steps they are off.
    pub fn set_sequence(&self, payload: &Value) -> Answer {
        let asked = match payload {
            Value::Dict(asked) => asked,
            _ => {
                return Err(refusal(
                    "expected {\"on\": true or false, \"steps\": [...]}",
                    400,
                ))
            }
        };
        let Some(Value::Bool(on)) = asked.get("on") else {
            return Err(refusal(
                "expected {\"on\": true or false, \"steps\": [...]}",
                400,
            ));
        };
        let raw = match asked.get("steps") {
            None => Vec::new(),
            Some(Value::List(raw)) if raw.iter().all(|step| matches!(step, Value::Dict(_))) => {
                raw.clone()
            }
            Some(_) => {
                return Err(refusal(
                    "steps: expected a list of {\"theme\": ..., \"seconds\": ...}",
                    400,
                ))
            }
        };
        // each step as {"theme": ..., "seconds": ...}, what is missing as None: a None that
        // is no text and no number is refused as Python refuses it
        let as_toml = |value: Option<&Value>| match value {
            None | Some(Value::None) => Toml::Table(Default::default()),
            Some(value) => {
                crate::settings::toml_of(value).unwrap_or(Toml::Table(Default::default()))
            }
        };
        let tables = raw
            .iter()
            .map(|step| {
                let Value::Dict(step) = step else {
                    unreachable!()
                };
                let mut table = lp_py::toml::Table::new();
                table.insert("theme".into(), as_toml(step.get("theme")));
                table.insert("seconds".into(), as_toml(step.get("seconds")));
                Toml::Table(table)
            })
            .collect();
        let steps = lp_config::sequence::parse_steps(&Toml::Array(tables), "sequence.steps")
            .map_err(|why| refusal(why.msg, 400))?;
        let places = self.home.places();
        for step in &steps {
            versions::find_theme(&step.theme, &places).map_err(|why| refusal(why.msg, 400))?;
        }
        let tables = steps
            .iter()
            .map(|step| {
                let (unit, size) = lp_config::sequence::UNITS
                    .into_iter()
                    .find(|(_, size)| step.seconds % size == 0)
                    .unwrap_or(("seconds", 1));
                let mut table = lp_py::toml::Table::new();
                table.insert("theme".into(), Toml::Str(step.theme.clone()));
                table.insert(unit.into(), Toml::Int((step.seconds / size).into()));
                table
            })
            .collect();
        let changes = [
            Change::Value("sequence.on".into(), Toml::Bool(*on && !steps.is_empty())),
            Change::Tables("sequence.steps".into(), tables),
        ];
        lp_config::edit::edit_config(&changes, self.config_file, lp_config::platform())
            .map_err(|why| refusal(why.msg, 409))?;
        self.sequence()
    }
}
