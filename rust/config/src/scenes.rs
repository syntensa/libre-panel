//! Scenes: a theme for every panel at once ("Gaming", "Work", "Film"), put on with one click
//! on Start, a keyboard shortcut (the next one) or the tray (scenes.py).
//!
//! A scene is kept in config.toml (`[[scenes]]`: its name and the themes, the first panel's
//! first) and the one that is on in `scene`; parse_config gives the panels its themes. A
//! theme chosen for a panel by itself afterwards takes the scene off (set_active_theme,
//! Settings → Panels).

use std::path::Path;

use lp_py::toml::{Table, Toml};

use crate::edit::{edit_config, Change};
use crate::{config_error, load_config, Config, Result, MAX_SCENES, SCENE_NAME_LENGTH};

fn table(name: &str, themes: &[String]) -> Table {
    let themes = themes.iter().cloned().map(Toml::Str).collect();
    let mut table = Table::new();
    table.insert("name".into(), Toml::Str(name.to_string()));
    table.insert("themes".into(), Toml::Array(themes));
    table
}

fn tables(config: &Config) -> Vec<Table> {
    let scenes = config.scenes.iter();
    scenes
        .map(|scene| table(&scene.name, &scene.themes))
        .collect()
}

/// The scenes there are and the one that is on.
#[derive(Clone, Debug, PartialEq)]
pub struct State {
    /// (each `{name, themes}`)
    pub scenes: Vec<Table>,
    /// the name of the one that is on, or ""
    pub on: String,
}

/// state: `{"scenes": [{name, themes}], "on": the name of the one that is on, or ""}`.
pub fn state(path: &Path, platform: &str) -> Result<State> {
    let config = load_config(path, platform)?;
    let on = config.scenes.iter().any(|scene| scene.name == config.scene);
    Ok(State {
        scenes: tables(&config),
        on: if on { config.scene } else { String::new() },
    })
}

/// put_on: put the scene `name` on ("": none; the panels show their own themes again).
pub fn put_on(name: &str, path: &Path, platform: &str) -> Result<State> {
    let config = load_config(path, platform)?;
    if !name.is_empty() && !config.scenes.iter().any(|scene| scene.name == name) {
        return Err(config_error(format!("no scene {}", lp_py::repr(name))));
    }
    if !name.is_empty() {
        let mut changes = vec![Change::Value("scene".into(), Toml::Str(name.to_string()))];
        // a scene, like a theme chosen, stops the screens in turn
        if config.sequence.on && !config.sequence.steps.is_empty() {
            changes.push(Change::Value("sequence.on".into(), Toml::Bool(false)));
        }
        edit_config(&changes, path, platform)?;
    } else if !config.scene.is_empty() {
        edit_config(&[Change::Unset("scene".into())], path, platform)?;
    }
    state(path, platform)
}

/// next_one: put the scene after the one that is on, the first after the last; its name ("":
/// there are none).
pub fn next_one(path: &Path, platform: &str) -> Result<String> {
    let config = load_config(path, platform)?;
    if config.scenes.is_empty() {
        return Ok(String::new());
    }
    let at = config
        .scenes
        .iter()
        .position(|scene| scene.name == config.scene);
    let next = at.map_or(0, |at| (at + 1) % config.scenes.len());
    let name = config.scenes[next].name.clone();
    put_on(&name, path, platform)?;
    Ok(name)
}

/// save: keep what the panels show now as the scene `name` (one of that name is replaced),
/// and put it on.
pub fn save(name: &str, path: &Path, platform: &str) -> Result<State> {
    let words: Vec<&str> = name
        .split(lp_py::isspace)
        .filter(|w| !w.is_empty())
        .collect();
    let name = words.join(" ");
    if name.is_empty() || name.chars().count() > SCENE_NAME_LENGTH {
        return Err(config_error(format!(
            "a scene's name: 1 to {SCENE_NAME_LENGTH} characters"
        )));
    }
    let config = load_config(path, platform)?;
    let first = match config.theme_given {
        true => config.theme.clone(),
        false => String::new(),
    };
    let mut themes = vec![first];
    themes.extend(config.panels.iter().map(|panel| panel.theme.clone()));
    let mut kept: Vec<Table> = (config.scenes.iter())
        .filter(|scene| scene.name != name)
        .map(|scene| table(&scene.name, &scene.themes))
        .collect();
    if kept.len() >= MAX_SCENES {
        return Err(config_error(format!("at most {MAX_SCENES} scenes")));
    }
    kept.push(table(&name, &themes));
    let changes = [
        Change::Tables("scenes".into(), kept),
        Change::Value("scene".into(), Toml::Str(name)),
    ];
    edit_config(&changes, path, platform)?;
    state(path, platform)
}

/// delete: take the scene `name` out (off, if it was on).
pub fn delete(name: &str, path: &Path, platform: &str) -> Result<State> {
    let config = load_config(path, platform)?;
    let kept: Vec<Table> = (config.scenes.iter())
        .filter(|scene| scene.name != name)
        .map(|scene| table(&scene.name, &scene.themes))
        .collect();
    let mut changes = vec![Change::Tables("scenes".into(), kept)];
    if config.scene == name {
        changes.push(Change::Unset("scene".into()));
    }
    edit_config(&changes, path, platform)?;
    state(path, platform)
}
