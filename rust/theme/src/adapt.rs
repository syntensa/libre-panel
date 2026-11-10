//! A theme moved to another panel model or orientation (theme/adapt.py): "scale" stretches
//! positions to the new size while round things stay round and text keeps its proportions,
//! "keep" only changes the canvas; modules keep their share of the grid. And a theme laid out
//! for a panel it has no version for: the version made for the shape nearest the panel's,
//! scaled onto its size. The arithmetic is Python's: floats where Python divides, round() to
//! even, ints of any size where a theme holds them.

use std::collections::HashSet;
use std::path::{Component, Path, PathBuf};

use lp_py::{num, Dict, Value};
use num_bigint::BigInt;
use num_traits::{FromPrimitive, ToPrimitive};

use crate::grid::{make_cells, Cells};
use crate::model::{parse_theme, Env, Error, Kind, Result, Theme};
use crate::tables::{BLUR_MAX, THEME_FILENAME};
use crate::versions::{version_stem, VERTICAL};

/// Sizes that grow with the smaller scale factor (not stretched).
const UNIFORM: [&str; 14] = [
    "font_size",
    "thickness",
    "radius",
    "outline_width",
    "line_width",
    "glow_radius",
    "shadow_offset",
    "shadow_blur",
    "letter_spacing",
    "segment_gap",
    "backdrop_blur",
    "blur",
    "mark_width",
    "end_dot",
];
/// Widgets whose box keeps its aspect ratio.
const KEEP_ASPECT: [&str; 3] = ["gauge", "image", "fan"];
/// a version made for a shorter side is for such panels only
pub const TINY_SIDE: i64 = 300;
const ROUND_ON_RECT: f64 = 0.2;
/// Pairs past which Python's set or list of them would not fit a computer (MemoryError): a grid
/// of more cells than that comes only of a size no panel has.
const MOST_PAIRS: u64 = 1 << 26;

fn int(n: i64) -> Value {
    Value::int(n)
}

fn float(f: f64) -> Value {
    Value::Float(f)
}

fn is_number(v: &Value) -> bool {
    matches!(v, Value::Int(_) | Value::Float(_) | Value::Bool(_))
}

/// round(x * s)
fn scaled(x: &Value, s: f64) -> Result<Value> {
    Ok(num::round(&num::mul(x, &float(s))?)?)
}

/// round(x * s, digits) of a number: x * s is a float.
fn scaled_to(x: &Value, s: f64, digits: usize) -> Result<Value> {
    match num::mul(x, &float(s))? {
        Value::Float(f) => Ok(float(lp_py::round_digits(f, digits))),
        _ => unreachable!("a number times a float is a float"),
    }
}

/// max(least, round(x * s))
fn at_least(least: i64, x: &Value, s: f64) -> Result<Value> {
    Ok(num::max(&int(least), &scaled(x, s)?)?)
}

/// round(centre - side / 2)
fn corner(centre: &Value, side: &Value) -> Result<Value> {
    let half = num::true_div(side, &int(2))?;
    Ok(num::round(&num::sub(centre, &half)?)?)
}

/// (at + side / 2) * s
fn centre(at: &Value, side: &Value, s: f64) -> Result<Value> {
    let half = num::true_div(side, &int(2))?;
    Ok(num::mul(&num::add(at, &half)?, &float(s))?)
}

/// _scale_widget
fn scale_widget(widget: &Dict, sx: f64, sy: f64) -> Result<Dict> {
    let s = if sy < sx { sy } else { sx };
    let mut out = widget.clone();
    for key in UNIFORM {
        let Some(value) = out.get(key).cloned().filter(is_number) else {
            continue;
        };
        let made = match key {
            "letter_spacing" => scaled_to(&value, s, 2)?,
            "end_dot" => {
                if num::gt(&value, &int(0))? {
                    num::max(&float(1.0), &scaled_to(&value, s, 1)?)?
                } else {
                    value
                }
            }
            _ => {
                let least = match key {
                    "font_size" => 6,
                    "thickness" | "line_width" | "outline_width" | "mark_width" => 1,
                    _ => 0,
                };
                let v = at_least(least, &value, s)?;
                if matches!(
                    key,
                    "glow_radius" | "shadow_blur" | "backdrop_blur" | "blur"
                ) {
                    num::min(&v, &int(BLUR_MAX))?
                } else {
                    v
                }
            }
        };
        out.insert(key.into(), made);
    }
    let get = |k: &str| widget.get(k).cloned().unwrap_or(Value::None);
    let wtype = get("type").str();
    if matches!(
        wtype.as_str(),
        "icon" | "symbol" | "moon" | "agent" | "earth"
    ) {
        // square: kept so, centred
        let size = at_least(8, &get("size"), s)?;
        let cx = centre(&get("x"), &get("size"), sx)?;
        let cy = centre(&get("y"), &get("size"), sy)?;
        let (x, y) = (corner(&cx, &size)?, corner(&cy, &size)?);
        out.insert("size".into(), size);
        out.insert("x".into(), x);
        out.insert("y".into(), y);
        return Ok(out);
    }
    let has_box = out.contains_key("w") && out.contains_key("h");
    // a blurred picture is a backdrop of its colours: it fills its box on any panel
    let blurred = wtype == "image" && get("blur").truthy();
    let keep = KEEP_ASPECT.contains(&wtype.as_str()) && !blurred;
    if has_box && keep && num::gt(&out["w"], &int(0))? && num::gt(&out["h"], &int(0))? {
        let cx = centre(&get("x"), &get("w"), sx)?;
        let cy = centre(&get("y"), &get("h"), sy)?;
        let (w, h) = (at_least(1, &get("w"), s)?, at_least(1, &get("h"), s)?);
        let (x, y) = (corner(&cx, &w)?, corner(&cy, &h)?);
        out.insert("w".into(), w);
        out.insert("h".into(), h);
        out.insert("x".into(), x);
        out.insert("y".into(), y);
        return Ok(out);
    }
    let (x, y) = (scaled(&get("x"), sx)?, scaled(&get("y"), sy)?);
    out.insert("x".into(), x);
    out.insert("y".into(), y);
    if out.get("max_width").is_some_and(Value::truthy) {
        // the room a text has across: it narrows with the panel
        let width = at_least(1, &get("max_width"), sx)?;
        out.insert("max_width".into(), width);
    }
    if let Some(Value::List(area)) = widget.get("glow_area") {
        if area.len() == 4 {
            // a box on the panel, as the widget's
            let box_ = vec![
                scaled(&area[0], sx)?,
                scaled(&area[1], sy)?,
                at_least(1, &area[2], sx)?,
                at_least(1, &area[3], sy)?,
            ];
            out.insert("glow_area".into(), Value::List(box_));
        }
    }
    if has_box {
        let (w, h) = (at_least(1, &get("w"), sx)?, at_least(1, &get("h"), sy)?);
        out.insert("w".into(), w);
        out.insert("h".into(), h);
    }
    Ok(out)
}

/// _on_frame: a rule in the last column or row of the old frame keeps to the new frame's.
fn on_frame(mut widget: Dict, width: &BigInt, height: &BigInt) -> Result<Dict> {
    for (key, side) in [("x", width), ("y", height)] {
        let value = widget.get(key).cloned().unwrap_or(Value::None);
        let last = Value::Int(side - 1);
        widget.insert(key.into(), num::min(&value, &last)?);
    }
    Ok(widget)
}

/// An int Python takes as a count (range()): one that fits, or a MemoryError where what it
/// counts could not be made.
fn count(v: &Value) -> Result<i64> {
    match v {
        Value::Int(n) => n.to_i64().ok_or(Error {
            kind: Kind::Memory,
            msg: String::new(),
        }),
        Value::Bool(b) => Ok(i64::from(*b)),
        other => Err(Error {
            kind: Kind::Type,
            msg: format!(
                "'{}' object cannot be interpreted as an integer",
                other.type_name()
            ),
        }),
    }
}

fn too_many() -> Error {
    Error {
        kind: Kind::Memory,
        msg: String::new(),
    }
}

/// _move_modules: modules onto another grid, the same share of it, without overlapping.
fn move_modules(widgets: Vec<Dict>, old: &Cells, new: &Cells) -> Result<Vec<Dict>> {
    if old.columns == new.columns && old.rows == new.rows {
        return Ok(widgets);
    }
    let Value::Float(sx) = num::true_div(&new.columns, &old.columns)? else {
        unreachable!("ints divide to a float")
    };
    let Value::Float(sy) = num::true_div(&new.rows, &old.rows)? else {
        unreachable!("ints divide to a float")
    };
    let mut taken: HashSet<(i64, i64)> = HashSet::new();
    let mut out = Vec::with_capacity(widgets.len());
    for widget in widgets {
        if widget.get("type") != Some(&Value::from("module")) {
            out.push(widget);
            continue;
        }
        let at = |k: &str| widget.get(k).cloned().unwrap_or(Value::None);
        let cols = num::max(&int(1), &num::min(&new.columns, &scaled(&at("cols"), sx)?)?)?;
        let rows = num::max(&int(1), &num::min(&new.rows, &scaled(&at("rows"), sy)?)?)?;
        let col = num::min(
            &num::max(&int(0), &scaled(&at("col"), sx)?)?,
            &num::sub(&new.columns, &cols)?,
        )?;
        let row = num::min(
            &num::max(&int(0), &scaled(&at("row"), sy)?)?,
            &num::sub(&new.rows, &rows)?,
        )?;
        // where it was, else the nearest free place, smaller if it must be: the sizes in the
        // order Python's set holds them, the largest first
        let (cols, rows) = (count(&cols)?, count(&rows)?);
        if (cols.max(0) as u64).saturating_mul(rows.max(0) as u64) > MOST_PAIRS {
            return Err(too_many());
        }
        let (columns, rows_of) = (count(&new.columns)?, count(&new.rows)?);
        if (columns.max(0) as u64).saturating_mul(rows_of.max(0) as u64) > MOST_PAIRS {
            return Err(too_many());
        }
        let (col, row) = (count(&col)?, count(&row)?);
        let mut sizes: Vec<(u64, u64)> = Vec::new();
        for c in (1..=cols.max(0)).rev() {
            for r in (1..=rows.max(0)).rev() {
                sizes.push((c as u64, r as u64));
            }
        }
        let mut sizes = lp_py::set_order(&sizes);
        sizes.sort_by_key(|&(c, r)| std::cmp::Reverse(c * r));
        let free = |x: i64, y: i64, c: i64, r: i64, taken: &HashSet<(i64, i64)>| {
            (x..x + c).all(|cc| (y..y + r).all(|rr| !taken.contains(&(cc, rr))))
        };
        let mut spot = None;
        for (c, r) in sizes {
            let (c, r) = (c as i64, r as i64);
            let mut places = Vec::new();
            for y in 0..(rows_of - r + 1).max(0) {
                for x in 0..(columns - c + 1).max(0) {
                    places.push((x, y));
                }
            }
            places.sort_by_key(|&(x, y)| (x - col).abs() + (y - row).abs());
            spot = places
                .into_iter()
                .find(|&(x, y)| free(x, y, c, r, &taken))
                .map(|(x, y)| (x, y, c, r));
            if spot.is_some() {
                break;
            }
        }
        let mut moved = widget.clone();
        match spot {
            None => {
                // no room left: kept, but hidden
                moved.insert("visible".into(), false.into());
            }
            Some((x, y, c, r)) => {
                for cc in x..x + c {
                    for rr in y..y + r {
                        taken.insert((cc, rr));
                    }
                }
                for (k, v) in [("col", x), ("row", y), ("cols", c), ("rows", r)] {
                    moved.insert(k.into(), int(v));
                }
            }
        }
        out.push(moved);
    }
    Ok(out)
}

/// adapt_theme: a copy of theme `data` for `model` in `orientation` ("custom": `width` x
/// `height`), "scale" or "keep".
pub fn adapt_theme(
    data: &Value,
    model: &str,
    orientation: &str,
    mode: &str,
    width: &BigInt,
    height: &BigInt,
    env: &Env,
) -> Result<Value> {
    let theme = parse_theme(data, None, env)?;
    let (old_w, old_h) = (BigInt::from(theme.width), BigInt::from(theme.height));
    let (new_w, new_h) = if model == "custom" {
        if *width <= BigInt::from(0) || *height <= BigInt::from(0) {
            return Err(Error {
                kind: Kind::Value,
                msg: "custom size needs width and height".into(),
            });
        }
        (width.clone(), height.clone())
    } else {
        match lp_devices::models::find_model(model) {
            Some(panel) => {
                let (w, h) = panel.size(orientation);
                (BigInt::from(w), BigInt::from(h))
            }
            None => return Err(unknown_model(model)),
        }
    };
    let Value::Dict(mut result) = theme.to_dict() else {
        unreachable!("a theme's dict")
    };
    let mut display = Dict::new();
    display.insert("model".into(), model.into());
    display.insert("orientation".into(), orientation.into());
    display.insert("width".into(), Value::Int(new_w.clone()));
    display.insert("height".into(), Value::Int(new_h.clone()));
    result.insert("display".into(), Value::Dict(display));
    let module = |w: &Dict| w.get("type") == Some(&Value::from("module"));
    let mut widgets: Vec<Dict> = theme.widgets.clone();
    if mode == "scale" {
        let sx = num::int_true_div(&new_w, &old_w)?;
        let sy = num::int_true_div(&new_h, &old_h)?;
        widgets = theme
            .widgets
            .iter()
            .map(|w| {
                if module(w) {
                    Ok(w.clone())
                } else {
                    on_frame(scale_widget(w, sx, sy)?, &new_w, &new_h)
                }
            })
            .collect::<Result<_>>()?;
    } else if mode != "keep" {
        return Err(Error {
            kind: Kind::Value,
            msg: "mode must be \"scale\" or \"keep\"".into(),
        });
    }
    if theme.widgets.iter().any(module) {
        // modules follow the grid either way
        let grid_of = |g: &Option<Dict>| g.clone().map(Value::Dict);
        let old = make_cells(&old_w, &old_h, grid_of(&theme.grid).as_ref(), &theme.model)?;
        if let (true, Some(grid)) = (
            mode == "scale",
            theme.grid.as_ref().filter(|g| !g.is_empty()),
        ) {
            // its margin and gap are pixels too (0: automatic)
            let a = num::int_true_div(&new_w, &old_w)?;
            let b = num::int_true_div(&new_h, &old_h)?;
            let s = if b < a { b } else { a };
            let mut scaled = Dict::new();
            for (key, value) in grid {
                let v = if matches!(key.as_str(), "margin" | "gap") && value.truthy() {
                    at_least(1, value, s)?
                } else {
                    value.clone()
                };
                scaled.insert(key.clone(), v);
            }
            result.insert("grid".into(), Value::Dict(scaled));
        }
        let new = make_cells(&new_w, &new_h, result.get("grid"), model)?;
        widgets = move_modules(widgets, &old, &new)?;
    }
    result.insert(
        "widgets".into(),
        Value::List(widgets.into_iter().map(Value::Dict).collect()),
    );
    let result = Value::Dict(result);
    parse_theme(&result, None, env)?; // the adapted theme must still be valid
    Ok(result)
}

/// get_model's KeyError for a model it does not know (str() of a KeyError is its repr).
fn unknown_model(model: &str) -> Error {
    let message = format!("unknown panel model {}", lp_py::repr(model));
    Error {
        kind: Kind::Key,
        msg: lp_py::repr(&message),
    }
}

/// A size as a version's file gives it: ints of any size.
pub type Size = (BigInt, BigInt);

/// _shape_gap: how far apart two sizes' shapes are, their aspect ratios as a factor (log).
fn shape_gap(a: (i64, i64), b: &Size) -> Result<f64> {
    let first = num::int_true_div(&BigInt::from(a.0), &BigInt::from(a.1))?;
    let second = num::int_true_div(&b.0, &b.1)?;
    let Value::Float(ratio) = num::true_div(&float(first), &float(second))? else {
        unreachable!("floats divide to a float")
    };
    Ok(num::log(ratio)?.abs())
}

/// int(v) as Python takes a JSON value: an int, a float cut toward 0, a bool, a str of digits;
/// None where Python raises ValueError or TypeError (OverflowError for infinity).
fn python_int(v: &Value) -> Result<Option<BigInt>> {
    Ok(match v {
        Value::Int(n) => Some(n.clone()),
        Value::Bool(b) => Some(BigInt::from(u8::from(*b))),
        Value::Float(f) if f.is_nan() => None,
        Value::Float(f) if f.is_infinite() => {
            return Err(Error {
                kind: Kind::Overflow,
                msg: "cannot convert float infinity to integer".into(),
            })
        }
        Value::Float(f) => BigInt::from_f64(f.trunc()),
        Value::Str(s) => lp_py::int_of_str(s),
        _ => None,
    })
}

/// The size a theme file says it is made for (_made_size): `int(display["width"]),
/// int(display["height"])` in that order; None where it cannot be read so.
fn made_size(file: &Path) -> Result<Option<Size>> {
    let Ok(bytes) = std::fs::read(file) else {
        return Ok(None);
    };
    let Ok(text) = crate::paths::read_text(&bytes) else {
        return Ok(None);
    };
    let data = match lp_py::json::loads(&text) {
        Ok(data) => data,
        Err(e) if e.kind == lp_py::json::ErrorKind::Recursion => {
            return Err(Error {
                kind: Kind::Recursion,
                msg: e.to_string(),
            })
        }
        Err(_) => return Ok(None),
    };
    let Some(display) = data.as_dict().and_then(|d| d.get("display")) else {
        return Ok(None);
    };
    let side = |key: &str| -> Result<Option<BigInt>> {
        match display.as_dict().and_then(|d| d.get(key)) {
            Some(v) => python_int(v),
            None => Ok(None),
        }
    };
    let Some(w) = side("width")? else {
        return Ok(None);
    };
    let Some(h) = side("height")? else {
        return Ok(None);
    };
    Ok(Some((w, h)))
}

/// Path.name: the last part of a path ("" where there is none but its anchor).
fn py_name(path: &Path) -> Option<&str> {
    match path.components().next_back() {
        Some(Component::Normal(name)) => name.to_str(),
        Some(Component::ParentDir) => Some(".."),
        _ => Some(""),
    }
}

/// Path.parent: "." above a name of its own.
fn py_parent(path: &Path) -> PathBuf {
    match path.parent() {
        Some(p) if p.as_os_str().is_empty() => PathBuf::from("."),
        Some(p) => p.to_path_buf(),
        None => path.to_path_buf(),
    }
}

/// versions_of: the versions of the theme in `folder` beside it (itself among them), with the
/// size each is made for, in the order the folder lists them (as Python's glob meets them).
pub fn versions_of(folder: &Path) -> Result<Vec<(String, Size)>> {
    // (a name that is no Unicode: none, see docs/RUST.md)
    let Some(name) = py_name(folder) else {
        return Ok(Vec::new());
    };
    let stem = version_stem(name).to_string();
    let parent = py_parent(folder);
    let mut found = Vec::new();
    // glob's scandir: listed whole first, nothing where that fails
    let Ok(entries) =
        std::fs::read_dir(&parent).and_then(|e| e.collect::<std::io::Result<Vec<_>>>())
    else {
        return Ok(found);
    };
    for entry in entries {
        let os_name = entry.file_name();
        // (a name that is no Unicode never starts with a stem that is and ends in a size)
        let Some(other) = os_name.to_str() else {
            continue;
        };
        let tail: String = if cfg!(windows) {
            // Windows: the pattern matches without regard to case, as re.IGNORECASE does
            match lp_py::re::prefix_ignoring_case(&stem, other) {
                Some(n) => other.chars().skip(n).collect(),
                None => continue,
            }
        } else {
            match other.strip_prefix(stem.as_str()) {
                Some(rest) => rest.to_string(),
                None => continue,
            }
        };
        if !tail.is_empty() && tail != VERTICAL && !sized_tail(&tail) {
            continue; // another theme of that stem ("lumen-pair", "lumen-companion")
        }
        let file = parent.join(other).join(THEME_FILENAME);
        if std::fs::metadata(&file).is_err() {
            continue;
        }
        if let Some(size) = made_size(&file)? {
            found.push((other.to_string(), size));
        }
    }
    Ok(found)
}

/// re.fullmatch(r"-\d{2,5}x\d{2,5}", tail)
fn sized_tail(tail: &str) -> bool {
    let Some(rest) = tail.strip_prefix('-') else {
        return false;
    };
    let Some((w, h)) = rest.split_once('x') else {
        return false;
    };
    let digits = |d: &str| {
        (2..=5).contains(&d.chars().count()) && d.chars().all(|c| lp_py::decimal(c).is_some())
    };
    digits(w) && digits(h)
}

fn smaller(size: &Size) -> &BigInt {
    if size.1 < size.0 {
        &size.1
    } else {
        &size.0
    }
}

/// nearest_version: the version whose shape is nearest to `size`'s, within SHAPE_MOST.
pub fn nearest_version(folder: &Path, size: (i64, i64)) -> Result<Option<String>> {
    let shape_most = 1.5f64.ln();
    let tiny = size.0.min(size.1) < TINY_SIDE;
    let mut fits = Vec::new();
    for (name, made) in versions_of(folder)? {
        if (tiny || *smaller(&made) >= BigInt::from(TINY_SIDE))
            && shape_gap(size, &made)? <= shape_most
        {
            fits.push((name, made));
        }
    }
    // the shape first; between two alike, the one made nearest to the size (a little); a
    // square version only where the panel is square too
    let mut best: Option<(f64, String)> = None;
    for (name, made) in fits {
        let round_on_rect = made.0 == made.1 && size.0 != size.1;
        let gap = shape_gap(size, &made)? + if round_on_rect { ROUND_ON_RECT } else { 0.0 };
        let near = num::int_true_div(&BigInt::from(size.0.min(size.1)), smaller(&made))?;
        let key = gap + 0.15 * num::log(near)?.abs();
        let better = match &best {
            None => true,
            Some((k, n)) => key < *k || (key == *k && name < *n),
        };
        if better {
            best = Some((key, name));
        }
    }
    Ok(best.map(|(_, name)| name))
}

/// int(made[key]) of a version's display, with Python's errors.
fn display_int(made: &Value, key: &str) -> Result<BigInt> {
    let Some(v) = made.as_dict().and_then(|d| d.get(key)) else {
        return Err(Error {
            kind: Kind::Key,
            msg: lp_py::repr(key),
        });
    };
    match (v, python_int(v)?) {
        (_, Some(n)) => Ok(n),
        (Value::Str(text), None) => Err(Error {
            kind: Kind::Value,
            msg: format!(
                "invalid literal for int() with base 10: {}",
                lp_py::repr(text)
            ),
        }),
        (Value::Float(_), None) => Err(Error {
            kind: Kind::Value,
            msg: "cannot convert float NaN to integer".into(),
        }),
        (other, None) => Err(Error {
            kind: Kind::Type,
            msg: format!(
                "int() argument must be a string, a bytes-like object or a real number, not '{}'",
                other.type_name()
            ),
        }),
    }
}

/// any(w.get("type") == "module" for w in data.get("widgets", [])): up to the first module,
/// with Python's errors for what is no list of dicts.
fn has_module(data: &Dict) -> Result<bool> {
    let no_get = |v: &str| Error {
        kind: Kind::Attribute,
        msg: format!("'{v}' object has no attribute 'get'"),
    };
    match data.get("widgets") {
        None => Ok(false),
        Some(Value::List(ws)) => {
            for w in ws {
                let Value::Dict(w) = w else {
                    return Err(no_get(w.type_name()));
                };
                if w.get("type") == Some(&Value::from("module")) {
                    return Ok(true);
                }
            }
            Ok(false)
        }
        // a dict goes by its keys, a str by its characters: each a str
        Some(Value::Dict(d)) if d.is_empty() => Ok(false),
        Some(Value::Str(s)) if s.is_empty() => Ok(false),
        Some(Value::Dict(_) | Value::Str(_)) => Err(no_get("str")),
        Some(other) => Err(Error {
            kind: Kind::Type,
            msg: format!("'{}' object is not iterable", other.type_name()),
        }),
    }
}

/// _laid_out_data: the version file laid out on `width` x `height`, as theme data.
fn laid_out_data(file: &Path, width: i64, height: i64, model: &str, env: &Env) -> Result<Value> {
    let bytes = std::fs::read(file).map_err(|e| Error {
        kind: Kind::Os("OSError"),
        msg: e.to_string(),
    })?;
    let text = crate::paths::read_text(&bytes).map_err(|msg| Error {
        kind: Kind::UnicodeDecode,
        msg,
    })?;
    let mut data = lp_py::json::loads(&text).map_err(|e| Error {
        kind: if e.kind == lp_py::json::ErrorKind::Recursion {
            Kind::Recursion
        } else {
            Kind::Value
        },
        msg: e.to_string(),
    })?;
    let orientation = if height > width {
        "portrait"
    } else {
        "landscape"
    };
    let Value::Dict(dict) = &mut data else {
        return Err(Error {
            kind: Kind::Attribute,
            msg: format!("'{}' object has no attribute 'get'", data.type_name()),
        });
    };
    if has_module(dict)? {
        // its modules keep their cells: the grid it was made on, its columns and rows, on the
        // new size
        let made = dict
            .get("display")
            .filter(|d| d.truthy())
            .cloned()
            .unwrap_or(Value::Dict(Dict::new()));
        let (w, h) = (display_int(&made, "width")?, display_int(&made, "height")?);
        let given = dict.get("grid").cloned();
        let model_of = match made.as_dict().and_then(|d| d.get("model")) {
            Some(m) if m.truthy() => m.str(),
            _ => "custom".into(),
        };
        let grid = make_cells(&w, &h, given.as_ref(), &model_of)?;
        let mut merged = match given {
            Some(Value::Dict(g)) => g,
            _ => Dict::new(), // (falsy: anything else is no mapping, make_cells said so)
        };
        merged.insert("columns".into(), grid.columns);
        merged.insert("rows".into(), grid.rows);
        dict.insert("grid".into(), Value::Dict(merged));
    }
    let model = if model == "custom" {
        "custom"
    } else {
        match lp_devices::models::find_model(model) {
            Some(panel) if panel.size(orientation) == (width, height) => model,
            Some(_) => "custom",
            None => return Err(unknown_model(model)),
        }
    };
    let (w, h) = (BigInt::from(width), BigInt::from(height));
    adapt_theme(&data, model, orientation, "scale", &w, &h, env)
}

/// laid_out: `theme` for a panel whose frames are `size`: as it is where it is made for that
/// size, else its version made for the nearest shape laid out on `size`.
pub fn laid_out(
    theme: &Theme,
    size: Option<(i64, i64)>,
    model: &str,
    builtin: &Path,
    env: &Env,
) -> Result<Theme> {
    Ok(laid_out_anew(theme, size, model, builtin, env)?.unwrap_or_else(|| theme.clone()))
}

/// The same, None where `theme` is shown as it is (Python's `laid_out(...) is theme`).
pub fn laid_out_anew(
    theme: &Theme,
    size: Option<(i64, i64)>,
    model: &str,
    builtin: &Path,
    env: &Env,
) -> Result<Option<Theme>> {
    let Some(size) = size else {
        return Ok(None);
    };
    if (theme.width, theme.height) == size {
        return Ok(None);
    }
    let Some(folder) = theme.root.as_deref() else {
        return Ok(None);
    };
    let own = match (
        crate::paths::resolve(folder),
        crate::paths::resolve(builtin),
    ) {
        (Ok(f), Ok(b)) => py_parent(&f) != b,
        (Err(e), _) | (_, Err(e)) if !matches!(e.kind, Kind::Os(_)) => return Err(e),
        _ => return Ok(None),
    };
    if own && versions_of(folder)?.len() < 2 {
        return Ok(None);
    }
    let Some(name) = nearest_version(folder, size)? else {
        return Ok(None);
    };
    let made = py_parent(folder).join(&name);
    let file = made.join(THEME_FILENAME);
    if std::fs::metadata(&file).is_err() {
        return Ok(None);
    }
    match laid_out_data(&file, size.0, size.1, model, env).and_then(|data| {
        // (through JSON text and back, as the app keeps it)
        let text = lp_py::json::dumps(&data, &lp_py::json::Dumps::default());
        let data = lp_py::json::loads(&text).map_err(|e| Error {
            kind: Kind::Value,
            msg: e.to_string(),
        })?;
        parse_theme(&data, Some(&made), env)
    }) {
        Ok(laid) => Ok(Some(laid)),
        // (OSError, ValueError, ThemeError: the theme as it is)
        Err(e)
            if matches!(
                e.kind,
                Kind::Os(_) | Kind::Value | Kind::Theme | Kind::UnicodeDecode
            ) =>
        {
            Ok(None)
        }
        Err(e) => Err(e),
    }
}
