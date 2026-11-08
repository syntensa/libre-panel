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
use crate::fan;
use crate::finish::{self, Finish};
use crate::gauge;
use crate::graph;
use crate::icons;
use crate::image;
use crate::kept::Kept;
use crate::list;
use crate::lit;
use crate::metric::{self, Shown};
use crate::moon;
use crate::motion;
use crate::piece::{Failed, Piece, Result};
use crate::roll;
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
/// What Rust says of a theme's picture it does not read: one Pillow may read (it knows more
/// kinds of picture) or fail at otherwise than Python goes on from.
pub(crate) const UNREAD: &str = "pictures Rust does not read";

/// A theme's picture Rust does not read (UNREAD).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct Unread;

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
    /// whether the background's picture is one Rust does not read (UNREAD)
    ground_unread: bool,
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
    /// the time the frame is drawn for (render's `now`: a download's running light)
    pub(crate) at: f64,
    /// your place (latitude, longitude), for the earth: the loop sets it (Python's
    /// renderer.place); None for none
    pub place: Option<(f64, f64)>,
    /// what moves from frame to frame (render/motion.py's Motion): the springs of the values
    /// that glide, the fades of threshold colours
    pub motion: motion::Motion,
    /// whether the frame moves on (Python's moving): a value gliding, a colour fading
    pub moving: bool,
    /// the panel's frame rate (the loop sets it; 0: not known)
    pub fps: f64,
    /// the theme's pictures fitted (_images), and the animated pictures' frames (_animations)
    pub(crate) pictures: RefCell<Kept<image::PictureKey, Option<Rc<Image>>>>,
    pub(crate) animations: Kept<image::AnimationKey, Option<Rc<image::Animation>>>,
    /// whether a slideshow's picture moves (_moves)
    pub(crate) moves: HashMap<String, bool>,
    /// when each animated picture first showed, by widget and picture (_began)
    pub(crate) began: HashMap<(String, String), f64>,
    /// each slideshow's pictures, and when its folders were read (_slide_lists)
    pub(crate) slide_lists: HashMap<String, (std::time::Instant, Vec<String>)>,
    /// what of a list is drawn now (_list_piece's part): all of it, its text or its levels
    pub(crate) list_part: std::cell::Cell<list::ListPart>,
}

/// VOLUME_GLIDE_MS: the volume's bars and rings, 95 % of a step after about a third of a
/// second.
const VOLUME_GLIDE_MS: i64 = 320;

/// _own_glide_ms: a widget's own glide (ms): its glide_ms, else the volume's (0 for its number:
/// at once), else None (the theme's).
pub(crate) fn own_glide_ms(w: &Dict) -> Option<i64> {
    let own = widget::int(w, "glide_ms", 0);
    if own > 0 {
        return Some(own);
    }
    if widget::text(w, "sensor") == metric::VOLUME {
        return Some(if widget::text(w, "type") == "metric" {
            0
        } else {
            VOLUME_GLIDE_MS
        });
    }
    None
}

/// _spring: whose glide a widget follows: its sensor's, with the bars, rings and numbers of it;
/// one with a glide of its own follows one of its own.
pub(crate) fn spring_key(w: &Dict) -> String {
    let sensor = widget::text(w, "sensor");
    match own_glide_ms(w) {
        Some(own) if own != 0 => format!("{sensor}\0{own}"),
        _ => sensor.to_string(),
    }
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
            ground_unread: false,
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
            at: 0.0,
            place: None,
            motion: motion::Motion::default(),
            moving: false,
            fps: 0.0,
            pictures: RefCell::new(Kept::new(image::IMAGE_BYTES)),
            animations: Kept::new(image::ANIMATION_BYTES),
            moves: HashMap::new(),
            began: HashMap::new(),
            slide_lists: HashMap::new(),
            list_part: std::cell::Cell::new(list::ListPart::All),
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
    pub(crate) fn now(&self) -> Moment {
        self.snapshot
            .as_ref()
            .map_or_else(|| Snapshot::default().now, |s| s.now)
    }

    /// The fold of the frame's time.
    pub(crate) fn fold(&self) -> bool {
        self.snapshot.as_ref().is_some_and(|s| s.fold)
    }

    /// _time: the time in the widget's time zone (empty: the computer's): the frame's time put
    /// into the zone (astimezone); the frame's own where no zone has the name (warned, as
    /// timezones.zone finds none).
    fn time_of(&self, w: &Dict) -> std::result::Result<Moment, Drawn> {
        let now = self.now();
        let name = widget::text(w, "timezone");
        if name.is_empty() {
            return Ok(now);
        }
        let Some(zone) = lp_py::zones::named(name) else {
            if lp_py::zoneinfo::exists(name) {
                return Err(Drawn::Unported(
                    "a time zone's file Rust does not read".into(),
                ));
            }
            self.warn(format!(
                "unknown time zone {} (widget {})",
                lp_py::repr(name),
                lp_py::repr(widget::text(w, "id"))
            ));
            return Ok(now);
        };
        let Some(local) = lp_py::zones::local() else {
            return Err(Drawn::Unported(
                "another place's time without the computer's own time zone".into(),
            ));
        };
        lp_py::zones::astimezone(local, &zone, &now, self.fold())
            .ok_or_else(|| Drawn::Unported("a time in a time zone that Python fails at".into()))
    }

    /// _clock_format: the clock's text at `when`: a hundred characters at most, "--:--" where
    /// strftime refuses its format, in capitals where it asks.
    fn clock_format(w: &Dict, when: &Moment) -> std::result::Result<String, Drawn> {
        let text: String = match lp_i18n::format_date(when, widget::text(w, "format")) {
            Ok(text) => text.chars().take(100).collect(),
            Err(lp_i18n::DateError::ValueError(_)) => "--:--".into(),
            Err(lp_i18n::DateError::Unported(what)) => return Err(Drawn::Unported(what.into())),
            Err(lp_i18n::DateError::Failed(e)) => return Err(Drawn::Failed(Failed(e))),
        };
        Ok(if widget::truthy(w, "uppercase") {
            lp_py::upper(&text)
        } else {
            text
        })
    }

    /// _glide_s: how long a value takes to a new reading (the theme's animation.smoothing_ms,
    /// or the widget's own glide_ms; the volume's number at once); 0 in a still frame.
    pub(crate) fn glide_s(&self, w: Option<&Dict>) -> f64 {
        if !self.options.animate {
            return 0.0;
        }
        let own = w.map_or(Some(0), own_glide_ms);
        if let Some(own) = own.filter(|&own| own > 0) {
            return own as f64 / 1000.0;
        }
        if own == Some(0) && w.is_some_and(|w| widget::text(w, "sensor") == metric::VOLUME) {
            return 0.0;
        }
        self.theme.smoothing_ms.max(0) as f64 / 1000.0
    }

    /// _lead: the time between two planned frames (0: not known).
    pub(crate) fn lead(&self) -> f64 {
        if self.fps > 0.0 {
            1.0 / self.fps
        } else {
            0.0
        }
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

    /// _read_picture: a picture of the theme's folder, upright (EXIF) and in RGBA; None where
    /// Python's reading fails as at a file that is not there or cannot be read (a folder, say)
    /// or one outside the theme: OSError and ThemeError, which it catches (warned). A file
    /// Rust does not read: Err(UNREAD), as Pillow may read it (it knows more kinds) or fail at
    /// it otherwise than with an OSError (an IndexError, say, which Python does not catch).
    pub(crate) fn read_picture(&self, rel: &str) -> std::result::Result<Option<Image>, Unread> {
        let Some(root) = self.theme.root.clone() else {
            return Ok(None);
        };
        if rel.is_empty() {
            return Ok(None);
        }
        if rel.contains('\0') {
            return Err(Unread); // (a path with a NUL: Python's ValueError, which it does not catch)
        }
        let read = lp_theme::model::resolve_asset(&root, &Value::from(rel))
            .map_err(|e| e.msg.clone())
            .and_then(|path| std::fs::read(&path).map_err(|e| e.to_string()));
        match read {
            Ok(data) => lp_decode::open(&data, Mode::RGBA)
                .map(Some)
                .map_err(|_| Unread),
            Err(e) => {
                self.warn(format!(
                    "image {} could not be loaded ({e})",
                    lp_py::repr(rel)
                ));
                Ok(None)
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
            Ok(Some(image)) => Some(image.fit((w, h), Filter::Bicubic, 0.0, (0.5, 0.5))?),
            Ok(None) => None,
            Err(Unread) => {
                self.ground_unread = true;
                None
            }
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
    pub(crate) fn live_picture(&self, name: &str) -> Option<&Image> {
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
                let now = match self.time_of(&w) {
                    Ok(now) => now,
                    Err(drawn) => return drawn,
                };
                let text = match Self::clock_format(&w, &now) {
                    Ok(text) => text,
                    Err(drawn) => return drawn,
                };
                let colour = widget::text(&w, "color").to_string();
                let resting = self.text_widget(&w, text.clone(), colour.clone());
                // (in a moving frame, for digits_seconds after the second turns, what changed
                // rolls or fades from the text of the second before)
                let kind = widget::optional(&w, "digits").unwrap_or("jump");
                let seconds = widget::number(&w, "digits_seconds", roll::DIGITS_S);
                let p = f64::from(now.microsecond) / 1e6 / seconds;
                if resting.is_err()
                    || kind == "jump"
                    || !self.options.animate
                    || !(0.0 < seconds && seconds <= 1.0)
                    || p >= 1.0
                {
                    resting
                } else {
                    // (none before the first second of the year 1: Python's OverflowError)
                    let Some(earlier) = now.second_before() else {
                        return Drawn::Failed(Failed("date value out of range".into()));
                    };
                    let before = match Self::clock_format(&w, &earlier) {
                        Ok(text) => text,
                        Err(drawn) => return drawn,
                    };
                    if before == text || before.chars().count() != text.chars().count() {
                        resting
                    } else {
                        let e = 1.0 - (1.0 - p).powf(3.0);
                        match self.changing(&w, &before, &text, &colour, e, kind) {
                            Ok(None) => resting,
                            Ok(Some(piece)) => {
                                // (never kept: it is another one every frame)
                                self.moving = true;
                                self.effects(&w, piece).map(Rc::new)
                            }
                            Err(e) => Err(e),
                        }
                    }
                }
            }
            "analog" => {
                let w = w.clone();
                let now = match self.time_of(&w) {
                    Ok(now) => now,
                    Err(drawn) => return drawn,
                };
                let seconds = widget::truthy(&w, "seconds");
                let kind = widget::optional(&w, "hand").unwrap_or("tick");
                if self.options.animate && seconds && kind != "tick" {
                    return Drawn::Unported("clocks whose second hand glides or goes round".into());
                }
                // (the hour on the dial, the minute and the second, -1 where none is shown)
                let hands = [
                    i64::from(now.hour % 12),
                    i64::from(now.minute),
                    if seconds { i64::from(now.second) } else { -1 },
                ];
                let content = Value::List(hands.iter().map(|&v| Value::int(v)).collect());
                self.cached(&w, content, |r| {
                    r.analog_piece(&w, hands[0], hands[1], hands[2])
                })
            }
            "earth" => {
                let w = w.clone();
                return self.earth_widget(&w);
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
                if !["min", "max", "glide_ms"]
                    .iter()
                    .all(|k| widget::exact(&w, k))
                {
                    return Drawn::Unported("numbers a float does not hold as they are".into());
                }
                let snapshot = self.snapshot.clone().unwrap_or_default();
                // (in a moving frame on its glide to the reading)
                let value = self.eased(&w, number(&snapshot.value(widget::text(&w, "sensor"))));
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
                let numbers = ["min", "max", "start_angle", "end_angle", "glide_ms"];
                if !numbers.iter().all(|k| widget::exact(&w, k)) {
                    return Drawn::Unported("numbers a float does not hold as they are".into());
                }
                let snapshot = self.snapshot.clone().unwrap_or_default();
                // (in a moving frame on its glide to the reading)
                let value = self.eased(&w, number(&snapshot.value(widget::text(&w, "sensor"))));
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
                let snapshot = self.snapshot.clone().unwrap_or_default();
                // (_metric: whether this metric moves, apart from what moved before it)
                let before = std::mem::replace(&mut self.moving, false);
                let (text, colour) = match self.metric_text(&w, &snapshot) {
                    Ok(Shown::Text(text, colour)) => (text, colour),
                    Ok(Shown::Unported(what)) => return Drawn::Unported(what.into()),
                    Err(e) => {
                        self.moving = before || self.moving;
                        return Drawn::Failed(e);
                    }
                };
                let drawn = self.text_widget(&w, text, colour);
                if !self.moving && drawn.is_ok() {
                    // (its glide and colour told it was drawn now, as _settled tells them: also
                    // where it said "Mute" instead)
                    self.metric_settled(&w, &snapshot);
                }
                self.moving = before || self.moving;
                drawn
            }
            "image" => {
                let w = w.clone();
                return self.image_widget(&w);
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
                if self.options.animate && fans {
                    return Drawn::Unported("lists whose fans turn".into());
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
                } else if !(glide && self.options.animate) {
                    self.cached(&w, list::rows_key(&rows), |r| r.list_piece(&w, &rows))
                } else {
                    // gliding bars: drawn anew while they move, on their own; the text stays as
                    // it is, laid over them (where it meets no bar, Python lays it over the
                    // frame apart from them: the same pixels)
                    let text = self.cached(&w, list::texts_key(&rows), |r| {
                        r.list_part_piece(&w, &rows, list::ListPart::Text)
                    });
                    let text = match text {
                        Ok(text) => text,
                        Err(e) => return Drawn::Failed(e),
                    };
                    let mut apart = w.clone();
                    let id = format!("{}\0levels", widget::text(&w, "id"));
                    apart.insert("id".into(), id.into());
                    self.cached(&apart, list::rows_key(&rows), |r| {
                        r.list_part_piece(&w, &rows, list::ListPart::Levels)
                    })
                    .and_then(|levels| Ok(Rc::new(list::joined(&levels, &text)?)))
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
            "moon" => {
                let w = w.clone();
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let Some(phase) = number(&snapshot.value(widget::text(&w, "sensor"))) else {
                    return Drawn::Nothing;
                };
                let phase = lp_py::round_digits(lp_py::num::float_div_mod(phase, 1.0).1, 3);
                if widget::int(&w, "size", 64) > moon::LARGEST {
                    return Drawn::Unported("moons larger than 4096 px".into());
                }
                if self.pack(&w) == "classic" {
                    self.cached(&w, Value::Float(phase), |r| r.moon_classic(&w, phase))
                } else if self.options.animate {
                    // (its halo breathes where the panel moves: a moving frame)
                    return Drawn::Unported("moons whose halo breathes".into());
                } else {
                    let level = Value::int(moon::halo_level(phase, None));
                    let content = Value::List(vec![Value::Float(phase), level]);
                    self.cached(&w, content, |r| r.moon_piece(&w, phase))
                }
            }
            "fan" => {
                let w = w.clone();
                // (fan_choices.for_widget with none of the person's choices: the widget's look
                // and paint, else Noctua's G2 in the theme's colour)
                let look = match widget::text(&w, "look") {
                    "auto" => fan::DEFAULT_LOOK,
                    other => other,
                };
                if look != fan::DEFAULT_LOOK {
                    return Drawn::Unported("fans of other looks".into());
                }
                if widget::text(&w, "paint") == "own" {
                    return Drawn::Unported("fans in their look's own colours".into());
                }
                let ring = match widget::text(&w, "frame") {
                    "ring" => true,
                    "none" => false,
                    _ => return Drawn::Unported("fans in a square frame".into()),
                };
                if self.options.animate {
                    return Drawn::Unported("fans that turn".into());
                }
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let rpm = number(&snapshot.value(widget::text(&w, "sensor")));
                // (an int of the theme's as Python's sums take it, as a float: one past a
                // float's range fails them where there is a reading to take it to, and is
                // not looked at where there is none)
                let max_rpm = match w.get("max_rpm") {
                    Some(Value::Int(n)) => match lp_py::num::int_to_float(n) {
                        Ok(most) => most,
                        Err(e) if rpm.is_some() => return Drawn::Failed(Failed(e.msg)),
                        Err(_) => f64::INFINITY,
                    },
                    _ => widget::number(&w, "max_rpm", 3000.0),
                };
                // (a still frame shows it as Rotor.settle has it: standing as fast as it runs)
                let rotor = fan::Rotor::settled(widget::text(&w, "id"), rpm, max_rpm);
                let colour = match self.colour_of(&w, "color") {
                    Ok(Some([r, g, b, _])) => [r, g, b].map(|v| v.clamp(0, 255) as u8),
                    Ok(None) => {
                        return Drawn::Failed(Failed(
                            "'NoneType' object is not subscriptable".into(),
                        ))
                    }
                    Err(e) => return Drawn::Failed(e),
                };
                let (x, y) = (widget::int(&w, "x", 0), widget::int(&w, "y", 0));
                let (fw, fh) = (widget::int(&w, "w", 48), widget::int(&w, "h", 48));
                let (size, _margin, canvas) = fan::geometry(fw, fh);
                let content = Value::List(vec![
                    Value::List(colour.iter().map(|&c| Value::int(i64::from(c))).collect()),
                    Value::int(size),
                    Value::int(rotor.phase() as i64),
                    Value::int(rotor.blur()),
                    Value::int(rotor.dim()),
                    Value::int(rotor.steps()),
                    Value::Bool(ring),
                ]);
                let dir = fan::pictures_dir(&self.env.fonts_dir);
                self.cached(&w, content, |_| {
                    // (its ring as it is, the rotor over it, the square in the middle of its box)
                    let image = fan::picture(&dir, colour, size, &rotor, ring)?;
                    let at = (
                        x + (fw - canvas).div_euclid(2),
                        y + (fh - canvas).div_euclid(2),
                    );
                    let mut piece = Piece::new(image, at.0, at.1);
                    piece.hit = Some([x, y, fw, fh]);
                    Ok(piece)
                })
            }
            "downloads" => {
                let w = w.clone();
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let lit = finish::of(&w) == Finish::LitDownloads;
                return self.download(&w, lit, &snapshot, self.at);
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
        if self.ground_unread {
            return Some(UNREAD);
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
        self.at = now;
        if let Some(what) = self.beyond_widgets() {
            return Err(Unported(what.into()));
        }
        if !self.slots.is_empty() && self.options.animate {
            // (a moving frame warms every card up and rolls from one to the next)
            return Err(Unported("card slots in a moving frame".into()));
        }
        let snapshot = snapshot.finite();
        self.snapshot = Some(snapshot.clone());
        // (Motion.begin: the springs of values long gone go, the others forget old pieces)
        self.moving = false;
        let (glide, lead) = (self.glide_s(None), self.lead());
        let readings = |key: &str| {
            snapshot
                .readings
                .get(key)
                .and_then(|r| motion::number(&r.value))
        };
        self.motion.begin(&readings, now, glide, lead);
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
