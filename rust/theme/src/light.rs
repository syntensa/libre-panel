//! A theme in light colours (config.toml `light_colours`), as theme/light_colours.py makes it:
//! every colour worked out anew in OKLab by the job it does (the ground light and close
//! together in the same order, ink dark on light, colours of their hue as light as reads on a
//! light ground), the lit finish drawn flat, glows a third as strong, a background picture
//! made for a dark ground gone. The arithmetic is Python's, step by step, with the C library's
//! pow, cos, sin, atan2 and hypot that Python's float math calls.

use lp_py::{Dict, Value};

use crate::model::{style_defaults, Error, Kind, Result, Theme};

const GROUND_ROLES: [&str; 15] = [
    "bg", "bg2", "bg3", "bg_low", "surface", "surface2", "panel", "panel2", "card", "card2",
    "base", "base_low", "void", "surf_1", "surf_2",
];
const INK_ROLES: [&str; 16] = [
    "line",
    "edge",
    "rule",
    "rule_soft",
    "rule_strong",
    "grid",
    "grid_major",
    "track",
    "dim",
    "muted",
    "text",
    "text2",
    "text3",
    "tx_1",
    "tx_2",
    "tx_3",
];
const INK_KEYS: [&str; 6] = [
    "outline",
    "background",
    "tick_color",
    "grid_color",
    "mark_color",
    "marks",
];
/// a widget's fields (and a look block's) that hold colours
const COLOUR_KEYS: [&str; 13] = [
    "color",
    "color2",
    "colors",
    "background",
    "done",
    "face",
    "grid_color",
    "mark_color",
    "marks",
    "muted",
    "night",
    "outline",
    "tick_color",
];
const GROUND_L: f64 = 0.26;
const NEUTRAL_C: f64 = 0.045;
const DARK_INK_L: f64 = 0.45;
const INK: [(f64, f64); 5] = [
    (0.17, 0.96),
    (0.40, 0.80),
    (0.70, 0.56),
    (0.97, 0.24),
    (1.0, 0.21),
];
const GROUND_AT: (f64, f64) = (0.165, 0.95);
const GROUND_GAP: f64 = 2.5;
const GROUND_LIGHT: (f64, f64) = (0.90, 0.995);
const BLACK_L: f64 = 0.03;
const COLOUR_L: f64 = 0.56;
const YELLOW_L: f64 = 0.07;
const CHROMA: f64 = 1.3;
const MAX_CHROMA: f64 = 0.26;
const GLOW: f64 = 0.35;
const BODY_L: f64 = 0.80;
/// in a theme's style: its modules are drawn for a light ground
pub const LIGHT: &str = "light";

fn yellow_hue() -> f64 {
    95f64.to_radians() // (math.radians: degrees * (pi / 180))
}

fn linear(c: f64) -> f64 {
    if c <= 0.04045 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

fn gamma(c: f64) -> f64 {
    if c <= 0.0031308 {
        12.92 * c
    } else {
        1.055 * c.powf(1.0 / 2.4) - 0.055
    }
}

/// sRGB (0 to 255) as OKLab (Björn Ottosson's).
pub fn oklab(rgb: [i64; 3]) -> (f64, f64, f64) {
    let [r, g, b] = rgb.map(|v| linear(v as f64 / 255.0));
    let l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b;
    let m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b;
    let s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b;
    let [l, m, s] = [l, m, s].map(|v: f64| v.abs().powf(1.0 / 3.0).copysign(v));
    (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )
}

fn linear_rgb(lightness: f64, a: f64, b: f64) -> [f64; 3] {
    let l = (lightness + 0.3963377774 * a + 0.2158037573 * b).powf(3.0);
    let m = (lightness - 0.1055613458 * a - 0.0638541728 * b).powf(3.0);
    let s = (lightness - 0.0894841775 * a - 1.2914855480 * b).powf(3.0);
    [
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    ]
}

fn shown(lightness: f64, a: f64, b: f64) -> bool {
    linear_rgb(lightness, a, b)
        .iter()
        .all(|&v| (-1e-4..=1.0 + 1e-4).contains(&v))
}

/// Python's min(1.0, max(0.0, v)) (max(0.0, nan) is 0.0).
fn clamp01(v: f64) -> f64 {
    min(1.0, max(0.0, v))
}

/// "#RRGGBB" of an OKLab lightness, chroma and hue; a chroma the screen cannot show taken down
/// until it can.
pub fn lch_hex(lightness: f64, chroma: f64, hue: f64) -> String {
    let lightness = clamp01(lightness);
    let (mut a, mut b) = (chroma * hue.cos(), chroma * hue.sin());
    if !shown(lightness, a, b) {
        let (mut low, mut high) = (0.0, chroma);
        for _ in 0..24 {
            let mid = (low + high) / 2.0;
            if shown(lightness, mid * hue.cos(), mid * hue.sin()) {
                low = mid;
            } else {
                high = mid;
            }
        }
        a = low * hue.cos();
        b = low * hue.sin();
    }
    let channels = linear_rgb(lightness, a, b)
        .map(|v| lp_py::round(clamp01(gamma(clamp01(v))) * 255.0) as i64);
    format!("#{:02X}{:02X}{:02X}", channels[0], channels[1], channels[2])
}

/// OKLab lightness, chroma and hue (radians) of an sRGB colour.
pub fn lch(rgb: [i64; 3]) -> (f64, f64, f64) {
    let (lightness, a, b) = oklab(rgb);
    (lightness, lp_py::math::hypot(a, b), b.atan2(a))
}

fn along(x: f64, points: &[(f64, f64)]) -> f64 {
    for pair in points.windows(2) {
        let ((x0, y0), (x1, y1)) = (pair[0], pair[1]);
        if x <= x1 {
            return y0 + (y1 - y0) * (max(x, x0) - x0) / (x1 - x0);
        }
    }
    points[points.len() - 1].1
}

/// The role a colour is placed by: its palette name, "" for ink (a line's colour), or none.
#[derive(Clone, Copy)]
pub enum Role<'a> {
    Named(&'a str),
    Ink,
    None,
}

/// light_colour: `colour` for a light ground, by its job; its opacity kept. A reference to the
/// palette ("@cpu"), or what is no colour, as it is.
pub fn light_colour(colour: &str, role: Role<'_>) -> String {
    if colour.is_empty() || colour.starts_with('@') {
        return colour.to_string();
    }
    let Ok(found) = lp_pixels::colour::getrgb(colour) else {
        return colour.to_string();
    };
    let alpha = if found.len() > 3 && found[3] < 255 {
        format!("{:02X}", found[3])
    } else {
        String::new()
    };
    let (lightness, chroma, hue) = lch([found[0], found[1], found[2]]);
    let ground = match role {
        Role::Named(name) if !name.is_empty() => GROUND_ROLES.contains(&name),
        Role::None => lightness < GROUND_L,
        _ => false,
    };
    let ink_role = matches!(role, Role::Named(name) if INK_ROLES.contains(&name));
    let made = if ground {
        let (low, high) = GROUND_LIGHT;
        let light = GROUND_AT.1 + (lightness - GROUND_AT.0) * GROUND_GAP;
        let light = if lightness < BLACK_L {
            0.99
        } else {
            min(high, max(low, light))
        };
        lch_hex(light, min(chroma * 0.4, 0.02), hue)
    } else if ink_role || chroma < NEUTRAL_C || lightness < DARK_INK_L {
        lch_hex(along(lightness, &INK), min(chroma * 0.8, 0.05), hue)
    } else {
        let yellow = max(0.0, (hue - yellow_hue()).cos());
        lch_hex(
            COLOUR_L + YELLOW_L * yellow,
            min(MAX_CHROMA, chroma * CHROMA),
            hue,
        )
    };
    made + &alpha
}

/// Python's min(a, b): b only where it is smaller than a.
fn min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// Python's max(a, b): b only where it is larger than a.
fn max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// body_colour: the clouds' and the moon's colour on a light ground, a light grey of the
/// colour's hue.
pub fn body_colour(colour: &Value) -> String {
    let found = match colour {
        Value::Str(text) if text.starts_with('#') => lp_pixels::colour::getrgb(text).ok(),
        _ => None,
    };
    let (hue, chroma) = match found {
        Some(rgb) => {
            let (_, c, h) = lch([rgb[0], rgb[1], rgb[2]]);
            (h, min(c, 0.015))
        }
        None => (0.0, 0.0),
    };
    lch_hex(BODY_L, chroma, hue)
}

fn weather_icon(widget: &Dict) -> bool {
    let is = |key: &str, value: &str| widget.get(key) == Some(&Value::from(value));
    is("type", "symbol") || (is("type", "icon") && is("icon", "weather"))
}

/// light_part: the finish a module's part is drawn with in light colours (the part's colour
/// set here where it changes).
pub fn light_part(widget: &mut Dict, finish: &str) -> String {
    if weather_icon(widget) {
        let colour = widget.get("color").cloned().unwrap_or(Value::None);
        widget.insert("color".into(), body_colour(&colour).into());
        return finish.to_string();
    }
    if finish == "lit" { "flat" } else { finish }.to_string()
}

/// _light: a widget's (or a look block's) fields for a light ground.
fn light_value(value: &Value, key: &str) -> Result<Value> {
    Ok(match value {
        Value::Dict(dict) => {
            let body = weather_icon(dict);
            let mut out = Dict::new();
            for (name, field) in dict {
                let made = if body && (name == "finish" || name == "color") {
                    if name == "color" {
                        body_colour(field).into()
                    } else {
                        field.clone()
                    }
                } else if name == "finish" && matches!(field, Value::Str(_)) {
                    if *field == Value::from("lit") {
                        "flat".into()
                    } else {
                        field.clone()
                    }
                } else if name == "glow" && field.is_number() {
                    // (an int * 0.35: float(int) first, an OverflowError for one too large)
                    let Some(glow) = field.to_f64() else {
                        return Err(Error {
                            kind: Kind::Overflow,
                            msg: "int too large to convert to float".into(),
                        });
                    };
                    Value::Float(glow * GLOW)
                } else {
                    light_value(field, name)?
                };
                out.insert(name.clone(), made);
            }
            Value::Dict(out)
        }
        Value::List(items) => Value::List(
            items
                .iter()
                .map(|item| light_value(item, key))
                .collect::<Result<_>>()?,
        ),
        Value::Str(text) if COLOUR_KEYS.contains(&key) => {
            let role = if INK_KEYS.contains(&key) {
                Role::Ink
            } else {
                Role::None
            };
            light_colour(text, role).into()
        }
        other => other.clone(),
    })
}

fn light_dict(dict: &Dict) -> Result<Dict> {
    match light_value(&Value::Dict(dict.clone()), "")? {
        Value::Dict(d) => Ok(d),
        _ => unreachable!("a dict stays a dict"),
    }
}

/// light_theme: the theme in light colours; the theme itself is not changed.
pub fn light_theme(theme: &Theme) -> Result<Theme> {
    let mut light = theme.clone();
    let has_module = theme
        .widgets
        .iter()
        .any(|w| w.get("type") == Some(&Value::from("module")));
    if theme.style.is_some() || has_module {
        let mut style = theme.style.clone().unwrap_or_default();
        let given = style
            .get("glow")
            .cloned()
            .unwrap_or_else(|| style_defaults()["glow"].clone());
        let glow = given.to_f64().unwrap_or(f64::NAN);
        style.insert("glow".into(), Value::Float(glow * GLOW));
        style.insert(LIGHT.into(), Value::Bool(true));
        light.style = Some(style);
    }
    light.palette = theme
        .palette
        .iter()
        .map(|(name, value)| {
            let made = match value {
                Value::Str(text) => light_colour(text, Role::Named(name)).into(),
                other => other.clone(),
            };
            (name.clone(), made)
        })
        .collect();
    light.background_color = light_colour(&theme.background_color, Role::Named("bg"));
    light.background_gradient = theme
        .background_gradient
        .as_ref()
        .map(|(from, to)| (light_colour(from, Role::None), light_colour(to, Role::None)));
    light.background_image = None;
    light.widgets = theme
        .widgets
        .iter()
        .map(light_dict)
        .collect::<Result<_>>()?;
    light.look = light_dict(&theme.look)?;
    Ok(light)
}
