//! Card slots (render/slot.py's Slot): a module that shows one card of several, and the thin
//! frame round it in the colour of the card on show.
//!
//! Which card is on show is a rule: the first of the slot's cards that has something to show
//! (a track that plays or has been paused a short while, Claude Code or Codex at work or
//! waiting, the weather, a download that runs; a line of the slot's `items` may name a rule of
//! its own), and where none has, the first. With `order` `turns` each card that has something
//! to show is on show for its turn, then the next.
//!
//! In a moving frame a change from one card to the next takes the module's `change` seconds of
//! the frames' planned time, as its `transition` has it: a line of light ignites at the top
//! edge, the card on show rolls up into it and the new one rolls down out of it ("light"); or
//! the old one fades out as the new one fades in, the new one pushes the old one out to the
//! left or upwards, a beam of light wipes it away from the left, or the new one is there at
//! once. The old card shows what it showed last, cut, faded or moved as the change has it. A
//! new track on the playback card on show slides in from the right as the old one moves out
//! (`slide` seconds). The frame's colour follows the card's over half a second.
//!
//! In a still frame no change rolls, fades or moves, no new track slides in and no line of
//! light ignites: what the frame shows is the card on show and its frame. A renderer drawing
//! one frame after another keeps what the rules remember (a track paused since when, an agent
//! at work until when, whose turn it is since when), as Python's does.

use std::rc::Rc;

use indexmap::IndexMap;
use lp_pixels::{chops, Draw, Filter, Image, Mode, Transpose, ALL_CORNERS};
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
/// ROLL_UP and ROLL_DOWN: the card on show has rolled up into the top edge by this share of a
/// change; from the other share on the new one rolls down out of it.
const ROLL_UP: f64 = 0.42;
const ROLL_DOWN: f64 = 0.36;
/// GLOW: how bright the line of light gets.
const GLOW: f64 = 0.9;
/// FADE: seconds the frame takes to change its colour.
const FADE: f64 = 0.55;
/// GAP: px between the old card or track and the new one in a slide, on a bar 480 px tall.
const GAP: f64 = 48.0;
/// FADE_OUT and FADE_IN: in a fade the old card has gone by this share of the change, and the
/// new one comes from the other share on.
const FADE_OUT: f64 = 0.6;
const FADE_IN: f64 = 0.4;
/// CARD_TRANSITIONS: the ways from one card to the next a slot may name.
const TRANSITIONS: [&str; 7] = ["light", "fade", "slide", "rise", "wipe", "cut", "random"];

/// How a part of a card goes on the panel (Slot.shows): drawn now, or what the card that goes
/// showed last.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum OnShow {
    Live,
    Frozen,
}

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.to_string())
}

/// _shade: `part` with its first row at `first` of its strength and its last at `last` (light,
/// `additive`: its colour is what is added, all its bands dimmed).
fn shade(part: &Image, first: f64, last: f64, additive: bool) -> Result<Image> {
    let n = part.height();
    let mut values = vec![255u8; n];
    if n == 1 {
        // (both cuts in one row: what is left of it)
        values[0] = lp_py::round(255.0 * py_max(0.0, first + last - 1.0)) as u8;
    } else if n > 1 {
        values[0] = lp_py::round(255.0 * first) as u8;
        values[n - 1] = lp_py::round(255.0 * last) as u8;
    }
    let mask = Image::from_bytes(Mode::L, 1, n, values)
        .and_then(|m| m.resize(part.width(), n, Filter::Nearest, None, None))
        .map_err(failed)?;
    let part = if part.mode() == Mode::RGBA {
        part.clone()
    } else {
        part.convert(Mode::RGBA).map_err(failed)?
    };
    if additive {
        let bands = part.split();
        let dimmed = bands
            .iter()
            .map(|band| chops::multiply(band, &mask))
            .collect::<lp_pixels::Result<Vec<Image>>>()
            .map_err(failed)?;
        let refs: Vec<&Image> = dimmed.iter().collect();
        return lp_pixels::merge(Mode::RGBA, &refs).map_err(failed);
    }
    let mut out = part.clone();
    let alpha = chops::multiply(&part.getchannel(3).map_err(failed)?, &mask).map_err(failed)?;
    out.putalpha(&alpha).map_err(failed)?;
    Ok(out)
}

/// A piece that shows nothing but its glow: Python's Piece(Image.new("RGBA", (1, 1)), glow.x,
/// glow.y, piece.box, light=glow).
fn glow_alone(piece: &Piece, glow: Piece) -> Piece {
    let mut alone = Piece::new(Image::blank(Mode::RGBA, 1, 1), glow.x, glow.y);
    alone.hit = piece.hit;
    alone.light = Some(Box::new(glow));
    alone
}

/// window: `piece` cut to the panel rows from `top` to `bottom`; a row that is cut shows its
/// share, so an edge that moves slowly glides instead of stepping. None: nothing of it shows.
pub(crate) fn window(piece: Option<&Piece>, top: f64, bottom: f64) -> Result<Option<Piece>> {
    let Some(piece) = piece else {
        return Ok(None);
    };
    if bottom <= top {
        return Ok(None);
    }
    let (layer, y0) = (&piece.layer, piece.y as f64);
    let a = py_max(top, y0);
    let b = py_min(bottom, y0 + layer.height() as f64);
    let glow = match &piece.light {
        Some(light) => window(Some(light), top, bottom)?,
        None => None,
    };
    if b <= a {
        return Ok(glow.map(|glow| glow_alone(piece, glow)));
    }
    let first = a.floor() as i64 - piece.y;
    let last = b.ceil() as i64 - piece.y;
    let mut part = layer.crop(0, first, layer.width() as i64, last);
    let share_first = 1.0 - (a - (y0 + first as f64));
    let share_last = 1.0 - ((y0 + last as f64) - b);
    if share_first < 0.999 || share_last < 0.999 {
        part = shade(&part, share_first, share_last, piece.add)?;
    }
    let mut out = piece.clone();
    (out.layer, out.y, out.light) = (part, piece.y + first, glow.map(Box::new));
    Ok(Some(out))
}

/// slid: `piece` moved `dx` px to the right and cut to the panel columns from `left` to
/// `right`. None: nothing of it shows.
fn slid(piece: Option<&Piece>, dx: i64, left: i64, right: i64) -> Result<Option<Piece>> {
    let Some(piece) = piece else {
        return Ok(None);
    };
    let glow = match &piece.light {
        Some(light) => slid(Some(light), dx, left, right)?,
        None => None,
    };
    let (layer, x0) = (&piece.layer, piece.x + dx);
    let (a, b) = (left.max(x0), right.min(x0 + layer.width() as i64));
    if b <= a {
        return Ok(glow.map(|glow| glow_alone(piece, glow)));
    }
    let mut out = piece.clone();
    if (a, b) != (x0, x0 + layer.width() as i64) {
        out.layer = layer.crop(a - x0, 0, b - x0, layer.height() as i64);
    }
    (out.x, out.light) = (a, glow.map(Box::new));
    Ok(Some(out))
}

/// moved: `piece` moved `dx` px to the right and `dy` px down, its glow with it.
fn moved(piece: Option<&Piece>, dx: i64, dy: i64) -> Option<Piece> {
    let piece = piece?;
    let glow = piece.light.as_deref().and_then(|l| moved(Some(l), dx, dy));
    let mut out = piece.clone();
    (out.x, out.y, out.light) = (piece.x + dx, piece.y + dy, glow.map(Box::new));
    Some(out)
}

/// faded: `piece` at `share` of its strength (light dimmed in its colour, anything else in its
/// alpha), its glow with it. None: nothing of it shows.
fn faded(piece: Option<&Piece>, share: f64) -> Result<Option<Piece>> {
    let Some(piece) = piece else {
        return Ok(None);
    };
    if share <= 0.004 {
        return Ok(None);
    }
    if share >= 0.996 {
        return Ok(Some(piece.clone()));
    }
    let glow = match &piece.light {
        Some(light) => faded(Some(light), share)?,
        None => None,
    };
    let dim = |v: u8| f64::from(v) * share;
    let layer = if piece.layer.mode() == Mode::RGBA {
        piece.layer.clone()
    } else {
        piece.layer.convert(Mode::RGBA).map_err(failed)?
    };
    let layer = if piece.add {
        layer.point_with(dim).map_err(failed)?
    } else {
        let mut layer = layer;
        let alpha = layer
            .getchannel(3)
            .map_err(failed)?
            .point_with(dim)
            .map_err(failed)?;
        layer.putalpha(&alpha).map_err(failed)?;
        layer
    };
    let mut out = piece.clone();
    (out.layer, out.light) = (layer, glow.map(Box::new));
    Ok(Some(out))
}

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
    /// how it goes from one card to the next (one of TRANSITIONS), and how the change on its
    /// way moves
    transition: String,
    effect: String,
    /// seconds a change takes, and a new track to slide in
    change: f64,
    slide: f64,
    /// the panel's size (what a slide's gap and the line of light scale with)
    panel: (i64, i64),
    /// the card that rolls up, and the planned time its change began
    leaving: Option<usize>,
    since: f64,
    /// a card drawn for the warm-up, whatever the rules say (pinned)
    pub(crate) pinned: Option<usize>,
    /// what the leaving card showed last, by part; what the card on show shows
    frozen: IndexMap<String, Rc<Piece>>,
    last: IndexMap<String, Rc<Piece>>,
    started: bool,
    /// in a change: how far each card has rolled, the light's strength, in a fade the old
    /// card's and the new one's strength, in a slide or a rise the old one's and the new
    /// one's px, in a wipe the column the new card has reached
    rows: Option<(f64, f64)>,
    line: f64,
    alpha: Option<(f64, f64)>,
    push: Option<(i64, i64)>,
    edge: Option<f64>,
    /// the track the playback card on show shows; a slide's planned start and what the old
    /// track showed; in a slide the old track's and the new one's x
    track: Option<(Value, Value)>,
    sliding: Option<(f64, IndexMap<String, Rc<Piece>>)>,
    shift: Option<(i64, i64)>,
    /// the frame's colour: the one a change of it starts from and goes to, and when
    from: Option<[i64; 3]>,
    to: Option<[i64; 3]>,
    colour_at: f64,
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
        let transition = match module.get("transition") {
            Some(Value::Str(t)) if TRANSITIONS.contains(&t.as_str()) => t.clone(),
            _ => "light".to_string(),
        };
        let change = py_max(0.0, float_of(module, "change")?);
        let slide = py_max(0.0, float_of(module, "slide")?);
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
            transition,
            effect: "light".to_string(),
            change,
            slide,
            panel: (theme.width, theme.height),
            leaving: None,
            pinned: None,
            since: 0.0,
            frozen: IndexMap::new(),
            last: IndexMap::new(),
            started: false,
            rows: None,
            line: 0.0,
            alpha: None,
            push: None,
            edge: None,
            track: None,
            sliding: None,
            shift: None,
            from: None,
            to: None,
            colour_at: 0.0,
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

    /// _still: no change on its way: nothing rolls, fades or moves.
    fn still(&mut self) {
        (self.rows, self.line, self.alpha, self.push, self.edge) = (None, 0.0, None, None, None);
    }

    /// _inner: what rolls: the module's box inside its frame.
    fn inner(&self) -> [i64; 4] {
        let [x, y, w, h] = self.bounds;
        let f = self.width;
        [x + f, y + f, w - 2 * f, h - 2 * f]
    }

    /// What a slide's gap and the line of light scale with: the panel's height of 480 px.
    fn unit(&self) -> f64 {
        self.panel.0.min(self.panel.1) as f64 / 480.0
    }

    /// begin: the card for this frame, and where a change has got to; in a moving frame
    /// (`animate`) a change of cards begins (where the slot has been drawn before and its
    /// change takes a while), and a new track slides in. Whether it moves on. Unported where
    /// Python's frame fails at a rule, and where the change is chosen at random (Python's
    /// random module chooses it: no other frame can show the same).
    pub fn begin(
        &mut self,
        snapshot: &Snapshot,
        now: f64,
        animate: bool,
    ) -> std::result::Result<bool, String> {
        if let Some(card) = self.pinned {
            // (the warm-up draws each card once, without a change)
            (self.shown, self.leaving) = (card, None);
            self.still();
            (self.track, self.sliding, self.shift) = (None, None, None);
            return Ok(false);
        }
        if self.leaving.is_some() && now - self.since >= self.change {
            (self.leaving, self.frozen) = (None, IndexMap::new());
        }
        let want = self.wants(snapshot, now)?;
        if self.leaving.is_none() {
            // (a change on its way finishes first)
            if want != self.shown {
                let moves =
                    self.started && animate && self.change > 0.0 && self.transition != "cut";
                if moves {
                    if self.transition == "random" {
                        return Err("card slots whose change is chosen at random".into());
                    }
                    self.leaving = Some(self.shown);
                    self.since = now;
                    self.frozen = std::mem::take(&mut self.last);
                    self.effect = self.transition.clone();
                }
                self.last = IndexMap::new();
                (self.shown, self.turn_at) = (want, Some(now));
            } else if self.turn_at.is_none()
                || (self.turns_order
                    && self
                        .turn_at
                        .is_some_and(|at| now - at >= self.turns[self.shown]))
            {
                // (the first frame, or its turn again: no other has something to show)
                self.turn_at = Some(now);
            }
        }
        self.started = true;
        self.still();
        let mut moving = false;
        if self.leaving.is_some() {
            moving = true;
            self.move_to(light::clamp((now - self.since) / self.change));
        }
        moving |= self.slide_track(snapshot, now, animate);
        Ok(moving)
    }

    /// settle: forget what the warm-up showed: the first frame decides the card, with no
    /// change.
    pub fn settle(&mut self) {
        self.pinned = None;
        (self.shown, self.started, self.leaving, self.turn_at) = (0, false, None, None);
        (self.last, self.frozen) = (IndexMap::new(), IndexMap::new());
        self.still();
        (self.track, self.sliding, self.shift) = (None, None, None);
        (self.from, self.to, self.colour) = (None, None, None);
    }

    /// How many cards the slot shows in turn.
    pub fn cards(&self) -> usize {
        self.kinds.len()
    }

    /// _move: where the change has got to at `p` (0 to 1) of its time.
    fn move_to(&mut self, p: f64) {
        let [x, _, w, h] = self.inner();
        let (w, h) = (w as f64, h as f64);
        match self.effect.as_str() {
            "fade" => {
                self.alpha = Some((
                    1.0 - light::smooth(light::clamp(p / FADE_OUT)),
                    light::smooth(light::clamp((p - FADE_IN) / (1.0 - FADE_IN))),
                ));
            }
            "slide" | "rise" => {
                // (the new card pushes the old one out)
                let across = if self.effect == "slide" { w } else { h };
                let reach = lp_py::round(across) as i64 + lp_py::round(GAP * self.unit()) as i64;
                let away = lp_py::round(reach as f64 * light::smooth(p)) as i64;
                self.push = Some((-away, reach - away));
            }
            "wipe" => {
                // (a beam of light sweeps across, the new card behind it)
                self.edge = Some(x as f64 + w * light::smooth(p));
                self.line = GLOW * (std::f64::consts::PI * p).sin();
            }
            _ => {
                // (light: the old card rolls up into a line of light, the new one rolls down)
                let up = if p < ROLL_UP {
                    h * (1.0 - light::ease_in(p / ROLL_UP))
                } else {
                    0.0
                };
                let down = if p > ROLL_DOWN {
                    h * light::ease_out((p - ROLL_DOWN) / (1.0 - ROLL_DOWN))
                } else {
                    0.0
                };
                self.rows = Some((up, down));
                self.line = if p < ROLL_UP {
                    GLOW * light::ease_out(p / ROLL_UP)
                } else {
                    GLOW * (1.0 - light::ease_out((p - ROLL_UP) / (1.0 - ROLL_UP)))
                };
            }
        }
    }

    /// _slide: where a slide has got to: it begins when the playback card on show has a new
    /// track (not in a change of cards: that one shows the new track anyway), and takes `slide`
    /// seconds of the frames' planned time. Whether it moves on.
    fn slide_track(&mut self, snapshot: &Snapshot, now: f64, animate: bool) -> bool {
        let mut track = None;
        if self.leaving.is_none() && PLAYBACK.contains(&self.kinds[self.shown].as_str()) {
            let title = snapshot.value("media.title");
            if title.truthy() {
                track = Some((title, snapshot.value("media.artist")));
            }
        }
        match &track {
            None => self.sliding = None,
            Some(new) => {
                let other = self.track.as_ref().is_some_and(|old| old != new);
                if other && !self.last.is_empty() && self.slide > 0.0 && animate {
                    // (the new track fills a new set)
                    self.sliding = Some((now, std::mem::take(&mut self.last)));
                }
            }
        }
        (self.track, self.shift) = (track, None);
        let Some((began, _)) = &self.sliding else {
            return false;
        };
        let began = *began;
        if now - began >= self.slide {
            self.sliding = None;
            return false;
        }
        let reach = self.inner()[2] + lp_py::round(GAP * self.unit()) as i64;
        let away = lp_py::round(reach as f64 * light::ease_out((now - began) / self.slide)) as i64;
        self.shift = Some((-away, reach - away));
        true
    }

    /// shows: how part of `card` goes on the panel: drawn now (the card on show), what the card
    /// that rolls up showed before, or not at all.
    pub fn shows(&self, card: i64) -> Option<OnShow> {
        let card = usize::try_from(card).ok()?;
        if card == self.shown {
            return Some(OnShow::Live);
        }
        if self.leaving == Some(card) {
            return Some(OnShow::Frozen);
        }
        None
    }

    /// live: a part of the card on show, as drawn: kept for when the card goes or the track
    /// changes, and during a change as the new card is (cut to the rows it has rolled down to,
    /// faded, moved), or moved in from the right and cut to the card during a slide.
    pub fn live(&mut self, part: &str, piece: Rc<Piece>) -> Result<Option<Rc<Piece>>> {
        self.last.insert(part.to_string(), piece.clone());
        if let Some((_, new)) = self.shift {
            let [x, _, w, _] = self.inner();
            return Ok(slid(Some(&piece), new, x, x + w)?.map(Rc::new));
        }
        if self.leaving.is_none() {
            return Ok(Some(piece));
        }
        Ok(self.changing(Some(&piece), 1)?.map(Rc::new))
    }

    /// leave: a part of the card that goes: what it showed last, as the change has it.
    pub fn leave(&self, part: &str) -> Result<Option<Rc<Piece>>> {
        if self.leaving.is_none() {
            return Ok(None);
        }
        let piece = self.frozen.get(part).map(|p| p.as_ref());
        Ok(self.changing(piece, 0)?.map(Rc::new))
    }

    /// _changing: `piece` of the old card (`which` 0) or the new one (1) as the change has it.
    fn changing(&self, piece: Option<&Piece>, which: usize) -> Result<Option<Piece>> {
        let [x, y, w, h] = self.inner();
        let pick = |pair: (i64, i64)| if which == 0 { pair.0 } else { pair.1 };
        if let Some((old, new)) = self.alpha {
            return faded(piece, if which == 0 { old } else { new });
        }
        if let Some(push) = self.push {
            if self.effect == "slide" {
                return slid(piece, pick(push), x, x + w);
            }
            // (rise)
            return window(
                moved(piece, 0, pick(push)).as_ref(),
                y as f64,
                (y + h) as f64,
            );
        }
        if let Some(edge) = self.edge {
            let edge = lp_py::round(edge) as i64;
            return if which == 1 {
                slid(piece, 0, x, edge)
            } else {
                slid(piece, 0, edge, x + w)
            };
        }
        let Some((up, down)) = self.rows else {
            return Ok(None);
        };
        let y = y as f64;
        if which == 1 {
            return window(piece, y, y + down);
        }
        window(piece, y + down, y + up)
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

    /// _accent: the frame's colour for this frame, the card's `target`: at once in a still
    /// frame, reached over half a second in a moving one. Whether it moves on.
    pub fn accent(&mut self, target: [i64; 3], now: f64, animate: bool) -> bool {
        let Some(to) = self.to.filter(|_| animate) else {
            (self.from, self.to, self.colour, self.colour_at) =
                (Some(target), Some(target), Some(target), now);
            return false;
        };
        if target != to {
            (self.from, self.to, self.colour_at) = (self.colour, Some(target), now);
        }
        let u = light::clamp((now - self.colour_at) / FADE);
        let from = self.from.unwrap_or(target);
        self.colour = Some(light::mix(
            self.to.unwrap_or(target),
            from,
            light::smooth(u),
        ));
        u < 1.0
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

    /// _ignition: the line of light at the top edge, over the module's width; in a wipe the
    /// beam at its edge, down the card's height. None while it is out.
    fn ignition(&self, theme: &lp_theme::Theme) -> Result<Option<Piece>> {
        if self.line <= 0.004 {
            return Ok(None);
        }
        let [x, y, w, _] = self.bounds;
        let unit = self.unit();
        let half = (lp_py::round(32.0 * unit) as i64).max(3);
        let inner = self.inner();
        let across = if self.edge.is_none() { w } else { inner[3] };
        let mut strip = Image::blank(Mode::RGB, across.max(0) as usize, (2 * half) as usize);
        // (the card's that comes: one strip)
        let colour = self.to.or(self.colour).unwrap_or([255, 255, 255]);
        let look = light::Look {
            unit,
            soft: 40.0 * unit,
            halo: 7.0,
            share: 0.8,
            colour,
            white: light::role_colour(&theme.palette, "text"),
        };
        light::add_line(
            &mut strip,
            (0.0, across as f64, half as f64),
            self.line,
            &look,
        )?;
        let bands = strip.split();
        let brightest = chops::lighter(
            &chops::lighter(&bands[0], &bands[1]).map_err(failed)?,
            &bands[2],
        )
        .map_err(failed)?;
        strip.putalpha(&brightest).map_err(failed)?;
        let Some(edge) = self.edge else {
            let top = lp_py::round(y as f64 + self.width as f64 / 2.0) as i64 - half;
            let mut line = Piece::new(strip, x, top);
            line.add = true;
            return Ok(Some(line));
        };
        let mut beam = Piece::new(
            strip.transpose(Transpose::Rotate90),
            lp_py::round(edge) as i64 - half,
            inner[1],
        );
        beam.add = true;
        slid(Some(&beam), 0, inner[0], inner[0] + inner[2])
    }

    /// overlay: over the module's pieces, right after the last of them: in a slide the old
    /// track moving out, the frame, and during a change the line of light; none where none of
    /// its pieces is drawn.
    pub fn overlay(
        &mut self,
        theme: &lp_theme::Theme,
        pieces: &mut Vec<(String, Rc<Piece>)>,
    ) -> Result<()> {
        let prefix = format!("{}/", self.id);
        let Some(at) = pieces.iter().rposition(|(id, _)| id.starts_with(&prefix)) else {
            return Ok(());
        };
        let mut extra: Vec<(String, Rc<Piece>)> = Vec::new();
        if let (Some((old, _)), Some((_, parts))) = (self.shift, &self.sliding) {
            // (the old track, moving out)
            let [x, _, w, _] = self.inner();
            for (part, piece) in parts {
                if let Some(moved) = slid(Some(piece), old, x, x + w)? {
                    extra.push((format!("{part}\0old"), Rc::new(moved)));
                }
            }
        }
        if let (true, Some(colour)) = (self.width != 0, self.colour) {
            let strips = self.frame(theme, colour)?;
            for (i, strip) in strips.into_iter().enumerate() {
                let n = if i == 0 { String::new() } else { i.to_string() };
                extra.push((format!("{prefix}\0frame{n}"), strip));
            }
        }
        if let Some(line) = self.ignition(theme)? {
            extra.push((format!("{prefix}\0line"), Rc::new(line)));
        }
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
