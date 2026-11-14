//! Modules (theme/modules.py): building blocks that fill cells of a theme's grid. Before a
//! theme is drawn, every module becomes plain widgets laid out for its size, the way
//! home-screen widgets show more the larger they are; colours, fonts and the card behind each
//! module come from the theme's look. The widgets are Python's to the field: the same kinds,
//! ids, numbers (an int where Python has an int, a float where it has one) and order.

use std::collections::HashMap;

use lp_py::{Dict, Value};

use crate::design::{self, MODULE_LABEL, MODULE_PADDING, MODULE_PADDING_BARE};
use crate::grid::{make_grid, Grid, Settings};
use crate::model::{normalize_widget, Env, Error, Kind, Result, Theme};

mod cards;
pub mod info;
mod layouts;
mod lists;
mod media;
mod readings;
mod sky;
pub mod slots;
mod status;
mod time;
mod weather;

/// A number as Python has it in the layout: an int or a float, whichever its arithmetic made.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum N {
    I(i64),
    F(f64),
}

impl N {
    pub fn f(self) -> f64 {
        match self {
            N::I(i) => i as f64,
            N::F(f) => f,
        }
    }

    /// int(x): toward 0.
    pub fn to_int(self) -> i64 {
        match self {
            N::I(i) => i,
            N::F(f) => f as i64,
        }
    }

    pub fn value(self) -> Value {
        match self {
            N::I(i) => Value::int(i),
            N::F(f) => Value::Float(f),
        }
    }

    fn of(v: &Value) -> N {
        match v {
            Value::Int(_) | Value::Bool(_) => N::I(v.to_f64().unwrap_or(0.0) as i64),
            Value::Float(f) => N::F(*f),
            _ => N::I(0),
        }
    }
}

impl From<i64> for N {
    fn from(i: i64) -> N {
        N::I(i)
    }
}

impl From<f64> for N {
    fn from(f: f64) -> N {
        N::F(f)
    }
}

/// Python's max(a, b): b only where it is larger.
pub fn pmax(a: impl Into<N>, b: impl Into<N>) -> N {
    let (a, b) = (a.into(), b.into());
    if b.f() > a.f() {
        b
    } else {
        a
    }
}

/// Python's min(a, b): b only where it is smaller.
pub fn pmin(a: impl Into<N>, b: impl Into<N>) -> N {
    let (a, b) = (a.into(), b.into());
    if b.f() < a.f() {
        b
    } else {
        a
    }
}

/// max(a, b) of floats, as Python takes it.
pub fn fmax(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// min(a, b) of floats, as Python takes it.
pub fn fmin(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// round(x): the nearest int, halfway the even one.
pub fn round(x: impl Into<N>) -> i64 {
    match x.into() {
        N::I(i) => i,
        N::F(f) => lp_py::round(f) as i64,
    }
}

/// How many of `len` items `seq[:n]` takes (a negative `n`: all but that many).
pub fn py_take(len: usize, n: i64) -> usize {
    if n >= 0 {
        (n as usize).min(len)
    } else {
        len.saturating_sub(n.unsigned_abs() as usize)
    }
}

/// a // b of floats, as Python divides them (1.0 // 0.1 is 9.0: not floor(a / b)).
pub fn floor_div(a: f64, b: f64) -> f64 {
    lp_py::num::float_div_mod(a, b).0
}

/// round(x, digits) of a float.
pub fn round_to(x: f64, digits: usize) -> f64 {
    lp_py::round_digits(x, digits)
}

/// _clamp: max(low, min(high, value)).
pub fn clamp(value: impl Into<N>, low: impl Into<N>, high: impl Into<N>) -> N {
    pmax(low, pmin(high, value))
}

/// Barlow: the top of capitals and digits below the text's y, per font size
pub const CAP_TOP: f64 = 0.30;
/// their height, per font size
pub const CAP: f64 = 0.70;
/// "00:00" in font sizes
pub const CLOCK_WIDTH: f64 = 2.65;
/// what a card's corners and outline scale with, in a style (expand sets it)
pub const FRAME_U: &str = "\0frame_u";
/// kinds without a card unless asked
const CARDLESS: [&str; 4] = ["clock", "text", "analog", "image"];

/// `format(n, "02X")`
fn hex2(n: i64) -> String {
    if n < 0 {
        format!("-{:X}", n.unsigned_abs())
    } else {
        format!("{n:02X}")
    }
}

/// _rgb: any colour a theme may hold as red, green, blue.
fn rgb(color: &str) -> Result<[i64; 3]> {
    let found = getrgb(color)?;
    Ok([found[0], found[1], found[2]])
}

fn getrgb(color: &str) -> Result<Vec<i64>> {
    lp_pixels::colour::getrgb(color).map_err(|e| Error {
        kind: Kind::Value,
        msg: e.to_string(),
    })
}

/// _alpha: how opaque a colour is, 0 to 255.
fn alpha(color: &str) -> Result<i64> {
    let found = getrgb(color)?;
    Ok(found.get(3).copied().unwrap_or(255))
}

/// _hex: "#RRGGBB", or "#RRGGBBAA" where it is not opaque.
pub fn hex(color: &str) -> Result<String> {
    let a = alpha(color)?;
    let [r, g, b] = rgb(color)?;
    let mut out = format!("#{}{}{}", hex2(r), hex2(g), hex2(b));
    if a < 255 {
        out.push_str(&hex2(a));
    }
    Ok(out)
}

/// faded: `color` as "#RRGGBBAA" with `share` of the opacity it has.
pub fn faded(color: &str, share: f64) -> Result<String> {
    let [r, g, b] = rgb(color)?;
    let a = round(alpha(color)? as f64 * share);
    Ok(format!("#{}{}{}{}", hex2(r), hex2(g), hex2(b), hex2(a)))
}

/// mix: `a` moved `amount` (0-1) of the way to `b`.
pub fn mix(a: &str, b: &str, amount: f64) -> Result<String> {
    let (ca, cb) = (rgb(a)?, rgb(b)?);
    let mut out = String::from("#");
    for (x, y) in ca.iter().zip(cb.iter()) {
        out.push_str(&hex2(round(*x as f64 + (y - x) as f64 * amount)));
    }
    Ok(out)
}

/// render/formatting.py's counts: whether a format shows its value as a number that may
/// count to a new reading (`{value:.0f}°`, `{value:bytes}/s`), not a text or a time.
pub fn counts(fmt: &str) -> bool {
    // \{value:(bytes|rate|rate_unit|gb|[^{}A-Za-z]*[bcdeEfFgGnoxX%])\}
    let mut from = 0;
    while let Some(at) = fmt[from..].find("{value:") {
        let rest: Vec<char> = fmt[from + at + 7..].chars().collect();
        let starts = |word: &str| {
            let w: Vec<char> = word.chars().collect();
            rest.len() > w.len() && rest[..w.len()] == w[..] && rest[w.len()] == '}'
        };
        if starts("bytes") || starts("rate") || starts("rate_unit") || starts("gb") {
            return true;
        }
        let run = rest
            .iter()
            .position(|&c| c == '{' || c == '}' || c.is_ascii_alphabetic())
            .unwrap_or(rest.len());
        let kinds = "bcdeEfFgGnoxX%";
        if run < rest.len() && kinds.contains(rest[run]) && rest.get(run + 1) == Some(&'}') {
            return true;
        }
        if run >= 1 && run < rest.len() && rest[run - 1] == '%' && rest[run] == '}' {
            return true;
        }
        from += at + 1;
    }
    false
}

/// A source of readings a module shows (CPU, GPU, RAM, disk, network, a fan).
#[derive(Clone, Debug)]
pub struct Source {
    pub label: String,
    /// the main reading and its format
    pub load: &'static str,
    pub fmt: &'static str,
    pub icon: &'static str,
    /// palette role of its colour
    pub role: &'static str,
    /// (sensor, format, label)
    pub details: Vec<(&'static str, &'static str, String)>,
    /// the hardware's name, if the sensors report one
    pub name: &'static str,
    pub low: Option<i64>,
    pub high: Option<i64>,
    pub scale: &'static str,
}

impl Source {
    fn new(
        label: String,
        load: &'static str,
        fmt: &'static str,
        icon: &'static str,
        role: &'static str,
    ) -> Source {
        Source {
            label,
            load,
            fmt,
            icon,
            role,
            details: Vec::new(),
            name: "",
            low: Some(0),
            high: Some(100),
            scale: "linear",
        }
    }
}

/// _sources: built on use, the labels in the language.
pub fn sources() -> Vec<(&'static str, Source)> {
    let t = lp_i18n::t;
    let rate = "{value:bytes}/s";
    let mut cpu = Source::new("CPU".into(), "cpu.load", "{value:.0f}%", "cpu", "cpu");
    cpu.details = vec![
        ("cpu.temp", "{value:.0f}°C", t("Temp")),
        ("cpu.power", "{value:.0f} W", t("Power")),
        ("cpu.freq", "{value:.0f} MHz", t("Clock")),
    ];
    cpu.name = "cpu.name";
    let mut gpu = Source::new("GPU".into(), "gpu.load", "{value:.0f}%", "gpu", "gpu");
    gpu.details = vec![
        ("gpu.temp", "{value:.0f}°C", t("Temp")),
        ("gpu.power", "{value:.0f} W", t("Power")),
        ("gpu.fan", "{value:.0f} RPM", t("Fan")),
    ];
    gpu.name = "gpu.name";
    let mut mem = Source::new(t("RAM"), "mem.load", "{value:.0f}%", "ram", "mem");
    mem.details = vec![
        ("mem.used", "{value:.1f} GB", t("Used")),
        ("mem.total", "{value:.0f} GB", t("Total")),
        ("swap.load", "{value:.0f}%", t("Swap")),
    ];
    mem.name = "mem.name";
    let mut disk = Source::new(t("Disk"), "disk.load", "{value:.0f}%", "disk", "disk");
    disk.details = vec![
        ("disk.used", "{value:.0f} GB", t("Used")),
        ("disk.total", "{value:.0f} GB", t("Total")),
        ("disk.read", rate, t("Read")),
    ];
    let mut net = Source::new(t("Network"), "net.down", rate, "network", "net");
    net.details = vec![("net.down", rate, t("Down")), ("net.up", rate, t("Up"))];
    net.high = None;
    net.scale = "sqrt";
    let mut fan = Source::new(t("Fan"), "gpu.fan", "{value:.0f} RPM", "fan", "gpu");
    fan.high = None;
    vec![
        ("cpu", cpu),
        ("gpu", gpu),
        ("mem", mem),
        ("disk", disk),
        ("net", net),
        ("fan", fan),
    ]
}

fn source_of(name: &str) -> Option<Source> {
    sources()
        .into_iter()
        .find(|(k, _)| *k == name)
        .map(|(_, s)| s)
}

/// Fields of a widget in the order Python's call gives them.
pub type Fields = Vec<(&'static str, Value)>;

fn opt(color: Option<String>) -> Value {
    color.map_or(Value::None, Value::Str)
}

/// The widget types with a `finish` field (theme.model), the module left out.
fn finished(kind: &str) -> bool {
    kind != "module"
        && crate::tables::WIDGETS
            .iter()
            .any(|(k, fields)| *k == kind && fields.iter().any(|f| f.name == "finish"))
}

/// _Build: collects the widgets of one module, laid out in its box.
pub struct Build<'a> {
    pub module: Dict,
    pub x: i64,
    pub y: i64,
    pub w: i64,
    pub h: i64,
    pub cols: i64,
    pub rows: i64,
    colors: &'a Dict,
    pub style: &'a Dict,
    pub env: &'a Env,
    pub u: i64,
    pub card_on: bool,
    pub pad: i64,
    pub ix: i64,
    pub iy: i64,
    pub iw: i64,
    pub ih: i64,
    /// small labels: every size of type in a module is a multiple of theirs
    pub ts: i64,
    pub glow: f64,
    pub out: Vec<Dict>,
    /// added to every widget (a source's fallback)
    pub needs: String,
    widths: HashMap<(String, String), f64>,
}

impl<'a> Build<'a> {
    pub fn new(
        module: Dict,
        bx: [i64; 4],
        colors: &'a Dict,
        style: &'a Dict,
        env: &'a Env,
    ) -> Build<'a> {
        let [x, y, w, h] = bx;
        let int = |k: &str| module.get(k).map_or(N::I(0), N::of).pipe_int();
        let (cols, rows) = (int("cols"), int("rows"));
        let u = w.min(h);
        let card = module.get("card").map(Value::str).unwrap_or_default();
        let kind = module.get("module").map(Value::str).unwrap_or_default();
        let card_on = (card == "on" || (card == "auto" && !CARDLESS.contains(&kind.as_str())))
            && style.get("card").map(Value::str).unwrap_or_default() != "none";
        let padding = if card_on {
            MODULE_PADDING
        } else {
            MODULE_PADDING_BARE
        };
        let pad = round(clamp(u as f64 * padding.0, padding.1, padding.2));
        let ts = round(clamp(
            u as f64 * MODULE_LABEL.0,
            MODULE_LABEL.1,
            MODULE_LABEL.2,
        ));
        let glow = style.get("glow").and_then(Value::to_f64).unwrap_or(0.0);
        Build {
            module,
            x,
            y,
            w,
            h,
            cols,
            rows,
            colors,
            style,
            env,
            u,
            card_on,
            pad,
            ix: x + pad,
            iy: y + pad,
            iw: w - 2 * pad,
            ih: h - 2 * pad,
            ts,
            glow,
            out: Vec::new(),
            needs: String::new(),
            widths: HashMap::new(),
        }
    }

    /// A field of the module, as its widget holds it.
    pub fn m(&self, key: &str) -> Value {
        self.module.get(key).cloned().unwrap_or(Value::None)
    }

    /// A text field of the module ("" for none).
    pub fn ms(&self, key: &str) -> String {
        match self.module.get(key) {
            Some(Value::Str(s)) => s.clone(),
            _ => String::new(),
        }
    }

    /// A style's text field.
    pub fn st(&self, key: &str) -> String {
        self.style.get(key).map(Value::str).unwrap_or_default()
    }

    // colours
    pub fn c(&self, role: &str) -> String {
        self.colors.get(role).map(Value::str).unwrap_or_default()
    }

    /// The module's own colour if it has one, else the role's.
    pub fn tint(&self, role: &str) -> String {
        match self.module.get("color") {
            Some(v) if v.truthy() => v.str(),
            _ => self.c(role),
        }
    }

    pub fn deep(&self, color: &str) -> Result<String> {
        mix(color, &self.c("surface"), 0.55)
    }

    /// _width_per_size: how wide `text` is in `font`, per pixel of font size, as the panel
    /// draws it (what the font has no glyph for in the system font that draws it).
    pub fn width_per_size(&mut self, font: &str, text: &str) -> Result<f64> {
        let key = (font.to_string(), text.to_string());
        if let Some(w) = self.widths.get(&key) {
            return Ok(*w);
        }
        let made =
            match self.env.builtin_font_path(font) {
                None => {
                    // a theme's own font: about as wide as Barlow, a wide character a square
                    if text == "00:00" {
                        CLOCK_WIDTH
                    } else {
                        lp_py::sum_floats(text.chars().map(|ch| {
                            if lp_py::east_asian_wide(ch) {
                                1.0
                            } else {
                                0.55
                            }
                        }))
                    }
                }
                Some(path) => {
                    use lp_text::text::TextFace;
                    let face = lp_text::fallback::FallbackFont::open(&path, 100.0, 0, None)
                        .map_err(|e| Error {
                            kind: Kind::Os("OSError"),
                            msg: e.to_string(),
                        })?;
                    face.getlength(text, "", None, None, None)
                        .map_err(|e| Error {
                            kind: Kind::Value,
                            msg: e.to_string(),
                        })?
                        / 100.0
                }
            };
        self.widths.insert(key, made);
        Ok(made)
    }

    /// add: a widget of the module (its fields in the order given).
    pub fn add(&mut self, kind: &str, name: &str, fields: Fields) -> Result<usize> {
        let mut widget = Dict::default();
        widget.insert("type".into(), kind.into());
        widget.insert("id".into(), name.into());
        for (k, v) in fields {
            widget.insert(k.into(), v);
        }
        if !self.needs.is_empty() {
            let own = widget.get("needs").map(Value::str).unwrap_or_default();
            let needs = if own.is_empty() {
                self.needs.clone()
            } else {
                format!("{own},{}", self.needs)
            };
            widget.insert("needs".into(), needs.into());
        }
        let mut finish = self
            .module
            .get("finish")
            .map(Value::str)
            .unwrap_or_else(|| "flat".into());
        if self.style.get("light").is_some_and(Value::truthy) {
            finish = crate::light::light_part(&mut widget, &finish);
        }
        if finish != "flat" && finished(kind) && !widget.contains_key("finish") {
            widget.insert("finish".into(), finish.into());
        }
        self.out.push(widget);
        Ok(self.out.len() - 1)
    }

    /// frame_u: what a card's corners and outline scale with: its size, up to half the
    /// panel's height.
    pub fn frame_u(&self) -> N {
        let given = self.style.get(FRAME_U).filter(|v| v.truthy());
        pmin(self.u, given.map_or(N::I(self.u), N::of))
    }

    /// card: the card's surface and, unless `outline` is off, its thin outline.
    pub fn card(&mut self, outline: bool) -> Result<()> {
        if !self.card_on {
            return Ok(());
        }
        let radius_v = self.style.get("radius").map_or(N::I(0), N::of);
        let frame = self.frame_u();
        let radius = round(times_div(radius_v, frame, 200.0));
        let kind = self.st("card");
        let see = self
            .style
            .get("card_opacity")
            .and_then(Value::to_f64)
            .unwrap_or(1.0);
        let surface = self.c("surface");
        let glass = faded(&surface, 0.9 * see)?;
        let flat = faded(&surface, see)?;
        let fill = match kind.as_str() {
            "glass" => Some(glass),
            "flat" => Some(flat),
            _ => None,
        };
        let outline = (outline && (kind == "glass" || kind == "outline")).then(|| self.c("line"));
        let width = 1.max(round(frame.f() / 160.0));
        let (x, y, w, h) = (self.x, self.y, self.w, self.h);
        self.add(
            "rect",
            "card",
            vec![
                ("x", x.into()),
                ("y", y.into()),
                ("w", w.into()),
                ("h", h.into()),
                ("color", opt(fill)),
                ("radius", radius.into()),
                ("outline", opt(outline)),
                ("outline_width", width.into()),
            ],
        )?;
        Ok(())
    }

    /// _text_fields: a text's font, size and the y its capitals start at `cap_top` from.
    pub fn text_fields(&self, size: f64, cap_top: f64, font: &str) -> Fields {
        let size = 6.max(round(size));
        vec![
            ("font", self.style.get(font).cloned().unwrap_or(Value::None)),
            ("font_size", size.into()),
            ("y", round(cap_top - CAP_TOP * size as f64).into()),
        ]
    }

    /// label: a small heading in capitals.
    #[allow(clippy::too_many_arguments)]
    pub fn label(
        &mut self,
        name: &str,
        text: &str,
        x: f64,
        cap_top: f64,
        size: f64,
        color: Option<String>,
        align: &str,
        width: f64,
    ) -> Result<()> {
        let fields = self.text_fields(size, cap_top, "text_font");
        let font_size = 6.max(round(size));
        let color = color.unwrap_or_else(|| self.c("text3"));
        let mut all: Fields = vec![
            ("x", round(x).into()),
            ("text", lp_py::upper(text).into()),
            ("color", color.into()),
            (
                "letter_spacing",
                1.max(round(font_size as f64 * 0.14)).into(),
            ),
            ("align", align.into()),
            ("max_width", round(width).into()),
            ("tabular", false.into()),
        ];
        all.extend(fields);
        self.add("text", name, all)?;
        Ok(())
    }

    /// value: a reading in its format.
    #[allow(clippy::too_many_arguments)]
    pub fn value(
        &mut self,
        name: &str,
        sensor: &str,
        fmt: &str,
        x: f64,
        cap_top: f64,
        size: f64,
        color: Option<String>,
        align: &str,
        width: f64,
        font: &str,
        tabular: bool,
        kw: Fields,
    ) -> Result<()> {
        let fields = self.text_fields(size, cap_top, font);
        let mut kw = kw;
        if !kw.iter().any(|(k, _)| *k == "smooth") {
            kw.push(("smooth", counts(fmt).into()));
        }
        let color = color.unwrap_or_else(|| self.c("text"));
        let mut all: Fields = vec![
            ("x", round(x).into()),
            ("sensor", sensor.into()),
            ("format", fmt.into()),
            ("color", color.into()),
            ("align", align.into()),
            ("max_width", round(width).into()),
            ("tabular", tabular.into()),
        ];
        all.extend(fields);
        all.extend(kw);
        self.add("metric", name, all)?;
        Ok(())
    }

    /// clock: the time in a format of strftime's.
    #[allow(clippy::too_many_arguments)]
    pub fn clock(
        &mut self,
        name: &str,
        fmt: &str,
        x: f64,
        cap_top: f64,
        size: f64,
        color: Option<String>,
        align: &str,
        width: f64,
        font: &str,
        kw: Fields,
    ) -> Result<()> {
        let fields = self.text_fields(size, cap_top, font);
        let color = color.unwrap_or_else(|| self.c("text"));
        let mut all: Fields = vec![
            ("x", round(x).into()),
            ("format", fmt.into()),
            ("color", color.into()),
            ("align", align.into()),
            ("max_width", round(width).into()),
        ];
        all.extend(fields);
        all.extend(kw);
        self.add("clock", name, all)?;
        Ok(())
    }

    /// ring: a gauge of `d` across.
    #[allow(clippy::too_many_arguments)]
    pub fn ring(
        &mut self,
        name: &str,
        sensor: &str,
        x: f64,
        y: f64,
        d: f64,
        color: &str,
        low: Option<i64>,
        high: Option<i64>,
    ) -> Result<()> {
        let d = round(d);
        let deep = self.deep(color)?;
        let fields = vec![
            ("x", round(x).into()),
            ("y", round(y).into()),
            ("w", d.into()),
            ("h", d.into()),
            ("sensor", sensor.into()),
            ("min", low.unwrap_or(0).into()),
            ("max", high.unwrap_or(100).into()),
            ("thickness", 3.max(round(d as f64 * 0.08)).into()),
            ("color", deep.into()),
            ("color2", color.into()),
            ("background", self.c("track").into()),
            ("cap", "round".into()),
            ("glow", round_to(0.45 * self.glow, 2).into()),
            ("glow_radius", 2.max(round(d as f64 * 0.05)).into()),
            ("smooth", true.into()),
        ];
        self.add("gauge", name, fields)?;
        Ok(())
    }

    /// bar: a reading's bar.
    #[allow(clippy::too_many_arguments)]
    pub fn bar(
        &mut self,
        name: &str,
        sensor: &str,
        x: f64,
        y: f64,
        w: f64,
        h: f64,
        color: &str,
        low: Option<i64>,
        high: Option<i64>,
        scale: &str,
    ) -> Result<()> {
        let h = 2.max(round(h));
        let deep = self.deep(color)?;
        let fields = vec![
            ("x", round(x).into()),
            ("y", round(y).into()),
            ("w", 4.max(round(w)).into()),
            ("h", h.into()),
            ("sensor", sensor.into()),
            ("min", low.unwrap_or(0).into()),
            ("max", high.unwrap_or(100).into()),
            ("scale", scale.into()),
            ("color", deep.into()),
            ("color2", color.into()),
            ("background", self.c("track").into()),
            ("radius", h.div_euclid(2).into()),
            ("glow", round_to(0.3 * self.glow, 2).into()),
            ("glow_radius", 2.max(h).into()),
        ];
        self.add("bar", name, fields)?;
        Ok(())
    }

    /// graph: a history, its line and glow as thick as the module is large (`unit`: as one of
    /// that size's).
    #[allow(clippy::too_many_arguments)]
    pub fn graph(
        &mut self,
        name: &str,
        sensor: &str,
        x: f64,
        y: f64,
        w: f64,
        h: f64,
        color: &str,
        low: Option<i64>,
        high: Option<i64>,
        scale: &str,
        fill: bool,
        unit: N,
    ) -> Result<()> {
        let (w, h) = (8.max(round(w)), 8.max(round(h)));
        let u = if unit.f() != 0.0 { unit } else { N::I(self.u) };
        let opt_int = |v: Option<i64>| v.map_or(Value::None, Value::int);
        let fields = vec![
            ("x", round(x).into()),
            ("y", round(y).into()),
            ("w", w.into()),
            ("h", h.into()),
            ("sensor", sensor.into()),
            ("min", opt_int(low)),
            ("max", opt_int(high)),
            ("scale", scale.into()),
            ("history", 30.max(300.min(w.div_euclid(3))).into()),
            ("color", color.into()),
            ("fill", fill.into()),
            ("fill_fade", true.into()),
            ("smooth", true.into()),
            ("line_width", 1.max(round(u.f() / 110.0)).into()),
            ("glow", round_to(0.35 * self.glow, 2).into()),
            ("glow_radius", 2.max(round(u.f() / 40.0)).into()),
        ];
        self.add("graph", name, fields)?;
        Ok(())
    }

    /// icon: one of the app's icons.
    pub fn icon(
        &mut self,
        name: &str,
        icon: &str,
        x: f64,
        y: f64,
        size: f64,
        color: &str,
    ) -> Result<()> {
        let size = 8.max(round(size));
        let stroke = round_to(clamp(size as f64 / 14.0, 1.5, 3.5).f(), 1);
        let fields = vec![
            ("x", round(x).into()),
            ("y", round(y).into()),
            ("size", size.into()),
            ("icon", icon.into()),
            ("color", color.into()),
            ("stroke", stroke.into()),
        ];
        self.add("icon", name, fields)?;
        Ok(())
    }

    /// listing: a list of readings (temperatures, drives, cores ...), as many as fit.
    #[allow(clippy::too_many_arguments)]
    pub fn listing(
        &mut self,
        name: &str,
        items: &str,
        x: f64,
        y: f64,
        w: f64,
        h: f64,
        color: &str,
        style: &str,
        size: f64,
        kw: Fields,
    ) -> Result<()> {
        let font_size = 7.max(round(if size != 0.0 {
            size
        } else {
            self.ts as f64 * 1.3
        }));
        let mut background = Value::from(self.c("track"));
        let mut rest = Vec::new();
        for (k, v) in kw {
            if k == "background" {
                background = v;
            } else {
                rest.push((k, v));
            }
        }
        let mut fields: Fields = vec![
            ("x", round(x).into()),
            ("y", round(y).into()),
            ("w", 4.max(round(w)).into()),
            ("h", 4.max(round(h)).into()),
            ("items", items.into()),
            ("style", style.into()),
            (
                "font",
                self.style
                    .get("display_font")
                    .cloned()
                    .unwrap_or(Value::None),
            ),
            (
                "label_font",
                self.style.get("text_font").cloned().unwrap_or(Value::None),
            ),
            ("font_size", font_size.into()),
            ("color", self.c("text").into()),
            ("muted", self.c("text3").into()),
            ("color2", color.into()),
            ("background", background),
        ];
        fields.extend(rest);
        self.add("list", name, fields)?;
        Ok(())
    }

    /// head: icon and title at the top (`room` kept free on the right); the height they
    /// take.
    pub fn head(&mut self, icon: &str, title: &str, color: &str, room: f64) -> Result<f64> {
        let ts = self.ts as f64;
        self.icon(
            "icon",
            icon,
            self.ix as f64,
            self.iy as f64 - ts * 0.1,
            ts * 1.5,
            color,
        )?;
        let width = self.iw as f64 - ts * 2.1 - room;
        self.label(
            "title",
            title,
            self.ix as f64 + ts * 2.1,
            self.iy as f64 + ts * 0.15,
            ts * 1.05,
            Some(color.to_string()),
            "left",
            width,
        )?;
        Ok(ts * 2.4)
    }

    // shapes of the box
    pub fn aspect(&self) -> f64 {
        self.iw as f64 / 1.max(self.ih) as f64
    }

    pub fn large(&self) -> bool {
        self.cols >= 2 && self.rows >= 2
    }
}

trait PipeInt {
    fn pipe_int(self) -> i64;
}

impl PipeInt for N {
    fn pipe_int(self) -> i64 {
        match self {
            N::I(i) => i,
            N::F(f) => f as i64,
        }
    }
}

impl PipeInt for i64 {
    fn pipe_int(self) -> i64 {
        self
    }
}

/// a * b / c as Python counts it: ints multiplied as ints, then divided to a float.
fn times_div(a: N, b: N, c: f64) -> f64 {
    match (a, b) {
        (N::I(x), N::I(y)) => (x * y) as f64 / c,
        _ => a.f() * b.f() / c,
    }
}

/// _heat_rules: [{"above": warn, "color": warn}, {"above": crit, "color": crit}]
pub fn heat_rules(b: &Build, warn: i64, crit: i64) -> Value {
    let rule = |above: i64, color: String| {
        let mut d = Dict::default();
        d.insert("above".into(), above.into());
        d.insert("color".into(), color.into());
        Value::Dict(d)
    };
    Value::List(vec![rule(warn, b.c("warn")), rule(crit, b.c("crit"))])
}

/// _heat_rules(b): from 75 and 88.
pub fn heat(b: &Build) -> Value {
    heat_rules(b, 75, 88)
}

/// _detail_rows: source title, the hardware name and detail rows in a column.
#[allow(clippy::too_many_arguments)]
pub fn detail_rows(
    b: &mut Build,
    src: &Source,
    x: f64,
    top: f64,
    width: f64,
    height: f64,
    color: &str,
    title: bool,
) -> Result<()> {
    let ts = b.ts as f64;
    let mut y = top;
    if title {
        let size = ts * 1.15;
        let text = match b.ms("title") {
            t if !t.is_empty() => t,
            _ => src.label.clone(),
        };
        b.label(
            "title",
            &text,
            x,
            y,
            size,
            Some(color.to_string()),
            "left",
            width,
        )?;
        y += CAP * size + ts * 0.9;
        if !src.name.is_empty() && height - (y - top) > ts * 6.0 {
            let kw = vec![
                ("fallback", "".into()),
                ("hide_if_missing", true.into()),
                ("fit", "ellipsis".into()),
            ];
            b.value(
                "name",
                src.name,
                "{value}",
                x,
                y,
                ts * 0.95,
                Some(b.c("text3")),
                "left",
                width,
                "text_font",
                false,
                kw,
            )?;
            y += CAP * ts * 0.95 + ts * 1.1;
        }
    }
    let room = top + height - y;
    let fit = floor_div(room, ts * 2.6) as i64;
    let count = (src.details.len() as i64).min(3).min(fit);
    if count < 1 {
        return Ok(());
    }
    let row = fmin(room / count as f64, b.u as f64 * 0.26);
    let value_size = fmin(fmin(row * 0.56, b.u as f64 * 0.17), width * 0.2);
    for (i, (sensor, fmt, text)) in src.details.iter().take(count as usize).enumerate() {
        let base = y + i as f64 * row + (row - CAP * value_size) / 2.0;
        b.label(
            &format!("d{i}-label"),
            text,
            x,
            base + CAP * (value_size - ts) / 2.0,
            ts,
            None,
            "left",
            width * 0.4,
        )?;
        let rules = if sensor.ends_with(".temp") {
            heat(b)
        } else {
            Value::List(Vec::new())
        };
        let kw = vec![("hide_if_missing", true.into()), ("color_rules", rules)];
        b.value(
            &format!("d{i}"),
            sensor,
            fmt,
            x + width,
            base,
            value_size,
            None,
            "right",
            width * 0.58,
            "display_font",
            true,
            kw,
        )?;
    }
    Ok(())
}

/// The build of a module kind.
type BuildFn = fn(&mut Build, &Source) -> Result<()>;

/// A module kind: its build, the sources it takes, the size it starts with and what its
/// finish "auto" is.
pub struct KindOf {
    pub build: BuildFn,
    pub sources: &'static [&'static str],
    pub span: (i64, i64),
    pub finish: &'static str,
}

const READINGS: &[&str] = &["cpu", "gpu", "mem", "disk", "net", "sensor"];
const RINGS: &[&str] = &["cpu", "gpu", "mem", "disk"];
const INSTRUMENTS: &[&str] = &["cpu", "gpu", "mem", "disk", "fan"];

/// kinds(): the module kinds this crate builds.
pub fn kind_of(name: &str) -> Option<KindOf> {
    let k = |build: BuildFn, sources: &'static [&'static str], span: (i64, i64)| KindOf {
        build,
        sources,
        span,
        finish: "flat",
    };
    let lit = |kind: KindOf| KindOf {
        finish: "lit",
        ..kind
    };
    Some(match name {
        "ring" => k(readings::build_ring, RINGS, (1, 1)),
        "stat" => k(readings::build_stat, READINGS, (1, 1)),
        "graph" => k(readings::build_graph, READINGS, (2, 1)),
        "bars" => k(readings::build_bars, &[], (1, 2)),
        "network" => k(readings::build_network, &[], (2, 1)),
        "clock" => k(time::build_clock, &[], (2, 1)),
        "date" => k(time::build_date, &[], (1, 1)),
        "weather" => k(weather::build_weather, &[], (2, 1)),
        "outlook" => lit(k(weather::build_outlook, &[], (3, 1))),
        "system" => k(readings::build_system, &[], (1, 1)),
        "sun" => k(sky::build_sun, &[], (2, 1)),
        "moon" => k(sky::build_moon, &[], (1, 1)),
        "earth" => k(sky::build_earth, &[], (2, 1)),
        "analog" => k(time::build_analog, &[], (1, 1)),
        "world" => k(time::build_world, &[], (2, 1)),
        "countdown" => k(time::build_countdown, &[], (2, 1)),
        "huge" => k(lists::build_huge, READINGS, (1, 1)),
        "text" => k(lists::build_text, &[], (2, 1)),
        "temps" => k(lists::build_temps, &[], (1, 2)),
        "cores" => k(lists::build_cores, &[], (2, 1)),
        "drives" => k(lists::build_drives, &[], (2, 1)),
        "processes" => k(lists::build_processes, &[], (1, 2)),
        "netinfo" => k(lists::build_netinfo, &[], (2, 1)),
        "values" => k(lists::build_values, &[], (2, 1)),
        "ping" => k(lists::build_ping, &[], (2, 1)),
        "timer" => k(lists::build_timer, &[], (2, 1)),
        "power" => k(lists::build_power, &[], (2, 1)),
        "ups" => k(lists::build_ups, &[], (2, 1)),
        "mic" => k(status::build_mic, &[], (1, 1)),
        "battery" => k(status::build_battery, &[], (1, 1)),
        "ai" => k(status::build_ai, &[], (2, 1)),
        "image" => k(media::build_image, &[], (2, 2)),
        "music" => k(media::build_music, &[], (2, 1)),
        "volume" => k(media::build_volume, &[], (2, 1)),
        "agenda" => k(media::build_agenda, &[], (2, 2)),
        "game" => k(media::build_game, &[], (2, 1)),
        // the cards after SPUR II's Studio screen: lit by themselves (theme.cards)
        "playback" => lit(k(cards::build_playback, &[], (3, 2))),
        "forecast" => lit(k(cards::build_forecast, &[], (3, 2))),
        "download" => lit(k(cards::build_download, &[], (2, 1))),
        "instrument" => lit(k(cards::build_instrument, INSTRUMENTS, (1, 2))),
        "cards" => lit(k(cards::build_cards, &[], (3, 2))),
        "netcard" => lit(k(cards::build_netcard, &[], (2, 1))),
        _ => return None,
    })
}

/// The cards a slot holds while its `items` are empty.
pub const DEFAULT_CARDS: &str = "playback\nai\nforecast\ndate";

/// default_items: what a list module shows while its `items` are empty.
pub fn default_items(kind: &str) -> String {
    let t = lp_i18n::t;
    // (every text of the table made, as Python's dict is)
    let table = [
        (
            "temps",
            "cpu.temp = CPU\ngpu.temp = GPU\ntemp.*".to_string(),
        ),
        ("fans", format!("gpu.fan = {}\nfan.*", t("GPU fan"))),
        ("drives", "disk.*.load".to_string()),
        ("cards", DEFAULT_CARDS.to_string()),
        (
            "world",
            "America/New_York = New York\nEurope/London = London\nAsia/Tokyo = Tokyo\n\
             Australia/Sydney = Sydney"
                .to_string(),
        ),
        (
            "values",
            format!(
                "cpu.load = CPU\ngpu.load = GPU\nmem.load = {}\ncpu.temp = {}\ngpu.temp = {}\n\
                 net.down = {}",
                t("RAM"),
                t("CPU temp"),
                t("GPU temp"),
                t("Download"),
            ),
        ),
    ];
    table
        .into_iter()
        .find(|(k, _)| *k == kind)
        .map(|(_, v)| v)
        .unwrap_or_default()
}

/// _fallback: the source a module shows where its own has no readings.
fn fallback(module: &Dict) -> Option<String> {
    let get = |k: &str| module.get(k).map(Value::str).unwrap_or_default();
    let chosen = get("fallback");
    if chosen == "auto" {
        return (get("source") == "gpu").then(|| "disk".to_string());
    }
    if chosen == "none" || chosen == get("source") {
        None
    } else {
        Some(chosen)
    }
}

/// build_module: the plain widgets of one module in `bx` (raw, not yet checked).
pub fn build_module(
    module: &Dict,
    bx: [i64; 4],
    colors: &Dict,
    style: &Dict,
    env: &Env,
) -> Result<Vec<Dict>> {
    let name = module.get("module").map(Value::str).unwrap_or_default();
    let Some(kind) = kind_of(&name) else {
        return Err(Error {
            kind: Kind::Unsupported,
            msg: format!("module kind {name:?} is not built here yet"),
        });
    };
    let mut module = module.clone();
    if module.get("finish").map(Value::str).as_deref() == Some("auto") {
        module.insert("finish".into(), kind.finish.into());
    }
    let wanted = module.get("source").map(Value::str).unwrap_or_default();
    let mut source = if kind.sources.is_empty() {
        "cpu".to_string()
    } else {
        wanted
    };
    if !kind.sources.is_empty() && !kind.sources.contains(&source.as_str()) {
        source = kind.sources[0].to_string();
    }
    let src = source_of(&source).unwrap_or_else(|| source_of("cpu").expect("cpu"));
    let known = source_of(&source).is_some();
    let back = if !kind.sources.is_empty() && known {
        fallback(&module)
    } else {
        None
    };
    let mut b = Build::new(module, bx, colors, style, env);
    match back {
        None => {
            if !kind.sources.is_empty() {
                // without its readings the module stays empty
                let sensor = b.ms("sensor");
                let own = source == "sensor" || (source == "fan" && !sensor.is_empty());
                b.needs = if own { sensor } else { src.load.to_string() };
            }
            (kind.build)(&mut b, &src)?;
        }
        Some(other) => {
            // e.g. no GPU readings: the disk instead
            b.needs = src.load.to_string();
            (kind.build)(&mut b, &src)?;
            let first = b.out.len();
            b.needs = format!("!{}", src.load);
            let alt = source_of(&other).expect("a fallback's source");
            (kind.build)(&mut b, &alt)?;
            for widget in &mut b.out[first..] {
                let id = widget.get("id").map(Value::str).unwrap_or_default();
                widget.insert("id".into(), format!("alt-{id}").into());
            }
        }
    }
    Ok(b.out)
}

/// colors_of: the palette roles, from the theme where it has them, else the default look's.
pub fn colors_of(palette: &Dict) -> Result<Dict> {
    let looks = design::looks();
    let default = looks
        .get(design::DEFAULT_LOOK)
        .and_then(Value::as_dict)
        .and_then(|l| l.get("palette"))
        .and_then(Value::as_dict)
        .expect("the default look's palette");
    let mut out = Dict::default();
    for &role in design::ROLES {
        let given = palette
            .get(role)
            .or_else(|| default.get(role))
            .map(Value::str)
            .unwrap_or_default();
        out.insert(role.into(), hex(&given)?.into());
    }
    Ok(out)
}

/// What expand makes of a theme: the widgets to draw, every module replaced by its parts
/// (ids "<module id>/<part>", each with "_module": its module's id), the box of every module
/// by its id, and the warnings of parts that are no widget.
pub struct Expanded {
    pub widgets: Vec<Dict>,
    pub boxes: indexmap::IndexMap<String, [i64; 4]>,
    pub warnings: Vec<String>,
}

/// expand: the widgets to draw, with every module replaced by its parts.
pub fn expand(theme: &Theme, env: &Env) -> Result<Expanded> {
    let is_module = |w: &Dict| w.get("type") == Some(&Value::from("module"));
    let no_grid = theme.grid.as_ref().is_none_or(|g| g.is_empty());
    let mut done = Expanded {
        widgets: Vec::new(),
        boxes: indexmap::IndexMap::new(),
        warnings: Vec::new(),
    };
    if !theme.widgets.iter().any(is_module) && no_grid {
        done.widgets = theme.widgets.clone();
        return Ok(done);
    }
    let int = |g: &Dict, k: &str| g.get(k).map_or(N::I(0), N::of).pipe_int();
    let settings = theme
        .grid
        .as_ref()
        .map_or(Settings::default(), |g| Settings {
            columns: int(g, "columns"),
            rows: int(g, "rows"),
            gap: int(g, "gap"),
            margin: int(g, "margin"),
        });
    let grid = make_grid(theme.width, theme.height, settings, &theme.model);
    let colors = colors_of(&theme.palette)?;
    let mut style = Dict::default();
    for (k, given) in crate::tables::STYLE_DEFAULTS {
        style.insert((*k).into(), given.value());
    }
    if let Some(own) = &theme.style {
        for (k, v) in own {
            style.insert(k.clone(), v.clone());
        }
    }
    style.insert(
        FRAME_U.into(),
        Value::Float(theme.width.min(theme.height) as f64 / 2.0),
    );
    if style.get("backdrop").map(Value::str).as_deref() == Some("gradient") {
        let mut raw = Dict::default();
        raw.insert("type".into(), "rect".into());
        raw.insert("id".into(), "\0backdrop".into());
        raw.insert("x".into(), 0.into());
        raw.insert("y".into(), 0.into());
        raw.insert("w".into(), theme.width.into());
        raw.insert("h".into(), theme.height.into());
        raw.insert("color".into(), colors["bg"].clone());
        raw.insert("color2".into(), colors["bg2"].clone());
        done.widgets
            .push(normalize_widget(&Value::Dict(raw), 0, None, None, env)?.0);
    }
    for widget in &theme.widgets {
        if !is_module(widget) {
            done.widgets.push(widget.clone());
            continue;
        }
        let id = widget.get("id").map(Value::str).unwrap_or_default();
        let col = 0.max(int(widget, "col")).min(grid.columns - 1);
        let row = 0.max(int(widget, "row")).min(grid.rows - 1);
        let cols = 1.max(int(widget, "cols")).min(grid.columns - col);
        let rows = 1.max(int(widget, "rows")).min(grid.rows - row);
        let mut placed = widget.clone();
        for (k, v) in [("col", col), ("row", row), ("cols", cols), ("rows", rows)] {
            placed.insert(k.into(), v.into());
        }
        let mut color = match placed.get("color") {
            Some(v) if v.truthy() => v.str(),
            _ => String::new(),
        };
        if let Some(name) = color.strip_prefix('@') {
            // a palette entry
            color = theme.palette.get(name).map(Value::str).unwrap_or_default();
        }
        let own = if color.is_empty() {
            Value::None
        } else {
            hex(&color)?.into()
        };
        placed.insert("color".into(), own);
        let bx = grid.cells_box(col, row, cols, rows);
        done.boxes.insert(id.clone(), bx);
        for (i, mut raw) in build_module(&placed, bx, &colors, &style, env)?
            .into_iter()
            .enumerate()
        {
            let part = raw.get("id").map(Value::str).unwrap_or_default();
            raw.insert("id".into(), format!("{id}/{part}").into());
            let card = raw.shift_remove("_card");
            match normalize_widget(&Value::Dict(raw), i, None, None, env) {
                Ok((mut made, _warnings)) => {
                    made.insert("_module".into(), id.clone().into());
                    if let Some(card) = card {
                        made.insert("_card".into(), card);
                    }
                    done.widgets.push(made);
                }
                // a bug in a module must not break the theme
                Err(e) if e.kind == Kind::Theme => {
                    done.warnings
                        .push(format!("module {}: {}", lp_py::repr(&id), e.msg));
                }
                Err(e) => return Err(e),
            }
        }
    }
    Ok(done)
}

impl Grid {
    /// box: the cells (col, row) to (col + cols, row + rows) as [x, y, w, h].
    pub fn cells_box(&self, col: i64, row: i64, cols: i64, rows: i64) -> [i64; 4] {
        let x0 = self.x as f64 + col as f64 * (self.cell_w + self.gap as f64);
        let y0 = self.y as f64 + row as f64 * (self.cell_h + self.gap as f64);
        let x1 = x0 + cols as f64 * self.cell_w + ((cols - 1) * self.gap) as f64;
        let y1 = y0 + rows as f64 * self.cell_h + ((rows - 1) * self.gap) as f64;
        [
            round(x0),
            round(y0),
            round(x1) - round(x0),
            round(y1) - round(y0),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formats_that_count() {
        for (fmt, want) in [
            ("{value:.0f}%", true),
            ("{value:bytes}/s", true),
            ("{value:rate_unit}", true),
            ("{value:duration}", false),
            ("{value}", false),
            ("{value:%}", true),
            ("{value:10.1%}", true),
            ("x {value:upper} {value:,d}", true),
            ("{value:clock}", false),
        ] {
            assert_eq!(counts(fmt), want, "{fmt}");
        }
        assert_eq!(hex2(-1), "-1");
        assert_eq!(hex2(300), "12C");
    }
}
