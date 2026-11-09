//! List widgets (render/lists.py's pick and used, and the renderer's _draw_list, _list_room,
//! _list_layout, _list_rows, _list_columns, _list_cells, _list_tile, _list_bar, _list_text and
//! the fans' _list_fan_grid, _list_fan_places, _list_fan_texts and _list_turning) in a still
//! frame: the readings a list's lines pick (by key or pattern, named and sorted), as rows with
//! bars beside or below their names, as upright bars side by side, as tiles filled as high as
//! their values, or as fans turning beside their names (render/fan.py).

use std::collections::HashSet;
use std::rc::Rc;
use std::sync::{Mutex, OnceLock};

use lp_pixels::{chops, Draw, Image, Mode};
use lp_py::fnmatch;
use lp_py::num as pn;
use lp_py::{Dict, Value};

use crate::bar;
use crate::compose::paste;
use crate::fan;
use crate::formatting::{auto_format, safe_format, FormatError};
use crate::piece::{Failed, Piece, Result};
use crate::renderer::Renderer;
use crate::shapes::{self, ink, lerp, SUPERSAMPLE};
use crate::snapshot::{Reading, Snapshot};
use crate::widget;

/// The most items a list picks (lists.MAX_ITEMS; a later line adds one more, as Python's).
const MAX_ITEMS: usize = 64;
const DIGITS: &str = "0123456789";

/// A line of a list's items: a key or pattern, the name to show, or one to leave out.
struct Entry {
    pattern: String,
    name: String,
    exclude: bool,
}

/// A reading a list shows: its key, its name, the reading, and the one beside it to show too.
pub(crate) struct Item {
    pub key: String,
    pub name: String,
    pub reading: Reading,
    pub detail: Option<Reading>,
}

impl Item {
    /// Item.number: the reading as a float; none for a bool or anything not a number.
    pub fn number(&self) -> Option<f64> {
        match &self.reading.value {
            Value::Float(x) => Some(*x),
            Value::Int(n) => lp_py::num::int_to_float(n).ok(),
            _ => None,
        }
    }
}

/// parse_items: a line each, stripped; none for an empty one or a comment.
fn parse_items(text: &str) -> Vec<Entry> {
    let mut entries = Vec::new();
    for line in lp_py::splitlines(text) {
        let line = lp_py::strip(line);
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let exclude = line.starts_with('!');
        let rest = line.trim_start_matches('!');
        let (pattern, name) = rest.split_once('=').unwrap_or((rest, ""));
        let pattern = lp_py::strip(pattern);
        if !pattern.is_empty() {
            entries.push(Entry {
                pattern: pattern.to_string(),
                name: lp_py::strip(name).to_string(),
                exclude,
            });
        }
    }
    entries
}

/// A part of _natural's key: a run of digits as its number, the text between as it is.
#[derive(PartialEq, Eq, PartialOrd, Ord)]
enum Part {
    Text(String),
    Number(num_bigint::BigInt),
}

/// _natural: the key cut at its runs of digits (re's \d: any decimal digit), each run taken
/// for its number; a run of other digits ("²") int() refuses, as Python's.
fn natural(key: &str) -> Result<Vec<Part>> {
    let mut parts = Vec::new();
    let mut text = String::new();
    let mut digits = String::new();
    let flush_text = |text: &mut String, parts: &mut Vec<Part>| -> Result<()> {
        if !text.is_empty() && text.chars().all(lp_py::isdigit) {
            return Err(Failed(format!(
                "invalid literal for int() with base 10: {}",
                lp_py::repr(text)
            )));
        }
        parts.push(Part::Text(std::mem::take(text)));
        Ok(())
    };
    for ch in key.chars() {
        if lp_py::decimal(ch).is_some() {
            if digits.is_empty() {
                flush_text(&mut text, &mut parts)?;
            }
            digits.push(ch);
        } else {
            if !digits.is_empty() {
                let number = lp_py::int_of_str(&digits).unwrap_or_default();
                parts.push(Part::Number(number));
                digits.clear();
            }
            text.push(ch);
        }
    }
    if !digits.is_empty() {
        parts.push(Part::Number(lp_py::int_of_str(&digits).unwrap_or_default()));
    }
    flush_text(&mut text, &mut parts)?;
    Ok(parts)
}

/// _sibling: the key beside `key` named `name` ("disk.1.name" for "disk.1.load"); none for a
/// key without a dot.
fn sibling(key: &str, name: &str) -> String {
    match key.rsplit_once('.') {
        Some((head, _)) => format!("{head}.{name}"),
        None => String::new(),
    }
}

/// _matching: the keys of the readings `pattern` matches (fnmatch.filter), in natural order.
fn matching<'a>(snapshot: &'a Snapshot, pattern: &str) -> Result<Vec<&'a str>> {
    let keys = fnmatch::filter(snapshot.readings.keys().map(String::as_str), pattern)
        .map_err(|e| Failed(e.0))?;
    let mut keyed = Vec::with_capacity(keys.len());
    for key in keys {
        keyed.push((natural(key)?, key));
    }
    keyed.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(keyed.into_iter().map(|(_, key)| key).collect())
}

/// pick: the readings a list shows, a reading once (not again under the key it repeats, nor
/// one a line leaves out), named, sorted as the widget says.
pub(crate) fn pick(w: &Dict, snapshot: &Snapshot) -> Result<Vec<Item>> {
    let entries = parse_items(widget::text(w, "items"));
    let excluded: Vec<&str> = entries
        .iter()
        .filter(|e| e.exclude)
        .map(|e| e.pattern.as_str())
        .collect();
    let repeated: Vec<&str> = excluded
        .iter()
        .filter_map(|p| snapshot.readings.get(*p))
        .map(|r| r.origin.as_str())
        .filter(|o| !o.is_empty())
        .collect();
    let detail = widget::text(w, "detail");
    let mut items: Vec<Item> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for entry in entries.iter().filter(|e| !e.exclude) {
        let glob = entry.pattern.contains(['*', '?', '[']);
        let keys: Vec<&str> = if glob {
            matching(snapshot, &entry.pattern)?
        } else if snapshot.readings.contains_key(&entry.pattern) {
            vec![entry.pattern.as_str()]
        } else {
            vec![]
        };
        for key in keys {
            let reading = &snapshot.readings[key];
            let origin = if reading.origin.is_empty() {
                key
            } else {
                reading.origin.as_str()
            };
            if reading.value == Value::None || seen.contains(origin) || seen.contains(key) {
                continue;
            }
            let mut left_out = repeated.contains(&key);
            for pattern in &excluded {
                if left_out {
                    break;
                }
                let either =
                    |name: &str| fnmatch::fnmatchcase(name, pattern).map_err(|e| Failed(e.0));
                left_out = either(key)? || either(origin)?;
            }
            if left_out {
                continue;
            }
            seen.insert(origin.to_string());
            seen.insert(key.to_string());
            let mut name = entry.name.clone();
            if name.is_empty() {
                let beside_key = sibling(key, "name");
                name = match snapshot.readings.get(&beside_key) {
                    Some(beside) if matches!(beside.value, Value::Str(_)) && beside_key != key => {
                        match &beside.value {
                            Value::Str(s) => s.clone(),
                            _ => unreachable!(),
                        }
                    }
                    _ if !reading.label.is_empty() => reading.label.clone(),
                    _ => key.to_string(),
                };
            }
            let extra = if detail.is_empty() {
                None
            } else {
                snapshot.readings.get(&sibling(key, detail)).cloned()
            };
            items.push(Item {
                key: key.to_string(),
                name,
                reading: reading.clone(),
                detail: extra,
            });
            if items.len() >= MAX_ITEMS {
                break;
            }
        }
    }
    match widget::text(w, "sort") {
        order @ ("high" | "low") => {
            let (mut known, unknown): (Vec<Item>, Vec<Item>) =
                items.into_iter().partition(|i| i.number().is_some());
            // (sorted by `number or 0.0`, stably, the greatest first for "high")
            let key = |i: &Item| i.number().unwrap_or(0.0);
            known.sort_by(|a, b| {
                let (a, b) = if order == "high" {
                    (key(b), key(a))
                } else {
                    (key(a), key(b))
                };
                a.partial_cmp(&b).unwrap_or(std::cmp::Ordering::Equal)
            });
            known.extend(unknown);
            items = known;
        }
        "name" => {
            let mut keyed = Vec::with_capacity(items.len());
            for item in items {
                keyed.push((natural(&lp_py::casefold(&item.name))?, item));
            }
            keyed.sort_by(|a, b| a.0.cmp(&b.0));
            items = keyed.into_iter().map(|(_, item)| item).collect();
        }
        _ => {}
    }
    Ok(items)
}

/// The fans seen running since the process began, by key (lists._RAN: every panel's).
fn ran() -> &'static Mutex<HashSet<String>> {
    static RAN: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();
    RAN.get_or_init(|| Mutex::new(HashSet::new()))
}

/// No fan seen running yet (as a process that has just begun: a frame that stands alone).
pub fn forget_fans_run() {
    if let Ok(mut ran) = ran().lock() {
        ran.clear();
    }
}

/// lists.used: whether a fan of a list is one (hide_unused): it has run since the process
/// began, or it is the graphics card's (which stands while the card is cool). A mainboard's
/// header that has read 0 all along has nothing on it.
pub(crate) fn used(item: &Item) -> bool {
    let Ok(mut ran) = ran().lock() else {
        return true;
    };
    if item.number().is_none_or(|n| n > 0.0) {
        ran.insert(item.key.clone());
        return true;
    }
    ran.contains(&item.key) || item.key == "gpu.fan" || item.key.starts_with("fan.gpu")
}

/// short_names: names without the words they all start with ("Core 1", "Core 2": "1", "2").
fn short_names(names: &[String]) -> Vec<String> {
    if names.len() < 2 {
        return names.to_vec();
    }
    let words: Vec<Vec<&str>> = names.iter().map(|n| n.split(' ').collect()).collect();
    let mut common = 0;
    while words.iter().all(|w| w.len() > common + 1)
        && words.iter().all(|w| w[common] == words[0][common])
    {
        common += 1;
    }
    words.iter().map(|w| w[common..].join(" ")).collect()
}

/// A row of a list: its name, its detail, its value as text, its share of the bar (rounded to
/// three places), the colour of the highest rule its number is above.
pub(crate) struct Row {
    name: String,
    detail: String,
    text: String,
    frac: f64,
    rule: Option<String>,
}

impl Row {
    /// What the row is kept by.
    fn key(&self) -> Value {
        Value::List(vec![
            self.name.clone().into(),
            self.detail.clone().into(),
            self.text.clone().into(),
            Value::Float(self.frac),
            self.rule.clone().map_or(Value::None, Value::Str),
        ])
    }
}

/// The rows' key, for the piece kept.
pub(crate) fn rows_key(rows: &[Row]) -> Value {
    Value::List(rows.iter().map(Row::key).collect())
}

/// What the rows' texts show (their bars' shares left out: a list of fans keeps its names and
/// numbers while its fans turn on them).
pub(crate) fn texts_key(rows: &[Row]) -> Value {
    Value::List(
        rows.iter()
            .map(|row| match row.key() {
                Value::List(mut parts) => {
                    parts.remove(3);
                    Value::List(parts)
                }
                other => other,
            })
            .collect(),
    )
}

/// Python's a / b of two ints (correctly rounded, whatever their size).
fn int_div(a: i64, b: i64) -> Result<f64> {
    match pn::true_div(&Value::int(a), &Value::int(b)) {
        Ok(Value::Float(x)) => Ok(x),
        Ok(other) => Err(Failed(format!("a quotient {other:?}"))),
        Err(e) => Err(Failed(e.msg)),
    }
}

/// -(-a // b) of two ints, b from 1 up: a divided by b, rounded up.
fn ceil_div(a: i64, b: i64) -> i64 {
    -(-a).div_euclid(b)
}

/// _list_fan_grid: fans side by side, as Lumen's (a 50 px fan beside a 26 px number): for
/// `count` of them the columns and lines (as even as they go: 2 + 2, not 3 + 1), the width and
/// height of each place, the fan's size, and how many fit at most.
fn fan_grid(w: &Dict, count: usize) -> Result<(i64, i64, f64, f64, i64, i64)> {
    let lw = widget::int(w, "w", 240).max(1);
    let lh = widget::int(w, "h", 160).max(1);
    let fs = widget::int(w, "font_size", 18).max(6);
    let size = round_int(fs as f64 * 1.9).max(12);
    let given = widget::int(w, "columns", 0);
    let fit = if given > 0 {
        given
    } else {
        lw.div_euclid(size + fs * 5).max(1)
    };
    let step = size as f64 + fs as f64 * 0.6;
    let lines = floordiv(lh as f64 + fs as f64 * 0.6, step).max(1);
    let most = fit.saturating_mul(lines);
    let count = (count as i64).min(most).max(1);
    let used = ceil_div(count, fit);
    let columns = if given > 0 {
        fit
    } else {
        ceil_div(count, used)
    };
    let pitch = py_min(int_div(lh, used)?, step * 1.5);
    Ok((columns, used, int_div(lw, columns)?, pitch, size, most))
}

/// _list_fan_places: where each of `count` fans stands (its top left on the list), the lines in
/// the middle.
fn fan_places(w: &Dict, count: usize) -> Result<Vec<(f64, f64)>> {
    let (columns, used, cell_w, pitch, size, _most) = fan_grid(w, count)?;
    let h = widget::int(w, "h", 160) as f64;
    let top = (h - pitch * used as f64) / 2.0 + (pitch - size as f64) / 2.0;
    Ok((0..count as i64)
        .map(|i| {
            (
                i.rem_euclid(columns) as f64 * cell_w,
                top + i.div_euclid(columns) as f64 * pitch,
            )
        })
        .collect())
}

/// _rule_color: the colour of the highest rule `value` is above (none: none).
fn rule_colour(value: Option<f64>, rules: &[(Value, String)]) -> Option<String> {
    let v = value?;
    let step = rules
        .iter()
        .filter(|(above, _)| lp_py::num::gt(&Value::Float(v), above).unwrap_or(false))
        .count();
    (step > 0).then(|| rules[step.min(rules.len()) - 1].1.clone())
}

/// Python's max(a, b) of floats: `a` unless `b` is greater.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Python's min(a, b) of floats: `a` unless `b` is less.
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// Python's round(x) of a float as an int.
fn round_int(x: f64) -> i64 {
    lp_py::round(x) as i64
}

/// Python's a // b of floats (float_div_mod's floor: 1 // 0.1 is 9), as an int.
fn floordiv(a: f64, b: f64) -> i64 {
    lp_py::num::float_div_mod(a, b).0 as i64
}

/// The font size a list's texts are at least: max(6, font_size).
fn font_size(w: &Dict) -> f64 {
    widget::int(w, "font_size", 18).max(6) as f64
}

/// What of a list _list_piece draws: all of it, its text alone (names, numbers), or its levels
/// alone (bars and tiles: they glide, the text stays).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ListPart {
    All,
    Text,
    Levels,
}

/// A box by its corners: (x0, y0, x1, y1).
pub(crate) type Corners = (i64, i64, i64, i64);

/// _within: the part of `layer` that `image` at (x, y) covers (as paste lays it): its corners,
/// None where it covers none of it.
fn within(layer: &Image, image: &Image, x: i64, y: i64) -> Option<Corners> {
    let (x0, y0) = (x.max(0), y.max(0));
    let x1 = (x + image.width() as i64).min(layer.width() as i64);
    let y1 = (y + image.height() as i64).min(layer.height() as i64);
    (x1 > x0 && y1 > y0).then_some((x0, y0, x1, y1))
}

/// Whether nothing of `layer` (RGBA) shows in the box (x0, y0, x1, y1): alpha.crop(box).getbbox()
/// is None.
fn clear_in(layer: &Image, (x0, y0, x1, y1): Corners) -> Result<bool> {
    if layer.mode() != Mode::RGBA {
        return Err(Failed("band not found".into()));
    }
    Ok(layer.crop(x0, y0, x1, y1).getbbox(true).is_none())
}

/// _fans_apart: whether the fans (their pictures at their places on `layer`) lie apart from each
/// other and on nothing of the layer (the names and numbers).
fn fans_apart(layer: &Image, fans: &[(Image, i64, i64)]) -> Result<bool> {
    let shown: Vec<_> = fans
        .iter()
        .filter_map(|(image, x, y)| within(layer, image, *x, *y))
        .collect();
    let apart = shown.iter().enumerate().all(|(i, a)| {
        shown[i + 1..]
            .iter()
            .all(|b| a.2 <= b.0 || b.2 <= a.0 || a.3 <= b.1 || b.3 <= a.1)
    });
    if !apart {
        return Ok(false);
    }
    for &place in &shown {
        if !clear_in(layer, place)? {
            return Ok(false);
        }
    }
    Ok(true)
}

/// _fan_pieces: the fans as pieces over the list (`piece`), each cut as it lies on it, by name.
fn fan_pieces(piece: &Piece, fans: &[(Image, i64, i64)]) -> Vec<(String, Rc<Piece>)> {
    let mut out = Vec::new();
    for (i, (image, x, y)) in fans.iter().enumerate() {
        let Some((x0, y0, x1, y1)) = within(&piece.layer, image, *x, *y) else {
            continue;
        };
        let part = image.crop(x0 - x, y0 - y, x1 - x, y1 - y);
        let fan = Piece::new(part, piece.x + x0, piece.y + y0);
        out.push((format!("fan{i}"), Rc::new(fan)));
    }
    out
}

/// _text_apart: whether a list's names and numbers (`text`) lie on none of its bars (`bars`:
/// where they lie in the layer of its `levels`, None where it does not say): then the bars laid
/// on the frame and the text over them give the pixels the two joined give. Not with effects
/// (a halo reaches past the bars) or where the two are not laid alike.
pub(crate) fn text_apart(
    w: &Dict,
    levels: &Piece,
    text: &Piece,
    bars: Option<&[Corners]>,
) -> Result<bool> {
    if text.layer.mode() != Mode::RGBA {
        return Ok(false);
    }
    if widget::truthy(w, "glow") || widget::truthy(w, "shadow") || widget::truthy(w, "clip") {
        return Ok(false);
    }
    if widget::number(w, "opacity", 1.0) < 0.999 {
        return Ok(false);
    }
    if (levels.x, levels.y, levels.layer.size()) != (text.x, text.y, text.layer.size()) {
        return Ok(false);
    }
    let Some(bars) = bars else {
        return Ok(false);
    };
    for &place in bars {
        if !clear_in(&text.layer, place)? {
            return Ok(false);
        }
    }
    Ok(true)
}

/// _joined: two pieces of a widget as one, `above` laid on `below` (the place of both).
pub(crate) fn joined(below: &Piece, above: &Piece) -> Result<Piece> {
    let (bw, bh) = (below.layer.width() as i64, below.layer.height() as i64);
    let (aw, ah) = (above.layer.width() as i64, above.layer.height() as i64);
    let (x0, y0) = (below.x.min(above.x), below.y.min(above.y));
    let (x1, y1) = (
        (below.x + bw).max(above.x + aw),
        (below.y + bh).max(above.y + ah),
    );
    let mut layer = if (x0, y0, x1 - x0, y1 - y0) == (below.x, below.y, bw, bh) {
        below.layer.clone()
    } else {
        let mut layer = Image::blank(Mode::RGBA, (x1 - x0) as usize, (y1 - y0) as usize);
        layer.alpha_composite_at(&below.layer, (below.x - x0, below.y - y0), (0, 0, bw, bh))?;
        layer
    };
    layer.alpha_composite_at(&above.layer, (above.x - x0, above.y - y0), (0, 0, aw, ah))?;
    let mut piece = below.clone();
    (piece.layer, piece.x, piece.y) = (layer, x0, y0);
    Ok(piece)
}

impl Renderer {
    /// _list_piece of `part`: the list's text alone or its levels alone (or all of it).
    pub(crate) fn list_part_piece(&self, w: &Dict, rows: &[Row], part: ListPart) -> Result<Piece> {
        self.list_part.set(part);
        self.bars_laid.borrow_mut().clear();
        let made = self.list_piece(w, rows);
        self.list_part.set(ListPart::All);
        let laid = std::mem::take(&mut *self.bars_laid.borrow_mut());
        // (the levels of rows and columns: where their bars lie, in the piece's layer, for
        // text_apart: _Levels.boxes, the layer's info["bars"])
        if made.is_ok()
            && part == ListPart::Levels
            && !rows.is_empty()
            && widget::text(w, "style") != "cells"
        {
            let margin = (widget::int(w, "font_size", 18).div_euclid(3)).max(2);
            let boxes = laid
                .into_iter()
                .map(|(x0, y0, x1, y1)| (x0 + margin, y0 + margin, x1 + margin, y1 + margin));
            *self.bars_drawn.borrow_mut() = Some(boxes.collect());
        }
        made
    }

    /// _draw_list: of the `items` picked, those the list shows, their rows (the bars at their
    /// readings, in a moving frame smooth ones on their glide, each with its sensor's) by its
    /// colour `rules` (sorted), and the top of its range (what a fan's ring is a share of).
    pub(crate) fn list_rows(
        &mut self,
        w: &Dict,
        mut items: Vec<Item>,
        rules: &[(Value, String)],
    ) -> Result<(Vec<Item>, Vec<Row>, f64)> {
        let room = self.list_room(w, items.len())?;
        items.truncate(room);
        let lo = widget::number(w, "min", 0.0);
        let mut hi = widget::number(w, "max", 100.0);
        if hi <= lo {
            // (the largest value shown fills the bar)
            hi = items
                .iter()
                .filter_map(Item::number)
                .fold(lo + 1e-9, py_max);
        }
        let format = widget::text(w, "format");
        // (the bars glide to a new reading, with the other bars and rings of its sensor)
        let glide = widget::truthy(w, "smooth")
            && widget::truthy(w, "levels")
            && widget::text(w, "style") != "fans";
        let mut rows = Vec::new();
        for item in &items {
            let reading = &item.reading;
            // (auto_format raises no FormatError: what it raises, Python does not catch; of
            // safe_format's errors it catches FormatError, the text itself or "--" shown)
            let fallback = || match &reading.value {
                Value::Str(s) => s.clone(),
                _ => "--".to_string(),
            };
            let text = if format.is_empty() || format == "auto" {
                auto_format(&reading.value, &reading.unit).map_err(|e| Failed(e.msg))?
            } else {
                match safe_format(format, &reading.value, &reading.unit, &item.name) {
                    Ok(text) => text,
                    Err(FormatError::Format(_)) => fallback(),
                    Err(FormatError::Uncaught(_, msg)) => return Err(Failed(msg)),
                }
            };
            let mut detail = String::new();
            if let Some(extra) = item.detail.as_ref().filter(|d| d.value != Value::None) {
                let detail_format = widget::text(w, "detail_format");
                detail = match safe_format(detail_format, &extra.value, &extra.unit, &item.name) {
                    Ok(text) => text,
                    Err(FormatError::Format(_)) => String::new(),
                    Err(FormatError::Uncaught(_, msg)) => return Err(Failed(msg)),
                };
            }
            let number = item.number();
            let mut shown = number;
            if glide && number.is_some() {
                let mut spring = Dict::new();
                spring.insert("type".into(), "list".into());
                spring.insert("sensor".into(), item.key.clone().into());
                spring.insert("smooth".into(), Value::Bool(true));
                spring.insert("min".into(), Value::Float(lo));
                spring.insert("max".into(), Value::Float(hi));
                shown = self.eased(&spring, number);
            }
            let frac = bar::fraction(shown, lo, hi, widget::text(w, "scale"));
            rows.push(Row {
                name: item.name.clone(),
                detail,
                text,
                frac: lp_py::round_digits(frac, 3),
                rule: rule_colour(number, rules),
            });
        }
        Ok((items, rows, hi))
    }

    /// _list_room: how many items fit.
    fn list_room(&self, w: &Dict, count: usize) -> Result<usize> {
        let lw = widget::int(w, "w", 240).max(1) as f64;
        let fs = font_size(w);
        let limit = match widget::int(w, "max_items", 0) {
            0 => MAX_ITEMS as i64,
            n => n,
        };
        let room = match widget::text(w, "style") {
            "columns" => floordiv(lw, py_max(4.0, fs * 0.45)),
            "cells" => {
                let (iw, ih) = (
                    widget::int(w, "w", 240).max(1),
                    widget::int(w, "h", 160).max(1),
                );
                (iw / 12).saturating_mul(ih / 12)
            }
            // (as many as the grid holds: what it holds is the same whatever the count)
            "fans" => fan_grid(w, 1)?.5,
            _ => {
                let (columns, _, per_column, _, _) = list_layout(w, count);
                columns as i64 * per_column
            }
        };
        Ok((count as i64).min(limit).min(room).max(0) as usize)
    }

    /// _list_piece: the list drawn, its box the widget's (a margin round it for glyphs that
    /// overhang).
    pub(crate) fn list_piece(&self, w: &Dict, rows: &[Row]) -> Result<Piece> {
        let (lw, lh) = (
            widget::int(w, "w", 240).max(1),
            widget::int(w, "h", 160).max(1),
        );
        let margin = (widget::int(w, "font_size", 18).div_euclid(3)).max(2);
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let size = |n: i64| usize::try_from(n).map_err(|_| Failed("a list of no size".into()));
        let mut layer = Image::blank(Mode::RGBA, size(lw + 2 * margin)?, size(lh + 2 * margin)?);
        let mut inner = Image::blank(Mode::RGBA, size(lw)?, size(lh)?);
        if rows.is_empty() {
            let empty = widget::text(w, "empty");
            if !empty.is_empty() {
                let size = py_max(7.0, widget::int(w, "font_size", 18) as f64 * 0.7);
                let (muted, font) = (widget::text(w, "muted"), widget::text(w, "label_font"));
                let at = (lw as f64 / 2.0, lh as f64 / 2.0 - 0.35 * size);
                self.list_text(
                    &mut inner,
                    empty,
                    at,
                    size,
                    muted,
                    font,
                    ("center", lw as f64),
                    false,
                )?;
            }
        } else {
            match widget::text(w, "style") {
                "columns" => self.list_columns(&mut inner, w, rows)?,
                "cells" => self.list_cells(&mut inner, w, rows)?,
                "fans" => self.list_fan_texts(&mut inner, w, rows)?,
                _ => self.list_lines(&mut inner, w, rows)?,
            }
        }
        let whole = (0, 0, inner.width() as i64, inner.height() as i64);
        layer.alpha_composite_at(&inner, (margin, margin), whole)?;
        let mut piece = Piece::new(layer, x - margin, y - margin);
        piece.hit = Some([x, y, lw, lh]);
        Ok(piece)
    }

    /// _list_fan_texts: each fan's name and number beside the place it turns in.
    fn list_fan_texts(&self, layer: &mut Image, w: &Dict, rows: &[Row]) -> Result<()> {
        let fs = font_size(w);
        let (_c, _l, cell_w, _p, size, _r) = fan_grid(w, rows.len())?;
        let size = size as f64;
        // (names: small fans' a little larger)
        let ls = py_max(py_max(6.0, fs * 0.62), py_min(fs * 0.72, 11.0));
        let block = 0.7 * ls + 0.55 * fs + 0.7 * fs;
        let width = cell_w - size - fs * 0.65;
        let places = fan_places(w, rows.len())?;
        let (muted, label_font, font) = (
            widget::text(w, "muted"),
            widget::text(w, "label_font"),
            widget::text(w, "font"),
        );
        let label = widget::truthy(w, "uppercase");
        for (&(x0, y0), row) in places.iter().zip(rows) {
            let (x, cap) = (x0 + size + fs * 0.35, y0 + (size - block) / 2.0);
            let name = self.list_name(w, &row.name);
            self.list_text(
                layer,
                &name,
                (x, cap),
                ls,
                muted,
                label_font,
                ("left", width),
                label,
            )?;
            let colour = row.rule.as_deref().unwrap_or(widget::text(w, "color"));
            let at = (x, cap + 0.7 * ls + 0.55 * fs);
            self.list_text(
                layer,
                &row.text,
                at,
                fs,
                colour,
                font,
                ("left", width),
                false,
            )?;
        }
        Ok(())
    }

    /// _list_turning: the fans of a list of them, each turning with its speed in a moving frame
    /// (fan.Rotor's advance, on the beat of its item's sensor) or standing as its speed shows
    /// in a still one (its settle), in its ring (none where the list shows no levels), laid on
    /// the names and numbers (`piece`): over it as pieces of their own (`over`) where they lie
    /// apart from each other and on none of the names and numbers.
    pub(crate) fn list_turning(
        &mut self,
        w: &Dict,
        piece: &Rc<Piece>,
        items: &[Item],
        max_rpm: f64,
    ) -> Result<Rc<Piece>> {
        let size = fan_grid(w, items.len())?.4;
        let Some([r, g, b, _]) = self.colour_of(w, "color2")? else {
            return Err(Failed("'NoneType' object is not subscriptable".into()));
        };
        let colour = [r, g, b].map(|v| v.clamp(0, 255) as u8);
        // (its ring where it shows levels)
        let frame = if widget::truthy(w, "levels") {
            fan::Frame::Ring
        } else {
            fan::Frame::Bare
        };
        let (ox, oy) = (
            widget::int(w, "x", 0) - piece.x,
            widget::int(w, "y", 0) - piece.y,
        );
        let dir = fan::pictures_dir(&self.env.fonts_dir);
        let mut fans = Vec::new();
        let places = fan_places(w, items.len())?;
        for (&(x0, y0), item) in places.iter().zip(items) {
            let key = format!("{}\0{}", widget::text(w, "id"), item.key);
            // (the look and paint the person chose for its sensor or all, else Noctua's G2 in
            // the theme's colour: fan_choices.for_widget of a widget of its sensor alone)
            let (look, own) = self.fan_choices.for_widget(&item.key, "auto", "auto");
            let rotor = if self.options.animate {
                self.turned(&key, &item.key, look.blades, item.number(), max_rpm)
            } else {
                fan::Rotor::settled(&key, item.number(), max_rpm)
            };
            let image = fan::picture(&dir, colour, size, &rotor, frame, look, own)?;
            let half = (size - image.width() as i64) as f64 / 2.0;
            let x = round_int(ox as f64 + x0 + half);
            let y = round_int(oy as f64 + y0 + (size - image.height() as i64) as f64 / 2.0);
            fans.push((image, x, y));
        }
        // (where they lie apart from each other and on none of the names and numbers, they go
        // over the list as pieces of their own, the list as it is: the pixels they give laid
        // on it; a turned list turns them apart)
        if !w.contains_key("_card") && fans_apart(&piece.layer, &fans)? {
            let over = fan_pieces(piece, &fans);
            self.over.insert(widget::text(w, "id").to_string(), over);
            return Ok(piece.clone());
        }
        let mut layer = piece.layer.clone();
        for (image, x, y) in &fans {
            paste(&mut layer, image, *x, *y, false)?;
        }
        let mut turned = (**piece).clone();
        turned.layer = layer;
        Ok(Rc::new(turned))
    }

    /// _list_text: text on a list's layer, its capitals' top at `cap_top`, laid where it falls
    /// (`place`: its alignment and its width at most).
    #[allow(clippy::too_many_arguments)]
    fn list_text(
        &self,
        layer: &mut Image,
        text: &str,
        (x, cap_top): (f64, f64),
        size: f64,
        colour: &str,
        font: &str,
        (align, width): (&str, f64),
        label: bool,
    ) -> Result<()> {
        if text.is_empty() || self.list_part.get() == ListPart::Levels {
            return Ok(());
        }
        let size = round_int(size).max(6);
        let mut part = Dict::new();
        part.insert("x".into(), Value::int(round_int(x)));
        part.insert(
            "y".into(),
            Value::int(round_int(cap_top - 0.3 * size as f64)),
        );
        part.insert("font".into(), font.into());
        part.insert("font_size".into(), Value::int(size));
        part.insert("align".into(), align.into());
        part.insert("max_width".into(), Value::int(round_int(width).max(0)));
        part.insert("fit".into(), "ellipsis".into());
        let spacing = if label {
            round_int(size as f64 * 0.12).max(1)
        } else {
            0
        };
        part.insert("letter_spacing".into(), Value::int(spacing));
        part.insert("tabular".into(), Value::Bool(!label));
        let piece = self.text_piece(&part, text, colour)?;
        paste(layer, &piece.layer, piece.x, piece.y, false)
    }

    /// _list_bar: a bar of a list at `frac` (in steps of what can be seen), in its colours: the
    /// track the background, the bar the widget's second colour (or a rule's), from a deeper
    /// shade of it.
    fn list_bar(
        &self,
        layer: &mut Image,
        w: &Dict,
        frac: f64,
        rule: Option<&str>,
        bounds: (f64, f64, f64, f64),
        direction: &str,
    ) -> Result<()> {
        let (bx, by, bw, bh) = (
            round_int(bounds.0),
            round_int(bounds.1),
            round_int(bounds.2),
            round_int(bounds.3),
        );
        if bw < 2 || bh < 2 || self.list_part.get() == ListPart::Text {
            return Ok(());
        }
        let steps = (if direction == "up" || direction == "down" {
            bh
        } else {
            bw
        }) * SUPERSAMPLE as i64;
        let frac = lp_py::round(frac * steps as f64) / steps as f64;
        let accent = self.colour_of(w, "color2")?.unwrap_or([0; 4]);
        let track = self.colour_of(w, "background")?;
        let deep = match track {
            Some(track) => lerp(accent, track, 0.55),
            None => accent,
        };
        let mut piece = Dict::new();
        for (key, value) in [
            ("x", bx),
            ("y", by),
            ("w", bw),
            ("h", bh),
            ("radius", bw.min(bh).div_euclid(2)),
        ] {
            piece.insert(key.into(), Value::int(value));
        }
        piece.insert("direction".into(), direction.into());
        piece.insert("segments".into(), Value::int(0));
        piece.insert("segment_gap".into(), Value::int(0));
        let [r, g, b, a] = ink(deep);
        piece.insert(
            "color".into(),
            format!("#{r:02x}{g:02x}{b:02x}{a:02x}").into(),
        );
        piece.insert(
            "color2".into(),
            w.get("color2").cloned().unwrap_or(Value::None),
        );
        piece.insert(
            "background".into(),
            w.get("background").cloned().unwrap_or(Value::None),
        );
        let paint = self.paint(&piece, rule)?;
        let made = self.bar_piece(&piece, frac, paint)?;
        if self.list_part.get() == ListPart::Levels {
            // (where it lies, all of it whatever its share: _Levels.put_bar's box)
            let (x0, y0) = (bx.max(0), by.max(0));
            let x1 = (bx + made.layer.width() as i64).min(layer.width() as i64);
            let y1 = (by + made.layer.height() as i64).min(layer.height() as i64);
            if x1 > x0 && y1 > y0 {
                self.bars_laid.borrow_mut().push((x0, y0, x1, y1));
            }
        }
        paste(layer, &made.layer, bx, by, false)
    }

    /// _list_rows: rows in columns: the name, its detail, the value on the right; the bar below
    /// them, or beside the name where a column is wide enough.
    fn list_lines(&self, layer: &mut Image, w: &Dict, rows: &[Row]) -> Result<()> {
        let h = widget::int(w, "h", 160) as f64;
        let fs = font_size(w);
        let (columns, mut column_w, _per, row_h, beside) = list_layout(w, rows.len());
        let columns = columns.min(rows.len()).max(1);
        let gap = fs * 1.2;
        if widget::int(w, "columns", 0) <= 0 {
            // (automatic: the columns share the width)
            column_w =
                (widget::int(w, "w", 240) as f64 - (columns - 1) as f64 * gap) / columns as f64;
        }
        let per_column = rows.len().div_ceil(columns);
        let pitch = py_min(h / per_column as f64, row_h * 1.45);
        let ls = py_max(7.0, fs * 0.62);
        let bars = widget::text(w, "style") == "bars" && widget::truthy(w, "levels");
        let bar_h = round_int(fs * 0.26).max(3) as f64;
        let font = self.font(widget::text(w, "font"), fs as i64)?;
        let cell = Self::digit_cell(&font)?;
        let mut widest: Option<f64> = None;
        for row in rows {
            let mut advances = Vec::new();
            for ch in row.text.chars() {
                advances.push(if DIGITS.contains(ch) {
                    cell
                } else {
                    Self::advance(&font, ch)?
                });
            }
            let total = lp_py::sum_floats(advances);
            widest = Some(widest.map_or(total, |m| py_max(m, total)));
        }
        let widest = widest.unwrap_or(0.0);
        let value_w = py_min(widest + fs * 0.2, column_w * if bars { 0.5 } else { 0.62 });
        let (muted, label_font, own) = (
            widget::text(w, "muted"),
            widget::text(w, "label_font"),
            widget::text(w, "font"),
        );
        let label = widget::truthy(w, "uppercase");
        for (i, row) in rows.iter().enumerate() {
            let (col, line) = (i / per_column, i % per_column);
            let x0 = col as f64 * (column_w + gap);
            let top = line as f64 * pitch + (pitch - row_h) / 2.0;
            let colour = row.rule.as_deref().unwrap_or(widget::text(w, "color"));
            let name = self.list_name(w, &row.name);
            if beside {
                // (name | bar | value)
                let mid = top + row_h / 2.0;
                let name_w = column_w * 0.3;
                self.list_text(
                    layer,
                    &name,
                    (x0, mid - 0.35 * ls),
                    ls,
                    muted,
                    label_font,
                    ("left", name_w - fs * 0.4),
                    label,
                )?;
                let bounds = (
                    x0 + name_w,
                    mid - bar_h / 2.0,
                    column_w - name_w - value_w - fs * 0.5,
                    bar_h,
                );
                self.list_bar(layer, w, row.frac, row.rule.as_deref(), bounds, "right")?;
                self.list_text(
                    layer,
                    &row.text,
                    (x0 + column_w, mid - 0.35 * fs),
                    fs,
                    colour,
                    own,
                    ("right", value_w),
                    false,
                )?;
                continue;
            }
            let cap = top + (row_h - (0.7 * fs + if bars { fs * 0.45 + bar_h } else { 0.0 })) / 2.0;
            let name_w = column_w - value_w - fs * 0.6;
            let name_cap = cap + 0.35 * (fs - ls);
            self.list_text(
                layer,
                &name,
                (x0, name_cap),
                ls,
                muted,
                label_font,
                ("left", name_w),
                label,
            )?;
            if !row.detail.is_empty() {
                let font = self.font(label_font, round_int(ls))?;
                let spacing = if label {
                    round_int(ls * 0.12).max(1) as f64
                } else {
                    0.0
                };
                let used =
                    Self::length(&font, &name)? + spacing * name.chars().count() as f64 + ls * 0.7;
                if used < name_w - ls * 3.0 {
                    self.list_text(
                        layer,
                        &row.detail,
                        (x0 + used, name_cap),
                        ls,
                        muted,
                        label_font,
                        ("left", name_w - used),
                        false,
                    )?;
                }
            }
            self.list_text(
                layer,
                &row.text,
                (x0 + column_w, cap),
                fs,
                colour,
                own,
                ("right", column_w * 0.6),
                false,
            )?;
            if bars {
                let bounds = (x0, cap + 0.7 * fs + fs * 0.45, column_w, bar_h);
                self.list_bar(layer, w, row.frac, row.rule.as_deref(), bounds, "right")?;
            }
        }
        Ok(())
    }

    /// _list_columns: upright bars side by side (the cores of a processor), their values above
    /// them and their names below where there is room.
    fn list_columns(&self, layer: &mut Image, w: &Dict, rows: &[Row]) -> Result<()> {
        let (lw, h) = (
            widget::int(w, "w", 240) as f64,
            widget::int(w, "h", 160) as f64,
        );
        let fs = font_size(w);
        let slot = lw / rows.len() as f64;
        let ls = py_max(6.0, py_min(fs * 0.62, slot * 0.5));
        let names = short_names(&rows.iter().map(|r| r.name.clone()).collect::<Vec<_>>());
        let longest = names.iter().map(|n| n.chars().count()).max().unwrap_or(0);
        let show_names = slot >= ls * 1.4 && longest as f64 * ls * 0.62 <= slot;
        let vs = py_min(fs, slot / 2.3);
        let show_values = vs >= 7.0;
        let top = if show_values {
            vs * 0.7 + vs * 0.6
        } else {
            0.0
        };
        let bottom = h - if show_names { ls * 0.7 + ls * 0.6 } else { 0.0 };
        let bar_w = py_max(2.0, py_min(py_min(slot * 0.62, slot - 2.0), fs * 1.3));
        let (muted, label_font, own) = (
            widget::text(w, "muted"),
            widget::text(w, "label_font"),
            widget::text(w, "font"),
        );
        for (i, row) in rows.iter().enumerate() {
            let cx = slot * i as f64 + slot / 2.0;
            let bounds = (cx - bar_w / 2.0, top, bar_w, bottom - top);
            self.list_bar(layer, w, row.frac, row.rule.as_deref(), bounds, "up")?;
            if show_values {
                let colour = row.rule.as_deref().unwrap_or(widget::text(w, "color"));
                self.list_text(
                    layer,
                    &row.text,
                    (cx, 0.0),
                    vs,
                    colour,
                    own,
                    ("center", slot),
                    false,
                )?;
            }
            if show_names {
                self.list_text(
                    layer,
                    &names[i],
                    (cx, h - ls * 0.7),
                    ls,
                    muted,
                    label_font,
                    ("center", slot),
                    false,
                )?;
            }
        }
        Ok(())
    }

    /// _list_cells: a grid of tiles (as square as they go, wide ones before tall ones), each
    /// filled as high as its value, its name and number in it.
    fn list_cells(&self, layer: &mut Image, w: &Dict, rows: &[Row]) -> Result<()> {
        let (lw, h) = (
            widget::int(w, "w", 240) as f64,
            widget::int(w, "h", 160) as f64,
        );
        let fs = font_size(w);
        let n = rows.len();
        let gap = round_int(lw.min(h) * 0.03).max(2) as f64;
        let mut best = (0.0, 1, n);
        let given = widget::int(w, "columns", 0);
        let choices: Vec<usize> = if given <= 0 {
            (1..=n).collect()
        } else {
            vec![usize::try_from(given).unwrap_or(usize::MAX)]
        };
        for columns in choices {
            let lines = n.div_ceil(columns);
            let cell_w = (lw - (columns - 1) as f64 * gap) / columns as f64;
            let cell_h = (h - (lines - 1) as f64 * gap) / lines as f64;
            let size = py_min(cell_w, cell_h * 1.6);
            if size > best.0 {
                best = (size, columns, lines);
            }
        }
        let (_, columns, lines) = best;
        let cell_w = (lw - (columns - 1) as f64 * gap) / columns as f64;
        let cell_h = (h - (lines - 1) as f64 * gap) / lines as f64;
        let mut names: Vec<String> = rows.iter().map(|r| r.name.clone()).collect();
        let levels = widget::truthy(w, "levels");
        if levels {
            names = short_names(&names);
        }
        let (muted, label_font, own) = (
            widget::text(w, "muted"),
            widget::text(w, "label_font"),
            widget::text(w, "font"),
        );
        for (i, row) in rows.iter().enumerate() {
            let (line, col) = (i / columns, i % columns);
            let (x0, y0) = (col as f64 * (cell_w + gap), line as f64 * (cell_h + gap));
            let cw = round_int(x0 + cell_w) - round_int(x0);
            let ch = round_int(y0 + cell_h) - round_int(y0);
            let radius = cw.min(ch) as f64 * 0.16;
            self.list_tile(
                layer,
                w,
                (x0, y0, cw, ch),
                radius,
                row.frac,
                row.rule.as_deref(),
            )?;
            let (cwf, chf) = (cw as f64, ch as f64);
            let vs = py_min(
                py_min(fs, chf * 0.34),
                cwf / py_max(2.2, row.text.chars().count() as f64 * 0.62),
            );
            let ls = py_max(6.0, py_min(vs * 0.62, chf * 0.2));
            let with_name = chf >= vs * 0.7 + ls * 0.7 + ls * 1.6 && cwf >= ls * 2.0 && ls >= 7.5;
            let block = 0.7 * vs + if with_name { ls * 0.7 + ls * 0.7 } else { 0.0 };
            let mut cap = y0 + (chf - block) / 2.0;
            let cx = x0 + cwf / 2.0;
            if with_name {
                let name = self.list_name(w, &names[i]);
                let label = widget::truthy(w, "uppercase");
                self.list_text(
                    layer,
                    &name,
                    (cx, cap),
                    ls,
                    muted,
                    label_font,
                    ("center", cwf * 0.9),
                    label,
                )?;
                cap += ls * 0.7 + ls * 0.7;
            }
            let colour = row.rule.as_deref().unwrap_or(widget::text(w, "color"));
            self.list_text(
                layer,
                &row.text,
                (cx, cap),
                vs,
                colour,
                own,
                ("center", cwf * 0.9),
                false,
            )?;
        }
        Ok(())
    }

    /// _list_tile: a tile of a list's cells, its track the background, filled as high as its
    /// value in the second colour (or a rule's), two thirds opaque.
    fn list_tile(
        &self,
        layer: &mut Image,
        w: &Dict,
        (x0, y0, cw, ch): (f64, f64, i64, i64),
        radius: f64,
        frac: f64,
        rule: Option<&str>,
    ) -> Result<()> {
        if self.list_part.get() == ListPart::Text {
            return Ok(()); // (a tile is a level: not in the text alone)
        }
        let s = SUPERSAMPLE as i64;
        let track = self.colour_of(w, "background")?;
        let (tw, th) = (cw.saturating_mul(s).max(1), ch.saturating_mul(s).max(1));
        if tw.saturating_mul(th) > crate::renderer::MOST_PIXELS {
            return Err(Failed("a layer too large".into()));
        }
        let (tw, th) = (tw as usize, th as usize);
        let mut tile = Image::blank(Mode::RGBA, tw, th);
        let corners = (0.0, 0.0, (cw * s - 1) as f64, (ch * s - 1) as f64);
        let r = radius * s as f64;
        if let Some(track) = track {
            Draw::new(&mut tile).rounded_rectangle(
                corners,
                r,
                Some(ink(track)),
                None,
                1,
                [true; 4],
            )?;
        }
        if widget::truthy(w, "levels") && frac > 0.0 {
            let fill = match rule {
                Some(rule) => self.color(Some(rule), 1.0)?,
                None => self.colour_of(w, "color2")?,
            }
            .unwrap_or([0; 4]);
            let mut mask = Image::blank(Mode::L, tw, th);
            Draw::new(&mut mask).rounded_rectangle(
                corners,
                r,
                Some([255, 0, 0, 0]),
                None,
                1,
                [true; 4],
            )?;
            let mut cut = Image::blank(Mode::L, tw, th);
            // ([0, (1 - frac) * ch * s, cw * s, ch * s]: from the left, as Python counts)
            let fill_box = (
                0.0,
                (1.0 - frac) * ch as f64 * s as f64,
                (cw * s) as f64,
                (ch * s) as f64,
            );
            Draw::new(&mut cut).rectangle(fill_box, Some([170, 0, 0, 0]), None, 1)?;
            let mask = chops::multiply(&mask, &cut)?;
            let solid = Image::new(Mode::RGBA, tw, th, &ink(fill));
            tile.paste(&solid, 0, 0, Some(&mask))?;
        }
        let down = shapes::down(&tile, cw, ch, SUPERSAMPLE)?;
        paste(layer, &down, round_int(x0), round_int(y0), false)
    }

    /// _list_name: a name in capitals where the list says.
    fn list_name(&self, w: &Dict, name: &str) -> String {
        if widget::truthy(w, "uppercase") {
            lp_py::upper(name)
        } else {
            name.to_string()
        }
    }
}

/// _list_layout: rows and bars: the columns, their width, rows per column, a row's height and
/// whether bars stand beside the names (else below them); as few columns as hold them all,
/// each at least ten font sizes wide.
fn list_layout(w: &Dict, count: usize) -> (usize, f64, i64, f64, bool) {
    let (lw, lh) = (
        widget::int(w, "w", 240).max(1) as f64,
        widget::int(w, "h", 160).max(1) as f64,
    );
    let fs = font_size(w);
    let gap = fs * 1.2;
    let bars = widget::text(w, "style") == "bars" && widget::truthy(w, "levels");
    let given = widget::int(w, "columns", 0);
    let choices: Vec<i64> = if given > 0 {
        vec![given]
    } else {
        let most = floordiv(lw + gap, fs * 10.0 + gap);
        (1..=most.max(1)).collect()
    };
    let detail = widget::truthy(w, "detail");
    let mut result = (1, lw, 0, fs, false);
    for &columns in &choices {
        let column_w = (lw - (columns - 1) as f64 * gap) / columns as f64;
        let beside = bars && !detail && column_w >= fs * 15.0;
        let row_h = fs
            * if beside {
                1.8
            } else if bars {
                2.5
            } else {
                1.75
            };
        let per_column = floordiv(lh, row_h);
        result = (
            usize::try_from(columns).unwrap_or(usize::MAX),
            column_w,
            per_column,
            row_h,
            beside,
        );
        if per_column.saturating_mul(columns) >= count as i64
            || columns == *choices.last().unwrap_or(&1)
        {
            break;
        }
    }
    result
}
