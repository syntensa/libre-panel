//! Readings shown as texts (render/renderer.py's _draw_metric, _format, _rule_step, _rule_mix
//! and _rule_text): a reading's value in the widget's format (its fallback where it has none,
//! the text itself where the format refuses a text), in the colour of the highest threshold its
//! value is above. The volume says "Mute" where its number was, while it is muted. In a moving
//! frame a smooth number glides to a new reading (its sensor's spring, render/motion.py), its
//! text made from the gliding value at the history's points (25 a second), and a colour fades
//! to a threshold's, step by step.

use lp_py::{Dict, Value};

use crate::formatting::{safe_format, FormatError};
use crate::motion::{grid, RATE};
use crate::piece::{Failed, Result};
use crate::renderer::{spring_key, Renderer};
use crate::shapes::lerp;
use crate::snapshot::{number, Reading, Snapshot};
use crate::widget;

pub const VOLUME: &str = "audio.volume";
pub const MUTED: &str = "audio.muted";
/// Sensor texts shown in the panel's language ("Overcast" in German is "Bedeckt").
const TRANSLATED_READINGS: [&str; 4] = [
    "weather.description",
    "battery.state",
    "moon.name",
    "media.state",
];

/// What a metric shows: its text and its colour; or why it is not drawn here yet.
pub(crate) enum Shown {
    Text(String, String),
    Unported(&'static str),
}

/// A widget's colour rules, sorted by `above` as sorted() sorts them (stably); None where one is
/// NaN, which sorted() orders by its own way of merging: not drawn here yet.
pub(crate) fn rules_of(w: &Dict) -> Option<Vec<(Value, String)>> {
    let mut rules = Vec::new();
    if let Some(Value::List(given)) = w.get("color_rules") {
        for rule in given {
            let Value::Dict(rule) = rule else {
                continue; // (the theme loader made each a dict)
            };
            let above = rule.get("above").cloned().unwrap_or(Value::None);
            if matches!(above, Value::Float(x) if x.is_nan()) {
                return None;
            }
            rules.push((above, widget::text(rule, "color").to_string()));
        }
    }
    let less = |a: &Value, b: &Value| lp_py::num::lt(a, b).unwrap_or(false);
    rules.sort_by(|a, b| {
        if less(&a.0, &b.0) {
            std::cmp::Ordering::Less
        } else if less(&b.0, &a.0) {
            std::cmp::Ordering::Greater
        } else {
            std::cmp::Ordering::Equal
        }
    });
    Some(rules)
}

/// Whether the widget has colour rules (they fade where things move).
pub(crate) fn has_rules(w: &Dict) -> bool {
    matches!(w.get("color_rules"), Some(Value::List(rules)) if !rules.is_empty())
}

/// The threshold a value is above: how many rules' `above` it passes (none: 0).
fn step_of(value: Option<f64>, rules: &[(Value, String)]) -> usize {
    match value {
        None => 0,
        Some(v) => rules
            .iter()
            .filter(|(above, _)| lp_py::num::gt(&Value::Float(v), above).unwrap_or(false))
            .count(),
    }
}

/// _smoothstep.
fn smoothstep(u: f64) -> f64 {
    u * u * (3.0 - 2.0 * u)
}

/// _rule_mix: the colour step below `step` and how far on to the next (in eighths).
pub(crate) fn rule_mix(step: f64) -> (usize, f64) {
    let low = step.floor();
    (
        low as usize,
        lp_py::round(smoothstep(step - low) * 8.0) / 8.0,
    )
}

impl Renderer {
    /// _format: a reading's text in the widget's format, and its value as a number.
    pub(crate) fn format_reading(
        &self,
        w: &Dict,
        key: &str,
        reading: Option<&Reading>,
    ) -> Result<(String, Option<f64>)> {
        let fallback = || widget::optional(w, "fallback").unwrap_or("--").to_string();
        let Some(reading) = reading.filter(|r| r.value != Value::None) else {
            return Ok((fallback(), None));
        };
        let mut value = reading.value.clone();
        if let Value::Str(text) = &value {
            if TRANSLATED_READINGS.contains(&key) {
                value = Value::Str(lp_i18n::t(text));
            }
        }
        let format = widget::text(w, "format");
        let text = match safe_format(format, &value, &reading.unit, &reading.label) {
            Ok(text) => text,
            Err(FormatError::Format(_)) => match &value {
                Value::Str(s) => s.clone(),
                _ => fallback(),
            },
            Err(FormatError::Uncaught(_, msg)) => return Err(Failed(msg)),
        };
        Ok((text, number(&value)))
    }

    /// _rule_step: which colour a value shows, 0 the widget's own, i the i-th lowest
    /// threshold's; in a moving frame on its way there (Motion.fade, the same for every widget
    /// of the sensor with the same rules).
    pub(crate) fn rule_step(
        &mut self,
        w: &Dict,
        value: Option<f64>,
        rules: &[(Value, String)],
    ) -> f64 {
        if rules.is_empty() {
            return 0.0;
        }
        let step = step_of(value, rules);
        if !self.options.animate {
            return step as f64;
        }
        let key = (widget::text(w, "sensor").to_string(), rules.to_vec());
        let shown = self.motion.fade(&key, step, self.at);
        if shown != step as f64 {
            self.moving = true;
        }
        shown
    }

    /// _rule_text: the text's colour for `value`: its own, a threshold's, or on the way between
    /// two (mixed, "#rrggbbaa").
    pub(crate) fn rule_colour(
        &mut self,
        w: &Dict,
        value: Option<f64>,
        rules: &[(Value, String)],
    ) -> Result<String> {
        let step = self.rule_step(w, value, rules);
        let colours: Vec<String> = std::iter::once(widget::text(w, "color").to_string())
            .chain(rules.iter().map(|(_, c)| c.clone()))
            .collect();
        let (low, u) = rule_mix(step);
        if u <= 0.0 || low + 1 >= colours.len() {
            return Ok(colours[low.min(colours.len() - 1)].clone());
        }
        let (Some(a), Some(b)) = (
            self.color(Some(&colours[low]), 1.0)?,
            self.color(Some(&colours[low + 1]), 1.0)?,
        ) else {
            // (_lerp of None: Python's TypeError)
            return Err(Failed("a colour of none to fade".into()));
        };
        let [r, g, b, a] = lerp(a, b, u);
        Ok(format!("#{r:02x}{g:02x}{b:02x}{a:02x}"))
    }

    /// _settled's telling: a metric's glide and colour told it was drawn now, with its
    /// reading's value, as drawing it tells them (and where the volume said "Mute" instead).
    pub(crate) fn metric_settled(&mut self, w: &Dict, snapshot: &Snapshot) {
        let reading = snapshot.readings.get(widget::text(w, "sensor"));
        let value = reading.and_then(|r| crate::motion::number(&r.value));
        let glide = self.glide_s(Some(w));
        if widget::flag(w, "smooth", false) && glide > 0.0 {
            let (key, now, lead) = (spring_key(w), self.at, self.lead());
            if self.motion.is_fed(&key) {
                return;
            }
            self.motion.glide(&key, value, now, glide, lead);
            if self.motion.passes(&key, now) {
                return;
            }
        }
        if has_rules(w) {
            if let Some(rules) = rules_of(w) {
                self.rule_step(w, value, &rules);
            }
        }
    }

    /// _draw_metric: the text and its colour; in a moving frame a smooth number on its glide.
    pub(crate) fn metric_text(&mut self, w: &Dict, snapshot: &Snapshot) -> Result<Shown> {
        let sensor = widget::text(w, "sensor");
        let own = widget::text(w, "needs").contains(MUTED); // (a screen that shows muting itself)
        if sensor == VOLUME && !own && snapshot.value(MUTED) == Value::int(1) {
            let colour = match self.theme.palette.get("crit") {
                Some(Value::Str(c)) if !c.is_empty() => c.clone(),
                _ => widget::text(w, "color").to_string(),
            };
            return Ok(Shown::Text(lp_py::upper(&lp_i18n::t("Mute")), colour));
        }
        let reading = snapshot.readings.get(sensor);
        let (mut text, mut value) = self.format_reading(w, sensor, reading)?;
        let Some(rules) = rules_of(w) else {
            return Ok(Shown::Unported("colour rules above NaN"));
        };
        let glide = self.glide_s(Some(w));
        if widget::flag(w, "smooth", false) && glide > 0.0 {
            if !widget::exact(w, "glide_ms") {
                return Ok(Shown::Unported("numbers a float does not hold as they are"));
            }
            // (the gliding value, with bars and rings; 25 new texts a second at most, at the
            // graphs' points)
            let (key, now, lead) = (spring_key(w), self.at, self.lead());
            let state = self.motion.glide(&key, value, now, glide, lead);
            let start = self.motion.start(&key);
            let point = grid(now) as f64 / RATE;
            let at = self
                .motion
                .value(&key, if start > point { start } else { point });
            if let (Some(_), Some(_), Some(at), Some(reading)) = (state, value, at, reading) {
                let moved = Reading {
                    value: Value::Float(at),
                    ..reading.clone()
                };
                let (gliding, shown) = self.format_reading(w, sensor, Some(&moved))?;
                if gliding != text {
                    // (settled once it reads the same)
                    (text, value) = (gliding, shown);
                    self.moving = true;
                }
            }
        }
        let colour = self.rule_colour(w, value, &rules)?;
        Ok(Shown::Text(text, colour))
    }
}
