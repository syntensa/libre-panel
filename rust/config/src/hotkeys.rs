//! The keyboard shortcuts as config.toml writes them (hotkeys.py): `"ctrl+alt+p"` is the
//! combination Ctrl+Alt+P. What is wrong with one is said in the user's language.

use lp_py::Value;

use crate::Error;

/// What a shortcut can do, in the order the settings show them.
pub const ACTIONS: [&str; 8] = [
    "pause",
    "brightness_up",
    "brightness_down",
    "open",
    "next_scene",
    "timer",
    "timer_reset",
    "mic_mute",
];
/// How a combination is written.
pub const ORDER: [&str; 4] = ["Ctrl", "Alt", "Shift", "Win"];
/// The names a modifier goes by.
pub const MODIFIERS: [(&str, &str); 9] = [
    ("ctrl", "Ctrl"),
    ("control", "Ctrl"),
    ("strg", "Ctrl"),
    ("alt", "Alt"),
    ("shift", "Shift"),
    ("win", "Win"),
    ("super", "Win"),
    ("meta", "Win"),
    ("cmd", "Win"),
];
/// The keys with a name.
pub const NAMED: [(&str, &str); 13] = [
    ("space", "Space"),
    ("up", "Up"),
    ("down", "Down"),
    ("left", "Left"),
    ("right", "Right"),
    ("home", "Home"),
    ("end", "End"),
    ("pageup", "PageUp"),
    ("pagedown", "PageDown"),
    ("insert", "Insert"),
    ("delete", "Delete"),
    ("pause", "Pause"),
    ("scrolllock", "ScrollLock"),
];
/// The most digits int() reads (sys.get_int_max_str_digits()).
const INT_MAX_STR_DIGITS: usize = 4300;
/// The function keys: F1 to this one.
const LAST_F: u32 = 24;
/// The first function key nothing types: it needs no modifier (ALONE, with Pause and
/// ScrollLock).
const FIRST_F_ALONE: u32 = 13;

/// A combination: its modifiers in ORDER and its key ("A" to "Z", "0" to "9", "F1" to "F24",
/// or one of NAMED's).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Combo {
    pub mods: Vec<&'static str>,
    pub key: String,
}

impl std::fmt::Display for Combo {
    fn fmt(&self, out: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        for name in &self.mods {
            write!(out, "{name}+")?;
        }
        out.write_str(&self.key)
    }
}

/// `ValueError(t(text, **values))`: a message in the user's language. A catalog whose message
/// cannot take the values raises what str.format does instead.
fn wrong(text: &str, values: &[(&str, &str)]) -> Error {
    let values: Vec<(&str, Value)> = values
        .iter()
        .map(|(name, value)| (*name, Value::Str(value.to_string())))
        .collect();
    match lp_i18n::t_format(text, &values) {
        Ok(msg) => Error::new("ValueError", msg),
        Err(e) => Error::new(e.class, e.msg),
    }
}

/// `int(digits)` of a text str.isdigit() is true of: the number, at most `LAST_F + 1` (all
/// that is asked of it is whether it is a function key's). int() reads decimal digits only, so
/// a text with another digit (a superscript two) is the ValueError int() raises, as is one
/// with more digits than Python converts.
fn function_number(digits: &str) -> Result<u32, Error> {
    let leading = digits
        .chars()
        .take_while(|c| lp_py::decimal(*c).is_some())
        .count();
    if leading > INT_MAX_STR_DIGITS {
        return Err(Error::new(
            "ValueError",
            format!(
                "Exceeds the limit ({INT_MAX_STR_DIGITS} digits) for integer string conversion: \
                 value has {leading} digits; use sys.set_int_max_str_digits() to increase the limit"
            ),
        ));
    }
    if leading < digits.chars().count() {
        let shown: String = lp_py::repr(digits).chars().take(200).collect();
        return Err(Error::new(
            "ValueError",
            format!("invalid literal for int() with base 10: {shown}"),
        ));
    }
    Ok(digits.chars().fold(0u32, |number, c| {
        (number * 10 + lp_py::decimal(c).unwrap_or(0)).min(LAST_F + 1)
    }))
}

/// parse: `"ctrl+alt+p"` as a Combo (`Ctrl+Alt+P`); the error says what is wrong. A
/// combination needs Ctrl, Alt or Win (Shift alone would take a letter from typing), but for
/// the keys nothing types: F13 to F24, Pause, Scroll Lock.
pub fn parse(text: &str) -> Result<Combo, Error> {
    if lp_py::strip(text).is_empty() {
        return Err(wrong("a key combination such as Ctrl+Alt+P", &[]));
    }
    let one_key = || {
        wrong(
            "one key with Ctrl, Alt, Shift or Win: {keys}",
            &[("keys", text)],
        )
    };
    let mut mods: Vec<&'static str> = Vec::new();
    let mut key: Option<String> = None;
    let mut alone = false;
    for part in text.split('+').map(lp_py::strip) {
        let name = lp_py::lower(part);
        if let Some((_, modifier)) = MODIFIERS.iter().find(|(given, _)| *given == name) {
            if !mods.contains(modifier) {
                mods.push(modifier);
            }
            continue;
        }
        if key.is_some() || part.is_empty() {
            return Err(one_key());
        }
        let mut chars = part.chars();
        let single = match (chars.next(), chars.next()) {
            (Some(c), None) if c.is_ascii_alphanumeric() => Some(c.to_ascii_uppercase()),
            _ => None,
        };
        let number = match name.strip_prefix('f') {
            Some(digits) if !digits.is_empty() && digits.chars().all(lp_py::isdigit) => {
                Some(function_number(digits)?)
            }
            _ => None,
        };
        if let Some(c) = single {
            key = Some(c.to_string());
        } else if let Some(number) = number.filter(|n| (1..=LAST_F).contains(n)) {
            key = Some(format!("F{number}"));
            alone = number >= FIRST_F_ALONE;
        } else if let Some((_, named)) = NAMED.iter().find(|(given, _)| *given == name) {
            key = Some(named.to_string());
            alone = matches!(*named, "Pause" | "ScrollLock");
        } else {
            return Err(wrong(
                "not a key Libre Panel can take: {part}",
                &[("part", part)],
            ));
        }
    }
    let Some(key) = key else {
        return Err(one_key());
    };
    if !alone && !mods.iter().any(|m| matches!(*m, "Ctrl" | "Alt" | "Win")) {
        return Err(wrong("{keys} needs Ctrl, Alt or Win", &[("keys", text)]));
    }
    Ok(Combo {
        mods: ORDER.into_iter().filter(|m| mods.contains(m)).collect(),
        key,
    })
}
