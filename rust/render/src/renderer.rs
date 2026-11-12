//! A theme and a sensor snapshot made a frame (render/renderer.py's Renderer): every widget drawn
//! into its own layer, its effects applied, the layers laid on the theme's ground.

use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use indexmap::IndexMap;
use lp_decode::Raised;
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
use crate::slot::{OnShow, Slot};
use crate::snapshot::{need_holds, needs_of, number, Moment, Snapshot};
use crate::sunpath;
use crate::symbols;
use crate::turn::{self, Turn};
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

/// The part of the profile a widget's drawing counts in (prof.rs).
fn prof_name(kind: &str) -> &'static str {
    match kind {
        "graph" => "graph",
        "text" => "text",
        "metric" => "metric",
        "clock" => "clock",
        "bar" => "bar",
        "gauge" => "gauge",
        "list" => "list",
        "icon" => "icon",
        "rect" => "rect",
        "image" => "image",
        "fan" => "fan",
        "weather" => "weather",
        _ => "other widget",
    }
}

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

/// Why a theme's picture is not read as Python reads it: Rust cannot say what Pillow makes of
/// it (UNREAD), or Python's reading raises what it does not catch (its text: the widget fails).
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Unread {
    Unported,
    Raised(String),
}

impl From<Unread> for Drawn {
    fn from(why: Unread) -> Drawn {
        match why {
            Unread::Unported => Drawn::Unported(UNREAD.into()),
            Unread::Raised(text) => Drawn::Failed(Failed(text)),
        }
    }
}

pub(crate) enum Drawn {
    Piece(Rc<Piece>),
    /// nothing to draw (Python's None: no piece, no box)
    Nothing,
    Failed(Failed),
    Unported(String),
}

/// The widgets a renderer draws, kept behind Rcs.
pub type Widgets = Rc<Vec<Rc<Dict>>>;

/// A box as getbbox gives it: left, top, right, bottom.
pub type Bbox = (f64, f64, f64, f64);

pub struct Renderer {
    pub theme: Theme,
    pub(crate) env: Env,
    pub options: Options,
    pub(crate) dither: bool,
    /// the widgets to draw: the theme's, its modules replaced by their parts
    /// (kept behind Rcs: a frame walks them without copying them)
    pub widgets: Widgets,
    /// the box of each module, for the editor
    pub module_boxes: IndexMap<String, [i64; 4]>,
    /// the theme's modules by id
    modules: IndexMap<String, Rc<Dict>>,
    /// the background picture at the theme's size (with its gradient), None for none
    background: Option<Image>,
    /// whether the background's picture is one Rust does not read (UNREAD)
    ground_unread: bool,
    /// frames start from an opaque colour: pieces go on with a paste in place, in RGB
    opaque: bool,
    mode: Mode,
    /// the ground of the frames, made once (the background changes it: made again then)
    ground_kept: Option<Image>,
    /// the same, shared with the engine's frames of layers (their ground picture)
    ground_shared: Option<Rc<Image>>,
    /// the frame composed last and its pieces: the next frame composes anew only where its
    /// pieces differ (the same pixels as composing all of it)
    composed: Option<(Image, Pieces)>,
    /// what lies under the pieces that change, by region (_underlays)
    underlays: compose::Underlays,
    /// the frames render_shared hands out, two in turn: the one shown last stays the panel's
    /// while the next is laid into the other, there only where the frame changed since
    frames: [Option<Rc<Image>>; 2],
    /// for each of `frames`, where the frame changed since it was laid (None: anywhere)
    behind: [Option<Vec<compose::Area>>; 2],
    /// which of `frames` is laid next
    turn: usize,
    /// where the frame drawn last differs from the one before it (None: anywhere, or not known)
    pub changed: Option<Vec<compose::Area>>,
    /// this renderer among all made, and how many frames it drew: a frame's `changed` holds
    /// for the frame drawn just before it by the same renderer
    pub serial: u64,
    pub renders: u64,
    /// the person's choice of the theme's switches (one not chosen is as the theme has it)
    pub switches: IndexMap<String, bool>,
    /// the readings it drew last (the cover's colour)
    snapshot: Option<Rc<Snapshot>>,
    warnings: RefCell<Vec<String>>,
    /// each widget's finished piece, while it and what it shows stay the same
    pub(crate) cache: HashMap<String, (Value, Rc<Piece>)>,
    /// the fonts by name and size, opened once
    pub(crate) fonts: RefCell<HashMap<(String, i64), Rc<FallbackFont>>>,
    /// the box the digits fill in each of them (_digit_bands)
    pub(crate) digit_bands: RefCell<HashMap<(String, i64), Bbox>>,
    /// what metal lettering mirrors: the sky (the palette's accent) and the ground
    pub(crate) world: ([u8; 3], [u8; 3]),
    /// the finishes' letters by font, character, quarter pixel and style, built once
    pub(crate) facet_glyphs: crate::text::FacetGlyphs,
    /// and coloured, kept while they hold at most PATCH_BYTES
    pub(crate) facet_patches: crate::text::FacetPatches,
    /// whether a widget of `widgets` has a finish not drawn here, found once for those
    /// widgets (held, so that another list is never taken for them)
    unported_finish: RefCell<Option<(Widgets, bool)>>,
    /// the look of the weather symbols the person chose (config.toml's symbols: the app sets
    /// it; vivid where it does not, as Python's renderer has it)
    pub symbols: String,
    /// each graph's own range as it glides (_ranges), by id: its end dot lies in it
    pub(crate) ranges: HashMap<String, graph::Glide>,
    /// each graph's strip in video mode (_strips), by id, and how many were drawn
    pub(crate) strips: HashMap<String, crate::timeline::Strips>,
    pub(crate) strip_count: u64,
    /// lit rings' grooves, their whole sweeps lit and their starts' light, kept (lit.py's
    /// _PICTURES for rings)
    pub(crate) ring_pictures: RefCell<HashMap<String, Rc<Image>>>,
    /// the letters' pictures kept (_glyphs)
    pub(crate) glyphs: RefCell<crate::text::Glyphs>,
    /// graphs that step a reading at a time and rest between (config.toml's graph_steps: the
    /// loop sets it), and when the reading before the last came (new_sample)
    pub graph_steps: bool,
    pub sample_before: Option<f64>,
    /// whether a reading came since the renderer was made
    pub(crate) sampled: bool,
    /// the slot of every module that shows one card of several, by its id (_slots)
    slots: IndexMap<String, Slot>,
    /// whether this frame's slots have chosen their cards
    slots_begun: bool,
    /// whether their cards have been drawn once (_slots_warm)
    slots_warm: bool,
    /// the time the frame is drawn for (render's `now`: a download's running light)
    pub(crate) at: f64,
    /// your place (latitude, longitude), for the earth: the loop sets it (Python's
    /// renderer.place); None for none
    pub place: Option<(f64, f64)>,
    /// what the person chose for the fans (config.toml's [hardware]: the app sets it, Python's
    /// renderer.fan_choices); none chosen: the widgets' own, else Noctua's G2 in the theme's
    /// colour
    pub fan_choices: fan::Choices,
    /// what moves from frame to frame (render/motion.py's Motion): the springs of the values
    /// that glide, the fades of threshold colours
    pub motion: motion::Motion,
    /// whether the frame moves on (Python's moving): a value gliding, a colour fading
    pub moving: bool,
    /// the panel's frame rate (the loop sets it; 0: not known)
    pub fps: f64,
    /// video mode: every frame reaches the panel (the loop sets it; Python's continuous)
    pub continuous: bool,
    /// whether a graph's window cut from its strip may leave its pixels out (Piece::left_out):
    /// where the frames go to an engine that cuts it itself
    pub left_out_windows: bool,
    /// whether a lit ring of one colour may go as what an engine makes it of (Piece::ring, its
    /// pixels left out): where the frames go to an engine that makes rings (render/lit.py's
    /// renderer.native_rings)
    pub native_rings: bool,
    /// how many rings went so (for the checks)
    pub native_rings_made: std::cell::Cell<u64>,
    /// whether a lit bar may go as what an engine makes it of (Piece::bar, its pixels and its
    /// glow's left out): where the frames go to an engine that makes bars (render/lit.py's
    /// renderer.native_bars); how many went so; the bars' bases kept
    pub native_bars: bool,
    pub native_bars_made: std::cell::Cell<u64>,
    pub(crate) bar_bases: RefCell<HashMap<String, Rc<lp_devices::scene::BarBase>>>,
    /// whether a fan may go as the pictures an engine lays it from, which it makes itself
    /// (Piece::laid, its pixels left out): where the frames go to an engine that makes fans
    /// (renderer.native_fans and native_laid); how many went so
    pub native_fans: bool,
    pub native_fans_made: std::cell::Cell<u64>,
    /// when the last reading came and the seconds to the next (Python's sample, the loop's)
    pub sample: (f64, f64),
    /// what draws the plugins' widget types and screen (the app's plugin host); None: a frame
    /// that holds one is not made here
    pub plugins: Option<Rc<dyn crate::plugin::Plugins>>,
    /// the frame the panel showed last and the toast on show with its age (the loop sets them,
    /// for the plugins: Python's renderer.shown and renderer.toast)
    pub shown: Option<Rc<Image>>,
    pub toast: Option<(Rc<crate::overlays::Toast>, f64)>,
    /// the screen's last picture and its piece (_screen_last): the same picture is not
    /// composed again
    screen_last: Option<(Rc<Image>, Rc<Piece>)>,
    /// the theme's pictures fitted (_images), and the animated pictures' frames (_animations)
    pub(crate) pictures: RefCell<Kept<image::PictureKey, Option<Rc<Image>>>>,
    pub(crate) animations: Kept<image::AnimationKey, Option<Rc<image::Animation>>>,
    /// whether a slideshow's picture moves (_moves)
    pub(crate) moves: HashMap<String, bool>,
    /// when each animated picture first showed, by widget and picture (_began)
    pub(crate) began: HashMap<(String, String), f64>,
    /// what each slideshow with a picture that moves shows (_shows): the picture's place in
    /// it, when it began and when it ends
    pub(crate) shows: HashMap<String, (i64, f64, f64)>,
    /// each slideshow's pictures, and when its folders were read (_slide_lists)
    pub(crate) slide_lists: HashMap<String, (std::time::Instant, Vec<String>)>,
    /// what of a list is drawn now (_list_piece's part): all of it, its text or its levels
    pub(crate) list_part: std::cell::Cell<list::ListPart>,
    /// how each fan turns in a moving frame (_rotors): a fan widget's by its id, a list's fans
    /// by the list's id and the item's key
    rotors: HashMap<String, fan::Turning>,
    /// where the blades of each sensor's fans stand (beats: Beats.of), by the sensor, the frame
    /// rate and the blades of the look
    beats: HashMap<(String, u64, i64), fan::Beat>,
    /// the faces of clocks whose second hand moves, without it (_analog_parts)
    pub(crate) analog_parts: crate::analog::Faces<Rc<Image>>,
    /// their shadow and glow where the face is not see-through (_analog_halos)
    pub(crate) analog_halos: crate::analog::Faces<Option<(Rc<Image>, i64)>>,
    /// the globes that turn, by id (_earths)
    pub(crate) earths: HashMap<String, crate::earth::Globe>,
    /// when something wants a new frame, not before (Python's due: a turning globe's next
    /// step); None for nothing
    pub due: Option<f64>,
    /// the pieces of turned widgets, turned, by id: as they were and how they were turned
    /// (_turned): a piece turned as it was the frame before is that frame's again
    turned: HashMap<String, (Rc<Piece>, Turn, Rc<Piece>)>,
    /// what goes over a widget's piece as pieces of their own, by its id, by name (_over: a
    /// list's fans, its names and numbers over its gliding bars)
    pub(crate) over: HashMap<String, Vec<(String, Rc<Piece>)>>,
    /// where the bars of a list's levels lie as they are drawn (_Levels.boxes), and those of the
    /// levels just drawn (in their layer; None where they say nothing: cells, no rows)
    pub(crate) bars_laid: RefCell<Vec<list::Corners>>,
    pub(crate) bars_drawn: RefCell<Option<Vec<list::Corners>>>,
    /// each list's levels with where their bars lie (their layer's info["bars"])
    level_bars: HashMap<String, (Rc<Piece>, Vec<list::Corners>)>,
    /// where ffmpeg is, for the videos (the app finds it as devices/h264.find_ffmpeg does);
    /// None: Rust leaves a frame with a video to Python
    pub ffmpeg: Option<std::path::PathBuf>,
    /// a picture under the theme instead of its own background picture: the panel's part of
    /// one across all panels (arrangement.py), fitted to the theme's size (set_backdrop)
    backdrop: Option<Image>,
    /// the frames a second a video is decoded at: the panel's (the loop sets it, as Python's
    /// renderer.video_fps); None or 0: moving.VIDEO_FPS
    pub video_fps: Option<u32>,
    /// videos decoded at their keyframes alone (the app's picture of a video the panel takes
    /// from its file: renderer.video_keys)
    pub video_keys: bool,
    /// the videos playing, by what they are read as (_feeds), and what ffmpeg says of each
    /// (_video_infos)
    pub(crate) feeds: HashMap<image::VideoKey, crate::video::VideoFeed>,
    pub(crate) video_infos: HashMap<String, crate::video::VideoInfo>,
    /// the video of this frame (_video_drawn): its widget's id, its file, the widget and its
    /// box
    pub(crate) video_drawn: Option<(String, std::path::PathBuf, Dict, [i64; 4])>,
    /// the video of this frame where the frame is that video alone (full_video): its file, how
    /// it is fitted and its speed; [video] direct has the panel take it from its file then
    pub full_video: Option<(std::path::PathBuf, String, f64)>,
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

/// A frame shared (render_shared), and the boxes its widgets took.
pub type Shared = (Rc<Image>, IndexMap<String, [i64; 4]>);

/// A frame drawn: composed here, or as its layers for an engine that composes them.
pub enum Drawing {
    Frame(Rc<Image>),
    Scene(crate::scene::SceneFrame),
}

impl Renderer {
    pub fn new(theme: Theme, env: &Env, options: Options) -> Result<Renderer> {
        let expanded = lp_theme::modules::expand(&theme, env).map_err(|e| Failed(e.msg.clone()))?;
        let modules = theme
            .widgets
            .iter()
            .filter(|w| widget::text(w, "type") == "module")
            .map(|w| (widget::text(w, "id").to_string(), Rc::new(w.clone())))
            .collect();
        let mut renderer = Renderer {
            theme,
            env: env.clone(),
            options,
            dither: options.dither,
            // (finish.py's attach: the widgets as their finishes keep them)
            widgets: Rc::new(
                (expanded.widgets.into_iter())
                    .map(|w| Rc::new(finish::kept(w)))
                    .collect(),
            ),
            module_boxes: expanded.boxes,
            modules,
            background: None,
            ground_unread: false,
            opaque: false,
            mode: Mode::RGBA,
            ground_kept: None,
            ground_shared: None,
            composed: None,
            underlays: compose::Underlays::default(),
            frames: [None, None],
            behind: [None, None],
            turn: 0,
            changed: None,
            serial: {
                static SERIAL: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
                SERIAL.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
            },
            renders: 0,
            switches: IndexMap::new(),
            snapshot: None,
            warnings: RefCell::new(Vec::new()),
            cache: HashMap::new(),
            fonts: RefCell::new(HashMap::new()),
            digit_bands: RefCell::new(HashMap::new()),
            world: ([0; 3], [0; 3]),
            facet_glyphs: RefCell::new(HashMap::new()),
            facet_patches: RefCell::new(Kept::new(crate::text::PATCH_BYTES)),
            unported_finish: RefCell::new(None),
            symbols: crate::symbols::DEFAULT_PACK.to_string(),
            ranges: HashMap::new(),
            strips: HashMap::new(),
            strip_count: 0,
            ring_pictures: RefCell::new(HashMap::new()),
            glyphs: RefCell::new(crate::text::Glyphs::default()),
            graph_steps: false,
            sample_before: None,
            sampled: false,
            slots: IndexMap::new(),
            slots_begun: false,
            slots_warm: false,
            at: 0.0,
            place: None,
            fan_choices: fan::Choices::default(),
            motion: motion::Motion::default(),
            moving: false,
            fps: 0.0,
            continuous: false,
            left_out_windows: false,
            native_rings: false,
            native_rings_made: std::cell::Cell::new(0),
            native_bars: false,
            native_bars_made: std::cell::Cell::new(0),
            bar_bases: RefCell::new(HashMap::new()),
            native_fans: false,
            native_fans_made: std::cell::Cell::new(0),
            sample: (0.0, 1.0),
            plugins: None,
            shown: None,
            screen_last: None,
            toast: None,
            pictures: RefCell::new(Kept::new(image::IMAGE_BYTES)),
            animations: Kept::new(image::ANIMATION_BYTES),
            moves: HashMap::new(),
            began: HashMap::new(),
            shows: HashMap::new(),
            slide_lists: HashMap::new(),
            list_part: std::cell::Cell::new(list::ListPart::All),
            rotors: HashMap::new(),
            beats: HashMap::new(),
            analog_parts: crate::analog::Faces::default(),
            analog_halos: crate::analog::Faces::default(),
            earths: HashMap::new(),
            due: None,
            turned: HashMap::new(),
            over: HashMap::new(),
            bars_laid: RefCell::new(Vec::new()),
            bars_drawn: RefCell::new(None),
            level_bars: HashMap::new(),
            ffmpeg: None,
            backdrop: None,
            video_fps: None,
            video_keys: false,
            feeds: HashMap::new(),
            video_infos: HashMap::new(),
            video_drawn: None,
            full_video: None,
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

    /// The slots' cards for this frame (Slot.begin: a change from one to the next on its way in
    /// a moving frame, a new track sliding in), and the colour of each one's frame: its module's
    /// own (a colour, a palette's, the cover's), else its card's, reached over half a second in
    /// a moving frame. The frame moves on while one of them does.
    fn begin_slots(&mut self, snapshot: &Snapshot, now: f64) -> std::result::Result<(), Unported> {
        let mut slots = std::mem::take(&mut self.slots);
        let mut begun = Ok(());
        let animate = self.options.animate;
        for slot in slots.values_mut() {
            match slot.begin(snapshot, now, animate) {
                Ok(moves) => self.moving |= moves,
                Err(what) => {
                    begun = Err(Unported(what));
                    break;
                }
            }
            if slot.pinned.is_some() {
                continue; // (drawn for the warm-up: its frame's colour as it was)
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
            let target = slot.target(own, snapshot);
            self.moving |= slot.accent(target, now, animate);
        }
        self.slots = slots;
        self.slots_begun = begun.is_ok();
        begun
    }

    /// _warm_slots: every card of every slot drawn once, whichever is on show, so that the first
    /// change finds its pieces made; each slot then forgets what it showed (the frames drawn on
    /// the way go, what they made stays).
    fn warm_slots(&mut self, snapshot: &Snapshot, now: f64) -> std::result::Result<(), Unported> {
        let ids: Vec<String> = self.slots.keys().cloned().collect();
        for id in ids {
            let cards = self.slots[&id].cards();
            for card in 0..cards {
                if let Some(slot) = self.slots.get_mut(&id) {
                    slot.pinned = Some(card);
                }
                self.render(snapshot.clone(), now)?;
            }
            if let Some(slot) = self.slots.get_mut(&id) {
                slot.settle();
            }
        }
        Ok(())
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

    /// A warning of what draws for it (the plugins'), with its own.
    pub fn warn_of(&self, message: String) {
        self.warn(message);
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

    /// context.image: a picture a service or a source published, as of the readings the frame
    /// was last drawn of; None for none.
    pub(crate) fn image(&self, key: &str) -> Option<&Image> {
        self.snapshot.as_ref()?.images.get(key)
    }

    /// The readings what is drawn over the frame takes its pictures of, as a frame drawn of
    /// them leaves them (renderer.snapshot).
    pub fn set_snapshot(&mut self, snapshot: Snapshot) {
        self.snapshot = Some(Rc::new(snapshot));
    }

    /// _reads: the sensor keys (and patterns) the widgets read: their sensors, marks and fine
    /// courses, what their conditions read (a switch of the theme is none), a weather widget's
    /// field, a list's items and the pictures an image widget takes from a source.
    pub fn reads(&self) -> std::collections::HashSet<String> {
        let mut keys = std::collections::HashSet::new();
        for w in self.widgets.iter() {
            for field in ["sensor", "mark", "trace"] {
                let key = widget::text(w, field);
                if !key.is_empty() {
                    keys.insert(key.to_string());
                }
            }
            for need in widget::text(w, "needs").split(',') {
                let need = crate::snapshot::need_key(need);
                if !need.is_empty() && !need.starts_with(SWITCH) {
                    keys.insert(need.to_string());
                }
            }
            match widget::text(w, "type") {
                "weather" => {
                    keys.insert(format!("weather.{}", widget::text(w, "field")));
                }
                "list" => keys.extend(list::read_keys(widget::text(w, "items"))),
                "image" => {
                    // (_live_pictures: "@media.cover" as its src or on a line of its slides)
                    let slides = lp_py::splitlines(widget::text(w, "slides"));
                    for name in std::iter::once(widget::text(w, "src")).chain(slides) {
                        let name = lp_py::strip(name);
                        if let Some(key) = name.strip_prefix('@').filter(|k| !k.is_empty()) {
                            keys.insert(key.to_string());
                        }
                    }
                }
                _ => {}
            }
        }
        keys
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

    /// A fan in a moving frame (Rotor.advance with beats.of(sensor, fps, blades)): the fan
    /// `key` of `sensor`, its look of `blades` blades, at this frame's time with `rpm`; the frame
    /// moves on while it turns.
    pub(crate) fn turned(
        &mut self,
        key: &str,
        sensor: &str,
        blades: i64,
        rpm: Option<f64>,
        max_rpm: f64,
    ) -> fan::Rotor {
        // (the frame rate as a key: 0.0 and -0.0 are one, as in Python)
        let fps = if self.fps == 0.0 { 0.0 } else { self.fps };
        let beat = self
            .beats
            .entry((sensor.to_string(), fps.to_bits(), blades))
            .or_insert_with(|| fan::Beat::new(sensor));
        let turning = self
            .rotors
            .entry(key.to_string())
            .or_insert_with(|| fan::Turning::new(key));
        turning.advance(rpm, max_rpm, self.at, blades, beat);
        if turning.rotor.turning() {
            self.moving = true;
        }
        turning.rotor
    }

    /// _lead: the time between two planned frames (0: not known).
    pub(crate) fn lead(&self) -> f64 {
        if self.fps > 0.0 {
            1.0 / self.fps
        } else {
            0.0
        }
    }

    /// _range_now: a graph's own range at the frame's time, on its way to the one it glides
    /// to (in a still frame the one it was drawn in); None for one with min and max, or none
    /// drawn yet.
    pub(crate) fn range_now(&self, w: &Dict) -> Result<Option<graph::Range>> {
        if graph::fixed(w) {
            return Ok(None);
        }
        let Some(state) = self.ranges.get(widget::text(w, "id")) else {
            return Ok(None);
        };
        graph::glide_now(w, state, self.at, self.options.animate).map(Some)
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
    /// Python's reading raises what it catches, an OSError (a file that is not there or
    /// cannot be read, one Pillow cannot identify or fails at so) or a ThemeError (one outside
    /// the theme), warned. What else it raises fails the widget (Err: its text); a file Rust
    /// cannot say Pillow's answer to: Err(UNREAD).
    pub(crate) fn read_picture(&self, rel: &str) -> std::result::Result<Option<Image>, Unread> {
        let Some(root) = self.theme.root.clone() else {
            return Ok(None);
        };
        if rel.is_empty() {
            return Ok(None);
        }
        if rel.contains('\0') {
            // (resolve_asset's ValueError, which _read_picture does not catch)
            return Err(Unread::Raised(lp_theme::model::NUL_IN_PATH.into()));
        }
        let warn = |text: &str| {
            let rel = lp_py::repr(rel);
            self.warn(format!("image {rel} could not be loaded ({text})"));
        };
        let path = match lp_theme::model::resolve_asset(&root, &Value::from(rel)) {
            Ok(path) => path,
            Err(e) => {
                warn(&e.msg);
                return Ok(None);
            }
        };
        let data = match std::fs::read(&path) {
            Ok(data) => data,
            Err(e) => {
                warn(&lp_theme::model::os_error_of(&e, &path).1);
                return Ok(None);
            }
        };
        match lp_decode::open(&data, Mode::RGBA) {
            Ok(image) => Ok(Some(image)),
            Err(e) if e.raised == Raised::Unported => Err(Unread::Unported),
            Err(e) => {
                let text = e.text(&lp_py::repr(&path.to_string_lossy()));
                if !e.raised.is_os_error() {
                    return Err(Unread::Raised(text));
                }
                warn(&text);
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
        if let Some(backdrop) = &self.backdrop {
            let fitted = backdrop.fit((w, h), Filter::Bicubic, 0.0, (0.5, 0.5))?;
            return Ok(Some(fitted.convert(Mode::RGBA)?));
        }
        let rel = self.theme.background_image.clone().unwrap_or_default();
        let image = match self.read_picture(&rel) {
            Ok(Some(image)) => Some(image.fit((w, h), Filter::Bicubic, 0.0, (0.5, 0.5))?),
            Ok(None) => None,
            Err(Unread::Unported) => {
                self.ground_unread = true;
                None
            }
            // (Python's renderer is not made: what it raises)
            Err(Unread::Raised(text)) => return Err(Failed(text)),
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

    /// The picture under the theme instead of its own background picture (Renderer's
    /// backdrop): the background made again with it.
    pub fn set_backdrop(&mut self, backdrop: Option<Image>) -> Result<()> {
        self.backdrop = backdrop;
        self.background = self.load_background()?;
        self.ground_kept = None;
        self.ground_shared = None;
        self.composed = None;
        self.behind = [None, None];
        self.underlays.clear();
        Ok(())
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
    pub(crate) fn effects(&self, w: &Dict, mut piece: Piece) -> Result<Piece> {
        let opacity = widget::number(w, "opacity", 1.0);
        let scaled = effects::scale_alpha(&piece.layer, opacity)?;
        piece.hit = Some(piece.bounds());
        piece.light = effects::dimmed(&piece.light, opacity)?;
        if let Some(layer) = scaled {
            piece.layer = layer;
        }
        let shadow = self.shadow_tint(w)?;
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

    /// The colour of a widget's shadow (_halo's tint), None without one.
    pub(crate) fn shadow_tint(&self, w: &Dict) -> Result<Option<Rgba>> {
        match widget::optional(w, "shadow") {
            Some(s) if !s.is_empty() => self.color(Some(s), 1.0),
            _ => Ok(None),
        }
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
    fn draw(&mut self, w: &Rc<Dict>) -> Drawn {
        let made = match widget::text(w, "type") {
            "rect" => {
                let w = Rc::clone(w);
                self.cached(&w, Value::None, |r| r.rect_piece(&w))
            }
            "text" => {
                let w = Rc::clone(w);
                let text = self.text_of(&w);
                self.text_widget(&w, text, widget::text(&w, "color").to_string())
            }
            "icon" => {
                let w = Rc::clone(w);
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
                let w = Rc::clone(w);
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let key = format!("weather.{}", widget::text(&w, "field"));
                let text = match self.format_reading(&w, &key, snapshot.readings.get(&key)) {
                    Ok((text, _)) => text,
                    Err(e) => return Drawn::Failed(e),
                };
                self.text_widget(&w, text, widget::text(&w, "color").to_string())
            }
            "countdown" => {
                let w = Rc::clone(w);
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
                let w = Rc::clone(w);
                let now = match self.time_of(&w) {
                    Ok(now) => now,
                    Err(drawn) => return drawn,
                };
                let text = match Self::clock_format(&w, &now) {
                    Ok(text) => text,
                    Err(drawn) => return drawn,
                };
                let colour = widget::text(&w, "color").to_string();
                let resting = crate::prof::part("clock text", || {
                    self.text_widget(&w, text.clone(), colour.clone())
                });
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
                        let changing = crate::prof::part("clock roll", || {
                            self.changing(&w, &before, &text, &colour, e, kind)
                        });
                        match changing {
                            Ok(None) => resting,
                            Ok(Some(piece)) => {
                                // (never kept: it is another one every frame)
                                self.moving = true;
                                crate::prof::part("clock glow", || self.effects(&w, piece))
                                    .map(Rc::new)
                            }
                            Err(e) => Err(e),
                        }
                    }
                }
            }
            "analog" => {
                let w = Rc::clone(w);
                let now = match self.time_of(&w) {
                    Ok(now) => now,
                    Err(drawn) => return drawn,
                };
                let seconds = widget::truthy(&w, "seconds");
                let kind = widget::optional(&w, "hand").unwrap_or("tick");
                // (the hour on the dial, the minute and the second, -1 where none is shown)
                let hands = [
                    i64::from(now.hour % 12),
                    i64::from(now.minute),
                    if seconds { i64::from(now.second) } else { -1 },
                ];
                if kind != "tick" && hands[2] >= 0 && self.options.animate {
                    // (its second hand glides or goes round: laid onto the face it keeps)
                    self.analog_moving(&w, &now, kind)
                } else {
                    let content = Value::List(hands.iter().map(|&v| Value::int(v)).collect());
                    self.cached(&w, content, |r| {
                        r.analog_piece(&w, hands[0], hands[1], hands[2], false)
                    })
                }
            }
            "earth" => {
                let w = Rc::clone(w);
                return self.earth_widget(&w);
            }
            "calendar" => {
                let w = Rc::clone(w);
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
                let w = Rc::clone(w);
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let state = match snapshot.value(widget::text(&w, "sensor")) {
                    Value::Str(s) if matches!(s.as_str(), "working" | "waiting" | "idle") => s,
                    _ => "off".to_string(),
                };
                // (a sign at work or waiting moves, a step at a time)
                let at = if self.options.animate {
                    agent::step(&state, self.at)
                } else {
                    None
                };
                if at.is_some() {
                    self.moving = true;
                }
                let size = widget::int(&w, "size", 64).max(8);
                let mut colours = [[0i64; 4]; 3];
                for (colour, key) in colours.iter_mut().zip(["color", "color2", "muted"]) {
                    match self.colour_of(&w, key) {
                        Ok(c) => *colour = c.unwrap_or([0, 0, 0, 0]),
                        Err(e) => return Drawn::Failed(e),
                    }
                }
                let step = at.map_or(Value::None, Value::int);
                let mut content = vec![state.clone().into(), step, Value::int(size)];
                content.extend(colours.map(|c| Value::List(c.map(Value::int).to_vec())));
                let (x, y) = (widget::int(&w, "x", 0), widget::int(&w, "y", 0));
                self.cached(&w, Value::List(content), |_| {
                    let [a, b, c] = colours;
                    Ok(agent::piece(
                        x,
                        y,
                        size,
                        agent::picture(size, &state, at, a, b, c)?,
                    ))
                })
            }
            "sunpath" => {
                let w = Rc::clone(w);
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
                let w = Rc::clone(w);
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
                let w = Rc::clone(w);
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
                    // (one the engine makes is another piece than one drawn here)
                    let native = Value::Bool(self.native_rings);
                    let content = Value::List(vec!["lit".into(), content, native]);
                    self.cached(&w, content, |r| r.lit_gauge_piece(&w, frac, paint))
                } else {
                    self.cached(&w, content, |r| r.gauge_piece(&w, frac, paint))
                }
            }
            "metric" => {
                let w = Rc::clone(w);
                let snapshot = self.snapshot.clone().unwrap_or_default();
                // (_metric: whether this metric moves, apart from what moved before it)
                let before = std::mem::replace(&mut self.moving, false);
                let shown = crate::prof::part("metric text", || self.metric_text(&w, &snapshot));
                let (text, colour) = match shown {
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
                let w = Rc::clone(w);
                return self.image_widget(&w);
            }
            "graph" => {
                let w = Rc::clone(w);
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
                } else if self.continuous {
                    // (video mode: a window cut from the strip drawn ahead, every frame)
                    self.timeline(&w, &snapshot)
                } else {
                    let (values, slots) = graph::history_of(&snapshot, &w);
                    let numbers: Vec<f64> = values.iter().flatten().copied().collect();
                    let mut range = None;
                    if !numbers.is_empty() {
                        // (a range of its own glides from one frame to the next)
                        let (at, animate) = (self.at, self.options.animate);
                        match graph::glided_bounds(&w, &numbers, at, animate, &mut self.ranges) {
                            Ok((bounds, gliding)) => {
                                range = Some(bounds);
                                self.moving |= gliding;
                            }
                            Err(e) => return Drawn::Failed(e),
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
                let w = Rc::clone(w);
                // (bars gliding to a reading: motion; fans turn on their names in a moving
                // frame, and stand in a still one)
                let fans = widget::text(&w, "style") == "fans";
                let glide = widget::truthy(&w, "smooth") && widget::truthy(&w, "levels") && !fans;
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
                        Ok(piece) if !items.is_empty() => self.list_turning(&w, &piece, &items, hi),
                        other => other,
                    }
                } else if !(glide && self.options.animate) {
                    self.cached(&w, list::rows_key(&rows), |r| r.list_piece(&w, &rows))
                } else {
                    // gliding bars: drawn anew while they move, on their own; the text stays as
                    // it is, laid over them: over the frame apart from them where it meets no
                    // bar, else on them
                    let text = self.cached(&w, list::texts_key(&rows), |r| {
                        r.list_part_piece(&w, &rows, list::ListPart::Text)
                    });
                    let text = match text {
                        Ok(text) => text,
                        Err(e) => return Drawn::Failed(e),
                    };
                    let wid = widget::text(&w, "id").to_string();
                    let mut apart = (*w).clone();
                    apart.insert("id".into(), format!("{wid}\0levels").into());
                    self.bars_drawn.borrow_mut().take();
                    let levels = match self.cached(&apart, list::rows_key(&rows), |r| {
                        r.list_part_piece(&w, &rows, list::ListPart::Levels)
                    }) {
                        Ok(levels) => levels,
                        Err(e) => return Drawn::Failed(e),
                    };
                    let drawn = self.bars_drawn.borrow_mut().take();
                    if let Some(bars) = drawn {
                        self.level_bars.insert(wid.clone(), (levels.clone(), bars));
                    }
                    let bars = self
                        .level_bars
                        .get(&wid)
                        .filter(|(kept, _)| Rc::ptr_eq(kept, &levels))
                        .map(|(_, bars)| bars.as_slice());
                    let apart = !w.contains_key("_card")
                        && match list::text_apart(&w, &levels, &text, bars) {
                            Ok(apart) => apart,
                            Err(e) => return Drawn::Failed(e),
                        };
                    if apart {
                        // (laid over the bars by the frame)
                        self.over.insert(wid, vec![("text".to_string(), text)]);
                        Ok(levels)
                    } else {
                        list::joined(&levels, &text).map(Rc::new)
                    }
                }
            }
            "symbol" => {
                let w = Rc::clone(w);
                // (moving with the planned time of the frame, a step a 25th of a second)
                let live = widget::truthy(&w, "moving") && self.options.animate;
                let offset = widget::number(&w, "offset", 0.0);
                let step = live.then(|| lp_py::round((self.at + offset) * symbols::STEP));
                if live {
                    self.moving = true;
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
                    step.map_or(Value::None, Value::Float),
                ]);
                let t = step.map(|step| step / symbols::STEP);
                self.cached(&w, content, |r| r.symbol_piece(&w, &snapshot, lit, t))
            }
            "moon" => {
                let w = Rc::clone(w);
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
                } else {
                    // (its halo breathes where the panel moves, a step a tenth of a second: the
                    // picture made anew only when its strength changed, a few times a breath)
                    let step = self
                        .options
                        .animate
                        .then(|| lp_py::round(self.at * moon::MOON_STEP));
                    if step.is_some() {
                        self.moving = true;
                    }
                    let t = step.map(|step| step / moon::MOON_STEP);
                    let level = Value::int(moon::halo_level(phase, t));
                    let content = Value::List(vec![Value::Float(phase), level]);
                    self.cached(&w, content, |r| r.moon_piece(&w, phase, t))
                }
            }
            "fan" => {
                let w = Rc::clone(w);
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let sensor = widget::text(&w, "sensor");
                // (the widget's look and paint, else the person's for its sensor or all, else
                // Noctua's G2 in the theme's colour: fan_choices.for_widget)
                let (look, own) = self.fan_choices.for_widget(
                    sensor,
                    widget::text(&w, "look"),
                    widget::text(&w, "paint"),
                );
                let frame = fan::Frame::named(widget::text(&w, "frame"));
                let rpm = number(&snapshot.value(sensor));
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
                // (a moving frame turns it, Rotor.advance; a still one shows it as Rotor.settle
                // has it: standing as fast as it runs)
                let id = widget::text(&w, "id");
                let rotor = if self.options.animate {
                    self.turned(id, sensor, look.blades, rpm, max_rpm)
                } else {
                    fan::Rotor::settled(id, rpm, max_rpm)
                };
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
                // (laid by an engine of the pictures it makes itself, where it makes them and the
                // widget's effects need no pixels here: _laid_fan's native)
                let native = self.native_fans && fan::native(&w);
                let content = Value::List(vec![
                    Value::List(colour.iter().map(|&c| Value::int(i64::from(c))).collect()),
                    Value::int(size),
                    Value::int(rotor.phase() as i64),
                    Value::int(rotor.blur()),
                    Value::int(rotor.dim()),
                    Value::int(rotor.steps()),
                    Value::int(rotor.shades()),
                    Value::Str(format!("{frame:?}")),
                    Value::Str(look.name.to_string()),
                    Value::Bool(own),
                    Value::Bool(native),
                ]);
                let dir = fan::pictures_dir(&self.env.fonts_dir);
                self.cached(&w, content, |r| {
                    // (its frame, the rotor over it, the square in the middle of its box)
                    let at = (
                        x + (fw - canvas).div_euclid(2),
                        y + (fh - canvas).div_euclid(2),
                    );
                    let made = if native {
                        fan::made_parts(&dir, colour, size, &rotor, frame, look, own, id)?
                    } else {
                        None
                    };
                    let mut piece = match made {
                        Some(fan::Made { parts, kept, side }) => {
                            let mut piece = Piece::new(Image::blank(Mode::RGBA, 0, 0), at.0, at.1);
                            piece.left_out = Some((side, side));
                            piece.made = Some(crate::piece::Maker(Rc::new(move || {
                                fan::picture(&dir, colour, size, &rotor, frame, look, own).ok()
                            })));
                            piece.laid = Some(Rc::new(lp_devices::scene::LaidLayer {
                                w: side,
                                h: side,
                                parts,
                                kept,
                            }));
                            r.native_fans_made.set(r.native_fans_made.get() + 1);
                            piece
                        }
                        None => {
                            let image = fan::picture(&dir, colour, size, &rotor, frame, look, own)?;
                            Piece::new(image, at.0, at.1)
                        }
                    };
                    piece.hit = Some([x, y, fw, fh]);
                    Ok(piece)
                })
            }
            "downloads" => {
                let w = Rc::clone(w);
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let lit = finish::of(&w) == Finish::LitDownloads;
                return self.download(&w, lit, &snapshot, self.at);
            }
            other => {
                let Some(plugins) = self.plugins.clone() else {
                    return Drawn::Unported(format!("{other} widgets"));
                };
                let snapshot = self.snapshot.clone().unwrap_or_default();
                let frame = self.frame_info();
                return match plugins.widget(w, &snapshot, &frame) {
                    Ok(drawing) => {
                        self.moving |= drawing.moving;
                        drawing
                            .warnings
                            .into_iter()
                            .for_each(|said| self.warn(said));
                        drawing.made.map_or(Drawn::Nothing, Drawn::Piece)
                    }
                    Err(e) => Drawn::Failed(Failed(e)),
                };
            }
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

    /// How far `now` is from the last reading to the next, 0 to 1 (Renderer.progress).
    pub fn progress(&self, now: f64) -> f64 {
        let (at, interval) = self.sample;
        ((now - at) / interval).clamp(0.0, 1.0)
    }

    /// The name of the theme's screen ("" for none).
    fn screen_name(&self) -> String {
        self.theme
            .screen
            .as_ref()
            .map_or("", |s| widget::text(s, "name"))
            .to_string()
    }

    /// What a plugin's drawing is told of this frame.
    fn frame_info(&self) -> crate::plugin::Frame {
        crate::plugin::Frame {
            now: self.at,
            fps: self.fps,
            continuous: self.continuous,
            progress: self.progress(self.at),
            shown: self.shown.clone(),
            toast: self.toast.clone(),
        }
    }

    /// The screen's picture of this frame (_screen_image): None where the theme has none or no
    /// plugin draws it (with a warning where it failed).
    fn screen_image(&mut self, snapshot: &Snapshot) -> Option<Rc<Image>> {
        self.theme.screen.as_ref()?;
        let plugins = self.plugins.clone()?;
        match plugins.screen(snapshot, &self.frame_info()) {
            Ok(drawing) => {
                self.moving |= drawing.moving;
                drawing
                    .warnings
                    .into_iter()
                    .for_each(|said| self.warn(said));
                drawing.made
            }
            Err(e) => {
                let name = self.screen_name();
                self.warn(format!("screen {} failed: {e}", lp_py::repr(&name)));
                None
            }
        }
    }

    /// _screen_piece: the screen's picture as the piece beneath the widgets, in the theme's
    /// size (one of another size is resized, with a warning).
    fn screen_piece(&mut self, image: Rc<Image>) -> std::result::Result<Rc<Piece>, Unported> {
        if let Some((last, piece)) = &self.screen_last {
            if Rc::ptr_eq(last, &image) {
                return Ok(piece.clone()); // the same picture: nothing to compose again
            }
        }
        let (w, h) = (self.theme.width as usize, self.theme.height as usize);
        let mut layer = if image.mode() == Mode::RGBA {
            (*image).clone()
        } else {
            image
                .convert(Mode::RGBA)
                .map_err(|e| Unported(format!("a screen's picture: {e}")))?
        };
        if (layer.width(), layer.height()) != (w, h) {
            let name = self.screen_name();
            self.warn(format!(
                "screen {} draws {}x{}, not ({w}, {h})",
                lp_py::repr(&name),
                layer.width(),
                layer.height()
            ));
            layer = layer
                .resize(w, h, Filter::Bicubic, None, None)
                .map_err(|e| Unported(format!("a screen's picture: {e}")))?;
        }
        let piece = Rc::new(Piece::new(layer, 0, 0));
        self.screen_last = Some((image, piece.clone()));
        Ok(piece)
    }

    /// What a frame of the theme holds beyond its widgets that is not drawn here yet: a screen
    /// (a plugin's, drawn under the widgets), the panel's look (its grid beneath them) and the
    /// finishes Rust does not draw yet (render/finish.py: lit, facet, acrylic; one that does not
    /// take a widget leaves it flat, here as there). Rust says so for any of them, also where
    /// Python goes on without (a screen no plugin has): never a frame drawn without what Python
    /// draws.
    fn beyond_widgets(&self) -> Option<&'static str> {
        if self.theme.screen.is_some() && self.plugins.is_none() {
            return Some("screens");
        }
        if self.ground_unread {
            return Some(UNREAD);
        }
        if widget::truthy(&self.theme.look, "grid") {
            return Some("looks");
        }
        if self.unported_finishes() {
            return Some("finishes");
        }
        None
    }

    /// Whether a widget has a finish not drawn here: looked at once for each list of
    /// widgets, not on every frame (the widgets of a list do not change).
    fn unported_finishes(&self) -> bool {
        let mut known = self.unported_finish.borrow_mut();
        match known.as_ref() {
            Some((widgets, found)) if Rc::ptr_eq(widgets, &self.widgets) => *found,
            _ => {
                let found = (self.widgets.iter()).any(|w| finish::of(w) == Finish::Unported);
                *known = Some((Rc::clone(&self.widgets), found));
                found
            }
        }
    }

    /// render: the frame (RGB) of `snapshot` at `now` (the clock the slots' rules keep their
    /// times by: render's own `now`, not the snapshot's moment), and the widgets' boxes;
    /// Unported where it holds what is not drawn here yet.
    pub fn render(
        &mut self,
        snapshot: Snapshot,
        now: f64,
    ) -> std::result::Result<(Image, IndexMap<String, [i64; 4]>), Unported> {
        let (frame, boxes) = self.drawn(snapshot, now, false, false)?;
        let Drawing::Frame(frame) = frame else {
            unreachable!("a frame composed here was asked for");
        };
        Ok((Rc::unwrap_or_clone(frame), boxes))
    }

    /// render, the frame shared: laid into one of two kept frames where it changed (see
    /// `changed`), not copied whole each time.
    pub fn render_shared(
        &mut self,
        snapshot: Snapshot,
        now: f64,
    ) -> std::result::Result<Shared, Unported> {
        let (frame, boxes) = self.drawn(snapshot, now, true, false)?;
        let Drawing::Frame(frame) = frame else {
            unreachable!("a frame composed here was asked for");
        };
        Ok((frame, boxes))
    }

    /// render, as the frame's layers where the engine can compose them all as they are
    /// (`_layered`): its pixels are not made here then; else the frame as render_shared.
    pub fn render_scene(
        &mut self,
        snapshot: Snapshot,
        now: f64,
    ) -> std::result::Result<(Drawing, IndexMap<String, [i64; 4]>), Unported> {
        self.drawn(snapshot, now, true, true)
    }

    fn drawn(
        &mut self,
        snapshot: Snapshot,
        now: f64,
        shared: bool,
        scene: bool,
    ) -> std::result::Result<(Drawing, IndexMap<String, [i64; 4]>), Unported> {
        let begun = std::time::Instant::now();
        self.renders += 1;
        self.changed = None;
        self.slots_begun = false;
        self.at = now;
        if let Some(what) = self.beyond_widgets() {
            return Err(Unported(what.into()));
        }
        let snapshot = Rc::new(crate::prof::part("begin snapshot", || snapshot.finite()));
        if !self.slots.is_empty()
            && self.options.animate
            && !self.options.preview
            && !self.slots_warm
        {
            // (once, with a panel's first moving frame)
            self.slots_warm = true;
            self.warm_slots(&snapshot, now)?;
        }
        self.snapshot = Some(Rc::clone(&snapshot));
        // (Motion.begin: the springs of values long gone go, the others forget old pieces)
        self.moving = false;
        self.due = None;
        self.video_drawn = None;
        let (glide, lead) = (self.glide_s(None), self.lead());
        let readings = |key: &str| {
            snapshot
                .readings
                .get(key)
                .and_then(|r| motion::number(&r.value))
        };
        crate::prof::part("begin motion", || {
            self.motion.begin(&readings, now, glide, lead)
        });
        if let Some(plugins) = self.plugins.clone() {
            crate::prof::part("begin plugins", || plugins.begin(&self.frame_info()));
        }
        let screen = crate::prof::part("begin screen", || self.screen_image(&snapshot));
        // which card each slot shows
        crate::prof::part("begin slots", || self.begin_slots(&snapshot, now))?;
        let mut boxes = IndexMap::new();
        let mut pieces: Pieces = Vec::new();
        // the turned widgets' turns, and the turned modules' ("<id>/")
        let mut turns: IndexMap<String, Turn> = IndexMap::new();
        let mut gone: HashMap<String, bool> = HashMap::new();
        let widgets = Rc::clone(&self.widgets);
        crate::prof::add("render begin", begun.elapsed());
        for w in widgets.iter() {
            if !widget::flag(w, "visible", true) {
                continue;
            }
            let module = self.modules.get(widget::text(w, "_module")).cloned();
            // a part of a card in a slot: drawn while its card is on show, what it showed last
            // while it goes
            let slot_id = match &module {
                Some(module) if w.contains_key("_card") => {
                    let id = widget::text(module, "id");
                    self.slots.contains_key(id).then(|| id.to_string())
                }
                _ => None,
            };
            let in_slot = slot_id.is_some();
            let how = match &slot_id {
                Some(id) => match self.slots[id].shows(widget::int(w, "_card", 0)) {
                    Some(how) => Some(how),
                    None => continue,
                },
                None => None,
            };
            // (what a card that rolls up shows is what it showed: its conditions held then)
            if how != Some(OnShow::Frozen) && self.missing(w, &snapshot) {
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
            let drawn = match how {
                // (a card that rolls up: what it showed last, as the change has it)
                Some(OnShow::Frozen) => {
                    match self.slots[slot_id.as_deref().unwrap_or_default()].leave(&id) {
                        Ok(Some(piece)) => Drawn::Piece(piece),
                        Ok(None) => Drawn::Nothing,
                        Err(e) => Drawn::Failed(e),
                    }
                }
                _ => {
                    let began = std::time::Instant::now();
                    let drawn = self.draw(w);
                    crate::prof::add(prof_name(widget::text(w, "type")), began.elapsed());
                    drawn
                }
            };
            let piece = match drawn {
                Drawn::Piece(piece) => piece,
                Drawn::Nothing => continue,
                Drawn::Unported(what) => return Err(Unported(what)),
                Drawn::Failed(e) => {
                    self.warn(format!("widget {} failed: {e}", lp_py::repr(&id)));
                    continue;
                }
            };
            // (the card on show: kept for when it goes, cut or moved as a change or a slide
            // has it)
            let piece = match (how, &slot_id) {
                (Some(OnShow::Live), Some(sid)) => {
                    let slot = self.slots.get_mut(sid).expect("a slot of this frame");
                    match slot.live(&id, piece) {
                        Ok(Some(piece)) => piece,
                        Ok(None) => continue,
                        Err(e) => {
                            self.warn(format!("widget {} failed: {e}", lp_py::repr(&id)));
                            continue;
                        }
                    }
                }
                _ => piece,
            };
            pieces.push((id.clone(), piece.clone()));
            if let Some(turn) = self.turn_of(w, module.as_deref(), &piece)? {
                turns.insert(id.clone(), turn);
                if let Some(module) = &module {
                    // (and its frame and line of light, over its cards)
                    turns.insert(format!("{}/", widget::text(module, "id")), turn);
                }
            }
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
                let dot = crate::prof::part("graph dot", || self.graph_dot(w, &snapshot));
                if let Some((plain, content, made)) = dot.map_err(refused)? {
                    let made = self
                        .cached(&plain, content, |_| Ok(made))
                        .map_err(refused)?;
                    pieces.push((format!("{id}\0dot"), made));
                }
            }
            if !in_slot {
                // (a list's fans, its names and numbers over its gliding bars)
                for (name, top) in self.over.remove(&id).unwrap_or_default() {
                    pieces.push((format!("{id}\0{name}"), top));
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
        let ending = crate::prof::Span::new("render end");
        // the slots' frames, over their cards
        let mut slots = std::mem::take(&mut self.slots);
        let laid = slots
            .values_mut()
            .try_for_each(|slot| slot.overlay(&self.theme, &mut pieces));
        self.slots = slots;
        laid.map_err(|Failed(e)| Unported(format!("a card slot's frame failed: {e}")))?;
        if !self.strips.is_empty() {
            self.forget_strips(now);
        }
        if !turns.is_empty() || !self.turned.is_empty() {
            self.turn_pieces(&mut pieces, &turns, &mut boxes)?;
        }
        self.full_video = self.whole_video(&pieces, &turns);
        if let Some(screen) = screen {
            self.full_video = None;
            let whole = (screen.width(), screen.height())
                == (self.theme.width as usize, self.theme.height as usize);
            if pieces.is_empty() && screen.mode() == Mode::RGB && whole {
                // an opaque screen alone is the frame: nothing to compose
                self.behind = [None, None];
                return Ok((Drawing::Frame(screen), boxes));
            }
            pieces.insert(0, ("\0screen".to_string(), self.screen_piece(screen)?));
        }
        drop(ending);
        if scene && self.layered(&pieces) {
            return Ok((Drawing::Scene(self.scene_frame(pieces)?), boxes));
        }
        let frame = crate::prof::part("compose", || self.compose(pieces, shared))?;
        Ok((Drawing::Frame(frame), boxes))
    }

    /// _layered: whether the engine can compose this frame of `pieces` as `compose` does: on
    /// the opaque ground of a frame in RGB, of pieces it takes as they are.
    fn layered(&self, pieces: &Pieces) -> bool {
        self.mode == Mode::RGB
            && self.opaque
            && pieces
                .iter()
                .all(|(_, piece)| crate::scene::layerable(piece))
    }

    /// _scene_frame: the frame of `pieces` for the engine to compose: what changed is the
    /// engine's to work out. The frame composed here last goes with what lay under its pieces:
    /// one composed here again (a transition, a message drawn here) is composed whole.
    fn scene_frame(&mut self, pieces: Pieces) -> Result<crate::scene::SceneFrame> {
        self.changed = None;
        self.composed = None;
        self.behind = [None, None];
        self.underlays.clear();
        let colour = self.color(Some(&self.theme.background_color), 1.0)?;
        let ground = match (&self.background, colour) {
            (None, Some(c)) => crate::scene::Ground::Colour([
                c[0].clamp(0, 255) as u8,
                c[1].clamp(0, 255) as u8,
                c[2].clamp(0, 255) as u8,
            ]),
            _ => {
                if self.ground_shared.is_none() {
                    if self.ground_kept.is_none() {
                        self.ground_kept = Some(self.ground()?);
                    }
                    let ground = self.ground_kept.clone().expect("made above");
                    self.ground_shared = Some(Rc::new(ground));
                }
                crate::scene::Ground::Picture(Rc::clone(self.ground_shared.as_ref().expect("made")))
            }
        };
        Ok(crate::scene::SceneFrame {
            size: (self.theme.width, self.theme.height),
            ground,
            pieces,
        })
    }

    /// _whole_video: the video of this frame (its file, how it is fitted, its speed) where the
    /// frame is that video alone: drawn edge to edge across the theme and nothing else with it
    /// (a turn, a rounded or blurred corner, an effect, a picture behind one that is
    /// contained); None otherwise. (A screen's picture and a look's grid, which leave the frame
    /// to Python here, are no such frame either.)
    fn whole_video(
        &self,
        pieces: &Pieces,
        turns: &IndexMap<String, Turn>,
    ) -> Option<(std::path::PathBuf, String, f64)> {
        let (id, path, w, bounds) = self.video_drawn.as_ref()?;
        if !turns.is_empty() {
            return None;
        }
        let whole = [0, 0, self.theme.width, self.theme.height];
        if pieces.len() != 1 || &pieces[0].0 != id || *bounds != whole {
            return None;
        }
        if widget::truthy(w, "radius")
            || widget::truthy(w, "blur")
            || widget::truthy(w, "glow")
            || widget::truthy(w, "shadow")
        {
            return None;
        }
        let opacity = w.get("opacity").cloned().unwrap_or(Value::int(1));
        if opacity != Value::int(1)
            || (widget::text(w, "fit") == "contain"
                && self
                    .theme
                    .background_image
                    .as_deref()
                    .is_some_and(|name| !name.is_empty()))
        {
            return None;
        }
        let speed = image::speed_of(w.get("speed").unwrap_or(&Value::int(1))).ok()?;
        Some((path.clone(), widget::text(w, "fit").to_string(), speed))
    }

    /// close: the videos' ffmpeg stopped (a replaced renderer: Python's close lets go of its
    /// feeds; what else it lets go of, Rust has no part of yet).
    pub fn close(&mut self) {
        self.feeds.clear();
    }

    /// For the comparisons: each video's thread with its frames ahead (or no more to come), so
    /// that the next frame does not depend on how fast ffmpeg is.
    pub fn settle_videos(&self, timeout: std::time::Duration) {
        for feed in self.feeds.values() {
            feed.settle(timeout);
        }
    }

    /// _turn: how far a widget is turned (`rotate`, degrees clockwise) and about which point:
    /// a module's parts about the middle of the module; a text about the point it is aligned
    /// at, halfway down its line (its box follows its letters: a value that changes would move
    /// it); anything else about the middle of its box. None for one that is not turned.
    fn turn_of(
        &self,
        w: &Dict,
        module: Option<&Dict>,
        piece: &Piece,
    ) -> std::result::Result<Option<Turn>, Unported> {
        let owner = module.unwrap_or(w);
        let angle = turn::py_mod(widget::number(owner, "rotate", 0.0), 360.0);
        if angle == 0.0 {
            return Ok(None);
        }
        let middle =
            |[x, y, w, h]: [i64; 4]| (x as f64 + w as f64 / 2.0, y as f64 + h as f64 / 2.0);
        if let Some(module) = module {
            let id = widget::text(module, "id");
            let Some(&bounds) = self.module_boxes.get(id) else {
                return Err(Unported(format!("a module of no box: {}", lp_py::repr(id))));
            };
            return Ok(Some((angle, middle(bounds))));
        }
        if w.contains_key("font_size") && w.contains_key("align") && !w.contains_key("w") {
            let size = widget::int(w, "font_size", 0);
            let font = self
                .font(widget::text(w, "font"), size)
                .map_err(|Failed(e)| Unported(format!("a turned text's font: {e}")))?;
            let (ascent, descent) = font.font.getmetrics();
            let line = match ascent + descent {
                0 => widget::number(w, "font_size", 0.0),
                line => line as f64,
            };
            let (x, y) = (widget::number(w, "x", 0.0), widget::number(w, "y", 0.0));
            return Ok(Some((angle, (x, y + line / 2.0))));
        }
        Ok(Some((angle, middle(piece.bounds()))))
    }

    /// _turn_pieces: the pieces of the turned widgets turned, with what lies over them (a bar's
    /// mark and dot, a list's fans) and a module's parts with its frame, and their boxes in the
    /// editor. A piece turned as it was the frame before is that frame's again.
    fn turn_pieces(
        &mut self,
        pieces: &mut Pieces,
        turns: &IndexMap<String, Turn>,
        boxes: &mut IndexMap<String, [i64; 4]>,
    ) -> std::result::Result<(), Unported> {
        let before = std::mem::take(&mut self.turned);
        for (wid, piece) in pieces.iter_mut() {
            let base = wid.split('\0').next().unwrap_or_default();
            let owner = base.split('/').next().unwrap_or_default();
            let Some(&turn) = turns.get(base).or_else(|| turns.get(&format!("{owner}/"))) else {
                continue;
            };
            let turned = match before.get(wid.as_str()) {
                Some((was, how, turned)) if Rc::ptr_eq(was, piece) && *how == turn => {
                    turned.clone()
                }
                _ => Rc::new(turn::turned(piece, turn).map_err(|Failed(e)| {
                    Unported(format!(
                        "widget {} could not be turned: {e}",
                        lp_py::repr(wid)
                    ))
                })?),
            };
            self.turned
                .insert(wid.clone(), (piece.clone(), turn, turned.clone()));
            if let (Some(bounds), Some(hit)) = (boxes.get_mut(wid.as_str()), turned.hit) {
                *bounds = hit;
            }
            *piece = turned;
        }
        for (key, &turn) in turns {
            // a module: around all of it
            if let Some(id) = key.strip_suffix('/') {
                if boxes.contains_key(id) {
                    let Some(&bounds) = self.module_boxes.get(id) else {
                        return Err(Unported(format!("a module of no box: {}", lp_py::repr(id))));
                    };
                    boxes.insert(id.to_string(), turn::bounds(bounds, turn));
                }
            }
        }
        Ok(())
    }

    /// _compose (the first frame, or keep_static off: the whole of it from the ground up).
    /// _compose: the pieces laid on the ground in their order, as the frame (RGB). Where the
    /// frame before had the same pieces in the same order, only the boxes of those that
    /// changed are laid again (from the ground up, each piece over them in its order): the
    /// same pixels as laying all of them, at a fraction of the cost.
    fn compose(&mut self, pieces: Pieces, shared: bool) -> Result<Rc<Image>> {
        if self.ground_kept.is_none() {
            self.ground_kept = Some(self.ground()?);
        }
        let ground = self.ground_kept.as_ref().expect("made above");
        let size = ground.size();
        let areas = match self.composed.take() {
            Some((canvas, before)) if canvas.size() == size && canvas.mode() == ground.mode() => {
                crate::prof::part("compose changed", || {
                    compose::changed(&before, &pieces, size)
                })
                .map(|areas| (canvas, areas))
            }
            _ => None,
        };
        let mut failed = Vec::new();
        self.underlays.next_frame();
        let mut changed = None;
        let canvas = match areas {
            Some((mut canvas, (areas, moved))) => {
                changed = Some(areas.clone());
                for area in areas {
                    compose::lay_again(
                        &mut canvas,
                        ground,
                        &pieces,
                        &moved,
                        area,
                        self.opaque,
                        &mut self.underlays,
                        &mut failed,
                    );
                }
                canvas
            }
            None => {
                let mut canvas = ground.clone();
                for (id, piece) in &pieces {
                    if let Err(Failed(e)) =
                        compose::composite(&mut canvas, piece, (0, 0), self.opaque)
                    {
                        failed.push((id.clone(), e));
                    }
                }
                canvas
            }
        };
        for (id, e) in failed {
            self.warn(format!("widget {} failed: {e}", lp_py::repr(&id)));
        }
        let frame = crate::prof::part("compose rgb", || match shared {
            true => self.shared_frame(&canvas, changed.as_deref()),
            false => {
                self.behind = [None, None];
                Ok(Rc::new(compose::as_rgb(&canvas)?))
            }
        })?;
        self.changed = changed;
        self.composed = Some((canvas, pieces));
        Ok(frame)
    }

    /// The frame of `canvas` (RGB) in one of the two kept frames, laid where it changed since
    /// that one was laid: `changed` is where the canvas changed since the frame before. A kept
    /// frame still held elsewhere is made anew.
    fn shared_frame(
        &mut self,
        canvas: &Image,
        changed: Option<&[compose::Area]>,
    ) -> Result<Rc<Image>> {
        if canvas.mode() != Mode::RGB {
            self.frames = [None, None];
            self.behind = [None, None];
            return Ok(Rc::new(compose::as_rgb(canvas)?));
        }
        for behind in &mut self.behind {
            match (behind.as_mut(), changed) {
                (Some(list), Some(areas)) if list.len() + areas.len() <= 64 => {
                    list.extend_from_slice(areas);
                }
                _ => *behind = None,
            }
        }
        let k = self.turn;
        self.turn = 1 - k;
        let size = canvas.size();
        let kept = self.frames[k]
            .as_mut()
            .and_then(Rc::get_mut)
            .filter(|frame| frame.size() == size);
        match (kept, &self.behind[k]) {
            (Some(frame), Some(areas)) => {
                for &area in areas {
                    compose::copy_area(frame, canvas, area);
                }
            }
            (Some(frame), None) => compose::copy_area(frame, canvas, (0, 0, i64::MAX, i64::MAX)),
            (None, _) => self.frames[k] = Some(Rc::new(canvas.clone())),
        }
        self.behind[k] = Some(Vec::new());
        Ok(Rc::clone(self.frames[k].as_ref().expect("laid above")))
    }
}
