//! Card slots (theme/modules.py): the cards a slot holds, one a line of its `items` (a name,
//! then `for <time>` for a turn of its own, then `if <conditions>` for a rule of its own), and
//! the person's cards in each slot that leaves them to the person.

use lp_py::{Dict, Value};

use super::{kind_of, DEFAULT_CARDS};
use crate::model::Theme;

/// How a slot goes from one card to the next, and which card is on show (render.slot).
pub const CARD_ORDERS: &[&str] = &["first", "turns"];
pub const CARD_TRANSITIONS: &[&str] = &["light", "fade", "slide", "rise", "wipe", "cut", "random"];
/// A card's turn: the shortest and the longest, in seconds.
pub const TURN_S: (f64, f64) = (5.0, 24.0 * 3600.0);

/// A card of a slot: its name, its rule (the conditions after `if`, as a `needs` has them;
/// "": the card's own) and how long its turn lasts in seconds (None: the slot's `turn`).
#[derive(Clone, Debug, PartialEq)]
pub struct SlotCard {
    pub name: String,
    pub rule: String,
    pub seconds: Option<f64>,
}

impl SlotCard {
    fn named(name: &str) -> SlotCard {
        SlotCard {
            name: name.into(),
            rule: String::new(),
            seconds: None,
        }
    }
}

/// Whether `name` is a card a slot can hold (CARD_NAMES): every module kind but the slots,
/// and "net" (the network as the Studio's NET section has it).
pub fn is_card(name: &str) -> bool {
    name == "net" || (name != "cards" && name != "netcard" && kind_of(name).is_some())
}

/// card_time: the time of a card's turn in seconds, as a line has it after `for`: `5m`,
/// `90s`, `1h`, `2.5 min` (a bare number: minutes); None for what is no time, or out of
/// TURN_S.
pub fn card_time(text: &str) -> Option<f64> {
    let text = lp_py::lower(lp_py::strip(text));
    let number = lp_py::strip(text.trim_end_matches(|c: char| c.is_ascii_lowercase()));
    // (the number is where the text begins: it has no white space at its start)
    let unit = match lp_py::strip(&text[number.len()..]) {
        "" => "min",
        unit => unit,
    };
    let size = match unit {
        "s" | "sec" => 1.0,
        "m" | "min" => 60.0,
        "h" => 3600.0,
        _ => return None,
    };
    let seconds = lp_py::float_of_str(number)? * size;
    (seconds.is_finite() && TURN_S.0 <= seconds && seconds <= TURN_S.1).then_some(seconds)
}

/// time_text: a turn's time as a line writes it: `5m`, `90s`, `1h` (card_time reads it back).
pub fn time_text(seconds: f64) -> String {
    for (unit, size) in [("h", 3600.0), ("m", 60.0)] {
        let (whole, rest) = lp_py::num::float_div_mod(seconds, size);
        if rest == 0.0 {
            let whole = num_traits::FromPrimitive::from_f64(whole)
                .unwrap_or_else(num_bigint::BigInt::default);
            return format!("{whole}{unit}");
        }
    }
    format!("{}s", lp_py::format_g(seconds, 6))
}

/// card_line: a line of a slot's `items` (a `#` begins a comment); None for a line that names
/// no card.
pub fn card_line(line: &str) -> Option<SlotCard> {
    let line = lp_py::strip(line.split('#').next().unwrap_or(""));
    let (head, rule) = line.split_once(" if ").unwrap_or((line, ""));
    let head = lp_py::strip(head);
    let (name, time) = head.split_once(" for ").unwrap_or((head, ""));
    let name = lp_py::strip(name);
    if !is_card(name) {
        return None;
    }
    let seconds = if lp_py::strip(time).is_empty() {
        None
    } else {
        card_time(time)
    };
    Some(SlotCard {
        name: name.into(),
        rule: lp_py::strip(rule).into(),
        seconds,
    })
}

/// slot_cards: the cards a slot module holds, in the order they are asked (render.slot); a
/// line that names no card is left out; `netcard` holds a download card and the network.
pub fn slot_cards(module: &Dict) -> Vec<SlotCard> {
    if module.get("module") == Some(&Value::from("netcard")) {
        return vec![SlotCard::named("download"), SlotCard::named("net")];
    }
    let items = match module.get("items") {
        Some(Value::Str(s)) if !s.is_empty() => s.clone(),
        _ => DEFAULT_CARDS.to_string(),
    };
    let found: Vec<SlotCard> = lp_py::splitlines(&items)
        .into_iter()
        .filter_map(card_line)
        .collect();
    if found.is_empty() {
        lp_py::splitlines(DEFAULT_CARDS)
            .into_iter()
            .map(SlotCard::named)
            .collect()
    } else {
        found
    }
}

/// slot_items: the cards a slot module holds, each with its rule.
pub fn slot_items(module: &Dict) -> Vec<(String, String)> {
    slot_cards(module)
        .into_iter()
        .map(|card| (card.name, card.rule))
        .collect()
}

/// Whether a widget is a card slot that leaves its cards to the person (its `items` empty).
fn open_slot(widget: &Dict) -> bool {
    let is = |k: &str, v: &str| widget.get(k) == Some(&Value::from(v));
    let items = match widget.get("items") {
        Some(v) if v.truthy() => v.str(),
        _ => String::new(),
    };
    is("type", "module") && is("module", "cards") && lp_py::strip(&items).is_empty()
}

/// with_cards: `theme` with the person's cards (lines as a slot's `items` has them), their
/// order and their transition in each card slot that leaves them to the person; a slot that
/// names its own keeps them. None where nothing changes (the theme as it is).
pub fn with_cards(theme: &Theme, cards: &[String], order: &str, transition: &str) -> Option<Theme> {
    let lines: Vec<&str> = cards
        .iter()
        .filter(|line| card_line(line).is_some())
        .map(|line| lp_py::strip(line))
        .collect();
    if lines.is_empty() {
        return None;
    }
    let mut chosen: Vec<(&str, Value)> = vec![("items", lines.join("\n").into())];
    if CARD_ORDERS.contains(&order) {
        chosen.push(("order", order.into()));
    }
    if CARD_TRANSITIONS.contains(&transition) {
        chosen.push(("transition", transition.into()));
    }
    let mut changed = false;
    let widgets = theme
        .widgets
        .iter()
        .map(|widget| {
            let mut widget = widget.clone();
            if open_slot(&widget) {
                for (k, v) in &chosen {
                    widget.insert((*k).into(), v.clone());
                }
                changed = true;
            }
            widget
        })
        .collect();
    changed.then(|| Theme {
        widgets,
        ..theme.clone()
    })
}

/// follows_cards: whether `theme` has a card slot that leaves its cards to the person.
pub fn follows_cards(theme: &Theme) -> bool {
    theme.widgets.iter().any(open_slot)
}
