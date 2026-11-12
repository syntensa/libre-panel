//! The readings a frame is drawn of (sensors/base.py's Snapshot and Reading), and a widget's
//! conditions on them (render/renderer.py's `needs`: need_holds).

use indexmap::IndexMap;
use lp_pixels::Image;
use lp_py::Value;

/// The clock's own reading: the second of the frame's time, with its fraction.
pub const TIME_SECOND: &str = "time.second";

/// One sensor value (sensors/base.py's Reading).
#[derive(Clone, Debug, PartialEq)]
pub struct Reading {
    pub value: Value,
    pub unit: String,
    pub label: String,
    /// the key of the reading this one repeats under a friendlier name
    pub origin: String,
    /// a state's: when it was published, on the main loop's clock
    pub stamp: Option<f64>,
    /// the device it is read from, as its source names it
    pub device: String,
}

impl Reading {
    /// A reading of `value` with nothing else.
    pub fn of(value: Value) -> Reading {
        Reading {
            value,
            unit: String::new(),
            label: String::new(),
            origin: String::new(),
            stamp: None,
            device: String::new(),
        }
    }
}

/// A moment of local time, as a (naive) datetime has it.
pub use lp_py::datetime::DateTime as Moment;

/// The readings of a frame: by key, their histories, the frame's time and the pictures the
/// services published.
#[derive(Clone, Debug)]
pub struct Snapshot {
    pub readings: IndexMap<String, Reading>,
    /// shared: a snapshot is copied for every frame (the readings that follow on every frame
    /// change it), the histories only with the readings (up to 600 numbers each)
    pub history: std::sync::Arc<IndexMap<String, Vec<f64>>>,
    pub now: Moment,
    /// the fold of `now` (datetime's: true for the later of two moments of one local time)
    pub fold: bool,
    pub images: IndexMap<String, Image>,
}

impl Default for Snapshot {
    /// No readings, at the first moment of 1970 (Python's takes the time it is made at: a
    /// frame that shows the time is given its own).
    fn default() -> Snapshot {
        Snapshot {
            readings: IndexMap::new(),
            history: std::sync::Arc::default(),
            now: Moment::date(1970, 1, 1),
            fold: false,
            images: IndexMap::new(),
        }
    }
}

/// How deep lists and dicts nest in `value` (a number or a text: 0).
fn depth(value: &Value) -> usize {
    match value {
        Value::List(items) => 1 + items.iter().map(depth).max().unwrap_or(0),
        Value::Dict(d) => 1 + d.values().map(depth).max().unwrap_or(0),
        _ => 0,
    }
}

impl Snapshot {
    /// Snapshot.value: the reading's value, the clock's own second, or None.
    pub fn value(&self, key: &str) -> Value {
        if let Some(reading) = self.readings.get(key) {
            return reading.value.clone();
        }
        if key == TIME_SECOND {
            let now = self.now;
            return Value::Float(f64::from(now.second) + f64::from(now.microsecond) / 1e6);
        }
        Value::None
    }

    /// Snapshot.state: the dict or list a service published (its text, read by json.loads anew);
    /// None where there is none, it is null or the text is no JSON. Err where Python fails at
    /// it otherwise (RecursionError), reads what Rust's str cannot hold, or nests it deeper
    /// than Python's stack is known to take here.
    pub fn state(&self, key: &str) -> std::result::Result<Option<Value>, String> {
        let Value::Str(text) = self.value(key) else {
            return Ok(None);
        };
        match lp_py::json::loads(&text) {
            Ok(found) => {
                if depth(&found) > 1000 {
                    return Err("a state nested deeper than 1000 levels".into());
                }
                Ok(matches!(found, Value::Dict(_) | Value::List(_)).then_some(found))
            }
            Err(e) => match e.kind {
                lp_py::json::ErrorKind::Decode { .. } => Ok(None),
                lp_py::json::ErrorKind::Value if e.msg.starts_with("Exceeds the limit") => Ok(None),
                _ => Err(format!(
                    "a state Rust does not read as Python does: {}",
                    e.msg
                )),
            },
        }
    }

    /// _finite: every reading that is NaN, infinite or an int too large for a float taken for
    /// none (no widget must draw it or glide to it).
    pub fn finite(mut self) -> Snapshot {
        for reading in self.readings.values_mut() {
            let bad = match &reading.value {
                Value::Float(x) => !x.is_finite(),
                Value::Int(n) => n.bits() > 1023,
                _ => false,
            };
            if bad {
                reading.value = Value::None;
            }
        }
        self
    }
}

/// _number: a reading's value as a number; None for none, a bool, a text, and NaN or
/// infinity (and an int too large for a float).
pub fn number(value: &Value) -> Option<f64> {
    let x = match value {
        Value::Float(x) => *x,
        Value::Int(n) => lp_py::num::int_to_float(n).ok()?,
        _ => return None,
    };
    x.is_finite().then_some(x)
}

/// _needs_of: the conditions of a `needs`, one by one.
pub fn needs_of(needs: &str) -> impl Iterator<Item = &str> {
    needs
        .split(',')
        .map(lp_py::strip)
        .filter(|need| !need.is_empty())
}

/// What _COMPARE finds in a condition that compares: the key, the operator and the value
/// (a number: `numeric`; else a word).
struct Compare<'a> {
    key: &'a str,
    op: &'a str,
    value: &'a str,
    numeric: bool,
}

/// \w of Python's str patterns: what isalnum() takes (the letters and the numbers of every
/// script), and the underscore.
fn word(ch: char) -> bool {
    use unicode_general_category::{get_general_category, GeneralCategory as G};
    ch == '_'
        || matches!(
            get_general_category(ch),
            G::UppercaseLetter
                | G::LowercaseLetter
                | G::TitlecaseLetter
                | G::ModifierLetter
                | G::OtherLetter
                | G::DecimalNumber
                | G::LetterNumber
                | G::OtherNumber
        )
}

/// `-?\d+(?:\.\d+)?` over all of `text`.
fn numeric(text: &str) -> bool {
    let text = text.strip_prefix('-').unwrap_or(text);
    let (whole, part) = match text.split_once('.') {
        Some((whole, part)) => (whole, Some(part)),
        None => (text, None),
    };
    let digits = |s: &str| !s.is_empty() && s.chars().all(|c| lp_py::decimal(c).is_some());
    digits(whole) && part.is_none_or(digits)
}

/// _COMPARE.match of a condition without its `!` and white space:
/// `^(?P<key>[^=<>!]+?)\s*(?P<op>==|=|!=|>=|<=|>|<)\s*(?P<value>-?\d+(?:\.\d+)?|[A-Za-z_][\w .-]*?)$`.
/// The key cannot hold an operator's character, so the operator begins at the first of them,
/// and the shortest key is what lies before it without the white space after it; the value
/// is all the rest after the white space after the operator.
fn compare(body: &str) -> Option<Compare<'_>> {
    let at = body.find(['=', '<', '>', '!'])?;
    let head = body[..at].trim_end_matches(lp_py::isspace);
    let key = if head.is_empty() {
        // (all of it white space: the key is its first character, \s* the rest)
        let first = body[..at].chars().next()?;
        &body[..first.len_utf8()]
    } else {
        head
    };
    let rest = &body[at..];
    let op = ["==", "=", "!=", ">=", "<=", ">", "<"]
        .into_iter()
        .find(|op| rest.starts_with(op))?;
    let value = rest[op.len()..].trim_start_matches(lp_py::isspace);
    if numeric(value) {
        return Some(Compare {
            key,
            op,
            value,
            numeric: true,
        });
    }
    let mut chars = value.chars();
    let first = chars.next()?;
    let wordy = (first.is_ascii_alphabetic() || first == '_')
        && chars.all(|c| word(c) || c == ' ' || c == '.' || c == '-');
    wordy.then_some(Compare {
        key,
        op,
        value,
        numeric: false,
    })
}

/// need_key: the sensor a condition of `needs` reads: "!gpu.load" and "media.playing=1" read
/// gpu.load and media.playing; "@media.cover" (a picture) media.cover.
pub fn need_key(need: &str) -> &str {
    let need = lp_py::strip(lp_py::strip(need).trim_start_matches('!'));
    let key = match compare(need) {
        Some(found) => lp_py::strip(found.key),
        None => need,
    };
    key.trim_start_matches('@')
}

/// need_holds: whether one condition of `needs` holds (a switch of the theme aside: the
/// renderer's own). A reading that has a value (`gpu.load`), none (`!gpu.load`; a state that
/// is `null` has none), a picture a source has (`@media.app_icon`), or compares so
/// (`media.playing=1`; a reading without a number never does; `game.limiter=gpu`: `=` and
/// `!=` with a word, in any case); a `!` in front says the opposite.
pub fn need_holds(need: &str, snapshot: &Snapshot) -> bool {
    let need = lp_py::strip(need);
    let body = lp_py::strip(need.trim_start_matches('!'));
    let holds = if let Some(name) = body.strip_prefix('@') {
        snapshot.images.contains_key(name)
    } else if let Some(found) = compare(body) {
        let value = snapshot.value(lp_py::strip(found.key));
        if found.numeric {
            let value = match value {
                Value::Bool(on) => Value::Float(if on { 1.0 } else { 0.0 }), // (on is 1)
                other => other,
            };
            let wanted = lp_py::float_of_str(found.value).unwrap_or(f64::NAN);
            number(&value).is_some_and(|n| match found.op {
                "=" | "==" => n == wanted,
                "!=" => n != wanted,
                ">" => n > wanted,
                ">=" => n >= wanted,
                "<" => n < wanted,
                _ => n <= wanted,
            })
        } else if let (Value::Str(text), "=" | "==" | "!=") = (&value, found.op) {
            let same =
                lp_py::casefold(lp_py::strip(text)) == lp_py::casefold(lp_py::strip(found.value));
            same != (found.op == "!=")
        } else {
            false // (a number is no word, and words have no order)
        }
    } else {
        let value = snapshot.value(body);
        value != Value::None && value != Value::from("null")
    };
    holds != need.starts_with('!')
}
