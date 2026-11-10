//! The steps of a sequence (sequence.py): the themes shown one after the other, each for its
//! time, as `[[sequence.steps]]` of config.toml names them.

use lp_py::toml::Toml;
use num_bigint::BigInt;
use num_traits::{ToPrimitive, Zero};

use crate::Error;

/// A step shorter than this would be little more than its transition.
pub const SHORTEST_S: i64 = 5;
pub const LONGEST_S: i64 = 7 * 24 * 3600;
/// The times a step may give, and the seconds each counts; they add up.
pub const UNITS: [(&str, i64); 3] = [("hours", 3600), ("minutes", 60), ("seconds", 1)];

/// A theme and how long it stays, in seconds.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Step {
    pub theme: String,
    pub seconds: i64,
}

/// StepError: a step that cannot be shown: says which and why.
fn step_error(msg: String) -> Error {
    Error::new("StepError", msg)
}

/// parse_step: a step as a table has it. What Python does not catch on the way (a time too
/// large for a float, one that is no number) comes out as the error Python raises.
pub fn parse_step(raw: &Toml, at: &str) -> Result<Step, Error> {
    let Toml::Table(raw) = raw else {
        return Err(step_error(format!(
            "{at}: expected a table with a theme and a time"
        )));
    };
    let theme = match raw.get("theme") {
        Some(Toml::Str(theme)) if !lp_py::strip(theme).is_empty() => lp_py::strip(theme),
        _ => return Err(step_error(format!("{at}: needs a theme"))),
    };
    let known = |key: &str| key == "theme" || UNITS.iter().any(|(unit, _)| *unit == key);
    if let Some(unknown) = raw.keys().filter(|key| !known(key)).min() {
        return Err(step_error(format!(
            "{at}: unknown key {} (theme, seconds, minutes, hours)",
            lp_py::repr(unknown)
        )));
    }
    let mut total = 0.0f64;
    for (unit, size) in UNITS {
        let no_number = || step_error(format!("{at}.{unit}: expected a number of {unit}"));
        match raw.get(unit) {
            None => {}
            Some(Toml::Int(value)) => {
                if *value < BigInt::zero() {
                    return Err(no_number());
                }
                // (the product is a whole number still; adding it to the float is what fails)
                let more = lp_py::num::int_to_float(&(value * BigInt::from(size)))
                    .map_err(|e| Error::new(e.kind.name(), e.msg))?;
                total += more;
            }
            // (a NaN is not below zero: it goes on, and is caught where it is rounded)
            Some(Toml::Float(value)) => {
                if *value < 0.0 {
                    return Err(no_number());
                }
                total += value * size as f64;
            }
            Some(_) => return Err(no_number()),
        }
    }
    let seconds = lp_py::num::round_float(total).map_err(|e| Error::new(e.kind.name(), e.msg))?;
    match seconds.to_i64() {
        Some(seconds) if (SHORTEST_S..=LONGEST_S).contains(&seconds) => Ok(Step {
            theme: theme.to_string(),
            seconds,
        }),
        _ => Err(step_error(format!(
            "{at}: shows {seconds} s; a step lasts {SHORTEST_S} s to {} days",
            LONGEST_S / 86400
        ))),
    }
}

/// parse_steps: the steps of a list of tables; `at` is the setting's name (sequence.steps).
pub fn parse_steps(raw: &Toml, at: &str) -> Result<Vec<Step>, Error> {
    let Toml::Array(raw) = raw else {
        return Err(step_error(format!(
            "{at}: expected a list of steps ([[{at}]])"
        )));
    };
    raw.iter()
        .enumerate()
        .map(|(n, entry)| parse_step(entry, &format!("{at}[{n}]")))
        .collect()
}
