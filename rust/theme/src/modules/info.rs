//! What the editor asks of the modules (theme/modules.py): the kinds with their names,
//! sources and sizes, the looks, the starting layouts of a grid, and a module detached into
//! its parts.

use lp_py::{Dict, Value};

use super::layouts::{Wish, TEMPLATES, WISHES};
use super::{default_items, expand, kind_of};
use crate::model::{Env, Result, Theme};

use lp_i18n::t;

/// The module kinds in the order kinds() has them, each with its name (to be translated).
pub const KINDS: &[(&str, &str)] = &[
    ("clock", "Time"),
    ("date", "Calendar"),
    ("weather", "Weather"),
    ("ring", "Ring"),
    ("stat", "Big number"),
    ("huge", "Very large number"),
    ("graph", "History"),
    ("bars", "Bars"),
    ("network", "Network"),
    ("system", "System"),
    ("text", "Title"),
    ("temps", "Temperatures"),
    ("cores", "CPU cores"),
    ("drives", "Drives"),
    ("processes", "Processes"),
    ("netinfo", "Network details"),
    ("battery", "Battery"),
    ("values", "Dashboard"),
    ("sun", "Sun & moon"),
    ("analog", "Analog clock"),
    ("world", "World clock"),
    ("countdown", "Countdown"),
    ("image", "Picture"),
    ("music", "Music"),
    ("volume", "Volume"),
    ("moon", "Moon"),
    ("earth", "Earth"),
    ("outlook", "Next days"),
    ("agenda", "Agenda"),
    ("game", "Game FPS"),
    ("ping", "Ping"),
    ("timer", "Timer"),
    ("power", "Power use"),
    ("mic", "Microphone"),
    ("ups", "UPS"),
    ("ai", "AI activity"),
    ("playback", "Playback card"),
    ("forecast", "Weather card"),
    ("download", "Download card"),
    ("instrument", "Ring instrument"),
    ("cards", "Card slot"),
    ("netcard", "Network card"),
];

/// look_theme_parts: the palette and style a look sets (for the editor's look picker).
pub fn look_theme_parts(look: &str) -> Option<(Dict, Dict)> {
    let chosen = crate::design::looks().get(look)?.as_dict()?;
    let part = |k: &str| {
        chosen
            .get(k)
            .and_then(Value::as_dict)
            .cloned()
            .unwrap_or_default()
    };
    let mut style = crate::model::style_defaults();
    for (k, v) in part("style") {
        style.insert(k, v);
    }
    Some((part("palette"), style))
}

fn ints(values: &[i64]) -> Value {
    Value::List(values.iter().map(|&v| Value::int(v)).collect())
}

/// module_info: what the editor needs: the kinds, their sources and sizes, the looks.
pub fn module_info() -> Dict {
    let mut kinds = Dict::default();
    for &(key, name) in KINDS {
        let kind = kind_of(key).expect("every kind is built");
        let mut info = Dict::default();
        info.insert("name".into(), t(name).into());
        let sources = kind.sources.iter().map(|&s| Value::from(s)).collect();
        info.insert("sources".into(), Value::List(sources));
        info.insert("span".into(), ints(&[kind.span.0, kind.span.1]));
        info.insert("items".into(), default_items(key).into());
        let fans = if key == "temps" {
            default_items("fans")
        } else {
            String::new()
        };
        info.insert("fan_items".into(), fans.into());
        kinds.insert(key.into(), Value::Dict(info));
    }
    let mut looks = Dict::default();
    for (key, look) in crate::design::looks() {
        let (palette, style) = look_theme_parts(key).expect("a look of the table");
        let mut info = Dict::default();
        let name = look.as_dict().and_then(|l| l.get("name")).cloned();
        info.insert("name".into(), name.unwrap_or(Value::None));
        info.insert("palette".into(), Value::Dict(palette));
        info.insert("style".into(), Value::Dict(style));
        looks.insert(key.clone(), Value::Dict(info));
    }
    let grid: Dict = crate::tables::GRID_DEFAULTS
        .iter()
        .map(|&(k, v)| (k.to_string(), Value::int(v)))
        .collect();
    let mut info = Dict::default();
    info.insert("kinds".into(), Value::Dict(kinds));
    info.insert("looks".into(), Value::Dict(looks));
    info.insert("grid".into(), Value::Dict(grid));
    info.insert("style".into(), Value::Dict(crate::model::style_defaults()));
    info
}

/// _m: a module of a starting layout.
fn placed(kind: &str, col: i64, row: i64, cols: i64, rows: i64, source: &str) -> Value {
    let mut m = Dict::default();
    m.insert("module".into(), kind.into());
    m.insert("col".into(), Value::int(col));
    m.insert("row".into(), Value::int(row));
    m.insert("cols".into(), Value::int(cols));
    m.insert("rows".into(), Value::int(rows));
    if !source.is_empty() {
        m.insert("source".into(), source.into());
    }
    Value::Dict(m)
}

/// _pack: each wish placed at the first free spot, smaller if it must be.
fn pack(columns: i64, rows: i64, wishes: &[Wish]) -> Vec<Value> {
    let (w, h) = (columns.max(0) as usize, rows.max(0) as usize);
    let mut free = vec![vec![true; w]; h];
    let mut out = Vec::new();
    for &(kind, source, want_cols, want_rows) in wishes {
        // the sizes of a set of Python's, largest first (sorted is stable: the set's order
        // among those as large)
        let mut made = Vec::new();
        for c in (1..=want_cols).rev() {
            for r in (1..=want_rows).rev() {
                made.push((c.min(columns), r.min(rows)));
            }
        }
        let pairs: Vec<(u64, u64)> = made.iter().map(|&(c, r)| (c as u64, r as u64)).collect();
        let mut sizes: Vec<(i64, i64)> = lp_py::set_order(&pairs)
            .into_iter()
            .map(|(c, r)| (c as i64, r as i64))
            .collect();
        sizes.sort_by_key(|&(c, r)| -c * r);
        let fits = |free: &[Vec<bool>], col: i64, row: i64, cols: i64, rows: i64| {
            (row..row + rows).all(|r| (col..col + cols).all(|c| free[r as usize][c as usize]))
        };
        let mut spot = None;
        'sizes: for &(cols, rows_) in &sizes {
            for row in 0..(rows - rows_ + 1).max(0) {
                for col in 0..(columns - cols + 1).max(0) {
                    if fits(&free, col, row, cols, rows_) {
                        spot = Some((col, row, cols, rows_));
                        break 'sizes;
                    }
                }
            }
        }
        let Some((col, row, cols, rows_)) = spot else {
            continue;
        };
        for r in row..row + rows_ {
            for c in col..col + cols {
                free[r as usize][c as usize] = false;
            }
        }
        out.push(placed(kind, col, row, cols, rows_, source));
    }
    out
}

/// templates: starting layouts for a grid of `columns` x `rows` cells.
pub fn templates(columns: i64, rows: i64) -> Value {
    let names = |key: &str| match key {
        "overview" => t("Overview"),
        "performance" => t("Performance"),
        _ => t("Calm"),
    };
    let chosen: Vec<(&str, Vec<Value>)> =
        match TEMPLATES.iter().find(|(g, _)| *g == (columns, rows)) {
            Some((_, layouts)) => layouts
                .iter()
                .map(|&(key, mods)| {
                    let mods = mods
                        .iter()
                        .map(|&(k, c, r, cs, rs, s)| placed(k, c, r, cs, rs, s))
                        .collect();
                    (key, mods)
                })
                .collect(),
            None => WISHES
                .iter()
                .map(|&(key, wishes)| (key, pack(columns, rows, wishes)))
                .collect(),
        };
    Value::List(
        chosen
            .into_iter()
            .map(|(key, modules)| {
                let mut d = Dict::default();
                d.insert("id".into(), key.into());
                d.insert("name".into(), names(key).into());
                d.insert("modules".into(), Value::List(modules));
                Value::Dict(d)
            })
            .collect(),
    )
}

/// detach: the parts of one module as plain widgets, to edit them one by one (of a slot, its
/// first card).
pub fn detach(theme: &Theme, env: &Env, module_id: &str) -> Result<Vec<Dict>> {
    let expanded = expand(theme, env)?;
    let mut parts = Vec::new();
    for widget in expanded.widgets {
        let of = widget.get("_module") == Some(&Value::from(module_id));
        let card = widget.get("_card").cloned().unwrap_or(Value::int(0));
        if !of || card != Value::int(0) {
            continue; // (the other cards of a slot are not there: it is its first card)
        }
        let mut part: Dict = widget
            .into_iter()
            .filter(|(k, _)| k != "_module" && k != "_card")
            .collect();
        let id = part.get("id").map(Value::str).unwrap_or_default();
        part.insert("id".into(), id.replace('/', "-").into());
        part.insert("group".into(), module_id.into()); // (to be put back together)
        parts.push(part);
    }
    Ok(parts)
}
