//! The ways from one screen to the next, as Python draws them: render/overlays.py's cut, fade
//! and slide; render/transitions.py's tube and entrance, drawn with light; render/handovers.py's
//! wipe, iris, blinds, dissolve, the slides from each side, zoom, the line of light, the glitch,
//! the tiles and the shatter. Each is made for a switch with the new theme's colours and the
//! parameters a service passed (overlays.transition), and draws its frame of the old screen,
//! the new one and how far it has gone (0 to 1), what it keeps from frame to frame kept as
//! Python keeps it: a map or a strip of light for the frame's size, the logo or title with its
//! halo, the pieces of the old screen.

use std::borrow::Cow;

use lp_pixels::{chops, merge, Filter, Image, Mode, Transpose};
use lp_py::random::Random;
use lp_py::{Dict, Value};

use crate::light::{
    add_light, add_line, draw_tracked, ease_in, ease_out, face, mix, phase, py_max, py_min,
    role_colour, scaled, smooth, tracked_width, whole, Look,
};
use crate::piece::{Failed, Result};
use crate::renderer::Renderer;

fn failed(e: lp_pixels::Error) -> Failed {
    Failed(e.0)
}

/// round(x) of a float as an int (ValueError and OverflowError of NaN and infinity).
fn round_int(x: f64) -> Result<i64> {
    whole(x, f64::round_ties_even)
}

/// int(x) of a float: towards zero.
fn trunc_int(x: f64) -> Result<i64> {
    whole(x, f64::trunc)
}

/// math.ceil(x) of a float.
fn ceil_int(x: f64) -> Result<i64> {
    whole(x, f64::ceil)
}

/// _rgb: the picture as RGB (itself where it is).
fn rgb(image: &Image) -> Result<Cow<'_, Image>> {
    if image.mode() == Mode::RGB {
        Ok(Cow::Borrowed(image))
    } else {
        image.convert(Mode::RGB).map(Cow::Owned).map_err(failed)
    }
}

/// A box of `image`, as Image.crop takes it.
fn crop(image: &Image, (x0, y0, x1, y1): (i64, i64, i64, i64)) -> Image {
    image.crop(x0, y0, x1, y1)
}

fn ink(colour: [i64; 3]) -> [u8; 4] {
    [colour[0], colour[1], colour[2], 255].map(|v| v.clamp(0, 255) as u8)
}

fn colour_of(colour: [i64; 3]) -> [u8; 3] {
    colour.map(|v| v.clamp(0, 255) as u8)
}

/// A transition as overlays.transition makes it, and what it keeps while it plays.
pub struct Transition {
    /// its name, the duration in seconds and what a switch and toasts do while it plays
    pub name: &'static str,
    pub duration: f64,
    pub switch: &'static str,
    pub toasts: &'static str,
    pub from_shown: bool,
    /// the new theme's palette, as its context took it (RenderContext.palette)
    palette: Dict,
    kind: Kind,
}

enum Kind {
    Cut,
    Fade,
    Slide,
    Tube,
    Entrance(Box<Entrance>),
    Masked(Masked),
    SlideFrom(Side),
    Zoom,
    LightLine(Option<((usize, usize), Image)>),
    Glitch,
    Tiles,
    Shatter(Option<(u64, Vec<Shard>)>),
    /// a plugin's (render/plugin.rs)
    Plugin(Box<dyn crate::plugin::PluginTransition>),
}

#[derive(Clone, Copy)]
enum Side {
    Left,
    Bottom,
    Top,
}

/// The maps of _Masked: what each is drawn as.
#[derive(Clone, Copy, PartialEq)]
enum Map {
    Wipe,
    Iris,
    Blinds,
    Dissolve,
}

struct Masked {
    map: Map,
    soft: f64,
    made: Option<((usize, usize), Image)>,
}

/// A piece of the old screen as Shatter breaks it: its picture, place, speed and start.
struct Shard {
    picture: Image,
    x: i64,
    y: i64,
    vx: f64,
    vy: f64,
    start: f64,
}

impl Transition {
    /// overlays.transition((name, params), context): None for none ("" or "cut"), one this
    /// does not know (a plugin's: later) and one that fails to start (an entrance's kind that
    /// is no key).
    pub fn new(name: &str, params: &Dict, renderer: &Renderer) -> Option<Transition> {
        let (duration, switch, toasts, from_shown, kind) = match name {
            "" | "cut" => return None,
            "fade" => (0.6, "wait", "wait", false, Kind::Fade),
            "slide" => (0.5, "wait", "wait", false, Kind::Slide),
            "tube" => (0.9, "restart", "over", true, Kind::Tube),
            "entrance" => (
                3.2,
                "follow",
                "hide",
                true,
                Kind::Entrance(Box::new(Entrance::new(params)?)),
            ),
            "wipe" => (0.7, "wait", "over", false, Kind::masked(Map::Wipe, 40.0)),
            "iris" => (0.8, "wait", "over", false, Kind::masked(Map::Iris, 18.0)),
            "blinds" => (0.9, "wait", "over", false, Kind::masked(Map::Blinds, 30.0)),
            "dissolve" => (0.8, "wait", "over", false, Kind::masked(Map::Dissolve, 6.0)),
            "slide-right" => (0.6, "wait", "wait", false, Kind::SlideFrom(Side::Left)),
            "slide-up" => (0.6, "wait", "wait", false, Kind::SlideFrom(Side::Bottom)),
            "slide-down" => (0.6, "wait", "wait", false, Kind::SlideFrom(Side::Top)),
            "zoom" => (0.8, "wait", "over", false, Kind::Zoom),
            "light" => (1.1, "wait", "over", true, Kind::LightLine(None)),
            "glitch" => (0.7, "wait", "over", false, Kind::Glitch),
            "tiles" => (1.2, "wait", "over", false, Kind::Tiles),
            "shatter" => (1.3, "wait", "over", false, Kind::Shatter(None)),
            _ => return Transition::plugin(name, params, renderer),
        };
        let name = BUILT_IN.iter().copied().find(|n| *n == name)?;
        Some(Transition {
            name,
            duration,
            switch,
            toasts,
            from_shown,
            palette: renderer.theme.palette.clone(),
            kind,
        })
    }

    /// A plugin's transition (overlays.transition: the registry's), from what draws the plugins
    /// for `renderer`; None where none has it or it failed to start (with a warning).
    fn plugin(name: &str, params: &Dict, renderer: &Renderer) -> Option<Transition> {
        let started = renderer.plugins.as_ref()?.transition(name, params)?;
        let (info, played) = match started {
            Ok(started) => started,
            Err(why) => {
                renderer.warn_of(format!(
                    "transition {} failed to start; switching without one: {why}",
                    lp_py::repr(name)
                ));
                return None;
            }
        };
        Some(Transition {
            name: Box::leak(name.to_string().into_boxed_str()),
            duration: info.duration,
            switch: policy(&info.switch),
            toasts: match info.toasts.as_str() {
                "over" => "over",
                "restart" => "restart",
                "hide" => "hide",
                _ => "wait",
            },
            from_shown: info.from_shown,
            palette: renderer.theme.palette.clone(),
            kind: Kind::Plugin(played),
        })
    }

    /// A cut, as the built-in table has it (overlays.transition makes none for it).
    pub fn cut(renderer: &Renderer) -> Transition {
        Transition {
            name: "cut",
            duration: 0.0,
            switch: "wait",
            toasts: "wait",
            from_shown: false,
            palette: renderer.theme.palette.clone(),
            kind: Kind::Cut,
        }
    }

    /// on_switch (switch_policy): what a switch to `theme` in `mode` does while it plays
    /// ("wait", "follow" or "restart"); the entrance restarts where the mode turned back, and
    /// else reveals the screen asked for; a plugin's says (one that fails: "wait", the error
    /// given back to be said once).
    pub fn on_switch(
        &mut self,
        theme: &str,
        mode: Option<&str>,
        mode_changed: bool,
    ) -> (&'static str, Option<String>) {
        match &mut self.kind {
            Kind::Entrance(_) if mode_changed => ("restart", None),
            Kind::Entrance(_) => ("follow", None),
            Kind::Plugin(played) => match played.on_switch(theme, mode, mode_changed) {
                Ok(said) => (policy(&said), None),
                Err(why) => ("wait", Some(why)),
            },
            _ => (self.switch, None),
        }
    }

    fn colour(&self, role: &str) -> [i64; 3] {
        role_colour(&self.palette, role)
    }

    /// frame(old, new, t): the frame it shows `t` of the way (0 to 1) from `old` to `new`;
    /// `old_key` says which old frame it is (Shatter breaks each anew, as Python does by its
    /// id). An error where Python's raises.
    pub fn frame(
        &mut self,
        old: &Image,
        old_key: u64,
        new: &Image,
        t: f64,
        renderer: &Renderer,
    ) -> Result<Image> {
        let colours = Colours {
            void: self.colour("void"),
            info: self.colour("info"),
            text: self.colour("text"),
            muted: self.colour("muted"),
        };
        match &mut self.kind {
            Kind::Cut => Ok(new.clone()),
            Kind::Fade => chops::blend(old, new, (t * t * (3.0 - 2.0 * t)) as f32).map_err(failed),
            Kind::Slide => {
                let eased = 1.0 - (1.0 - t).powf(3.0);
                let shift = round_int(new.width() as f64 * eased)?;
                let mut out = Image::blank(new.mode(), new.width(), new.height());
                out.paste(old, -shift, 0, None).map_err(failed)?;
                out.paste(new, new.width() as i64 - shift, 0, None)
                    .map_err(failed)?;
                Ok(out)
            }
            Kind::Tube => tube(old, new, t, &colours),
            Kind::Entrance(entrance) => entrance.frame(old, new, t, &colours, renderer),
            Kind::Masked(masked) => masked.frame(old, new, t),
            Kind::SlideFrom(side) => slide_from(old, new, t, *side),
            Kind::Zoom => zoom(old, new, t, &colours),
            Kind::LightLine(glow) => light_line(glow, old, new, t, &colours),
            Kind::Glitch => glitch(old, new, t),
            Kind::Tiles => tiles(old, new, t, &colours),
            Kind::Shatter(pieces) => shatter(pieces, old, old_key, new, t, 1.3),
            Kind::Plugin(played) => played.frame(old, new, t).map_err(Failed),
        }
    }
}

/// A switch policy as switch_policy keeps it: one of SWITCH_POLICIES, else "wait".
fn policy(said: &str) -> &'static str {
    match said {
        "follow" => "follow",
        "restart" => "restart",
        _ => "wait",
    }
}

/// The built-in names (overlays.BUILT_IN_TRANSITIONS).
pub const BUILT_IN: [&str; 17] = [
    "cut",
    "fade",
    "slide",
    "tube",
    "entrance",
    "wipe",
    "iris",
    "blinds",
    "dissolve",
    "slide-right",
    "slide-up",
    "slide-down",
    "zoom",
    "light",
    "glitch",
    "tiles",
    "shatter",
];

impl Kind {
    fn masked(map: Map, soft: f64) -> Kind {
        Kind::Masked(Masked {
            map,
            soft,
            made: None,
        })
    }
}

/// The theme's colours a transition draws with (role_color).
struct Colours {
    void: [i64; 3],
    info: [i64; 3],
    text: [i64; 3],
    muted: [i64; 3],
}

// -- the tube and the entrance (render/transitions.py) ------------------------------------------

/// Tube: a line ignites at mid-height and opens into two edges; between them the new screen
/// is already live.
fn tube(old: &Image, new: &Image, t: f64, c: &Colours) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let (w, h) = (new.width() as f64, new.height() as f64);
    let u = py_min(w, h) / 480.0;
    let mut out = rgb(old)?.into_owned();
    let middle = h / 2.0;
    let wide = ease_out(phase(t, 0.0, 0.3));
    let opening = smooth(phase(t, 0.15, 1.0));
    let beyond = 40.0 * u; // (the edges leave the frame before they go out)
    let top = middle - (middle + beyond) * opening;
    let bottom = middle + (h - middle + beyond) * opening;
    if opening > 0.0 {
        let (a, b) = (
            trunc_int(top)?.max(0),
            (new.height() as i64).min(ceil_int(bottom)?),
        );
        if b > a {
            let part = crop(new, (0, a, new.width() as i64, b));
            let part = rgb(&part)?;
            out.paste(&part, 0, a, None).map_err(failed)?;
        }
    }
    let bright = 1.0 - phase(t, 0.7, 1.0);
    if wide > 0.0 && bright > 0.0 {
        let look = Look {
            unit: u,
            soft: 140.0 * w / 1920.0,
            halo: 13.0,
            share: 0.55,
            colour: c.info,
            white: c.text,
        };
        let reach = wide * w / 2.0;
        let edges = if opening <= 0.0 {
            vec![middle]
        } else {
            vec![top, bottom]
        };
        for y in edges {
            add_line(
                &mut out,
                (w / 2.0 - reach, w / 2.0 + reach, y),
                bright,
                &look,
            )?;
        }
    }
    Ok(out)
}

/// Entrance: into a mode and out of it (a game, music, the PC at rest): the old frame closes
/// to a line, a band opens with a logo or a title while a text types in below it, then the
/// band closes and the line opens onto the new screen.
struct Entrance {
    title: String,
    text: String,
    /// the parameters as given: the logo's key and the kind
    logo: Option<Value>,
    kind: Option<Value>,
    /// _figure: the frame size it was made for, the picture, its halo and margin
    figure: Option<((usize, usize), Image, Image, i64)>,
}

/// The entrance's times (TIMELINE_S): the times below are seconds of it.
const TIMELINE_S: f64 = 3.2;

impl Entrance {
    /// Entrance(context, params): None where a kind is no key (Python's TypeError).
    fn new(params: &Dict) -> Option<Entrance> {
        let kind = params.get("kind").cloned();
        let default = match &kind {
            Some(Value::List(_)) | Some(Value::Dict(_)) => return None,
            Some(Value::Str(k)) => match k.as_str() {
                "start" => lp_i18n::t("Game mode"),
                "end" => lp_i18n::t("Game ended"),
                "media" => lp_i18n::t("Now playing"),
                "media-end" => lp_i18n::t("Music ended"),
                "idle" => lp_i18n::t("Taking a break"),
                "idle-end" => lp_i18n::t("Welcome back"),
                _ => String::new(),
            },
            _ => String::new(),
        };
        let text_of = |key: &str, otherwise: String| match params.get(key) {
            Some(v) if v.truthy() => v.str(),
            _ => otherwise,
        };
        Some(Entrance {
            title: text_of("title", String::new()),
            text: lp_py::upper(&text_of("text", default)),
            logo: params.get("logo").cloned(),
            kind,
            figure: None,
        })
    }

    /// _logo: the picture a service published under the logo's key, None for none.
    fn logo<'a>(&self, renderer: &'a Renderer) -> Option<&'a Image> {
        match &self.logo {
            Some(Value::Str(key)) if !key.is_empty() => renderer.image(key),
            _ => None,
        }
    }

    /// _picture: the logo (or the title in big letters) and its halo, made when the band first
    /// opens (a logo published with the switch is there by then), anew for another size.
    fn picture(
        &mut self,
        (w, h): (usize, usize),
        u: f64,
        white: [i64; 3],
        renderer: &Renderer,
    ) -> Result<(Image, Image, i64)> {
        if let Some((size, picture, halo, margin)) = &self.figure {
            if *size == (w, h) {
                return Ok((picture.clone(), halo.clone(), *margin));
            }
        }
        let room_w = 16.max((round_int(900.0 * u)?).min(w as i64 - round_int(80.0 * u)?));
        let room_h = 8.max(round_int(150.0 * u)?);
        let picture = match self.logo(renderer) {
            Some(logo) => {
                let media = matches!(&self.kind, Some(Value::Str(k)) if k == "media");
                if !self.title.is_empty() && media {
                    // (the cover: the track below it)
                    let joined = format!("{} · {}", self.text, lp_py::upper(&self.title));
                    self.text = joined.chars().take(64).collect();
                }
                let picture = logo.convert(Mode::RGBA).map_err(failed)?;
                let (pw, ph) = (picture.width() as f64, picture.height() as f64);
                let scale = py_min(py_min(room_w as f64 / pw, room_h as f64 / ph), 2.0); // (no blur)
                let size = (
                    1.max(round_int(pw * scale)?) as usize,
                    1.max(round_int(ph * scale)?) as usize,
                );
                picture
                    .resize(size.0, size.1, Filter::Lanczos, None, None)
                    .map_err(failed)?
            }
            None => {
                let title = lp_py::upper(if self.title.is_empty() {
                    &self.text
                } else {
                    &self.title
                });
                if self.title.is_empty() {
                    self.text = String::new(); // (the text stands as the title; nothing to type)
                }
                lettering(
                    &title,
                    room_w,
                    round_int(130.0 * u)?,
                    u,
                    white,
                    &renderer.env,
                )?
            }
        };
        let margin = round_int(60.0 * u)?;
        let mut halo = Image::blank(
            Mode::L,
            (picture.width() as i64 + 2 * margin).max(0) as usize,
            (picture.height() as i64 + 2 * margin).max(0) as usize,
        );
        halo.paste(
            &picture.getchannel(3).map_err(failed)?,
            margin,
            margin,
            None,
        )
        .map_err(failed)?;
        let radius = py_max(1.0, 18.0 * u) as f32;
        let halo = halo.gaussian_blur(radius, radius).map_err(failed)?;
        self.figure = Some(((w, h), picture.clone(), halo.clone(), margin));
        Ok((picture, halo, margin))
    }

    fn frame(
        &mut self,
        old: &Image,
        new: &Image,
        t: f64,
        c: &Colours,
        renderer: &Renderer,
    ) -> Result<Image> {
        if t >= 1.0 {
            return Ok(new.clone());
        }
        let (wi, hi) = (new.width() as i64, new.height() as i64);
        let (w, h) = (wi as f64, hi as f64);
        let u = py_min(w, h) / 480.0;
        let s = t * TIMELINE_S;
        let mut out = Image::new(Mode::RGB, new.width(), new.height(), &colour_of(c.void));
        let middle = round_int(h / 2.0 - 20.0 * u)?;
        let mid = middle as f64;
        let beyond = 40.0 * u;
        let line = Look {
            unit: u,
            soft: 1.0,
            halo: 13.0,
            share: 0.55,
            colour: c.info,
            white: c.text,
        };

        // The old frame closes as a tube: two edges run onto the middle line.
        let closing = smooth(phase(s, 0.0, 0.6));
        if closing < 1.0 {
            let top = -beyond + (mid + beyond) * closing;
            let bottom = h + beyond - (h + beyond - mid) * closing;
            let (a, b) = (trunc_int(top)?.max(0), hi.min(ceil_int(bottom)?));
            if b > a {
                let part = crop(old, (0, a, wi, b));
                out.paste(&*rgb(&part)?, 0, a, None).map_err(failed)?;
            }
            for y in [top, bottom] {
                add_line(
                    &mut out,
                    (0.0, w, y),
                    0.9 * py_min(1.0, 4.0 * closing),
                    &line,
                )?;
            }
        }

        // The line opens to a band around the logo or title, which lights up; later the band
        // closes again, and out of that line the new screen opens.
        let shut = smooth(phase(s, 2.30, 2.65));
        let opening = smooth(phase(s, 0.80, 1.30)) * (1.0 - shut);
        let mut half = 0.0;
        if s >= 0.80 {
            let (picture, halo, margin) =
                self.picture((new.width(), new.height()), u, c.text, renderer)?;
            let half_i = (picture.height() as i64).div_euclid(2) + round_int(34.0 * u)?;
            half = half_i as f64;
            let (a, b) = (
                trunc_int(mid - half * opening)?,
                ceil_int(mid + half * opening)?,
            );
            if opening > 0.0 && b > a {
                let mut stage =
                    Image::new(Mode::RGB, new.width(), (b - a) as usize, &colour_of(c.void));
                let px = (wi - picture.width() as i64).div_euclid(2);
                let py = middle - (picture.height() as i64).div_euclid(2) - a;
                let mut glow = ease_out(phase(s, 0.90, 1.30));
                if s >= 1.30 {
                    // (it dims to a steady glow)
                    glow *= 0.35 + 0.65 * (-(s - 1.30) * 3.0).exp();
                }
                if glow > 0.004 {
                    add_light(
                        &mut stage,
                        (px - margin, py - margin),
                        &scaled(halo, glow)?,
                        c.info,
                    )?;
                }
                stage
                    .paste(&picture, px, py, Some(&picture))
                    .map_err(failed)?;
                out.paste(&stage, 0, a, None).map_err(failed)?;
            }
            if !self.text.is_empty() && 1.25 < s && shut < 1.0 {
                // (the text types in below)
                let letters = self.text.chars().count();
                let shown_n = trunc_int(letters as f64 * phase(s, 1.30, 1.70) + 0.001)?;
                let shown: String = self.text.chars().take(shown_n.max(0) as usize).collect();
                let font = face(&renderer.env, "Barlow-Medium", 7.max(round_int(26.0 * u)?))?;
                let mut track = 12.0 * u;
                while track > 0.0 && tracked_width(&font, &self.text, track)? > w - 20.0 * u {
                    track -= u; // (tighter on a narrow panel)
                }
                let x = (w - tracked_width(&font, &self.text, track)?) / 2.0;
                draw_tracked(
                    &mut out,
                    (x, mid + half + 46.0 * u),
                    &shown,
                    &font,
                    ink(mix(c.muted, c.void, 1.0 - shut)),
                    track,
                )?;
            }
        }

        // The new screen opens, live, out of the closed line.
        let reveal = smooth(phase(s, 2.65, TIMELINE_S));
        let (mut top, mut bottom) = (mid, mid);
        if reveal > 0.0 {
            top = mid - (mid + beyond) * reveal;
            bottom = mid + (h - mid + beyond) * reveal;
            let (a, b) = (ceil_int(top)?.max(0), hi.min(trunc_int(bottom)?));
            if b > a {
                let part = crop(new, (0, a, wi, b));
                out.paste(&*rgb(&part)?, 0, a, None).map_err(failed)?;
            }
        }

        // One light over all of it: bright where edges meet, calm while the band stands, and it
        // goes out as it moves out. Nothing ignites a second time.
        if closing >= 1.0 {
            let bright = if reveal > 0.0 {
                1.0 - smooth(phase(s, 2.85, TIMELINE_S))
            } else if shut > 0.0 {
                0.3 + 0.7 * shut
            } else {
                1.0 - 0.7 * smooth(phase(s, 0.80, 1.30))
            };
            let edges = if reveal > 0.0 {
                vec![top, bottom]
            } else if opening > 0.0 {
                vec![mid - half * opening, mid + half * opening]
            } else {
                vec![mid]
            };
            for y in edges {
                add_line(&mut out, (0.0, w, y), bright, &line)?;
            }
        }
        Ok(out)
    }
}

/// _lettering: `text` in big, spaced letters on a clear picture `height` high, as narrow as it
/// is (it gets smaller to fit `width`).
fn lettering(
    text: &str,
    width: i64,
    height: i64,
    u: f64,
    colour: [i64; 3],
    env: &lp_theme::Env,
) -> Result<Image> {
    let (mut size, mut track) = (round_int(96.0 * u)?, 10.0 * u);
    let mut font = face(env, "Barlow-Bold", size)?;
    while size > 8 && tracked_width(&font, text, track)? + 20.0 * u > width as f64 {
        size = 8.max(round_int(size as f64 * 0.92)?);
        font = face(env, "Barlow-Bold", size)?;
        track *= 0.92;
    }
    let span = width.min(1.max(round_int(tracked_width(&font, text, track)? + 20.0 * u)?));
    let mut mask = Image::blank(Mode::L, span.max(0) as usize, height.max(1) as usize);
    let baseline = round_int(height as f64 * 100.0 / 130.0)? as f64;
    draw_tracked(
        &mut mask,
        (10.0 * u, baseline),
        text,
        &font,
        [255, 0, 0, 0],
        track,
    )?;
    let c = colour_of(colour);
    let mut picture = Image::new(
        Mode::RGBA,
        mask.width(),
        mask.height(),
        &[c[0], c[1], c[2], 0],
    );
    picture.putalpha(&mask).map_err(failed)?;
    Ok(picture)
}

// -- the plain handovers (render/handovers.py): the new screen where a map lies below a rising
// threshold -------------------------------------------------------------------------------------

impl Masked {
    fn frame(&mut self, old: &Image, new: &Image, t: f64) -> Result<Image> {
        if t >= 1.0 {
            return Ok(new.clone());
        }
        let size = (new.width(), new.height());
        if self.made.as_ref().is_none_or(|(s, _)| *s != size) {
            self.made = Some((size, map_of(self.map, size.0, size.1)?));
        }
        let level = smooth(t) * (255.0 + self.soft);
        let lut: Vec<i64> = (0..256)
            .map(|v| {
                let v = v as f64;
                if v <= level - self.soft {
                    Ok(255)
                } else if v > level {
                    Ok(0)
                } else {
                    round_int(255.0 * (level - v) / self.soft)
                }
            })
            .collect::<Result<_>>()?;
        let made = &self.made.as_ref().expect("made").1;
        let mask = made.point(&lut).map_err(failed)?;
        chops::composite(&*rgb(new)?, &*rgb(old)?, &mask).map_err(failed)
    }
}

/// _row: a map that changes across, the same down every column.
fn row(values: Vec<u8>, w: usize, h: usize) -> Result<Image> {
    let n = values.len();
    Image::from_bytes(Mode::L, n, 1, values)
        .and_then(|r| r.resize(w, h, Filter::Bilinear, None, None))
        .map_err(failed)
}

/// _map: the map (L) a masked handover makes once for a size.
fn map_of(map: Map, w: usize, h: usize) -> Result<Image> {
    let (wf, hf) = (w as f64, h as f64);
    let u = py_min(wf, hf) / 480.0;
    match map {
        // a soft edge goes across, from the left
        Map::Wipe => row((0..=255).collect(), w, h),
        // a circle opens from the middle (it reaches the corners at 255)
        Map::Iris => {
            let side = ceil_int(lp_py::math::hypot(wf, hf))?;
            let circle = Image::radial_gradient()
                .resize(side as usize, side as usize, Filter::Bilinear, None, None)
                .map_err(failed)?;
            let (left, top) = (
                (side - w as i64).div_euclid(2),
                (side - h as i64).div_euclid(2),
            );
            Ok(circle.crop(left, top, left + w as i64, top + h as i64))
        }
        // upright slats turn one after another, from the left, each from its own left edge
        Map::Blinds => {
            let slats = 4.max(round_int(wf / (150.0 * u))?);
            let width = wf / slats as f64;
            let mut values = Vec::with_capacity(w);
            for x in 0..w {
                let (k, inside) = lp_py::num::float_div_mod(x as f64, width);
                let v = 255.0 * (0.45 * k / 1.max(slats - 1) as f64 + 0.55 * inside / width);
                values.push(255.min(round_int(v)?).clamp(0, 255) as u8);
            }
            Image::from_bytes(Mode::L, w, 1, values)
                .and_then(|r| r.resize(w, h, Filter::Nearest, None, None))
                .map_err(failed)
        }
        // the new screen comes through in small squares, here and there (the same squares
        // every time: calm, not noise)
        Map::Dissolve => {
            let block = 2.max(round_int(8.0 * u)?);
            let cols = ceil_int(wf / block as f64)?.max(0) as usize;
            let rows = ceil_int(hf / block as f64)?.max(0) as usize;
            let mut chance = Random::new(0x5EED);
            let bytes = (0..cols * rows)
                .map(|_| chance.randbelow(256) as u8)
                .collect();
            let noise = Image::from_bytes(Mode::L, cols, rows, bytes).map_err(failed)?;
            let b = block as usize;
            Ok(noise
                .resize(cols * b, rows * b, Filter::Nearest, None, None)
                .map_err(failed)?
                .crop(0, 0, w as i64, h as i64))
        }
    }
}

/// _SlideFrom: the new screen pushes the old one out, from one side.
fn slide_from(old: &Image, new: &Image, t: f64, side: Side) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let (w, h) = (new.width() as i64, new.height() as i64);
    let eased = 1.0 - (1.0 - t).powf(3.0);
    let (dx, dy) = match side {
        Side::Left => (1, 0),
        Side::Bottom => (0, -1),
        Side::Top => (0, 1),
    };
    let (sx, sy) = (
        round_int((dx * w) as f64 * eased)?,
        round_int((dy * h) as f64 * eased)?,
    );
    let mut out = Image::blank(Mode::RGB, new.width(), new.height());
    out.paste(&*rgb(old)?, sx, sy, None).map_err(failed)?;
    out.paste(&*rgb(new)?, sx - dx * w, sy - dy * h, None)
        .map_err(failed)?;
    Ok(out)
}

/// Zoom: the old screen draws back into the dark, the new one comes forward out of it.
fn zoom(old: &Image, new: &Image, t: f64, c: &Colours) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    const DEPTH: f64 = 0.12; // (how far back a screen stands at its farthest)
    let (w, h) = (new.width(), new.height());
    let going = t < 0.5;
    let u = if going {
        smooth(t / 0.5)
    } else {
        1.0 - smooth((t - 0.5) / 0.5)
    }; // (0 near, 1 far)
    let scale = 1.0 - DEPTH * u;
    let picture = rgb(if going { old } else { new })?;
    let size = (
        1.max(round_int(w as f64 * scale)?) as usize,
        1.max(round_int(h as f64 * scale)?) as usize,
    );
    let dark = colour_of(c.void);
    let mut out = Image::new(Mode::RGB, w, h, &dark);
    let small = picture
        .resize(size.0, size.1, Filter::Bilinear, None, None)
        .map_err(failed)?;
    let small = chops::blend(
        &small,
        &Image::new(Mode::RGB, size.0, size.1, &dark),
        py_min(1.0, 0.9 * u) as f32,
    )
    .map_err(failed)?;
    out.paste(
        &small,
        (w as i64 - size.0 as i64).div_euclid(2),
        (h as i64 - size.1 as i64).div_euclid(2),
        None,
    )
    .map_err(failed)?;
    Ok(out)
}

// -- the elaborate ones -------------------------------------------------------------------------

/// LightLine: a line of light runs across, the old screen ahead of it and the new one behind,
/// and goes out as it leaves.
fn light_line(
    kept: &mut Option<((usize, usize), Image)>,
    old: &Image,
    new: &Image,
    t: f64,
    c: &Colours,
) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let (w, h) = (new.width() as i64, new.height() as i64);
    if kept
        .as_ref()
        .is_none_or(|(size, _)| *size != (new.width(), new.height()))
    {
        // _strip: the line's light, upright, made once: a horizontal line drawn along a strip
        // as long as the frame is high, turned
        let u = py_min(w as f64, h as f64) / 480.0;
        let half = 3.max(round_int(32.0 * u)?);
        let mut strip = Image::blank(Mode::RGB, (h + 2 * half) as usize, (2 * half) as usize);
        let look = Look {
            unit: u,
            soft: 1.0,
            halo: 13.0,
            share: 0.55,
            colour: c.info,
            white: c.text,
        };
        add_line(
            &mut strip,
            (-half as f64, (h + 3 * half) as f64, half as f64),
            1.0,
            &look,
        )?;
        let turned = strip
            .crop(half, 0, half + h, 2 * half)
            .transpose(Transpose::Rotate90);
        *kept = Some(((new.width(), new.height()), turned));
    }
    let glow = &kept.as_ref().expect("kept").1;
    let gw = glow.width() as i64;
    let x = round_int(-(gw as f64) + (w + 2 * gw) as f64 * smooth(t))?;
    let mut out = rgb(old)?.into_owned();
    if x > 0 {
        let part = crop(&*rgb(new)?, (0, 0, w.min(x), h));
        out.paste(&part, 0, 0, None).map_err(failed)?;
    }
    let bright = ease_out(phase(t, 0.0, 0.15)) * (1.0 - ease_in(phase(t, 0.8, 1.0)));
    let (a, b) = (0.max(x - gw.div_euclid(2)), w.min(x + gw.div_euclid(2)));
    if b > a && bright > 0.004 {
        let at = x - gw.div_euclid(2);
        let light = glow.crop(a - at, 0, b - at, h);
        let light = if bright < 0.999 {
            light
                .point_with(|v| f64::from(v) * bright)
                .map_err(failed)?
        } else {
            light
        };
        let sum = chops::add(&out.crop(a, 0, b, h), &light, 1.0, 0).map_err(failed)?;
        out.paste(&sum, a, 0, None).map_err(failed)?;
    }
    Ok(out)
}

/// Glitch: a short digital fault: rows slip sideways, the colours split, bits of the other
/// screen flash in; at its height the new screen takes over and the fault fades.
fn glitch(old: &Image, new: &Image, t: f64) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    let (w, h) = (new.width() as i64, new.height() as i64);
    let u = py_min(w as f64, h as f64) / 480.0;
    let strength = 1.0 - (t - 0.5).abs() * 2.0; // (strongest in the middle)
    let (base, other) = if t < 0.5 {
        (rgb(old)?, rgb(new)?)
    } else {
        (rgb(new)?, rgb(old)?)
    };
    // (a new fault a few times a second, not every frame)
    let mut chance = Random::new(trunc_int(t * 40.0)?);
    let mut out = base.clone().into_owned();
    for _ in 0..round_int(10.0 * strength)?.saturating_add(1).max(0) {
        let top = chance
            .randrange(h)
            .ok_or_else(|| Failed("empty range for randrange()".into()))?;
        let tall = 2.max(round_int(chance.uniform(4.0, 40.0) * u)?);
        let shift = round_int(chance.uniform(-80.0, 80.0) * u * strength)?;
        let from = if chance.random() < 0.25 {
            &other
        } else {
            &base
        };
        let band = crop(from, (0, top, w, h.min(top + tall)));
        out.paste(&band.offset(shift, 0), 0, top, None)
            .map_err(failed)?;
    }
    let split = round_int(12.0 * u * strength)?;
    if split != 0 {
        let bands = out.split();
        out = merge(
            Mode::RGB,
            &[
                &bands[0].offset(split, 0),
                &bands[1],
                &bands[2].offset(-split, 0),
            ],
        )
        .map_err(failed)?;
    }
    Ok(out)
}

/// Tiles: the screen turns over tile by tile, from one corner across, the new one on their
/// backs.
fn tiles(old: &Image, new: &Image, t: f64, c: &Colours) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    const SPREAD: f64 = 0.55; // (the share of the time over which the tiles begin)
    let (w, h) = (new.width() as i64, new.height() as i64);
    let u = py_min(w as f64, h as f64) / 480.0;
    let side = 16.max(round_int(120.0 * u)?);
    let cols = ceil_int(w as f64 / side as f64)?;
    let rows = ceil_int(h as f64 / side as f64)?;
    let dark = colour_of(c.void);
    let mut out = Image::new(Mode::RGB, new.width(), new.height(), &dark);
    let (old, new) = (rgb(old)?, rgb(new)?);
    let last = 1.max(cols + rows - 2);
    for row in 0..rows.max(0) {
        for col in 0..cols.max(0) {
            let bounds = (
                col * side,
                row * side,
                w.min((col + 1) * side),
                h.min((row + 1) * side),
            );
            let start = SPREAD * (col + row) as f64 / last as f64;
            let turn = smooth(phase(t, start, start + 1.0 - SPREAD));
            let width = (std::f64::consts::PI * turn).cos().abs();
            let face = crop(if turn < 0.5 { &old } else { &new }, bounds);
            if width >= 0.999 {
                out.paste(&face, bounds.0, bounds.1, None).map_err(failed)?;
                continue;
            }
            let size = (
                1.max(round_int(face.width() as f64 * width)?) as usize,
                face.height(),
            );
            if size.0 <= 1 {
                continue;
            }
            let edge = face
                .resize(size.0, size.1, Filter::Bilinear, None, None)
                .map_err(failed)?;
            let edge = chops::blend(
                &edge,
                &Image::new(Mode::RGB, size.0, size.1, &dark),
                (0.5 * (1.0 - width)) as f32,
            )
            .map_err(failed)?;
            let x = bounds.0 + (face.width() as i64 - size.0 as i64).div_euclid(2);
            out.paste(&edge, x, bounds.1, None).map_err(failed)?;
        }
    }
    Ok(out)
}

/// Shatter: the old screen breaks into pieces that fly apart from the middle and fall, the new
/// one already there behind them; the pieces made once for each old frame.
fn shatter(
    kept: &mut Option<(u64, Vec<Shard>)>,
    old: &Image,
    old_key: u64,
    new: &Image,
    t: f64,
    duration: f64,
) -> Result<Image> {
    if t >= 1.0 {
        return Ok(new.clone());
    }
    if kept.as_ref().is_none_or(|(key, _)| *key != old_key) {
        *kept = Some((old_key, broken(old)?));
    }
    let (w, h) = (new.width() as i64, new.height() as i64);
    let u = py_min(w as f64, h as f64) / 480.0;
    let mut out = rgb(new)?.into_owned();
    let seconds = t * duration;
    for shard in &kept.as_ref().expect("kept").1 {
        let s = py_max(0.0, seconds - shard.start * duration);
        let px = round_int(shard.x as f64 + shard.vx * s)?;
        let py = round_int(shard.y as f64 + shard.vy * s + 0.5 * 2400.0 * u * s * s)?; // (it falls)
        let (pw, ph) = (shard.picture.width() as i64, shard.picture.height() as i64);
        if -pw < px && px < w && -ph < py && py < h {
            out.paste(&shard.picture, px, py, None).map_err(failed)?;
        }
    }
    Ok(out)
}

/// _broken: the pieces of the old frame, each with its place, its speed and its start.
fn broken(old: &Image) -> Result<Vec<Shard>> {
    let (w, h) = (old.width() as i64, old.height() as i64);
    let u = py_min(w as f64, h as f64) / 480.0;
    let side = 12.max(round_int(60.0 * u)?);
    let mut chance = Random::new(0xB10C);
    let (cx, cy) = (w as f64 / 2.0, h as f64 / 2.0);
    let reach = lp_py::math::hypot(cx, cy);
    let picture = rgb(old)?;
    let mut pieces = Vec::new();
    let mut y = 0;
    while y < h {
        let mut x = 0;
        while x < w {
            let bounds = (x, y, w.min(x + side), h.min(y + side));
            let dx = bounds.0 as f64 + side as f64 / 2.0 - cx;
            let dy = bounds.1 as f64 + side as f64 / 2.0 - cy;
            let far = lp_py::math::hypot(dx, dy) / reach;
            let speed = (0.6 + chance.random()) * 900.0 * u;
            let angle = dy.atan2(dx) + chance.uniform(-0.4, 0.4);
            let (vx, vy) = (angle.cos() * speed, angle.sin() * speed - 300.0 * u);
            pieces.push(Shard {
                picture: crop(&picture, bounds),
                x: bounds.0,
                y: bounds.1,
                vx,
                vy,
                start: 0.45 * far,
            });
            x += side;
        }
        y += side;
    }
    Ok(pieces)
}
