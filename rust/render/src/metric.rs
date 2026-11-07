//! Readings shown as texts (render/renderer.py's _draw_metric, _format and _rule_text) in a
//! still frame: a reading's value in the widget's format (its fallback where it has none, the
//! text itself where the format refuses a text), in the colour of the highest threshold its
//! value is above. The volume says "Mute" where its number was, while it is muted. A number
//! gliding to a new reading and a colour fading to a threshold's are motion (render/motion.py):
//! not drawn here yet.

use lp_py::{Dict, Value};

use crate::formatting::{safe_format, FormatError};
use crate::piece::{Failed, Result};
use crate::renderer::Renderer;
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

    /// _rule_text in a still frame: the widget's own colour, or that of the highest threshold
    /// `value` is above.
    pub(crate) fn rule_text(w: &Dict, value: Option<f64>, rules: &[(Value, String)]) -> String {
        let step = match value {
            None => 0,
            Some(v) => rules
                .iter()
                .filter(|(above, _)| lp_py::num::gt(&Value::Float(v), above).unwrap_or(false))
                .count(),
        };
        if step == 0 {
            return widget::text(w, "color").to_string();
        }
        rules[step.min(rules.len()) - 1].1.clone()
    }

    /// _draw_metric (a still frame): the text and its colour.
    pub(crate) fn metric_text(&self, w: &Dict, snapshot: &Snapshot) -> Result<Shown> {
        let sensor = widget::text(w, "sensor");
        let own = widget::text(w, "needs").contains(MUTED); // (a screen that shows muting itself)
        if sensor == VOLUME && !own && snapshot.value(MUTED) == Value::int(1) {
            let colour = match self.theme.palette.get("crit") {
                Some(Value::Str(c)) if !c.is_empty() => c.clone(),
                _ => widget::text(w, "color").to_string(),
            };
            return Ok(Shown::Text(lp_py::upper(&lp_i18n::t("Mute")), colour));
        }
        let (text, value) = self.format_reading(w, sensor, snapshot.readings.get(sensor))?;
        let Some(rules) = rules_of(w) else {
            return Ok(Shown::Unported("colour rules above NaN"));
        };
        Ok(Shown::Text(text, Self::rule_text(w, value, &rules)))
    }
}
