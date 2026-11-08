//! A theme and a sensor snapshot made a frame (render/renderer.py's Renderer): every widget drawn
//! into its own layer, its effects applied, the layers laid on the theme's ground.

use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use indexmap::IndexMap;
use lp_pixels::{alpha_composite, dither, Filter, Image, Mode};
use lp_py::{Dict, Value};
use lp_text::fallback::FallbackFont;
use lp_theme::{Env, Theme};

use crate::agent;
use crate::bar;
use crate::compose;
use crate::countdown;
use crate::cover;
use crate::effects;
use crate::finish::{self, Finish};
use crate::gauge;
use crate::graph;
use crate::icons;
use crate::image;
use crate::list;
use crate::lit;
use crate::metric::{self, Shown};
use crate::piece::{Failed, Piece, Result};
use crate::shapes::{self, Rgba};
use crate::slot::Slot;
use crate::snapshot::{need_holds, needs_of, number, Moment, Snapshot};
use crate::sunpath;
use crate::symbols;
use crate::widget;

/// The most pixels one layer may have: what Python could not make either (an absurd size a
/// theme gives fails there, the widget left out; none a panel's theme needs comes near).
pub const MOST_PIXELS: i64 = 1 << 28;

/// The colour that is the colour of the cover on show (theme/model.py's COVER_COLOR).
pub const COVER_COLOR: &str = "@cover";
/// A need on one of the theme's switches: "switch.frames".
const SWITCH: &str = "switch.";
/// The modules that show one card of several (theme/modules.py's SLOT_KINDS).
const SLOT_KINDS: [&str; 2] = ["cards", "netcard"];

/// How a renderer draws: as the editor's preview or a picture (not the panel), with what moves
/// (animate), with the dither of the panel's video.
#[derive(Clone, Copy, Debug, Default)]
pub struct Options {
    pub preview: bool,
    pub animate: bool,
    pub dither: bool,
}

/// What a frame is made of: its pieces in the order they are laid, by widget id.
type Pieces = Vec<(String, Rc<Piece>)>;

/// What a card slot shows: its id, the card on show, its frame's colour (None: none chosen) and
/// thickness.
pub type SlotShown = (String, String, Option<[i64; 3]>, i64);

/// Why a frame is not made here: something on it the Rust renderer does not draw yet (the
/// Python app's frame is the reference: none is made in its place).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Unported(pub String);

impl From<Failed> for Unported {
    fn from(e: Failed) -> Unported {
        Unported(format!("the frame failed: {e}"))
    }
}

/// A widget's drawing: its piece, or why there is none.
pub(crate) enum Drawn {
    Piece(Rc<Piece>),
    /// nothing to draw (Python's None: no piece, no box)
    Nothing,
    Failed(Failed),
    Unported(String),
}

pub struct Renderer {
    pub theme: Theme,
    pub(crate) env: Env,
    pub options: Options,
    pub(crate) dither: bool,
    /// the widgets to draw: the theme's, its modules replaced by their parts
    pub widgets: Vec<Dict>,
    /// the box of each module, for the editor
    pub module_boxes: IndexMap<String, [i64; 4]>,
    /// the theme's modules by id
    modules: IndexMap<String, Dict>,
    /// the background picture at the theme's size (with its gradient), None for none
    background: Option<Image>,
    /// frames start from an opaque colour: pieces go on with a paste in place, in RGB
    opaque: bool,
    mode: Mode,
    /// the person's choice of the theme's switches (one not chosen is as the theme has it)
    pub switches: IndexMap<String, bool>,
    /// the readings it drew last (the cover's colour)
    snapshot: Option<Snapshot>,
    warnings: RefCell<Vec<String>>,
    /// each widget's finished piece, while it and what it shows stay the same
    cache: HashMap<String, (Value, Rc<Piece>)>,
    /// the fonts by name and size, opened once
    pub(crate) fonts: RefCell<HashMap<(String, i64), Rc<FallbackFont>>>,
    /// what metal lettering mirrors: the sky (the palette's accent) and the ground
    pub(crate) world: ([u8; 3], [u8; 3]),
    /// the finishes' letters by font, character, quarter pixel and style, built once
    pub(crate) facet_glyphs: crate::text::FacetGlyphs,
    /// the look of the weather symbols the person chose (config.toml's symbols: the app sets
    /// it; vivid where it does not, as Python's renderer has it)
    pub symbols: String,
    /// the range each graph of its own range was drawn in (_ranges: still, the values' with
    /// room), by id: its end dot lies in it
    ranges: HashMap<String, graph::Range>,
    /// the slot of every module that shows one card of several, by its id (_slots)
    slots: IndexMap<String, Slot>,
    /// whether this frame's slots have chosen their cards
    slots_begun: bool,
}

impl Renderer {
    pub fn new(theme: Theme, env: &Env, options: Options) -> Result<Renderer> {
        let expanded = lp_theme::modules::expand(&theme, env).map_err(|e| Failed(e.msg.clone()))?;
        let modules = theme
            .widgets
            .iter()
            .filter(|w| widget::text(w, "type") == "module")
            .map(|w| (widget::text(w, "id").to_string(), w.clone()))
            .collect();
        let mut renderer = Renderer {
            theme,
            env: env.clone(),
            options,
            dither: options.dither,
            // (finish.py's attach: the widgets as their finishes keep them)
            widgets: expanded.widgets.into_iter().map(finish::kept).collect(),
            module_boxes: expanded.boxes,
            modules,
            background: None,
            opaque: false,
            mode: Mode::RGBA,
            switches: IndexMap::new(),
            snapshot: None,
            warnings: RefCell::new(Vec::new()),
            cache: HashMap::new(),
            fonts: RefCell::new(HashMap::new()),
            world: ([0; 3], [0; 3]),
            facet_glyphs: RefCell::new(HashMap::new()),
            symbols: crate::symbols::DEFAULT_PACK.to_string(),
            ranges: HashMap::new(),
            slots: IndexMap::new(),
            slots_begun: false,
        };
        renderer.slots = renderer.make_slots()?;
        renderer.background = renderer.load_background()?;
        let ground = renderer.color(Some(&renderer.theme.background_color.clone()), 1.0)?;
        renderer.opaque = ground.is_some_and(|c| c[3] == 255);
        // (FacetText.glyph: the sky is the palette's accent, the ground the background)
        renderer.world =
            crate::facets::environment_for(&renderer.theme.palette, ground.unwrap_or([0; 4]));
        renderer.mode = if renderer.opaque {
            Mode::RGB
        } else {
            Mode::RGBA
        };
        Ok(renderer)
    }

    /// _make_slots: the slot of every module that shows one card of several (render/slot.py),
    /// a module laid out in a box.
    fn make_slots(&self) -> Result<IndexMap<String, Slot>> {
        let mut slots = IndexMap::new();
        for module in self.modules.values() {
            let id = widget::text(module, "id");
            if !SLOT_KINDS.contains(&widget::text(module, "module")) {
                continue;
            }
            if let Some(&bounds) = self.module_boxes.get(id) {
                slots.insert(id.to_string(), Slot::new(module, bounds, &self.theme)?);
            }
        }
        Ok(slots)
    }

    /// The slots' cards for this frame (Slot.begin, in a still frame), and the colour of each
    /// one's frame: its module's own (a colour, a palette's, the cover's), else its card's.
    fn begin_slots(&mut self, snapshot: &Snapshot, now: f64) -> std::result::Result<(), Unported> {
        let mut slots = std::mem::take(&mut self.slots);
        let mut begun = Ok(());
        for slot in slots.values_mut() {
            if let Err(what) = slot.begin(snapshot, now) {
                begun = Err(Unported(what));
                break;
            }
            let own = match slot.module.get("color") {
                Some(v) if v.truthy() => match self.color(Some(&v.str()), 1.0) {
                    Ok(Some(c)) => Some([c[0], c[1], c[2]]),
                    // (Python's frame fails at a colour that is none: None[:3])
                    Ok(None) => {
                        begun = Err(Unported("a card slot's colour failed".into()));
                        break;
                    }
                    Err(Failed(e)) => {
                        begun = Err(Unported(format!("a card slot's colour failed: {e}")));
                        break;
                    }
                },
                _ => None,
            };
            slot.set_colour(slot.target(own, snapshot));
        }
        self.slots = slots;
        self.slots_begun = begun.is_ok();
        begun
    }

    /// What each slot shows in the frame drawn last, by its id: the card on show, the frame's
    /// colour and its thickness; None where the frame did not get as far as choosing them.
    pub fn slots_shown(&self) -> Option<Vec<SlotShown>> {
        self.slots_begun.then(|| {
            self.slots
                .iter()
                .map(|(id, slot)| {
                    let (card, colour, width) = slot.state();
                    (id.clone(), card, colour, width)
                })
                .collect()
        })
    }

    pub(crate) fn warn(&self, message: String) {
        let mut warnings = self.warnings.borrow_mut();
        if !warnings.contains(&message) {
            warnings.push(message);
        }
    }

    /// What went wrong while it drew, each once (a widget left out, a picture or a font not read).
    pub fn warnings(&self) -> Vec<String> {
        self.warnings.borrow().clone()
    }

    // -- resources -------------------------------------------------------------------------

    /// color: a colour of the theme (a palette entry after "@", the cover's colour) as RGBA,
    /// its alpha times `alpha_scale`; None for none.
    pub fn color(&self, value: Option<&str>, alpha_scale: f64) -> Result<Option<Rgba>> {
        let Some(mut value) = value else {
            return Ok(None);
        };
        let palette = |name: &str, default: &'static str| -> String {
            match self.theme.palette.get(name) {
                Some(Value::Str(s)) => s.clone(),
                _ => default.to_string(),
            }
        };
        let entry;
        if value == COVER_COLOR {
            let carried = self
                .snapshot
                .as_ref()
                .and_then(|s| s.images.get("media.cover"))
                .and_then(cover::dominant);
            if let Some([r, g, b]) = carried {
                let alpha = (255.0 * alpha_scale).trunc() as i64;
                return Ok(Some([r.into(), g.into(), b.into(), alpha]));
            }
            entry = palette("accent", "#22d3ee");
            value = &entry;
        } else if let Some(name) = value.strip_prefix('@') {
            entry = palette(name, "#ff00ff");
            value = &entry;
        }
        let rgb = lp_pixels::colour::getrgb(value).map_err(|e| Failed(e.0))?;
        let alpha = if rgb.len() == 4 { rgb[3] } else { 255 };
        Ok(Some([
            rgb[0],
            rgb[1],
            rgb[2],
            (alpha as f64 * alpha_scale).trunc() as i64,
        ]))
    }

    /// The frame's time (a frame drawn without readings has Snapshot's own).
    fn now(&self) -> Moment {
        self.snapshot
            .as_ref()
            .map_or_else(|| Snapshot::default().now, |s| s.now)
    }

    /// _range_now in a still frame: the range a graph of its own range was drawn in; None for
    /// one with min and max, or none drawn yet.
    pub(crate) fn range_now(&self, w: &Dict) -> Option<graph::Range> {
        if graph::fixed(w) {
            return None;
        }
        self.ranges.get(widget::text(w, "id")).cloned()
    }

    /// The colour of a widget's colour field (None where it has none).
    pub(crate) fn colour_of(&self, w: &Dict, key: &str) -> Result<Option<Rgba>> {
        self.color(widget::optional(w, key), 1.0)
    }

    /// How much of the background a card covers (its style's card_opacity), as float() takes it.
    pub(crate) fn card_opacity(&self) -> Result<f64> {
        let given = self
            .theme
            .style
            .as_ref()
            .and_then(|s| s.get("card_opacity"))
            .cloned()
            .unwrap_or(Value::Float(1.0));
        lp_py::format::float_of(&given).map_err(|e| Failed(e.msg))
    }

    /// _read_picture: a picture of the theme's folder, upright (EXIF) and in RGBA; None where it
    /// cannot be read (warned).
    fn read_picture(&mut self, rel: &str) -> Option<Image> {
        let root = self.theme.root.clone()?;
        if rel.is_empty() {
            return None;
        }
        let read = lp_theme::model::resolve_asset(&root, &Value::from(rel))
            .map_err(|e| e.msg.clone())
            .and_then(|path| std::fs::read(&path).map_err(|e| e.to_string()))
            .and_then(|data| lp_decode::open(&data, Mode::RGBA).map_err(|e| e.0));
        match read {
            Ok(image) => Some(image),
            Err(e) => {
                self.warn(format!(
                    "image {} could not be loaded ({e})",
                    lp_py::repr(rel)
                ));
                None
            }
        }
    }

    /// _load_background: the background picture at the theme's size, laid on its gradient.
    fn load_background(&mut self) -> Result<Option<Image>> {
        let (w, h) = (
            self.theme.width.max(0) as usize,
            self.theme.height.max(0) as usize,
        );
        let rel = self.theme.background_image.clone().unwrap_or_default();
        let image = match self.read_picture(&rel) {
            Some(image) => Some(image.fit((w, h), Filter::Bicubic, 0.0, (0.5, 0.5))?),
            None => None,
        };
        if self.theme.background_gradient.is_none() {
            return Ok(image);
        }
        let ground = self.gradient_ground()?;
        Ok(Some(match image {
            Some(image) => alpha_composite(&ground, &image)?,
            None => ground,
        }))
    }

    /// _gradient_ground: the theme's background gradient, the panel's size, worked out in
    /// numpy's float32 and made 8 bits (with the dither in video mode).
    fn gradient_ground(&self) -> Result<Image> {
        let (w, h) = (
            self.theme.width.max(0) as usize,
            self.theme.height.max(0) as usize,
        );
        let (first, second) = self.theme.background_gradient.clone().expect("a gradient");
        let colour = |c: &str| -> Result<[f32; 4]> {
            let rgba = self.color(Some(c), 1.0)?.unwrap_or([0, 0, 0, 255]);
            Ok(rgba.map(|v| v as f32))
        };
        let (a, b) = (colour(&first)?, colour(&second)?);
        let across = self.theme.background_direction == "horizontal";
        let n = if across { w } else { h };
        let line = shapes::ground_line(a, b, n);
        let length = if across { h } else { w };
        let rgb = dither::ramp_u8(&line, n, 3, length, across, self.dither);
        Ok(Image::from_bytes(Mode::RGB, w, h, rgb)?.convert(Mode::RGBA)?)
    }

    /// The ground of the whole frame: the background colour, with the picture laid over it
    /// (the picture itself where it is opaque), in the frames' mode.
    fn ground(&self) -> Result<Image> {
        let (w, h) = (
            self.theme.width.max(0) as usize,
            self.theme.height.max(0) as usize,
        );
        let colour = self.color(Some(&self.theme.background_color), 1.0)?;
        let Some(background) = &self.background else {
            if let Some(c) = colour {
                let ink = c.map(|v| v.clamp(0, 255) as u8);
                return Ok(match self.mode {
                    Mode::RGB => Image::new(Mode::RGB, w, h, &ink[..3]),
                    _ => Image::new(Mode::RGBA, w, h, &ink),
                });
            }
            return Ok(Image::blank(self.mode, w, h));
        };
        let opaque = background.mode() == Mode::RGB
            || background
                .getchannel(3)?
                .getextrema()
                .is_some_and(|e| e[0].0 == 255);
        let ground = if opaque {
            background.clone()
        } else {
            let ink = colour
                .unwrap_or([0, 0, 0, 0])
                .map(|v| v.clamp(0, 255) as u8);
            alpha_composite(&Image::new(Mode::RGBA, w, h, &ink), background)?
        };
        if self.mode == Mode::RGB && ground.mode() != Mode::RGB {
            return Ok(ground.convert(Mode::RGB)?);
        }
        Ok(ground)
    }

    // -- the frame -------------------------------------------------------------------------

    /// _holds: whether a condition of `needs` holds; one on a switch of the theme as the person
    /// set it, else as the theme does.
    fn holds(&self, need: &str, snapshot: &Snapshot) -> bool {
        let body = lp_py::strip(need.trim_start_matches('!'));
        let Some(name) = body.strip_prefix(SWITCH) else {
            return need_holds(need, snapshot);
        };
        let on = match self.switches.get(name) {
            Some(&on) => on,
            None => match self.theme.switches.get(name) {
                Some(Value::Dict(d)) => d.get("on").is_some_and(Value::truthy),
                _ => false,
            },
        };
        on != need.starts_with('!')
    }

    /// _missing: whether a widget is left out: a condition of its needs that does not hold, or
    /// (hide_if_missing) its reading has no value: a weather widget's field, the code a
    /// weather icon follows, else its sensor's; a graph without a course published whole and
    /// with fewer than two values in its history.
    fn missing(&self, w: &Dict, snapshot: &Snapshot) -> bool {
        let needs = widget::text(w, "needs");
        if needs_of(needs).any(|need| !self.holds(need, snapshot)) {
            return true;
        }
        let kind = widget::text(w, "type");
        if kind == "module" || !widget::truthy(w, "hide_if_missing") {
            return false;
        }
        let sensor = widget::text(w, "sensor");
        let key = match kind {
            "weather" => format!("weather.{}", widget::text(w, "field")),
            "icon" if widget::text(w, "icon") == "weather" && sensor.is_empty() => {
                "weather.code".to_string()
            }
            "graph" => {
                if graph::course_of(&snapshot.value(sensor)).is_some() {
                    return false;
                }
                return snapshot.history.get(sensor).map_or(0, Vec::len) < 2;
            }
            _ => sensor.to_string(),
        };
        !key.is_empty() && snapshot.value(&key) == Value::None
    }

    /// _effects: the widget's opacity, and its shadow and glow round it.
    fn effects(&self, w: &Dict, mut piece: Piece) -> Result<Piece> {
        let opacity = widget::number(w, "opacity", 1.0);
        let scaled = effects::scale_alpha(&piece.layer, opacity)?;
        piece.hit = Some(piece.bounds());
        piece.light = effects::dimmed(&piece.light, opacity)?;
        if let Some(layer) = scaled {
            piece.layer = layer;
        }
        let shadow = match widget::optional(w, "shadow") {
            Some(s) if !s.is_empty() => self.color(Some(s), 1.0)?,
            _ => None,
        };
        let Some((mut out, pad)) = effects::halo(w, &piece.layer, shadow, self.dither)? else {
            return Ok(piece);
        };
        let (lw, lh) = (piece.layer.width() as i64, piece.layer.height() as i64);
        out.alpha_composite_at(&piece.layer, (pad, pad), (0, 0, lw, lh))?;
        piece.layer = out;
        piece.x -= pad;
        piece.y -= pad;
        Ok(if widget::truthy(w, "clip") {
            effects::clip_sides(piece)
        } else {
            piece
        })
    }

    /// The picture a source or a service has under `name`, in the frame's snapshot.
    fn live_picture(&self, name: &str) -> Option<&Image> {
        self.snapshot.as_ref()?.images.get(name)
    }

    /// _cached: the finished piece kept while the widget and what it shows (`content`) stay the
    /// same.
    pub(crate) fn cached(
        &mut self,
        w: &Dict,
        content: Value,
        build: impl FnOnce(&Renderer) -> Result<Piece>,
    ) -> Result<Rc<Piece>> {
        let id = widget::text(w, "id").to_string();
        if let Some((kept, piece)) = self.cache.get(&id) {
            if *kept == content {
                return Ok(piece.clone());
            }
        }
        let piece = build(self)?;
        let piece = Rc::new(self.effects(w, piece)?);
        self.cache.insert(id, (content, piece.clone()));
        Ok(piece)
    }

    /// _text_widget: the text as a piece in its colour, kept by the two.
    fn text_widget(&mut self, w: &Dict, text: String, colour: String) -> Result<Rc<Piece>> {
        let content = Value::List(vec![text.clone().into(), colour.clone().into()]);
        self.cached(w, content, |r| r.text_piece(w, &text, &colour))
    }

    /// A widget's piece, or why it has none.
    fn draw(&mut self, w: &Dict) -> Drawn {
        let made = match widget::text(w, "type") {
            "rect" => {
                let w = w.clone();
                self.cached(&w, Value::None, |r| r.rect_piece(&w))
            }
            "text" => {
                let w = w.clone();
                let text = self.text_of(&w);
                self.text_widget(&w, text, widget::text(&w, "color").to_string())
            }
            "icon" => {
                let w = w.clone();
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let name = Self::icon_name(&w, &snapshot);
                // (LitIcon: a weather's icon as its symbol, lit, where it is large enough)
                let size = widget::int(&w, "size", 32);
                if finish::of(&w) == Finish::LitIcon
                    && symbols::SYMBOLS.contains(&name.as_str())
                    && size >= symbols::MIN_ICON
                {
                    let own = match self.colour_of(&w, "color") {
                        Ok(colour) => colour.unwrap_or([255; 4]),
                        Err(e) => return Drawn::Failed(e),
                    };
                    let content = Value::List(vec![
                        "lit".into(),
                        name.clone().into(),
                        Value::List(own.iter().map(|&c| Value::int(c)).collect()),
                        self.pack(&w).into(),
                    ]);
                    self.cached(&w, content, |r| r.lit_icon_piece(&w, &name))
                } else {
                    let colour = match self.color(Some(widget::text(&w, "color")), 1.0) {
                        Ok(colour) => colour.unwrap_or([0, 0, 0, 0]),
                        Err(e) => return Drawn::Failed(e),
                    };
                    let content = Value::List(vec![
                        name.clone().into(),
                        Value::List(colour.iter().map(|&c| Value::int(c)).collect()),
                    ]);
                    self.cached(&w, content, |_| {
                        let stroke = widget::number(&w, "stroke", 2.0);
                        let layer = icons::draw_icon(&name, size, shapes::ink(colour), stroke)?;
                        Ok(Piece::new(
                            layer,
                            widget::int(&w, "x", 0),
                            widget::int(&w, "y", 0),
                        ))
                    })
                }
            }
            "weather" => {
                let w = w.clone();
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let key = format!("weather.{}", widget::text(&w, "field"));
                let text = match self.format_reading(&w, &key, snapshot.readings.get(&key)) {
                    Ok((text, _)) => text,
                    Err(e) => return Drawn::Failed(e),
                };
                self.text_widget(&w, text, widget::text(&w, "color").to_string())
            }
            "countdown" => {
                let w = w.clone();
                let now = self.now();
                let (target, part) = (widget::text(&w, "target"), widget::text(&w, "part"));
                let text =
                    match countdown::countdown_text(target, part, &now, widget::text(&w, "done")) {
                        Ok(countdown::Said::Text(text)) => text,
                        Ok(countdown::Said::Unported(what)) => return Drawn::Unported(what.into()),
                        Err(e) => return Drawn::Failed(e),
                    };
                self.text_widget(&w, text, widget::text(&w, "color").to_string())
            }
            "clock" => {
                let w = w.clone();
                // (another place's time: the computer's own zone and the other's, not here yet)
                if !widget::text(&w, "timezone").is_empty() {
                    return Drawn::Unported("clocks in a time zone".into());
                }
                let kind = widget::optional(&w, "digits").unwrap_or("jump");
                let seconds = widget::number(&w, "digits_seconds", 0.42);
                if self.options.animate && kind != "jump" && 0.0 < seconds && seconds <= 1.0 {
                    return Drawn::Unported("clocks whose digits roll or fade".into());
                }
                let now = self.now();
                // _clock_format: a hundred characters at most; "--:--" where strftime refuses
                let text: String = match lp_i18n::format_date(&now, widget::text(&w, "format")) {
                    Ok(text) => text.chars().take(100).collect(),
                    Err(lp_i18n::DateError::ValueError(_)) => "--:--".into(),
                    Err(lp_i18n::DateError::Unported(what)) => return Drawn::Unported(what.into()),
                    Err(lp_i18n::DateError::Failed(e)) => return Drawn::Failed(Failed(e)),
                };
                let text = if widget::truthy(&w, "uppercase") {
                    lp_py::upper(&text)
                } else {
                    text
                };
                self.text_widget(&w, text, widget::text(&w, "color").to_string())
            }
            "calendar" => {
                let w = w.clone();
                let now = self.now();
                let today = now.midnight();
                let content = Value::List(vec![
                    Value::int(today.toordinal()),
                    widget::text(&w, "color").into(),
                    widget::text(&w, "color2").into(),
                    widget::text(&w, "muted").into(),
                    lp_i18n::language().code().into(),
                ]);
                self.cached(&w, content, |r| r.calendar_piece(&w, &today))
            }
            "agent" => {
                let w = w.clone();
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let state = match snapshot.value(widget::text(&w, "sensor")) {
                    Value::Str(s) if matches!(s.as_str(), "working" | "waiting" | "idle") => s,
                    _ => "off".to_string(),
                };
                // (a sign at work or waiting moves)
                if self.options.animate && matches!(state.as_str(), "working" | "waiting") {
                    return Drawn::Unported("agents' signs that move".into());
                }
                let size = widget::int(&w, "size", 64).max(8);
                let mut colours = [[0i64; 4]; 3];
                for (colour, key) in colours.iter_mut().zip(["color", "color2", "muted"]) {
                    match self.colour_of(&w, key) {
                        Ok(c) => *colour = c.unwrap_or([0, 0, 0, 0]),
                        Err(e) => return Drawn::Failed(e),
                    }
                }
                let mut content = vec![state.clone().into(), Value::int(size)];
                content.extend(colours.map(|c| Value::List(c.map(Value::int).to_vec())));
                let (x, y) = (widget::int(&w, "x", 0), widget::int(&w, "y", 0));
                self.cached(&w, Value::List(content), |_| {
                    let [a, b, c] = colours;
                    Ok(agent::piece(
                        x,
                        y,
                        size,
                        agent::picture(size, &state, a, b, c)?,
                    ))
                })
            }
            "sunpath" => {
                let w = w.clone();
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let at = match sunpath::step(&snapshot.value(widget::text(&w, "sensor"))) {
                    Ok(at) => at,
                    Err(e) => return Drawn::Failed(e),
                };
                let up = snapshot.value("sun.up");
                if at.is_none() && up == Value::None {
                    return Drawn::Nothing; // (no place: no sun's times)
                }
                // (the midnight sun: up all day, at its height)
                let at = at.or_else(|| (number(&up) == Some(1.0)).then_some(sunpath::STEPS / 2));
                let way = match self.sunpath_of(&w, at) {
                    Ok(way) => way,
                    Err(e) => return Drawn::Failed(e),
                };
                self.cached(&w, way.content(), |_| way.piece())
            }
            "bar" => {
                let w = w.clone();
                // (a bar gliding to a new reading, a colour fading: motion, not drawn here yet)
                if self.options.animate
                    && (widget::flag(&w, "smooth", true) || metric::has_rules(&w))
                {
                    return Drawn::Unported("bars that move".into());
                }
                if !["min", "max"].iter().all(|k| widget::exact(&w, k)) {
                    return Drawn::Unported("numbers a float does not hold as they are".into());
                }
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let value = number(&snapshot.value(widget::text(&w, "sensor")));
                let Some(rules) = metric::rules_of(&w) else {
                    return Drawn::Unported("colour rules above NaN".into());
                };
                let (lo, hi) = (
                    widget::number(&w, "min", 0.0),
                    widget::number(&w, "max", 100.0),
                );
                let frac = bar::fraction(value, lo, hi, widget::text(&w, "scale"));
                if finish::of(&w) == Finish::LitBar {
                    if let Some(lit) = self.lit_bar(&w, frac, value, &rules) {
                        return lit;
                    }
                }
                let horizontal = matches!(widget::text(&w, "direction"), "right" | "left");
                let length = widget::int(&w, if horizontal { "w" } else { "h" }, 0);
                let (frac, paint) = match bar::steps(frac, length)
                    .and_then(|frac| Ok((frac, self.rule_paint(&w, value, &rules)?)))
                {
                    Ok(made) => made,
                    Err(e) => return Drawn::Failed(e),
                };
                let content = bar::content(frac, paint);
                if finish::of(&w) == Finish::FacetBar {
                    self.cached(&w, content, |r| r.facet_bar_piece(&w, frac, paint))
                } else {
                    self.cached(&w, content, |r| r.bar_piece(&w, frac, paint))
                }
            }
            "gauge" => {
                let w = w.clone();
                // (a ring gliding to a new reading, a colour fading: motion, not drawn here yet)
                if self.options.animate
                    && (widget::flag(&w, "smooth", true) || metric::has_rules(&w))
                {
                    return Drawn::Unported("gauges that move".into());
                }
                let numbers = ["min", "max", "start_angle", "end_angle"];
                if !numbers.iter().all(|k| widget::exact(&w, k)) {
                    return Drawn::Unported("numbers a float does not hold as they are".into());
                }
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let value = number(&snapshot.value(widget::text(&w, "sensor")));
                let Some(rules) = metric::rules_of(&w) else {
                    return Drawn::Unported("colour rules above NaN".into());
                };
                let (lo, hi) = (
                    widget::number(&w, "min", 0.0),
                    widget::number(&w, "max", 100.0),
                );
                let frac = bar::fraction(value, lo, hi, widget::text(&w, "scale"));
                // (the arc's length on the ring, as many steps of the supersampled grid)
                let sweep = (widget::number(&w, "end_angle", 405.0)
                    - widget::number(&w, "start_angle", 135.0))
                .abs();
                let size = widget::int(&w, "w", 120).max(widget::int(&w, "h", 120));
                let length = sweep.to_radians() * size as f64 / 2.0;
                let (frac, paint) = match gauge::steps_along(frac, length)
                    .and_then(|frac| Ok((frac, self.rule_paint(&w, value, &rules)?)))
                {
                    Ok(made) => made,
                    Err(e) => return Drawn::Failed(e),
                };
                let content = bar::content(frac, paint);
                if finish::of(&w) == Finish::LitGauge && lit::ringed(&w) {
                    let content = Value::List(vec!["lit".into(), content]);
                    self.cached(&w, content, |r| r.lit_gauge_piece(&w, frac, paint))
                } else {
                    self.cached(&w, content, |r| r.gauge_piece(&w, frac, paint))
                }
            }
            "metric" => {
                let w = w.clone();
                // (a number gliding, a colour fading: motion, not drawn here yet)
                if self.options.animate
                    && (widget::flag(&w, "smooth", false) || metric::has_rules(&w))
                {
                    return Drawn::Unported("metrics that move".into());
                }
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let (text, colour) = match self.metric_text(&w, &snapshot) {
                    Ok(Shown::Text(text, colour)) => (text, colour),
                    Ok(Shown::Unported(what)) => return Drawn::Unported(what.into()),
                    Err(e) => return Drawn::Failed(e),
                };
                self.text_widget(&w, text, colour)
            }
            "image" => {
                let w = w.clone();
                match image::shown(&w) {
                    image::Shown::Unported(what) => return Drawn::Unported(what.into()),
                    image::Shown::Clear => {
                        self.cached(&w, Value::None, |r| r.clear_image_piece(&w))
                    }
                    image::Shown::Live(name) => {
                        let picture = self.live_picture(&name);
                        let Some(content) = picture.map(|p| image::picture_key(&name, p)) else {
                            return Drawn::Nothing;
                        };
                        self.cached(&w, content, |r| match r.live_picture(&name) {
                            Some(p) => r.live_image_piece(&w, p),
                            None => Err(Failed("a picture gone".into())),
                        })
                    }
                }
            }
            "graph" => {
                let w = w.clone();
                let snapshot = self.snapshot.clone().unwrap_or_default();
                // (a course published whole: across the width, its range the values' own)
                if let Some(course) = graph::course_of(&snapshot.value(widget::text(&w, "sensor")))
                {
                    let numbers: Vec<f64> = course.iter().flatten().copied().collect();
                    let range = if numbers.is_empty() {
                        None
                    } else {
                        match graph::bounds(&w, &numbers) {
                            Ok(range) => Some(range),
                            Err(e) => return Drawn::Failed(e),
                        }
                    };
                    if graph::points_undrawable(&w, &course, range.as_ref()) {
                        return Drawn::Unported(graph::NAN_POINTS.into());
                    }
                    let content = graph::course_key(&course, range.as_ref());
                    let slots = course.len();
                    self.cached(&w, content, |r| {
                        r.graph_piece(&w, &course, slots, range.as_ref())
                    })
                } else {
                    let (values, slots) = graph::history_of(&snapshot, &w);
                    let numbers: Vec<f64> = values.iter().flatten().copied().collect();
                    let mut range = None;
                    if !numbers.is_empty() {
                        let id = widget::text(&w, "id").to_string();
                        let own = !graph::fixed(&w);
                        // (a range of its own glides from one frame to the next: motion)
                        if own && self.options.animate && self.ranges.contains_key(&id) {
                            return Drawn::Unported("graphs whose own range glides".into());
                        }
                        match graph::still_bounds(&w, &numbers) {
                            Ok(want) => range = Some(want),
                            Err(e) => return Drawn::Failed(e),
                        }
                        if let (true, Some(want)) = (own, &range) {
                            self.ranges.insert(id, want.clone());
                        }
                    }
                    if graph::points_undrawable(&w, &values, range.as_ref()) {
                        return Drawn::Unported(graph::NAN_POINTS.into());
                    }
                    let content = graph::history_key(&values, range.as_ref());
                    self.cached(&w, content, |r| {
                        r.graph_piece(&w, &values, slots, range.as_ref())
                    })
                }
            }
            "list" => {
                let w = w.clone();
                // (bars gliding to a reading: motion; fans turn on their names in a moving
                // frame, and stand in a still one)
                let fans = widget::text(&w, "style") == "fans";
                let glide = widget::truthy(&w, "smooth") && widget::truthy(&w, "levels") && !fans;
                if self.options.animate && (glide || fans) {
                    return Drawn::Unported("lists whose bars glide or whose fans turn".into());
                }
                if !["min", "max"].iter().all(|k| widget::exact(&w, k)) {
                    return Drawn::Unported("numbers a float does not hold as they are".into());
                }
                let Some(rules) = metric::rules_of(&w) else {
                    return Drawn::Unported("colour rules above NaN".into());
                };
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let mut items = match list::pick(&w, &snapshot) {
                    Ok(items) => items,
                    Err(e) => return Drawn::Failed(e),
                };
                if widget::truthy(&w, "hide_unused") {
                    // (a mainboard's fan headers with nothing on them)
                    items.retain(list::used);
                }
                let (items, rows, hi) = match self.list_rows(&w, items, &rules) {
                    Ok(shown) => shown,
                    Err(e) => return Drawn::Failed(e),
                };
                if fans {
                    // (the names and numbers stay; the fans turn on them)
                    match self.cached(&w, list::texts_key(&rows), |r| r.list_piece(&w, &rows)) {
                        Ok(piece) if !items.is_empty() => {
                            self.list_turning(&w, &piece, &items, hi).map(Rc::new)
                        }
                        other => other,
                    }
                } else {
                    self.cached(&w, list::rows_key(&rows), |r| r.list_piece(&w, &rows))
                }
            }
            "symbol" => {
                let w = w.clone();
                if self.options.animate && widget::truthy(&w, "moving") {
                    return Drawn::Unported("weather symbols that move".into());
                }
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let lit = finish::of(&w) == Finish::LitSymbol;
                let sensor = widget::text(&w, "sensor");
                let colours = match self.symbol_colours(&w) {
                    Ok(c) => format!("{c:?}"),
                    Err(e) => return Drawn::Failed(e),
                };
                let content = Value::List(vec![
                    symbols::kind_of(&snapshot.value(sensor)).into(),
                    Value::Bool(symbols::night_at(&snapshot, sensor)),
                    Value::Bool(lit),
                    self.pack(&w).into(),
                    colours.into(),
                ]);
                self.cached(&w, content, |r| r.symbol_piece(&w, &snapshot, lit))
            }
            other => return Drawn::Unported(format!("{other} widgets")),
        };
        match made {
            // (LitText: the text's or the metric's glow as light, its letters faded)
            Ok(piece) if finish::of(w) == Finish::LitText => match lit::lit_text(w, &piece) {
                Ok(Some(lit)) => Drawn::Piece(Rc::new(lit)),
                Ok(None) => Drawn::Piece(piece),
                Err(e) => Drawn::Failed(e),
            },
            Ok(piece) => Drawn::Piece(piece),
            Err(e) => Drawn::Failed(e),
        }
    }

    /// _icon_name: the icon a widget shows: its own, the network connection's for "link", the
    /// weather's for "weather" (by day or night as its reading says, else the clock).
    fn icon_name(w: &Dict, snapshot: &Snapshot) -> String {
        let name = widget::text(w, "icon");
        let sensor = widget::optional(w, "sensor").filter(|s| !s.is_empty());
        if name == "link" {
            let kind = snapshot.value(sensor.unwrap_or("net.link.kind"));
            return icons::link_icon(&kind, &snapshot.value("net.wifi.signal")).to_string();
        }
        if name == "weather" {
            // (day or night as symbols.night_at tells it: _icon_name tells it the same way)
            let key = sensor.unwrap_or("weather.code");
            let night = symbols::night_at(snapshot, key);
            return icons::weather_icon_name(&snapshot.value(key), night).to_string();
        }
        name.to_string()
    }

    /// What a frame of the theme holds beyond its widgets that is not drawn here yet: a screen
    /// (a plugin's, drawn under the widgets), the panel's look (its grid beneath them) and the
    /// finishes Rust does not draw yet (render/finish.py: lit, facet, acrylic; one that does not
    /// take a widget leaves it flat, here as there). Rust says so for any of them, also where
    /// Python goes on without (a screen no plugin has): never a frame drawn without what Python
    /// draws.
    fn beyond_widgets(&self) -> Option<&'static str> {
        if self.theme.screen.is_some() {
            return Some("screens");
        }
        if widget::truthy(&self.theme.look, "grid") {
            return Some("looks");
        }
        if self
            .widgets
            .iter()
            .any(|w| finish::of(w) == Finish::Unported)
        {
            return Some("finishes");
        }
        None
    }

    /// render: the frame (RGB) of `snapshot` at `now` (the clock the slots' rules keep their
    /// times by: render's own `now`, not the snapshot's moment), and the widgets' boxes;
    /// Unported where it holds what is not drawn here yet.
    pub fn render(
        &mut self,
        snapshot: Snapshot,
        now: f64,
    ) -> std::result::Result<(Image, IndexMap<String, [i64; 4]>), Unported> {
        self.slots_begun = false;
        if let Some(what) = self.beyond_widgets() {
            return Err(Unported(what.into()));
        }
        if !self.slots.is_empty() && self.options.animate {
            // (a moving frame warms every card up and rolls from one to the next)
            return Err(Unported("card slots in a moving frame".into()));
        }
        let snapshot = snapshot.finite();
        self.snapshot = Some(snapshot.clone());
        // which card each slot shows
        self.begin_slots(&snapshot, now)?;
        let mut boxes = IndexMap::new();
        let mut pieces: Pieces = Vec::new();
        let mut gone: HashMap<String, bool> = HashMap::new();
        let widgets = self.widgets.clone();
        for w in &widgets {
            if !widget::flag(w, "visible", true) {
                continue;
            }
            let module = self.modules.get(widget::text(w, "_module")).cloned();
            // a part of a card in a slot: drawn while its card is on show
            let slot = match &module {
                Some(module) if w.contains_key("_card") => {
                    self.slots.get(widget::text(module, "id"))
                }
                _ => None,
            };
            let in_slot = slot.is_some();
            if slot.is_some_and(|slot| !slot.shows(widget::int(w, "_card", 0))) {
                continue;
            }
            if self.missing(w, &snapshot) {
                continue;
            }
            if let Some(module) = &module {
                let id = widget::text(module, "id").to_string();
                let away = match gone.get(&id) {
                    Some(&away) => away,
                    None => {
                        let away = !widget::flag(module, "visible", true)
                            || self.missing(module, &snapshot);
                        gone.insert(id, away);
                        away
                    }
                };
                if away {
                    continue;
                }
            }
            let id = widget::text(w, "id").to_string();
            let piece = match self.draw(w) {
                Drawn::Piece(piece) => piece,
                Drawn::Nothing => continue,
                Drawn::Unported(what) => return Err(Unported(what)),
                Drawn::Failed(e) => {
                    self.warn(format!("widget {} failed: {e}", lp_py::repr(&id)));
                    continue;
                }
            };
            // (a module's parts turn with the module)
            let owner = module.as_ref().unwrap_or(w);
            if widget::number(owner, "rotate", 0.0) % 360.0 != 0.0 {
                return Err(Unported("turned widgets".into()));
            }
            pieces.push((id.clone(), piece.clone()));
            if in_slot {
                // (what lies over a bar or a graph is laid for widgets in no slot alone)
            } else if widget::text(w, "type") == "bar" {
                // what lies over a bar: its mark, its end dot (Python's frame fails where one
                // cannot be made: Rust makes none)
                let refused = |e: Failed| Unported(format!("a bar's mark or dot: {}", e.0));
                let mark = if widget::truthy(w, "mark") {
                    self.bar_mark(w, &snapshot).map_err(refused)?
                } else {
                    None
                };
                let dot = if widget::truthy(w, "end_dot") {
                    let rules = metric::rules_of(w).unwrap_or_default();
                    self.bar_dot(w, &snapshot, &rules).map_err(refused)?
                } else {
                    None
                };
                for (name, over) in [("mark", mark), ("dot", dot)] {
                    if let Some((plain, content, made)) = over {
                        let made = self
                            .cached(&plain, content, |_| Ok(made))
                            .map_err(refused)?;
                        pieces.push((format!("{id}\0{name}"), made));
                    }
                }
            } else if widget::text(w, "type") == "graph" && widget::truthy(w, "end_dot") {
                // (what lies over a graph: its end dot)
                let refused = |e: Failed| Unported(format!("a graph's end dot: {}", e.0));
                if let Some((plain, content, made)) =
                    self.graph_dot(w, &snapshot).map_err(refused)?
                {
                    let made = self
                        .cached(&plain, content, |_| Ok(made))
                        .map_err(refused)?;
                    pieces.push((format!("{id}\0dot"), made));
                }
            }
            if let Some(module) = &module {
                let mid = widget::text(module, "id").to_string();
                if let Some(b) = self.module_boxes.get(&mid) {
                    boxes.insert(mid, *b);
                }
                continue;
            }
            if id.starts_with('\0') {
                continue;
            }
            boxes.insert(id, piece.bounds());
        }
        // the slots' frames, over their cards
        let mut slots = std::mem::take(&mut self.slots);
        let laid = slots
            .values_mut()
            .try_for_each(|slot| slot.overlay(&self.theme, &mut pieces));
        self.slots = slots;
        laid.map_err(|Failed(e)| Unported(format!("a card slot's frame failed: {e}")))?;
        let canvas = self.compose(&pieces)?;
        Ok((compose::as_rgb(&canvas)?, boxes))
    }

    /// _compose (the first frame, or keep_static off: the whole of it from the ground up).
    fn compose(&mut self, pieces: &Pieces) -> Result<Image> {
        let mut part = self.ground()?;
        for (id, piece) in pieces {
            if let Err(Failed(e)) = compose::composite(&mut part, piece, (0, 0), self.opaque) {
                self.warn(format!("widget {} failed: {e}", lp_py::repr(id)));
            }
        }
        Ok(part)
    }
}
