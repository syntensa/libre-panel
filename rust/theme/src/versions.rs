//! A theme's versions and where themes are (theme/model.py): "lumen-1920x720" is Lumen for a
//! panel of that size, "lumen-vertical" for one that stands on end, "lumen-pair" for a panel
//! with a companion beside it, "lumen-companion" that companion; the user's themes before a
//! plugin's and before those that come with the app; a theme saved into the user's folder; and
//! a theme in the colours of a person's accent, their light colours, the look of the first
//! panel. Theme names are matched as Python's patterns match them (`\d` any script's digit,
//! `$` before a last line end too).

use std::path::{Path, PathBuf};

use lp_py::{Dict, Value};

use crate::model::{
    parse_theme, style_defaults, valid_theme_name, Env, Error, Kind, Result, Theme,
};
use crate::tables::{BUILTIN_PREFIX, CARD_OPACITY_MIN, DEFAULT_FONT, THEME_FILENAME};

pub const VERTICAL: &str = "-vertical";
pub const PAIR: &str = "-pair";

/// Where themes are, in the order they are looked for (_search_dirs): the user's own, then
/// plugins' (none here: R10), then those that come with the app.
#[derive(Clone, Debug)]
pub struct Places {
    pub user: PathBuf,
    pub plugins: Vec<PathBuf>,
    pub builtin: PathBuf,
}

impl Places {
    fn search(&self) -> Vec<&Path> {
        let mut dirs: Vec<&Path> = vec![&self.user];
        dirs.extend(self.plugins.iter().map(PathBuf::as_path));
        dirs.push(&self.builtin);
        dirs
    }
}

/// Python's `$` at `rest`: the end, or a line end that is the last character.
fn at_end(rest: &str) -> bool {
    rest.is_empty() || rest == "\n"
}

fn is_digit(c: char) -> bool {
    lp_py::decimal(c).is_some()
}

/// `\d+` from the start of `text`: how many bytes the digits take (0: none).
fn digits_len(text: &str) -> usize {
    text.char_indices()
        .find(|&(_, c)| !is_digit(c))
        .map_or(text.len(), |(i, _)| i)
}

/// _SIZED: `^(.+)-(\d{2,5})x(\d{2,5})$` — the name it is a version of and the size.
pub fn sized(name: &str) -> Option<(&str, i64, i64)> {
    let body = name.strip_suffix('\n').unwrap_or(name);
    let height_start = body
        .char_indices()
        .rev()
        .take_while(|&(_, c)| is_digit(c))
        .last()
        .map(|(i, _)| i)?;
    let height = &body[height_start..];
    let before_height = body[..height_start].strip_suffix('x')?;
    let width_start = before_height
        .char_indices()
        .rev()
        .take_while(|&(_, c)| is_digit(c))
        .last()
        .map(|(i, _)| i)?;
    let width = &before_height[width_start..];
    let prefix = before_height[..width_start].strip_suffix('-')?;
    let count = |d: &str| d.chars().count();
    if !(2..=5).contains(&count(width)) || !(2..=5).contains(&count(height)) {
        return None;
    }
    if prefix.is_empty() || prefix.contains('\n') {
        return None;
    }
    let value = |d: &str| lp_py::int_of_digits(d).expect("digits") as i64;
    Some((prefix, value(width), value(height)))
}

/// `(-vertical|-\d+x\d+)?$` at `rest`.
fn version_ending(rest: &str) -> bool {
    if at_end(rest) {
        return true;
    }
    if let Some(after) = rest.strip_prefix(VERTICAL) {
        if at_end(after) {
            return true;
        }
    }
    let Some(after) = rest.strip_prefix('-') else {
        return false;
    };
    let first = digits_len(after);
    if first == 0 {
        return false;
    }
    let Some(after) = after[first..].strip_prefix('x') else {
        return false;
    };
    let second = digits_len(after);
    second > 0 && at_end(&after[second..])
}

/// Where `marker` first stands with only a version's ending after it (a regular expression's
/// search for `marker(?=(-vertical|-\d+x\d+)?$)`).
fn find_ending(name: &str, marker: &str) -> Option<usize> {
    name.match_indices(marker)
        .map(|(i, _)| i)
        .find(|&i| version_ending(&name[i + marker.len()..]))
}

/// version_stem: the name the versions of `name` share ("lumen" for "lumen-1920x720" and
/// "lumen-vertical").
pub fn version_stem(name: &str) -> &str {
    match sized(name) {
        Some((stem, _, _)) => stem,
        None => name.strip_suffix(VERTICAL).unwrap_or(name),
    }
}

/// version_base: the theme `name` is a version of, as the name says; None for one that is no
/// version.
pub fn version_base(name: &str) -> Option<&str> {
    if let Some((stem, _, _)) = sized(name) {
        return Some(stem);
    }
    for ending in [VERTICAL, PAIR] {
        if name.len() > ending.len() {
            if let Some(base) = name.strip_suffix(ending) {
                return Some(base);
            }
        }
    }
    None
}

/// _root: the theme a version is one of, through all its endings.
pub fn root(name: &str) -> &str {
    let mut name = name;
    while let Some(base) = version_base(name) {
        name = base;
    }
    name
}

/// is_companion: made to go beside another theme on a second panel.
pub fn is_companion(name: &str) -> bool {
    find_ending(name, "-companion").is_some()
}

/// is_agents_screen: a screen of the AI agents (`^agents(-|$)`).
pub fn is_agents_screen(name: &str) -> bool {
    name.strip_prefix("agents")
        .is_some_and(|rest| rest.starts_with('-') || at_end(rest))
}

/// follows_first: whether a panel beside the first shows the companion of the first panel's
/// theme.
pub fn follows_first(follow: Option<bool>, theme: &str) -> bool {
    follow.unwrap_or_else(|| is_companion(theme))
}

/// find_theme: the folder of the theme `name`, the first place that has it.
pub fn find_theme(name: &str, places: &Places) -> Result<PathBuf> {
    if !valid_theme_name(name) {
        return Err(Error {
            kind: Kind::Theme,
            msg: format!("invalid theme name {}", lp_py::repr(name)),
        });
    }
    for base in places.search() {
        let folder = base.join(name);
        if folder.join(THEME_FILENAME).is_file() {
            return Ok(folder);
        }
    }
    Err(Error {
        kind: Kind::Theme,
        msg: format!("theme {} not found", lp_py::repr(name)),
    })
}

fn installed(name: &str, places: &Places) -> bool {
    find_theme(name, places).is_ok()
}

/// pair_version: the version of `shown` for a panel with a companion beside it, where that is
/// installed.
pub fn pair_version(shown: &str, places: &Places) -> Option<String> {
    if is_companion(shown) || shown.contains(&format!("{PAIR}-")) || shown.ends_with(PAIR) {
        return None;
    }
    let base = version_base(shown).unwrap_or(shown);
    let paired = format!("{base}{PAIR}{}", &shown[base.len()..]);
    installed(&paired, places).then_some(paired)
}

/// unpaired_version: the version of the pair version `shown` for a panel without a companion,
/// where that is installed.
pub fn unpaired_version(shown: &str, places: &Places) -> Option<String> {
    let at = find_ending(shown, PAIR)?;
    let alone = format!("{}{}", &shown[..at], &shown[at + PAIR.len()..]);
    (!alone.is_empty() && installed(&alone, places)).then_some(alone)
}

/// companion_for: the companion of the theme the first panel shows, where one is installed.
pub fn companion_for(first: &str, places: &Places) -> Option<String> {
    let root = root(first);
    if is_companion(root) {
        return None;
    }
    let name = format!("{root}-companion");
    installed(&name, places).then_some(name)
}

/// theme_for: the version of `name` for a panel mounted `orientation` ("" leaves it to the
/// theme) whose frames are `size`, where that version is installed; else `name`.
pub fn theme_for(
    name: &str,
    orientation: &str,
    size: Option<(i64, i64)>,
    places: &Places,
) -> String {
    let mut name = name.to_string();
    if let (Some((stem, w, h)), Some(size)) = (sized(&name), size) {
        if (w, h) != size && installed(stem, places) {
            name = stem.to_string();
        }
    }
    if let Some((width, height)) = size {
        let own = format!("{}-{width}x{height}", version_stem(&name));
        if own != name && installed(&own, places) {
            return own;
        }
        if own == name {
            return name;
        }
    }
    let other = if orientation == "portrait" && !name.ends_with(VERTICAL) {
        format!("{name}{VERTICAL}")
    } else if orientation == "landscape" && name.ends_with(VERTICAL) {
        name[..name.len() - VERTICAL.len()].to_string()
    } else {
        return name;
    };
    if installed(&other, places) {
        other
    } else {
        name
    }
}

/// list_themes: every installed theme, once (the user's over a plugin's over the app's), by id.
pub fn list_themes(places: &Places) -> Vec<Dict> {
    let mut found: Dict = Dict::new();
    for base in places.search().into_iter().rev() {
        if !base.is_dir() {
            continue;
        }
        let Ok(entries) = std::fs::read_dir(base) else {
            continue;
        };
        let mut folders: Vec<PathBuf> = entries.filter_map(|e| e.ok().map(|e| e.path())).collect();
        folders.sort();
        for folder in folders {
            let name = folder
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_string();
            if !(folder.join(THEME_FILENAME).is_file() && valid_theme_name(&name)) {
                continue;
            }
            let source = if base == places.user {
                "user"
            } else {
                "plugin"
            };
            let mut entry = Dict::new();
            entry.insert("id".into(), name.clone().into());
            entry.insert("builtin".into(), (source != "user").into());
            let shown = if base == places.builtin {
                "built-in"
            } else {
                source
            };
            entry.insert("source".into(), shown.into());
            entry.insert("path".into(), folder.to_string_lossy().into_owned().into());
            found.insert(name, Value::Dict(entry));
        }
    }
    let mut themes: Vec<(String, Dict)> = found
        .into_iter()
        .map(|(k, v)| match v {
            Value::Dict(d) => (k, d),
            _ => unreachable!(),
        })
        .collect();
    themes.sort_by(|a, b| a.0.cmp(&b.0));
    themes.into_iter().map(|(_, d)| d).collect()
}

/// shutil.copytree: the folder and all in it, files where links are (as copytree copies).
fn copy_tree(from: &Path, to: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(to)?;
    let mut entries: Vec<PathBuf> = std::fs::read_dir(from)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .collect();
    entries.sort();
    for entry in entries {
        let target = to.join(entry.file_name().expect("a name"));
        if entry.is_dir() {
            copy_tree(&entry, &target)?;
        } else {
            std::fs::copy(&entry, &target)?;
        }
    }
    Ok(())
}

/// save_theme: the theme `data` checked and saved as `name` in the user's themes (a copy of
/// the theme `source` takes that theme's pictures and fonts along), written whole or not at
/// all.
pub fn save_theme(
    name: &str,
    data: &Value,
    source: Option<&str>,
    places: &Places,
    env: &Env,
) -> Result<PathBuf> {
    if !valid_theme_name(name) {
        return Err(Error {
            kind: Kind::Theme,
            msg: format!("invalid theme name {}", lp_py::repr(name)),
        });
    }
    let target = places.user.join(name);
    if let Some(source) = source.filter(|s| !s.is_empty() && *s != name) {
        if !target.exists() {
            let from = find_theme(source, places)?;
            copy_tree(&from, &target).map_err(|e| Error {
                kind: Kind::Os("OSError"),
                msg: e.to_string(),
            })?;
        }
    }
    std::fs::create_dir_all(&target).map_err(|e| Error {
        kind: Kind::Os("OSError"),
        msg: e.to_string(),
    })?;
    let theme = parse_theme(data, Some(&target), env)?;
    let text = lp_py::json::dumps(&theme.to_dict(), &lp_py::json::Dumps::file(2)) + "\n";
    let tmp = target.join(format!("{THEME_FILENAME}.tmp"));
    let written = std::fs::write(&tmp, text.as_bytes())
        .and_then(|()| std::fs::rename(&tmp, target.join(THEME_FILENAME)));
    written.map_err(|e| Error {
        kind: Kind::Os("OSError"),
        msg: e.to_string(),
    })?;
    Ok(target)
}

/// with_accent: the palette's accent, and every entry of its colour, in `colour`.
pub fn with_accent(theme: &Theme, colour: Option<&str>) -> Theme {
    let base = theme
        .palette
        .get("accent")
        .and_then(Value::as_str)
        .filter(|b| !b.is_empty());
    let (Some(colour), Some(base)) = (colour.filter(|c| !c.is_empty()), base) else {
        return theme.clone();
    };
    let base = lp_py::lower(base);
    let mut out = theme.clone();
    out.palette = theme
        .palette
        .iter()
        .map(|(name, value)| {
            let same = value.as_str().is_some_and(|v| lp_py::lower(v) == base);
            (
                name.clone(),
                if same { colour.into() } else { value.clone() },
            )
        })
        .collect();
    out
}

/// with_card_opacity: cards that cover `percent` % of the background (0: as the theme has
/// them).
pub fn with_card_opacity(theme: &Theme, percent: i64) -> Theme {
    if percent <= 0 {
        return theme.clone();
    }
    let mut style = style_defaults();
    for (k, v) in theme.style.clone().unwrap_or_default() {
        style.insert(k, v);
    }
    let share = percent as f64 / 100.0;
    let opacity = if share > CARD_OPACITY_MIN {
        share
    } else {
        CARD_OPACITY_MIN
    };
    style.insert("card_opacity".into(), opacity.into());
    let mut out = theme.clone();
    out.style = Some(style);
    out
}

/// with_light_colours: the theme in light colours; off: as it is.
pub fn with_light_colours(theme: &Theme, on: bool) -> Result<Theme> {
    if on {
        crate::light::light_theme(theme)
    } else {
        Ok(theme.clone())
    }
}

/// with_look: the theme in the look of `source` (its palette's colours by name, its
/// background colour, its fonts where they can be reached, how its modules are framed).
pub fn with_look(theme: &Theme, source: Option<&Theme>) -> Theme {
    let Some(source) = source else {
        return theme.clone();
    };
    if source.root.is_some() && source.root == theme.root {
        return theme.clone();
    }
    let reachable = |font: &str| font.is_empty() || font.starts_with(BUILTIN_PREFIX);
    let mut style = theme.style.clone();
    if let Some(given) = &source.style {
        let mut merged = theme.style.clone().unwrap_or_default();
        for (k, v) in given {
            merged.insert(k.clone(), v.clone());
        }
        for key in ["display_font", "text_font"] {
            let font = given.get(key).map(Value::str).unwrap_or_default();
            if !reachable(&font) {
                // (`theme.style or STYLE_DEFAULTS`: an empty style is no style)
                let own = theme
                    .style
                    .clone()
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(style_defaults);
                let kept = own.get(key).filter(|v| v.truthy()).cloned();
                merged.insert(key.into(), kept.unwrap_or_else(|| DEFAULT_FONT.into()));
            }
        }
        style = Some(merged);
    }
    let mut out = theme.clone();
    let mut palette = theme.palette.clone();
    for (k, v) in &source.palette {
        palette.insert(k.clone(), v.clone());
    }
    out.palette = palette;
    out.background_color = source.background_color.clone();
    out.font = if reachable(&source.font) {
        source.font.clone()
    } else {
        theme.font.clone()
    };
    out.style = style;
    out
}

/// switches_for: the person's choice of the theme's switches, for every theme ("*") and then
/// for the theme its versions are made from.
pub fn switches_for(theme: &Theme, chosen: &Dict) -> Dict {
    let name = theme
        .root
        .as_ref()
        .and_then(|r| r.file_name())
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_string();
    let own = version_base(&name).unwrap_or(&name).to_string();
    let mut out = Dict::new();
    for key in ["*", own.as_str()] {
        if let Some(Value::Dict(d)) = chosen.get(key) {
            for (k, v) in d {
                out.insert(k.clone(), v.clone());
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_of_versions_as_python_s_patterns_read_them() {
        assert_eq!(sized("lumen-1920x720"), Some(("lumen", 1920, 720)));
        assert_eq!(sized("lumen-1920x720\n"), Some(("lumen", 1920, 720)));
        assert_eq!(sized("a-1-12x345"), Some(("a-1", 12, 345)));
        assert_eq!(sized("a-1234567x12"), None);
        assert_eq!(sized("-12x34"), None);
        assert_eq!(sized("x-\u{661}\u{662}x34"), Some(("x", 12, 34)));
        assert_eq!(version_stem("lumen-vertical"), "lumen");
        assert_eq!(version_base("lumen-pair"), Some("lumen"));
        assert_eq!(version_base("-pair"), None);
        assert_eq!(root("lumen-pair-1920x720"), "lumen");
        assert!(is_companion("arena-companion-vertical") && !is_companion("arena-companions"));
        assert!(
            is_agents_screen("agents-claude")
                && is_agents_screen("agents")
                && !is_agents_screen("agentsx")
        );
        assert_eq!(find_ending("lumen-pair-1920x720", PAIR), Some(5));
    }
}
