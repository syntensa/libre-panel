//! How one screen gives way to the next (app.py's _occasion, _switch_occasion, _begin and
//! _blend; render/overlays.py's handover, occasion_params and switch_policy): the occasion of
//! a switch, the transition config.toml's `[transitions]` has for it, and the frames it shows.

use indexmap::IndexMap;
use lp_config::{Config, TRANSITION_OCCASIONS};
use lp_pixels::Image;
use lp_py::{Dict, Value};
use lp_render::overlays::ToastLayer;
use lp_render::transitions::Transition;
use lp_render::{Moment, Reading, Renderer};
use lp_services::host::{PluginHost, Transition as Asked};

use crate::modes;

/// times the durations, by `[transitions] speed`
fn speed(name: &str) -> f64 {
    match name {
        "fast" => 0.6,
        "slow" => 1.6,
        _ => 1.0,
    }
}

/// _occasion: where the theme on show comes from, as an occasion of `[transitions]`: a
/// service's request, a page or mode (a game, music, the PC at rest), the screens in turn,
/// else the theme or scene chosen ("manual").
pub fn occasion(config: &Config, host: &PluginHost, in_sequence: bool) -> &'static str {
    let known = |name: &str| TRANSITION_OCCASIONS.iter().copied().find(|o| *o == name);
    if host.theme().is_some() {
        return match host.theme_owner().as_deref() {
            Some("autopilot") => "autopilot",
            Some("memory") => "report",
            _ => "manual",
        };
    }
    let mode = host.mode();
    if modes::page_theme(config, mode.as_deref()).is_some() {
        return mode.as_deref().and_then(known).unwrap_or("manual");
    }
    if in_sequence {
        return "sequence";
    }
    "manual"
}

/// _occasion for a panel beside the first, which has no services: the first panel's `mode`
/// where config.toml gives this panel a theme for it, else the screens in turn or "manual".
pub fn beside_occasion(config: &Config, mode: &str, in_sequence: bool) -> &'static str {
    let own = config.modes.get(mode).and_then(|own| own.theme.as_deref());
    if own.is_some_and(|theme| !theme.is_empty()) {
        let known = TRANSITION_OCCASIONS.iter().copied().find(|o| *o == mode);
        return known.unwrap_or("manual");
    }
    if in_sequence {
        return "sequence";
    }
    "manual"
}

/// _switch_occasion: the occasion of a switch and whether it leaves it: one into a game,
/// music or the like is that one's; one back from it is that one's too, leaving it.
pub fn switch_occasion(before: &'static str, after: &'static str) -> (&'static str, bool) {
    let special = |occasion: &str| !matches!(occasion, "manual" | "sequence");
    if special(after) || !special(before) {
        (after, false)
    } else {
        (before, true)
    }
}

/// occasion_params: what a transition chosen for an occasion says (the entrance's title, text
/// and picture): music with its cover and title, the PC at rest with the time, any other
/// switch with the name of the screen that comes.
fn occasion_params(
    name: &str,
    occasion: &str,
    leaving: bool,
    readings: Option<&IndexMap<String, Reading>>,
    theme_name: &str,
    now: &Moment,
) -> Dict {
    let mut params = Dict::new();
    if name != "entrance" {
        return params;
    }
    let mut put = |key: &str, value: &str| {
        params.insert(key.to_string(), Value::Str(value.to_string()));
    };
    let text = |key: &str| {
        let value = readings
            .and_then(|readings| readings.get(key))
            .map(|r| &r.value);
        value.filter(|value| value.truthy()).map(Value::str)
    };
    match (occasion, leaving) {
        ("media", true) => put("kind", "media-end"),
        ("media", false) => {
            let parts: Vec<String> = [text("media.artist"), text("media.title")]
                .into_iter()
                .flatten()
                .collect();
            put("kind", "media");
            put("title", &parts.join(" – "));
            put("logo", "media.cover");
        }
        ("idle", true) => put("kind", "idle-end"),
        ("idle", false) => {
            put("kind", "idle");
            put("title", &format!("{:02}:{:02}", now.hour, now.minute));
        }
        ("game", true) => put("kind", "end"),
        ("game", false) => put("kind", "start"),
        _ => put("title", theme_name),
    }
    params
}

/// handover: the transition for a switch, from config.toml's `[transitions]`: the occasion's
/// own (what the service asked for, `asked`, where it is the same transition: it knows the
/// game's logo), else what the service asked for, else the default; "random" picks one of its
/// list; the speed stretches it. `leaving`: the switch ends the occasion (the game is over).
#[allow(clippy::too_many_arguments)]
pub fn handover(
    config: &Config,
    occasion: &str,
    leaving: bool,
    asked: Option<Asked>,
    renderer: &Renderer,
    readings: Option<&IndexMap<String, Reading>>,
    theme_name: &str,
    now: &Moment,
    pick: usize,
) -> Option<Transition> {
    let chosen = &config.transitions;
    let spec_of = |name: &str| {
        let params = occasion_params(name, occasion, leaving, readings, theme_name, now);
        (name.to_string(), params)
    };
    let own = chosen.occasions.get(occasion).map_or("", String::as_str);
    let default = match config.transition.as_str() {
        "" => chosen.default.as_str(),
        named => named,
    };
    let asked_name = asked.as_ref().map(|asked| asked.name.as_str());
    let mut spec = if !own.is_empty() && Some(own) != asked_name {
        spec_of(own)
    } else {
        match asked {
            Some(asked) if !asked.name.is_empty() => (asked.name, asked.params.unwrap_or_default()),
            _ => spec_of(default),
        }
    };
    if spec.0 == "random" {
        let list = &chosen.random;
        spec = spec_of(
            list.get(pick % list.len().max(1))
                .map_or("fade", String::as_str),
        );
    }
    let mut kind = Transition::new(&spec.0, &spec.1, renderer)?;
    if kind.duration != 0.0 {
        kind.duration *= speed(&chosen.speed);
    }
    Some(kind)
}

/// A transition under way: what it is, the frame it leaves (and which one that is), and when
/// it began.
pub struct Blend {
    pub kind: Transition,
    old: Image,
    key: u64,
    began: f64,
}

impl Blend {
    /// _begin: a transition starts, from the last frame (with its toast, if the transition
    /// asks for that); a toast it must not cover steps aside.
    pub fn begin(
        kind: Option<Transition>,
        last_frame: Option<&Image>,
        shown_frame: Option<&Image>,
        now: f64,
        key: u64,
        toasts: Option<&mut ToastLayer>,
    ) -> Option<Blend> {
        let (kind, last) = (kind?, last_frame?);
        let old = match (kind.from_shown, shown_frame) {
            (true, Some(shown)) => shown,
            _ => last,
        };
        if kind.toasts == "restart" {
            if let Some(toasts) = toasts {
                toasts.set_aside();
            }
        }
        Some(Blend {
            kind,
            old: old.clone(),
            key,
            began: now,
        })
    }

    /// switch_policy: what a switch to `theme` does while it plays ("wait", "follow" or
    /// "restart"), and a plugin transition's failure to say, to be said once.
    pub fn policy(
        &mut self,
        theme: &str,
        mode: Option<&str>,
        began_in: Option<&str>,
    ) -> (&'static str, Option<String>) {
        self.kind.on_switch(theme, mode, mode != began_in)
    }

    /// _blend: the frame the transition shows now, and whether it goes on; a broken
    /// transition must not blank the panel (its failure is given back to be said).
    pub fn frame(
        &mut self,
        frame: Image,
        now: f64,
        renderer: &Renderer,
    ) -> (Image, bool, Option<String>) {
        // (one that begins later shows the old frame until then)
        let progress = match self.kind.duration {
            0.0 => 1.0,
            duration => ((now - self.began) / duration).max(0.0),
        };
        if progress >= 1.0 || self.old.size() != frame.size() {
            return (frame, false, None);
        }
        match self
            .kind
            .frame(&self.old, self.key, &frame, progress, renderer)
        {
            Ok(out) if out.size() == frame.size() => (out, true, None),
            Ok(out) => {
                let (w, h) = out.size();
                let (fw, fh) = frame.size();
                (frame, false, Some(format!("drew {w}x{h}, not {fw}x{fh}")))
            }
            Err(why) => (frame, false, Some(why.0)),
        }
    }
}
