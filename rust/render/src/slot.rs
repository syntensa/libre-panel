//! Card slots (render/slot.py's Slot): a module that shows one card of several, and the thin
//! frame round it in the colour of the card on show.
//!
//! Which card is on show is a rule: the first of the slot's cards that has something to show
//! (a track that plays or has been paused a short while, Claude Code or Codex at work or
//! waiting, the weather, a download that runs; a line of the slot's `items` may name a rule of
//! its own), and where none has, the first. With `order` `turns` each card that has something
//! to show is on show for its turn, then the next.
//!
//! In a still frame no change rolls, fades or moves, no new track slides in and no line of
//! light ignites: what the frame shows is the card on show and its frame. A renderer drawing
//! one frame after another keeps what the rules remember (a track paused since when, an agent
//! at work until when, whose turn it is since when), as Python's does.

use std::rc::Rc;

use lp_pixels::{Draw, Filter, Image, Mode, ALL_CORNERS};
use lp_py::{Dict, Value};
use lp_theme::modules::slots::{self, TURN_S};

use crate::cover;
use crate::light;
use crate::piece::{Failed, Piece, Result};
use crate::snapshot::{need_holds, Snapshot};
use crate::symbols;
use crate::widget;

/// The frame's colour: this much of the cover's or the weather's over the ground.
const MIXED: f64 = 0.72;
const PLAYBACK: [&str; 2] = ["playback", "music"];
/// The agents whose work keeps the AI card on show (agents.py).
const AGENTS: [&str; 2] = ["claude", "codex"];
/// Seconds the AI card stays on show after the last agent stopped working.
const AI_HOLD: f64 = 45.0;
const WEATHER: [&str; 2] = ["forecast", "weather"];
/// The frame's mask is drawn at this many times its size.
const SUPER: usize = 3;

/// _number: a number a module gives, else `default` (a bool, no number, or one not finite).
fn number_or(value: Option<&Value>, default: f64) -> std::result::Result<f64, Failed> {
    match value {
        Some(Value::Int(n)) => {
            // (math.isfinite of an int converts it first: one past a float's range fails)
            let x = lp_py::num::int_to_float(n).map_err(|e| Failed(e.msg))?;
            Ok(x)
        }
        Some(Value::Float(x)) if x.is_finite() => Ok(*x),
        _ => Ok(default),
    }
}

/// float(module[key]) of a field theme/model.py has made a number.
fn float_of(module: &Dict, key: &str) -> std::result::Result<f64, Failed> {
    let value = module.get(key).cloned().unwrap_or(Value::None);
    lp_py::num::to_float(&value).map_err(|e| Failed(e.msg))
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

/// M._rgb: red, green and blue of a colour a theme holds.
fn rgb(colour: &str) -> std::result::Result<[i64; 3], Failed> {
    let found = lp_pixels::colour::getrgb(colour).map_err(|e| Failed(e.to_string()))?;
    Ok([found[0], found[1], found[2]])
}

/// The roles of the palette a slot's frame takes its colours from, as colors_of has them.
struct Colours {
    bg: [i64; 3],
    text: [i64; 3],
    text2: [i64; 3],
    text3: [i64; 3],
    accent: [i64; 3],
    gpu: [i64; 3],
    net: [i64; 3],
    warn: [i64; 3],
    crit: [i64; 3],
}

impl Colours {
    fn of(palette: &Dict) -> std::result::Result<Colours, Failed> {
        let colors = lp_theme::modules::colors_of(palette).map_err(|e| Failed(e.msg.clone()))?;
        let role = |name: &str| rgb(&colors.get(name).map(Value::str).unwrap_or_default());
        Ok(Colours {
            bg: role("bg")?,
            text: role("text")?,
            text2: role("text2")?,
            text3: role("text3")?,
            accent: role("accent")?,
            gpu: role("gpu")?,
            net: role("net")?,
            warn: role("warn")?,
            crit: role("crit")?,
        })
    }
}

/// One slot module: which card is on show, and its frame.
pub(crate) struct Slot {
    pub id: String,
    /// the module as the theme gives it
    pub module: Dict,
    kinds: Vec<String>,
    rules: Vec<String>,
    turns_order: bool,
    /// seconds each card is on show, in turns
    turns: Vec<f64>,
    /// seconds a paused track stays on show
    hold: f64,
    /// the module's box: [x, y, w, h]
    pub bounds: [i64; 4],
    colours: Colours,
    /// the card on show
    shown: usize,
    /// in turns: when the card on show began its turn
    turn_at: Option<f64>,
    paused_at: Option<f64>,
    paused_for: Option<(Value, Value)>,
    /// when an agent last worked or waited for you
    busy_at: Option<f64>,
    /// the frame's thickness in px (none where the style has no outline)
    pub width: i64,
    /// the frame's colour this frame
    colour: Option<[i64; 3]>,
    /// the frame's mask in strips along the module's edges (_cut), made once
    strips: Option<Vec<(i64, i64, Image)>>,
    /// the frame in the colour it was drawn in last
    frame: Option<([i64; 3], Vec<Rc<Piece>>)>,
}

impl Slot {
    /// Slot(renderer, module, cards, box): what it fails at, Python's renderer fails at as it is
    /// made (a number past a float's range, a palette's colour no colour).
    pub fn new(
        module: &Dict,
        bounds: [i64; 4],
        theme: &lp_theme::Theme,
    ) -> std::result::Result<Slot, Failed> {
        let cards = slots::slot_cards(module);
        // (an order that is none of CARD_ORDERS: "first")
        let turns_order = module.get("order") == Some(&Value::from("turns"));
        let turn = number_or(module.get("turn"), 5.0)? * 60.0;
        let turn = py_min(py_max(turn, TURN_S.0), TURN_S.1);
        // (change and slide: what a moving frame takes, read as Python reads them)
        py_max(0.0, float_of(module, "change")?);
        py_max(0.0, float_of(module, "slide")?);
        let hold = py_max(0.0, float_of(module, "hold")?) * 60.0;
        let colours = Colours::of(&theme.palette)?;
        let mut slot = Slot {
            id: widget::text(module, "id").to_string(),
            module: module.clone(),
            kinds: cards.iter().map(|c| c.name.clone()).collect(),
            rules: cards.iter().map(|c| c.rule.clone()).collect(),
            turns_order,
            turns: cards
                .iter()
                .map(|c| match c.seconds {
                    Some(s) if s != 0.0 => s,
                    _ => turn,
                })
                .collect(),
            hold,
            bounds,
            colours,
            shown: 0,
            turn_at: None,
            paused_at: None,
            paused_for: None,
            busy_at: None,
            width: 0,
            colour: None,
            strips: None,
            frame: None,
        };
        slot.width = slot.line_width(theme);
        Ok(slot)
    }

    /// The style as the theme has it, over the defaults.
    fn style(theme: &lp_theme::Theme) -> Dict {
        let mut style = lp_theme::model::style_defaults();
        if let Some(own) = &theme.style {
            for (k, v) in own {
                style.insert(k.clone(), v.clone());
            }
        }
        style
    }

    /// _line_width: the frame's thickness in px; none where the style has no outline.
    fn line_width(&self, theme: &lp_theme::Theme) -> i64 {
        let style = Slot::style(theme);
        let card = style.get("card").map(Value::str).unwrap_or_default();
        if self.module.get("card") == Some(&Value::from("off")) || card == "none" || card == "flat"
        {
            return 0;
        }
        let u = Slot::frame_u(theme, self.bounds[2], self.bounds[3]);
        (lp_py::round(u / 160.0) as i64).max(1)
    }

    /// _frame_u: what the frame's corners and thickness scale with, as a module's card has it:
    /// the module's size, up to half the panel's height.
    fn frame_u(theme: &lp_theme::Theme, w: i64, h: i64) -> f64 {
        let panel = theme.width.min(theme.height) as f64 / 2.0;
        py_min(py_min(w as f64, h as f64), panel)
    }

    /// _playing: a track that plays, or that has been paused for less than `hold` seconds.
    fn playing(&mut self, snapshot: &Snapshot, now: f64) -> bool {
        let title = snapshot.value("media.title");
        if !title.truthy() {
            self.paused_at = None;
            return false;
        }
        let plays = match snapshot.value("media.playing") {
            Value::Bool(on) => on,
            Value::Int(n) => n != num_bigint::BigInt::from(0),
            Value::Float(x) => x != 0.0,
            _ => false,
        };
        if plays {
            self.paused_at = None;
            return true;
        }
        let track = (title, snapshot.value("media.artist"));
        let same = self
            .paused_for
            .as_ref()
            .is_some_and(|(t, a)| *t == track.0 && *a == track.1);
        let at = match self.paused_at {
            Some(at) if same => at,
            _ => {
                self.paused_for = Some(track);
                self.paused_at = Some(now);
                now
            }
        };
        now - at < self.hold
    }

    /// _agents: Claude Code or Codex at work or waiting for you (ai.<agent>.busy 1 or 2), or so
    /// less than AI_HOLD seconds ago.
    fn agents(&mut self, snapshot: &Snapshot, now: f64) -> bool {
        for agent in AGENTS {
            let busy = match snapshot.value(&format!("ai.{agent}.busy")) {
                v @ (Value::Int(_) | Value::Float(_)) => lp_py::value::compare(&v, &Value::int(1))
                    .is_some_and(|o| o != std::cmp::Ordering::Less),
                _ => false,
            };
            if busy {
                self.busy_at = Some(now);
                return true;
            }
        }
        self.busy_at.is_some_and(|at| now - at < AI_HOLD)
    }

    /// _has: whether card `i` has something to show; Unported where Python's frame fails at
    /// it, or what it reads is beyond what Rust reads as Python does.
    fn has(
        &mut self,
        i: usize,
        snapshot: &Snapshot,
        now: f64,
    ) -> std::result::Result<bool, String> {
        let rule = self.rules[i].clone();
        if !rule.is_empty() {
            return Ok(rule
                .split(',')
                .filter(|need| !lp_py::strip(need).is_empty())
                .all(|need| need_holds(lp_py::strip(need), snapshot)));
        }
        let kind = self.kinds[i].as_str();
        if PLAYBACK.contains(&kind) {
            return Ok(self.playing(snapshot, now));
        }
        if WEATHER.contains(&kind) {
            return Ok(snapshot.value("weather.temperature") != Value::None);
        }
        if kind == "ai" {
            return Ok(self.agents(snapshot, now));
        }
        if kind == "download" {
            return downloads(snapshot);
        }
        Ok(true)
    }

    /// wants: the card that has something to show: the first, else the first of all; in turns
    /// the one on show until its turn is over, then the next that has something to show.
    fn wants(&mut self, snapshot: &Snapshot, now: f64) -> std::result::Result<usize, String> {
        if self.turns_order {
            let mut has = Vec::with_capacity(self.kinds.len());
            for i in 0..self.kinds.len() {
                has.push(self.has(i, snapshot, now)?);
            }
            return Ok(self.turn(&has, now));
        }
        for i in 0..self.kinds.len() {
            if self.has(i, snapshot, now)? {
                return Ok(i);
            }
        }
        Ok(0)
    }

    /// _turn: in turns, the card on show while it has something to show and its turn lasts,
    /// else the next that has (itself again where no other has), else the first of all.
    fn turn(&self, has: &[bool], now: f64) -> usize {
        if !has.contains(&true) {
            return 0;
        }
        let at = self.shown;
        let Some(began) = self.turn_at else {
            // (the first frame: the first that has something to show)
            return has.iter().position(|&h| h).unwrap_or(0);
        };
        if has[at] && now - began < self.turns[at] {
            return at;
        }
        let n = has.len();
        (at + 1..at + n + 1)
            .map(|i| i % n)
            .find(|&i| has[i])
            .unwrap_or(0)
    }

    /// begin, in a still frame: the card for this frame (no change is on its way, none
    /// begins), and the frame's colour.
    pub fn begin(&mut self, snapshot: &Snapshot, now: f64) -> std::result::Result<(), String> {
        let want = self.wants(snapshot, now)?;
        if want != self.shown {
            self.shown = want;
            self.turn_at = Some(now);
        } else if self.turn_at.is_none()
            || (self.turns_order
                && self
                    .turn_at
                    .is_some_and(|at| now - at >= self.turns[self.shown]))
        {
            // (the first frame, or its turn again: no other has something to show)
            self.turn_at = Some(now);
        }
        Ok(())
    }

    /// shows: whether part of `card` goes on the panel (in a still frame: the card on show).
    pub fn shows(&self, card: i64) -> bool {
        usize::try_from(card).is_ok_and(|c| c == self.shown)
    }

    /// _target: the colour of the card on show, over the ground; `own` the module's own colour
    /// as the renderer reads it (None: it has none).
    pub fn target(&self, own: Option<[i64; 3]>, snapshot: &Snapshot) -> [i64; 3] {
        let c = &self.colours;
        let ground = c.bg;
        let kind = self.kinds[self.shown].as_str();
        if let Some(own) = own {
            return light::mix(own, ground, MIXED);
        }
        if PLAYBACK.contains(&kind) {
            if let Some(found) = snapshot.images.get("media.cover").and_then(cover::dominant) {
                return light::mix(found.map(i64::from), ground, MIXED);
            }
            return light::mix(c.text3, ground, 0.5);
        }
        if WEATHER.contains(&kind) {
            return light::mix(self.weather(snapshot), ground, MIXED);
        }
        if ["download", "net", "network"].contains(&kind) {
            return light::mix(c.net, ground, MIXED);
        }
        light::mix(c.accent, ground, MIXED)
    }

    /// _weather: the weather's colour as the Studio sets it: the sun's (or the clouds' by
    /// night), rain's blue, snow's white, a storm's red, grey for overcast and fog.
    fn weather(&self, snapshot: &Snapshot) -> [i64; 3] {
        let c = &self.colours;
        let kind = symbols::kind_of(&snapshot.value("weather.code"));
        let night = symbols::night_at(snapshot, "weather.code");
        match kind {
            "clear" | "mostly" | "partly" => {
                if night {
                    c.text2
                } else {
                    c.warn
                }
            }
            "rain" | "drizzle" => c.gpu,
            "snow" => c.text,
            "storm" => c.crit,
            _ => c.text3,
        }
    }

    /// _accent in a still frame: the frame's colour is the card's at once.
    pub fn set_colour(&mut self, colour: [i64; 3]) {
        self.colour = Some(colour);
    }

    /// The card on show, the frame's colour and its thickness (for the checks).
    pub fn state(&self) -> (String, Option<[i64; 3]>, i64) {
        (self.kinds[self.shown].clone(), self.colour, self.width)
    }

    /// _outline: the frame's mask, drawn three times its size and averaged down.
    fn outline(&self, theme: &lp_theme::Theme, w: i64, h: i64) -> Result<Image> {
        let failed = |e: lp_pixels::Error| Failed(e.to_string());
        let style = Slot::style(theme);
        let radius = style.get("radius").map_or(0.0, |v| match v {
            Value::Int(_) | Value::Float(_) | Value::Bool(_) => {
                lp_py::num::to_float(v).unwrap_or(0.0)
            }
            _ => 0.0,
        });
        // (as the module's card has it)
        let radius = lp_py::round(radius * Slot::frame_u(theme, w, h) / 200.0);
        if w < 0 || h < 0 {
            return Err(Failed("Width and height must be >= 0".into()));
        }
        let (bw, bh) = (w as usize * SUPER, h as usize * SUPER);
        let mut big = Image::blank(Mode::L, bw, bh);
        let s = SUPER as f64;
        Draw::new(&mut big)
            .rounded_rectangle(
                (0.0, 0.0, bw as f64 - 1.0, bh as f64 - 1.0),
                radius * s,
                None,
                Some([255, 0, 0, 0]),
                (self.width * SUPER as i64) as i32,
                ALL_CORNERS,
            )
            .map_err(failed)?;
        // (an area average: no ringing)
        big.resize(w as usize, h as usize, Filter::Box, None, None)
            .map_err(failed)
    }

    /// _cut: the frame's mask cut in strips along the module's edges that hold all of its line,
    /// its corners with it (what lies between them is transparent); whole where the module is
    /// turned or the mask is no such frame.
    fn cut(&self, mask: Image) -> Vec<(i64, i64, Image)> {
        let (w, h) = mask.size();
        let angle = match self.module.get("rotate") {
            Some(v) if v.truthy() => lp_py::num::to_float(v).unwrap_or(f64::NAN),
            _ => 0.0,
        };
        let turned = lp_py::num::float_div_mod(angle, 360.0).1 != 0.0;
        let a = mask.as_bytes();
        let middle = &a[(h / 2) * w..(h / 2) * w + w];
        let lit: Vec<usize> = (0..w).filter(|&x| middle[x] != 0).collect();
        let whole = |mask: Image| vec![(0, 0, mask)];
        if turned || w < 8 || h < 8 || lit.is_empty() || (0..h).all(|y| a[y * w + w / 2] == 0) {
            return whole(mask);
        }
        let left = lit.iter().filter(|&&x| x < w / 2).max();
        let right = lit.iter().filter(|&&x| x >= w / 2).min();
        let side = 1 + left.map_or(0, |&x| x + 1).max(right.map_or(0, |&x| w - x));
        if 4 * side >= w {
            return whole(mask);
        }
        // rows with something between the sides
        let inner: Vec<bool> = (0..h)
            .map(|y| a[y * w + side..y * w + w - side].iter().any(|&v| v != 0))
            .collect();
        let top = (0..h / 2).filter(|&y| inner[y]).max().map_or(0, |y| y + 1);
        let bottom = (h / 2..h).find(|&y| inner[y]).unwrap_or(h);
        if 2 * (top + h - bottom) >= h {
            return whole(mask);
        }
        let (wi, hi) = (w as i64, h as i64);
        let (t, b, s) = (top as i64, bottom as i64, side as i64);
        [
            (0, 0, mask.crop(0, 0, wi, t)),
            (0, b, mask.crop(0, b, wi, hi)),
            (0, t, mask.crop(0, t, s, b)),
            (wi - s, t, mask.crop(wi - s, t, wi, b)),
        ]
        .into_iter()
        .filter(|(_, _, m)| m.width() > 0 && m.height() > 0 && m.getbbox(true).is_some())
        .collect()
    }

    /// _frame: the frame, a thin flat line round the module in `colour`, in strips along its
    /// edges (made once; coloured anew where its colour changes).
    fn frame(&mut self, theme: &lp_theme::Theme, colour: [i64; 3]) -> Result<Vec<Rc<Piece>>> {
        if let Some((kept, pieces)) = &self.frame {
            if *kept == colour {
                return Ok(pieces.clone());
            }
        }
        let [x, y, w, h] = self.bounds;
        if self.strips.is_none() {
            let mask = self.outline(theme, w, h)?;
            self.strips = Some(self.cut(mask));
        }
        let rgba = [colour[0] as u8, colour[1] as u8, colour[2] as u8, 255];
        let mut pieces = Vec::new();
        for (sx, sy, mask) in self.strips.as_ref().expect("cut above") {
            let mut layer = Image::new(Mode::RGBA, mask.width(), mask.height(), &rgba);
            layer.putalpha(mask).map_err(|e| Failed(e.to_string()))?;
            let mut piece = Piece::new(layer, x + sx, y + sy);
            piece.hit = Some([x, y, w, h]);
            pieces.push(Rc::new(piece));
        }
        self.frame = Some((colour, pieces.clone()));
        Ok(pieces)
    }

    /// overlay: the frame over the module's pieces, right after the last of them; none where
    /// none of them is drawn.
    pub fn overlay(
        &mut self,
        theme: &lp_theme::Theme,
        pieces: &mut Vec<(String, Rc<Piece>)>,
    ) -> Result<()> {
        let prefix = format!("{}/", self.id);
        let Some(at) = pieces.iter().rposition(|(id, _)| id.starts_with(&prefix)) else {
            return Ok(());
        };
        let Some(colour) = self.colour else {
            return Ok(());
        };
        if self.width == 0 {
            return Ok(());
        }
        let strips = self.frame(theme, colour)?;
        let extra = strips.into_iter().enumerate().map(|(i, strip)| {
            let n = if i == 0 { String::new() } else { i.to_string() };
            (format!("{prefix}\0frame{n}"), strip)
        });
        pieces.splice(at + 1..at + 1, extra);
        Ok(())
    }
}

/// Whether the downloads' state is a dict (snapshot.state("downloads"): the text a service
/// published, read as json.loads reads it); Unported where Python's frame fails at it
/// (RecursionError), or reads what Rust's str cannot hold, or nests it deeper than Python's
/// stack is known to take here.
fn downloads(snapshot: &Snapshot) -> std::result::Result<bool, String> {
    Ok(matches!(snapshot.state("downloads")?, Some(Value::Dict(_))))
}
