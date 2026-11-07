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

use crate::bar;
use crate::compose;
use crate::countdown;
use crate::cover;
use crate::effects;
use crate::gauge;
use crate::icons;
use crate::metric::{self, Shown};
use crate::piece::{Failed, Piece, Result};
use crate::shapes::{self, Rgba};
use crate::snapshot::{need_holds, needs_of, number, Snapshot};
use crate::widget;

/// The most pixels one layer may have: what Python could not make either (an absurd size a
/// theme gives fails there, the widget left out; none a panel's theme needs comes near).
pub const MOST_PIXELS: i64 = 1 << 28;

/// The colour that is the colour of the cover on show (theme/model.py's COVER_COLOR).
pub const COVER_COLOR: &str = "@cover";
/// A need on one of the theme's switches: "switch.frames".
const SWITCH: &str = "switch.";
/// The finish a widget has unless it names another (theme/model.py's FINISHES[0]).
const FLAT: &str = "flat";

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
enum Drawn {
    Piece(Rc<Piece>),
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
            widgets: expanded.widgets,
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
        };
        renderer.background = renderer.load_background()?;
        let ground = renderer.color(Some(&renderer.theme.background_color.clone()), 1.0)?;
        renderer.opaque = ground.is_some_and(|c| c[3] == 255);
        renderer.mode = if renderer.opaque {
            Mode::RGB
        } else {
            Mode::RGBA
        };
        Ok(renderer)
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
    /// weather icon follows, else its sensor's. (A graph Python leaves out by its course and
    /// its history: graphs are not drawn here yet, so Rust leaves none out and says so.)
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
            "graph" => return false,
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

    /// _cached: the finished piece kept while the widget and what it shows (`content`) stay the
    /// same.
    fn cached(
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
                let colour = match self.color(Some(widget::text(&w, "color")), 1.0) {
                    Ok(colour) => colour.unwrap_or([0, 0, 0, 0]),
                    Err(e) => return Drawn::Failed(e),
                };
                let content = Value::List(vec![
                    name.clone().into(),
                    Value::List(colour.iter().map(|&c| Value::int(c)).collect()),
                ]);
                self.cached(&w, content, |_| {
                    let (size, stroke) = (
                        widget::int(&w, "size", 32),
                        widget::number(&w, "stroke", 2.0),
                    );
                    let layer = icons::draw_icon(&name, size, shapes::ink(colour), stroke)?;
                    Ok(Piece::new(
                        layer,
                        widget::int(&w, "x", 0),
                        widget::int(&w, "y", 0),
                    ))
                })
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
                let now = self.snapshot.as_ref().map(|s| s.now).unwrap_or_default();
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
                let now = self.snapshot.as_ref().map(|s| s.now).unwrap_or_default();
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
                let now = self.snapshot.as_ref().map(|s| s.now).unwrap_or_default();
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
                let horizontal = matches!(widget::text(&w, "direction"), "right" | "left");
                let length = widget::int(&w, if horizontal { "w" } else { "h" }, 0);
                let (frac, paint) = match bar::steps(frac, length)
                    .and_then(|frac| Ok((frac, self.rule_paint(&w, value, &rules)?)))
                {
                    Ok(made) => made,
                    Err(e) => return Drawn::Failed(e),
                };
                let content = bar::content(frac, paint);
                self.cached(&w, content, |r| r.bar_piece(&w, frac, paint))
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
                self.cached(&w, content, |r| r.gauge_piece(&w, frac, paint))
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
            other => return Drawn::Unported(format!("{other} widgets")),
        };
        match made {
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
            let key = sensor.unwrap_or("weather.code");
            let day = match key.strip_suffix(".code") {
                Some(stem) => format!("{stem}.day"),
                None => String::new(),
            };
            let mut daylight = snapshot.value(&day);
            if daylight == Value::None && key == "weather.code" {
                daylight = snapshot.value("weather.is_day");
            }
            let night = if daylight == Value::None {
                !(6..20).contains(&snapshot.now.hour)
            } else {
                !daylight.truthy()
            };
            return icons::weather_icon_name(&snapshot.value(key), night).to_string();
        }
        name.to_string()
    }

    /// What a frame of the theme holds beyond its widgets that is not drawn here yet: a screen
    /// (a plugin's, drawn under the widgets), the panel's look (its grid beneath them) and
    /// finishes other than flat (render/finish.py: lit, facet, acrylic). Rust says so for any of
    /// them, also where Python goes on without (a screen no plugin has, a finish a widget does
    /// not take): never a frame drawn without what Python draws.
    fn beyond_widgets(&self) -> Option<&'static str> {
        if self.theme.screen.is_some() {
            return Some("screens");
        }
        if widget::truthy(&self.theme.look, "grid") {
            return Some("looks");
        }
        let flat = |w: &Dict| match w.get("finish") {
            None => true,
            Some(Value::Str(name)) => name == FLAT,
            Some(_) => false,
        };
        if !self.widgets.iter().all(flat) {
            return Some("finishes");
        }
        None
    }

    /// render: the frame (RGB) of `snapshot`, and the widgets' boxes; Unported where it holds
    /// what is not drawn here yet.
    pub fn render(
        &mut self,
        snapshot: Snapshot,
    ) -> std::result::Result<(Image, IndexMap<String, [i64; 4]>), Unported> {
        if let Some(what) = self.beyond_widgets() {
            return Err(Unported(what.into()));
        }
        let snapshot = snapshot.finite();
        self.snapshot = Some(snapshot.clone());
        let mut boxes = IndexMap::new();
        let mut pieces: Pieces = Vec::new();
        let mut gone: HashMap<String, bool> = HashMap::new();
        let widgets = self.widgets.clone();
        for w in &widgets {
            if !widget::flag(w, "visible", true) {
                continue;
            }
            let module = self.modules.get(widget::text(w, "_module")).cloned();
            if w.contains_key("_card") && module.is_some() {
                return Err(Unported("card slots".into()));
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
            if widget::text(w, "type") == "bar" {
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
