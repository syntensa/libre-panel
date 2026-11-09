//! What is drawn over a finished frame (render/overlays.py): toasts, the short messages of
//! services, with their queue, ranks, keys and holds (ToastLayer), in the theme's toast style:
//! the built-in card (CardStyle) so far. Where a theme or a message asks for what is not here
//! yet (the band, the volume's looks), the layer says so (Unported) instead of drawing
//! something else.

use std::collections::HashSet;
use std::rc::Rc;

use lp_devices::models::{find_model, PanelModel};
use lp_pixels::{Draw, Filter, Image, Mode, ALL_CORNERS};
use lp_py::{Dict, Value};
use lp_text::fallback::FallbackFont;
use lp_text::text::{draw as draw_text, Text, TextFace};
use num_traits::ToPrimitive;

use crate::icons;
use crate::light;
use crate::piece::{Failed, Result};
use crate::renderer::Renderer;

/// Supersampling for smooth corners.
const SS: i64 = 3;

/// The kinds of message a screen shows itself when it reads these: the volume where its number
/// or bar is, what plays where its title is, a session that waits for you (SHOWN_BY).
const SHOWN_BY: [(&str, &[&str]); 3] = [
    ("volume", &["audio.volume"]),
    ("music", &["media.title"]),
    ("agent", &["ai.claude.state", "ai.codex.state"]),
];

/// The volume's looks the notices service may choose (volume_looks.VOLUME_LOOKS).
const VOLUME_LOOKS: [&str; 4] = ["bar", "levels", "ring", "big"];

/// What a theme or a message asks of the toasts that is not here yet.
#[derive(Clone, Debug, PartialEq)]
pub struct Unported(pub String);

/// A short message on the panel (plugins/host.py's Toast): its text, icon, level (info,
/// warning or error), the seconds it holds (None: the theme's hold time), what it is about
/// (`kind`), its rank, what a style draws besides the text (`payload`), who sent it, and the
/// key that makes it replace the one on show (empty: its kind). A toast is its `Rc`: two of
/// the same fields are two toasts, as Python's dataclass without `eq` compares them.
#[derive(Clone, Debug)]
pub struct Toast {
    pub text: String,
    pub icon: Option<String>,
    pub level: String,
    pub seconds: Option<f64>,
    pub kind: String,
    pub rank: i64,
    pub payload: Dict,
    pub service: String,
    pub key: String,
}

impl Toast {
    /// A toast of `text` at level info, with nothing else.
    pub fn new(text: impl Into<String>) -> Toast {
        Toast {
            text: text.into(),
            icon: None,
            level: "info".into(),
            seconds: None,
            kind: String::new(),
            rank: 0,
            payload: Dict::new(),
            service: String::new(),
            key: String::new(),
        }
    }

    /// key_of: what makes it replace the one on show: its key, else its kind.
    pub fn key_of(&self) -> &str {
        if self.key.is_empty() {
            &self.kind
        } else {
            &self.key
        }
    }
}

/// shown_by: the kinds of message the theme on show shows itself, on every screen alike.
pub fn shown_by(renderer: &Renderer) -> Vec<&'static str> {
    if renderer.widgets.is_empty() {
        return Vec::new();
    }
    let read = renderer.reads();
    SHOWN_BY
        .iter()
        .filter(|(_, keys)| keys.iter().any(|key| read.contains(*key)))
        .map(|(kind, _)| *kind)
        .collect()
}

/// A number of the theme's toast settings as float() takes it.
fn float_of(value: Option<&Value>, default: f64) -> f64 {
    match value {
        Some(Value::Float(x)) => *x,
        Some(Value::Int(n)) => n.to_f64().unwrap_or(f64::INFINITY),
        Some(Value::Bool(b)) => f64::from(u8::from(*b)),
        _ => default,
    }
}

/// The theme's toast settings (Theme.toast): where they show, how long they hold, whether they
/// wait in a queue, the kinds switched off, and the style with its options.
#[derive(Clone, Debug)]
struct Settings {
    anchor: String,
    seconds: f64,
    queue: bool,
    off: Vec<String>,
    style: String,
    options: Value,
}

impl Settings {
    fn of(toast: &Dict) -> Settings {
        let text = |key: &str, default: &str| match toast.get(key) {
            Some(Value::Str(s)) => s.clone(),
            _ => default.to_string(),
        };
        let off = match toast.get("off") {
            Some(Value::List(kinds)) => kinds
                .iter()
                .filter_map(|k| k.as_str().map(str::to_string))
                .collect(),
            _ => Vec::new(),
        };
        Settings {
            anchor: text("anchor", "top-right"),
            seconds: float_of(toast.get("seconds"), 4.0),
            queue: toast.get("queue").is_none_or(Value::truthy),
            off,
            style: text("style", ""),
            options: toast
                .get("options")
                .cloned()
                .unwrap_or_else(|| Value::Dict(Dict::new())),
        }
    }
}

/// What a style gets from the renderer (plugins/render.py's RenderContext): the theme's palette
/// and panel model as they were when it came, and which context this is (a style keeps what it
/// drew for one context only). Its fonts and colours are the renderer's.
struct Context {
    palette: Dict,
    model: Option<&'static PanelModel>,
    id: u64,
}

/// The toast on show: the toast, when it began, and the toast it replaced with that one's age
/// then (None: it rolled in afresh).
#[derive(Clone)]
struct Current {
    toast: Rc<Toast>,
    began: f64,
    previous: Option<(Rc<Toast>, f64)>,
}

/// The styles toasts are drawn in.
enum Style {
    Card(CardStyle),
}

/// Shows toasts in the theme's toast style (the built-in card unless the theme names another).
///
/// - A toast with the key of the one on show (`key`, else its `kind`) replaces it in place,
///   whatever its rank: turning the volume refreshes one toast instead of queueing three.
///   Waiting ones with that key collapse to the newest.
/// - With the theme's `toast.queue` on (the default) a higher rank takes over at once and the
///   others wait, by rank, then in order. With it off, toasts are decided one by one as they
///   come: the same or a higher rank takes over at once, a lower one is dropped.
/// - Kinds the theme switches off (`toast.off`) or the screen shows anyway (a theme that shows
///   the volume or what plays: SHOWN_BY) are left out when they come. One on show when such a
///   screen comes goes on unseen (it still holds back lower ranks) and shows again after it.
/// - While a transition plays (`hold`) no new toast starts; the one on show stays, unless the
///   transition set it aside to come again afterwards. One that hides toasts
///   (`draw(..., hidden = true)`) lets them be decided as usual: the one on show starts again
///   on the frame after it.
pub struct ToastLayer {
    /// arrived, not yet sorted in
    pub inbox: Vec<Rc<Toast>>,
    pub queue: Vec<Rc<Toast>>,
    current: Option<Current>,
    /// when the toast on show is gone (the style's lifetime)
    pub ends: f64,
    /// where the last draw drew (x0, y0, x1, y1), else None
    pub drawn_box: Option<[i64; 4]>,
    style: Option<Style>,
    settings: Option<Settings>,
    size: Option<(i64, i64)>,
    context: Context,
    contexts: u64,
    /// the kinds the theme switches off, those the screen shows itself, and both
    off: HashSet<String>,
    left_out: HashSet<String>,
    suppressed: HashSet<String>,
    /// the last failure (each is told once, not 50 times a second)
    failed: Option<String>,
    /// what Python logs: a style that is not installed, a failure
    pub log: Vec<String>,
}

impl ToastLayer {
    pub const MAX_QUEUE: usize = 20;

    pub fn new(renderer: &Renderer) -> std::result::Result<ToastLayer, Unported> {
        let mut layer = ToastLayer {
            inbox: Vec::new(),
            queue: Vec::new(),
            current: None,
            ends: 0.0,
            drawn_box: None,
            style: None,
            settings: None,
            size: None,
            context: Context {
                palette: Dict::new(),
                model: None,
                id: 0,
            },
            contexts: 0,
            off: HashSet::new(),
            left_out: HashSet::new(),
            suppressed: HashSet::new(),
            failed: None,
            log: Vec::new(),
        };
        layer.set_renderer(renderer)?;
        Ok(layer)
    }

    /// set_renderer: a new theme (and its renderer): its style, hold time and the kinds left
    /// out. The style goes on, with what it keeps per toast, while the theme names the same one
    /// with the same options at the same size. (A plugin's screen leaves out its own kinds too:
    /// they come with the plugins.)
    pub fn set_renderer(&mut self, renderer: &Renderer) -> std::result::Result<(), Unported> {
        let theme = &renderer.theme;
        let settings = Settings::of(&theme.toast);
        let size = (theme.width, theme.height);
        self.off = settings.off.iter().cloned().collect();
        self.left_out = shown_by(renderer).into_iter().map(String::from).collect();
        self.suppressed = self.off.union(&self.left_out).cloned().collect();
        let same = self
            .settings
            .as_ref()
            .is_some_and(|s| s.style == settings.style && s.options == settings.options)
            && self.size == Some(size);
        self.contexts += 1;
        self.context = Context {
            palette: theme.palette.clone(),
            model: find_model(&theme.model),
            id: self.contexts,
        };
        let anchor = settings.anchor.clone();
        let name = settings.style.clone();
        self.settings = Some(settings);
        self.size = Some(size);
        if same {
            if let Some(Style::Card(card)) = &mut self.style {
                card.anchor = anchor;
                return Ok(());
            }
        }
        self.close();
        match name.as_str() {
            "" | "card" => {}
            "band" => return Err(Unported("the band style".into())),
            other => self.log.push(format!(
                "toast style {} is not installed; using the built-in card",
                lp_py::repr(other)
            )),
        }
        self.style = Some(Style::Card(CardStyle { anchor, card: None }));
        Ok(())
    }

    fn settings(&self) -> &Settings {
        self.settings
            .as_ref()
            .expect("a toast layer has its theme's settings")
    }

    /// The style that draws `toast` (style_of): the theme's; the volume in the look the notices
    /// service chose (its payload's "look") is not here yet.
    fn style_of(&self, toast: &Toast) -> std::result::Result<(), Unported> {
        if toast.kind == "volume" {
            if let Some(Value::Str(look)) = toast.payload.get("look") {
                if VOLUME_LOOKS.contains(&look.as_str()) {
                    return Err(Unported(format!("the volume's look {}", lp_py::repr(look))));
                }
            }
        }
        Ok(())
    }

    pub fn add(&mut self, toasts: impl IntoIterator<Item = Rc<Toast>>) {
        self.inbox.extend(toasts);
    }

    /// active: a toast shows or waits: the frame keeps changing.
    pub fn active(&self) -> bool {
        self.current.is_some() || !self.queue.is_empty() || !self.inbox.is_empty()
    }

    /// layered: whether the frame may go as its layers with what shows of the toasts: no toast
    /// shows, or its style lays it as one picture through its alpha (the card does: `laid`).
    pub fn layered(&self) -> bool {
        true
    }

    /// set_aside: a transition begins that the toast on show must not cover: it leaves now and
    /// comes again, from the start, once the transition is over.
    pub fn set_aside(&mut self) {
        if let Some(current) = self.current.take() {
            self.queue.insert(0, current.toast);
        }
    }

    /// showing: the toast on show and its age (for screens); None while the screen leaves its
    /// kind out.
    pub fn showing(&self, now: f64) -> Option<(Rc<Toast>, f64)> {
        let current = self.current.as_ref()?;
        if self.suppressed.contains(&current.toast.kind) {
            return None;
        }
        Some((current.toast.clone(), now - current.began))
    }

    /// The toast on show, when it began and the toast it replaced (with that one's age then).
    #[allow(clippy::type_complexity)]
    pub fn current(&self) -> Option<(Rc<Toast>, f64, Option<(Rc<Toast>, f64)>)> {
        self.current
            .as_ref()
            .map(|c| (c.toast.clone(), c.began, c.previous.clone()))
    }

    /// advance: sort in what arrived and decide which toast shows at `now`. With `hold` (a
    /// transition plays) no new toast starts.
    pub fn advance(&mut self, now: f64, hold: bool) -> std::result::Result<(), Unported> {
        if self.current.is_some() && now >= self.ends {
            self.current = None;
        }
        let mut arrived = std::mem::take(&mut self.inbox);
        if hold || self.settings().queue {
            for toast in arrived.drain(..) {
                self.arrive(toast, now)?;
            }
        }
        if !hold && !self.queue.is_empty() {
            self.next_from_queue(now)?;
        }
        for toast in arrived {
            // (no queue: one by one, in the order they came)
            self.take(toast, now)?;
        }
        Ok(())
    }

    /// _prepared: the toast as it will show (with the theme's hold time where it has none of
    /// its own), or None when this screen leaves its kind out.
    fn prepared(&self, toast: Rc<Toast>) -> std::result::Result<Option<Rc<Toast>>, Unported> {
        if self.suppressed.contains(&toast.kind) {
            return Ok(None);
        }
        if toast.seconds.is_some() {
            return Ok(Some(toast));
        }
        self.style_of(&toast)?;
        // (the card's hold_s: the theme's hold time; max(0.0, seconds))
        let seconds = light::py_max(0.0, self.settings().seconds);
        let mut replaced = Toast::clone(&toast);
        replaced.seconds = Some(seconds);
        Ok(Some(Rc::new(replaced)))
    }

    fn arrive(&mut self, toast: Rc<Toast>, now: f64) -> std::result::Result<(), Unported> {
        let Some(toast) = self.prepared(toast)? else {
            return Ok(());
        };
        let key = toast.key_of().to_string();
        if !key.is_empty()
            && self
                .current
                .as_ref()
                .is_some_and(|c| c.toast.key_of() == key)
        {
            return self.show(toast, now); // the same thing again: in place, whatever its rank
        }
        if !key.is_empty() {
            let same: Vec<usize> = (0..self.queue.len())
                .filter(|&i| self.queue[i].key_of() == key)
                .collect();
            if let Some(&first) = same.first() {
                // waiting ones with this key collapse to the newest
                self.queue[first] = toast;
                for &i in same[1..].iter().rev() {
                    self.queue.remove(i);
                }
                return Ok(());
            }
        }
        // after those of the same or a higher rank
        let at = self
            .queue
            .iter()
            .position(|q| q.rank < toast.rank)
            .unwrap_or(self.queue.len());
        self.queue.insert(at, toast);
        self.queue.truncate(Self::MAX_QUEUE); // the lowest ranks, newest first, go
        Ok(())
    }

    fn next_from_queue(&mut self, now: f64) -> std::result::Result<(), Unported> {
        let shown = self.current.as_ref().map(|c| c.toast.rank);
        if self.settings().queue {
            if shown.is_none_or(|rank| self.queue[0].rank > rank) {
                let next = self.queue.remove(0);
                self.show(next, now)?;
            }
            return Ok(());
        }
        // no queue, after a hold: the newest of the highest rank, if it is not lower
        let mut best = self.queue[self.queue.len() - 1].clone();
        for q in self.queue.iter().rev().skip(1) {
            if q.rank > best.rank {
                best = q.clone();
            }
        }
        self.queue.clear();
        if shown.is_none_or(|rank| best.rank >= rank) {
            self.show(best, now)?;
        }
        Ok(())
    }

    /// _take: no queue: the same key replaces the one on show, the same or a higher rank takes
    /// over from it, a lower one is dropped.
    fn take(&mut self, toast: Rc<Toast>, now: f64) -> std::result::Result<(), Unported> {
        let Some(toast) = self.prepared(toast)? else {
            return Ok(());
        };
        let takes = match &self.current {
            None => true,
            Some(c) => {
                let key = toast.key_of();
                (!key.is_empty() && key == c.toast.key_of()) || toast.rank >= c.toast.rank
            }
        };
        if takes {
            self.show(toast, now)?;
        }
        Ok(())
    }

    fn show(&mut self, toast: Rc<Toast>, now: f64) -> std::result::Result<(), Unported> {
        let previous = self
            .current
            .as_ref()
            .map(|c| (c.toast.clone(), now - c.began));
        self.start(toast, now, previous)
    }

    fn start(
        &mut self,
        toast: Rc<Toast>,
        now: f64,
        previous: Option<(Rc<Toast>, f64)>,
    ) -> std::result::Result<(), Unported> {
        self.style_of(&toast)?;
        // (the card's lifetime: its hold time and the time it takes to leave)
        self.ends = now + (toast.seconds.unwrap_or(f64::NAN) + CardStyle::LEAVE_S);
        self.current = Some(Current {
            toast,
            began: now,
            previous,
        });
        Ok(())
    }

    /// What `draw` and `laid` do first: whether the toast on show is drawn now, and what of it.
    #[allow(clippy::type_complexity)]
    fn on_show(
        &mut self,
        now: f64,
        hidden: bool,
    ) -> std::result::Result<Option<(Rc<Toast>, f64, bool)>, Unported> {
        self.drawn_box = None;
        let Some(current) = self.current.clone() else {
            return Ok(None);
        };
        if self.suppressed.contains(&current.toast.kind) {
            return Ok(None); // the screen shows it anyway: it goes on unseen
        }
        if hidden {
            self.start(current.toast, now, None)?;
            return Ok(None);
        }
        self.style_of(&current.toast)?;
        Ok(Some((
            current.toast,
            now - current.began,
            current.previous.is_some(),
        )))
    }

    /// A failure of the style: the frame goes on without the toast, and it is told once.
    fn failure(&mut self, e: Failed) {
        if self.failed.as_ref() != Some(&e.0) {
            self.failed = Some(e.0.clone());
            self.log.push(format!("toast style failed: {}", e.0));
        }
    }

    /// draw: the frame with the toast on show drawn on it, None where it is the frame as it is.
    /// `hidden`: a transition that hides toasts plays; the toast on show starts again after
    /// it. `drawn_box`: where it drew.
    pub fn draw(
        &mut self,
        frame: &Image,
        now: f64,
        hidden: bool,
        renderer: &Renderer,
    ) -> std::result::Result<Option<Image>, Unported> {
        let Some((toast, age, previous)) = self.on_show(now, hidden)? else {
            return Ok(None);
        };
        let Some(Style::Card(card)) = &mut self.style else {
            return Ok(None);
        };
        match card.draw(frame, &toast, age, previous, &self.context, renderer) {
            Ok((drawn, bounds)) => {
                self.drawn_box = Some(bounds);
                Ok(Some(drawn))
            }
            Err(e) => {
                self.failure(e);
                Ok(None)
            }
        }
    }

    /// The toast on show as it lies over a frame of `size` that the engine composes
    /// (ToastLayer.draw of a SceneFrame): its picture, laid through its alpha, and where it goes;
    /// None where nothing shows. The same picture while it shows whole.
    pub fn laid(
        &mut self,
        size: (i64, i64),
        now: f64,
        hidden: bool,
        renderer: &Renderer,
    ) -> std::result::Result<Option<(Rc<Image>, i64, i64)>, Unported> {
        let Some((toast, age, previous)) = self.on_show(now, hidden)? else {
            return Ok(None);
        };
        let Some(Style::Card(card)) = &mut self.style else {
            return Ok(None);
        };
        match card.layer(size, &toast, age, previous, &self.context, renderer) {
            Ok((picture, x, y)) => {
                self.drawn_box = Some([
                    x,
                    y,
                    x + picture.width() as i64,
                    y + picture.height() as i64,
                ]);
                Ok(Some((picture, x, y)))
            }
            Err(e) => {
                self.failure(e);
                Ok(None)
            }
        }
    }

    pub fn close(&mut self) {
        self.style = None;
    }
}

/// faded: `picture` (RGBA) with the alpha a toast style lays it through while it shows `shown`
/// (0 to 1) of itself, as its draw does (int(a * shown)): the picture itself once it shows whole.
pub(crate) fn faded(picture: &Rc<Image>, shown: f64) -> Result<Rc<Image>> {
    if shown >= 1.0 {
        return Ok(picture.clone());
    }
    let mut out = Image::clone(picture);
    out.putalpha(&through(picture, shown)?)?;
    Ok(Rc::new(out))
}

/// The alpha of `picture` times `shown`, each value cut to a whole number (int(a * shown)).
fn through(picture: &Image, shown: f64) -> Result<Image> {
    let lut: Vec<i64> = (0..256).map(|a| (f64::from(a) * shown) as i64).collect();
    Ok(picture.getchannel(3)?.point(&lut)?)
}

/// ImageColor.getcolor(colour, "RGBA"): the colour, opaque where it says no alpha.
fn colour_of(value: &str) -> Result<[u8; 4]> {
    let rgb = lp_pixels::colour::getrgb(value).map_err(|e| Failed(e.0))?;
    let channel = |i: usize, default: i64| rgb.get(i).copied().unwrap_or(default);
    Ok(
        [channel(0, 0), channel(1, 0), channel(2, 0), channel(3, 255)]
            .map(|v| v.clamp(0, 255) as u8),
    )
}

/// One number of a payload's colour as `max(0, min(255, int(c)))` takes it; None where int()
/// raises (a text that is no whole number, NaN, infinity, nothing).
fn channel(value: &Value) -> Option<i64> {
    let clamp = |n: &num_bigint::BigInt| n.clamp(&0.into(), &255.into()).to_i64();
    match value {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Int(n) => clamp(n),
        Value::Float(x) if x.is_finite() => Some(x.trunc().clamp(0.0, 255.0) as i64),
        Value::Str(s) => clamp(&lp_py::int_of_str(s)?),
        _ => None,
    }
}

/// accent_color: a toast's accent: its payload's "color" ("#rrggbb", "@name", the cover's,
/// or three numbers), else its level's, which is also what a colour nobody can read is taken
/// for.
fn accent_colour(renderer: &Renderer, palette: &Dict, toast: &Toast) -> [i64; 3] {
    match toast.payload.get("color") {
        Some(Value::Str(colour)) if !colour.is_empty() => {
            if let Ok(Some([r, g, b, _])) = renderer.color(Some(colour), 1.0) {
                return [r, g, b];
            }
        }
        Some(Value::List(parts)) if parts.len() >= 3 => {
            if let (Some(r), Some(g), Some(b)) =
                (channel(&parts[0]), channel(&parts[1]), channel(&parts[2]))
            {
                return [r, g, b];
            }
        }
        _ => {}
    }
    let level = match toast.level.as_str() {
        level @ ("warning" | "error") => level,
        _ => "info",
    };
    light::role_colour(palette, level)
}

/// The card's font: the theme's at `size`, opened anew where the renderer's would be held to
/// its 512 px (Python's card truetypes the face's file at its own size).
fn card_font(renderer: &Renderer, size: i64) -> Result<Rc<FallbackFont>> {
    let font = renderer.font("", size)?;
    if size == size.clamp(4, 512) {
        return Ok(font);
    }
    match font.font.path() {
        Some(path) => Ok(Rc::new(
            FallbackFont::open(path, size as f64, 0, None).map_err(crate::text::failed)?,
        )),
        None => Ok(font),
    }
}

/// The built-in look (CardStyle): a card with an accent stripe, an icon and the text, fading
/// and sliding in next to the anchor, clear of what the bezel hides.
struct CardStyle {
    anchor: String,
    /// the card of the toast on show: the toast, the context it was drawn in, the card
    card: Option<(Rc<Toast>, u64, Rc<Image>)>,
}

impl CardStyle {
    const FADE_IN_S: f64 = 0.18;
    const LEAVE_S: f64 = 0.25;

    fn draw(
        &mut self,
        frame: &Image,
        toast: &Rc<Toast>,
        age: f64,
        previous: bool,
        context: &Context,
        renderer: &Renderer,
    ) -> Result<(Image, [i64; 4])> {
        let size = (frame.width() as i64, frame.height() as i64);
        let (card, fade, x, y) = self.laid(size, toast, age, previous, context, renderer)?;
        let alpha = through(&card, fade)?;
        let mut out = frame.clone();
        // (card.convert(frame.mode): an RGB frame takes its colours as they are)
        out.paste(&card, x, y, Some(&alpha))?;
        let bounds = [x, y, x + card.width() as i64, y + card.height() as i64];
        Ok((out, bounds))
    }

    fn layer(
        &mut self,
        size: (i64, i64),
        toast: &Rc<Toast>,
        age: f64,
        previous: bool,
        context: &Context,
        renderer: &Renderer,
    ) -> Result<(Rc<Image>, i64, i64)> {
        let (card, fade, x, y) = self.laid(size, toast, age, previous, context, renderer)?;
        Ok((faded(&card, fade)?, x, y))
    }

    /// _laid: the card, how far it shows (0 to 1) and where it goes, at `age`. A toast that
    /// replaced another (`previous`) shows at once.
    fn laid(
        &mut self,
        size: (i64, i64),
        toast: &Rc<Toast>,
        age: f64,
        previous: bool,
        context: &Context,
        renderer: &Renderer,
    ) -> Result<(Rc<Image>, f64, i64, i64)> {
        let Some(seconds) = toast.seconds else {
            return Err(Failed(
                "unsupported operand type(s) for +: 'NoneType' and 'float'".into(),
            ));
        };
        let leaving = (seconds + Self::LEAVE_S - age) / Self::LEAVE_S;
        let arriving = if previous { 1.0 } else { age / Self::FADE_IN_S };
        let fade = light::py_max(0.0, light::py_min(light::py_min(1.0, arriving), leaving));
        let kept = self
            .card
            .as_ref()
            .filter(|(t, id, _)| Rc::ptr_eq(t, toast) && *id == context.id);
        let card = match kept {
            Some((_, _, card)) => card.clone(),
            None => {
                let card = Rc::new(self.made(toast, size, context, renderer)?);
                self.card = Some((toast.clone(), context.id, card.clone()));
                card
            }
        };
        let (cw, ch) = (card.width() as i64, card.height() as i64);
        let (x, y) = self.position(size, (cw, ch), 1.0 - fade, context);
        Ok((card, fade, x, y))
    }

    /// _draw: the card of `toast` for a frame of `size`, drawn three times larger and reduced.
    fn made(
        &self,
        toast: &Toast,
        size: (i64, i64),
        context: &Context,
        renderer: &Renderer,
    ) -> Result<Image> {
        let unit = 12.max(size.0.min(size.1).div_euclid(16)); // 30 px on a 480 px high panel
        let font = card_font(renderer, unit * SS)?;
        let text: String = if toast.text.chars().count() <= 120 {
            toast.text.clone()
        } else {
            toast.text.chars().take(119).chain(['…']).collect()
        };
        let (pad, gap, bar) = (unit * SS * 2 / 3, unit * SS / 2, 3.max(unit / 8) * SS);
        let icon = toast
            .icon
            .as_deref()
            .filter(|name| lp_theme::tables::ICON_NAMES.contains(name));
        let icon_size = if icon.is_some() { unit * SS } else { 0 };
        let max_width = size.0 * SS * 2 / 3;
        let room = max_width - icon_size - 2 * pad - gap - bar; // what the text may take
        let text = light::ellipsize(&text, &font, room as f64)?; // (cut off at the card's edge)
        let (left, _, right, _) = font
            .getbbox(&text, "", None, None, None, 0.0, None)
            .map_err(crate::text::failed)?;
        let (left, right) = (left as i64, right as i64);
        let text_w = 1.max((right - left).min(room));
        let width = bar + pad + if icon.is_some() { icon_size + gap } else { 0 } + text_w + pad;
        let height = icon_size.max(unit * SS) + 2 * pad;
        if width.saturating_mul(height) > crate::renderer::MOST_PIXELS {
            return Err(Failed("a card too large".into()));
        }
        let (w, h) = (width as usize, height as usize);
        let whole = (0.0, 0.0, width as f64 - 1.0, height as f64 - 1.0);
        let radius = (unit * SS / 2) as f64;
        let mut card = Image::blank(Mode::RGBA, w, h);
        let background = light::role_value(&context.palette, "card");
        let fill = colour_of(&background.chars().take(7).collect::<String>())?;
        let mut pen = Draw::new(&mut card);
        pen.rounded_rectangle(whole, radius, Some(fill), None, 1, ALL_CORNERS)?;
        let edge = [255, 255, 255, 0x1c];
        pen.rounded_rectangle(whole, radius, None, Some(edge), SS as i32, ALL_CORNERS)?;
        let [r, g, b] = accent_colour(renderer, &context.palette, toast);
        let accent = [r, g, b, 255].map(|v| v as u8);
        let mut mask = Image::blank(Mode::L, w, h);
        Draw::new(&mut mask).rounded_rectangle(
            whole,
            radius,
            Some([255, 0, 0, 0]),
            None,
            1,
            ALL_CORNERS,
        )?;
        let stripe = Image::new(Mode::RGBA, bar as usize, h, &accent);
        card.paste(&stripe, 0, 0, Some(&mask.crop(0, 0, bar, height)))?;
        let mut x = bar + pad;
        if let Some(name) = icon {
            let picture = icons::draw_icon(name, icon_size, accent, 2.0)?;
            let (pw, ph) = (picture.width() as i64, picture.height() as i64);
            let y = (height - icon_size).div_euclid(2);
            card.alpha_composite_at(&picture, (x, y), (0, 0, pw, ph))?;
            x += icon_size + gap;
        }
        let colour = colour_of(&light::role_value(&context.palette, "text"))?;
        let mut layer = Image::blank(Mode::RGBA, text_w as usize, h);
        let written = Text::new(&text, font.as_ref(), "RGBA");
        let at = (-left as f64, (height / 2) as f64);
        draw_text(&mut layer, at, &written, colour, None, Some("lm"), "left")
            .map_err(crate::text::failed)?;
        card.alpha_composite_at(&layer, (x, 0), (0, 0, text_w, height))?;
        Ok(card.resize(
            w / SS as usize,
            h / SS as usize,
            Filter::Lanczos,
            None,
            None,
        )?)
    }

    /// _position: where the card goes on a frame of `frame`, `slide` (0 to 1) of the margin
    /// short of its place, clear of what the panel's bezel hides.
    fn position(
        &self,
        frame: (i64, i64),
        card: (i64, i64),
        slide: f64,
        context: &Context,
    ) -> (i64, i64) {
        let ((fw, fh), (cw, ch)) = (frame, card);
        let margin = 10.max(fw.min(fh).div_euclid(24));
        let orientation = if fw > fh { "landscape" } else { "portrait" };
        let [top, right, bottom, left] = context
            .model
            .map_or([0; 4], |model| model.hidden_edges(orientation));
        let shift = lp_py::round(slide * margin as f64) as i64;
        let y = if self.anchor.starts_with("top") {
            top + margin - shift
        } else {
            fh - bottom - margin - ch + shift
        };
        let x = if self.anchor.ends_with("right") {
            fw - right - margin - cw
        } else if self.anchor.ends_with("left") {
            left + margin
        } else {
            (fw - cw).div_euclid(2)
        };
        (x, y)
    }
}
