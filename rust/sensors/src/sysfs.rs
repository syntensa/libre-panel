//! The small files of /sys and /proc, read as Python reads them (sensors/hwmon.py's read_text
//! and read_number, hardware.py's _text and _number).

use std::io::Read;
use std::path::Path;

/// The contents of a small file, stripped; None where it cannot be read (a sensor that fails
/// answers with an error, not with text). At most 4096 bytes of it.
pub fn read_text(path: &Path) -> Option<String> {
    let mut data = Vec::new();
    std::fs::File::open(path)
        .ok()?
        .take(4096)
        .read_to_end(&mut data)
        .ok()?;
    Some(lp_py::strip(&String::from_utf8_lossy(&data)).to_string())
}

/// The whole of a text file, stripped; "" where it cannot be read.
pub fn whole_text(path: &Path) -> String {
    match std::fs::read(path) {
        Ok(data) => lp_py::strip(&String::from_utf8_lossy(&data)).to_string(),
        Err(_) => String::new(),
    }
}

/// int(text, base) as Python reads it, for base 10 and 16: white space around it, a sign,
/// "0x" before a number of base 16, digits of any script with single underscores between
/// them; None where Python raises ValueError (and beyond i128's range).
pub fn int_of(text: &str, base: u32) -> Option<i128> {
    let body = lp_py::strip(text);
    let (negative, body) = match body.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, body.strip_prefix('+').unwrap_or(body)),
    };
    let (prefixed, digits) = match body.get(..2) {
        Some("0x" | "0X") if base == 16 => (true, &body[2..]),
        _ => (false, body),
    };
    let digits = match digits.strip_prefix('_') {
        Some(rest) if prefixed => rest,
        Some(_) => return None,
        None => digits,
    };
    if digits.is_empty() || digits.starts_with('_') || digits.ends_with('_') {
        return None;
    }
    if digits.contains("__") {
        return None;
    }
    let mut n: i128 = 0;
    for ch in digits.chars().filter(|&c| c != '_') {
        let digit = match lp_py::decimal(ch) {
            Some(d) => d,
            None if base == 16 => ch.to_digit(16).filter(|d| *d >= 10)?,
            None => return None,
        };
        n = n.checked_mul(base as i128)?.checked_add(digit as i128)?;
    }
    Some(if negative { -n } else { n })
}

/// The number in a small file; None where there is none.
pub fn read_number(path: &Path, base: u32) -> Option<i128> {
    let text = read_text(path)?;
    if text.is_empty() {
        return None;
    }
    int_of(&text, base)
}

/// The names in a folder, sorted as Python sorts strs; none where it cannot be listed.
pub fn names(folder: &Path) -> Option<Vec<String>> {
    let mut names: Vec<String> = std::fs::read_dir(folder)
        .ok()?
        .flatten()
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort_by(|a, b| a.chars().cmp(b.chars()));
    Some(names)
}
