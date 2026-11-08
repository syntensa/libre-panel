//! Weather symbols (render/symbols.py) in a still frame: a kind of weather's layers (the sun or
//! the moon's crescent, the rays, stars, clouds, the light behind a cloud, what falls, fog's
//! bands, the bolt), drawn as weather_art draws them, flat or as frosted glass (the lit finish),
//! in the first look or the second ("vivid"); laid over each other in their resting places as
//! symbols.compose lays them where the symbol stands. A symbol that moves: not drawn here yet.

use lp_pixels::{alpha_composite, chops, Filter, Image, Mode, Transform};
use lp_py::{Dict, Value};

use crate::light;
use crate::piece::{Failed, Piece, Result};
use crate::renderer::Renderer;
use crate::snapshot::Snapshot;
use crate::weather_art::{self as art, Rgb};
use crate::widget;

/// The looks a symbol comes in (config.SYMBOL_PACKS), and the one the renderer takes where the
/// app sets none (config.DEFAULT_SYMBOLS).
const PACKS: [&str; 2] = ["classic", "vivid"];
pub(crate) const DEFAULT_PACK: &str = "vivid";
/// The second look has the most to it from this size on.
const SMALL: usize = 120;
/// The lit finish's weather icons draw a symbol from this size on (lit.MIN_ICON), and these
/// icons are the weathers' symbols (lit.SYMBOLS).
pub(crate) const MIN_ICON: i64 = 16;
pub(crate) const SYMBOLS: [&str; 8] = [
    "sun", "moon", "partly", "cloud", "fog", "rain", "snow", "storm",
];

// Where things are, in Meteocons' units: the sun (its middle, its disc's radius, its rays' inner
// and outer ends and width), the moon (its outline, size and shift), the cloud in front of the
// sun (size and shift), the stars (where, how large), fog's bands (from, to, how dense; a fifth
// on a big one), what falls and where.
fn sun_of(kind: &str) -> Option<[f64; 6]> {
    match kind {
        "clear" => Some([64.0, 64.0, 19.5, 31.0, 45.0, 6.0]),
        "mostly" => Some([46.0, 46.0, 14.5, 22.5, 32.0, 4.4]),
        "partly" => Some([41.0, 46.0, 11.0, 16.5, 24.0, 3.3]),
        _ => None,
    }
}

fn moon_of(kind: &str) -> (&'static str, f64, f64, f64) {
    match kind {
        "mostly" => ("moon", 0.69, -18.0, -17.0),
        "partly" => ("moon_small", 1.0, 0.0, 0.0),
        _ => ("moon", 1.0, 0.0, 0.0),
    }
}

fn sky_cloud(kind: &str) -> Option<(f64, f64, f64)> {
    match kind {
        "mostly" => Some((0.74, 17.0, 16.0)),
        "partly" => Some((0.94, 4.0, 6.0)),
        _ => None,
    }
}

fn stars_of(kind: &str) -> &'static [(f64, f64, f64)] {
    match kind {
        "clear" => &[(94.0, 30.0, 3.4), (106.0, 54.0, 2.4), (28.0, 100.0, 2.6)],
        "mostly" => &[(98.0, 26.0, 2.8), (18.0, 92.0, 2.2)],
        _ => &[(64.0, 24.0, 2.8), (16.0, 72.0, 2.2)],
    }
}

const STARS_BIG: [(f64, f64, f64); 4] = [
    (18.0, 28.0, 2.0),
    (112.0, 94.0, 2.2),
    (78.0, 14.0, 1.7),
    (52.0, 114.0, 1.8),
];
const FOG_LIFT: f64 = -6.0;
const BANDS: [(f64, f64, f64); 4] = [
    (30.0, 90.0, 0.70),
    (38.0, 100.0, 0.88),
    (26.0, 84.0, 0.62),
    (44.0, 96.0, 0.42),
];
const BAND_BIG: (f64, f64, f64) = (34.0, 92.0, 0.36);
const BAND_TOP: f64 = 89.0;
const BAND_FOOT: f64 = 113.0;
const BAND_WIDTH: f64 = 4.2;
const LIFT: f64 = -4.0;
const FALL: f64 = 76.0;
const LANES: [f64; 3] = [52.0, 64.0, 76.0];
const SNOW_LANES: [(f64, f64, f64); 3] =
    [(49.0, 89.0, 0.0), (64.0, 97.0, 18.0), (79.0, 88.0, 33.0)];
/// A drop's length, its width at the top and at the foot (lit; flat: Meteocons' strokes).
const RAIN: (f64, f64, f64) = (15.0, 0.9, 3.9);
const DRIZZLE: (f64, f64, f64) = (5.5, 1.6, 3.6);
const RAIN_FLAT: (f64, f64, f64) = (12.0, 4.0, 4.0);
const DRIZZLE_FLAT: (f64, f64, f64) = (3.0, 4.0, 4.0);
/// A snow crystal's size.
const FLAKE: f64 = 13.0;

/// kind_of: the kind of weather a WMO code stands for (int() of it; overcast for a code that is
/// none, or what int() refuses).
pub(crate) fn kind_of(code: &Value) -> &'static str {
    let Ok(n) = lp_py::format::int_of(code) else {
        return "overcast";
    };
    match i64::try_from(&n).unwrap_or(-1) {
        0 => "clear",
        1 => "mostly",
        2 => "partly",
        3 => "overcast",
        45 | 48 => "fog",
        51 | 53 | 55 | 56 | 57 => "drizzle",
        61 | 63 | 65 | 66 | 67 | 80 | 81 | 82 => "rain",
        71 | 73 | 75 | 77 | 85 | 86 => "snow",
        95 | 96 | 99 => "storm",
        _ => "overcast",
    }
}

/// night_at: whether it is night for the weather read from `sensor`: the forecast says so for
/// its hours (weather.hour.3.day), Open-Meteo for now (weather.is_day), else the clock does.
pub(crate) fn night_at(snapshot: &Snapshot, sensor: &str) -> bool {
    let key = if sensor.is_empty() {
        "weather.code"
    } else {
        sensor
    };
    let day = key
        .strip_suffix(".code")
        .map(|stem| format!("{stem}.day"))
        .unwrap_or_default();
    let mut daylight = snapshot.value(&day);
    if daylight == Value::None && key == "weather.code" {
        daylight = snapshot.value("weather.is_day");
    }
    if daylight == Value::None {
        return !(6..20).contains(&snapshot.now.hour);
    }
    !daylight.truthy()
}

/// The colours a symbol takes: clouds and the moon in the widget's colour (a darker cloud a mix
/// of it and the ground), the sun and bolts in the palette's warning colour, rain in its accent
/// and snow in its text colour.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Colours {
    cloud: [i64; 4],
    dark: [i64; 4],
    sun: [i64; 4],
    rain: [i64; 4],
    snow: [i64; 4],
}

/// A layer (premultiplied, RGBa) cut to what is drawn on it, and where its corner lies on the
/// picture it belongs to (for what falls: off the point it is put at).
#[derive(Clone)]
struct Part {
    image: Image,
    x: i64,
    y: i64,
}

/// Light behind a cloud: as much of it as shows through the cloud where it covers all (L), its
/// colour, and where its corner lies.
struct Glow {
    alpha: Image,
    colour: [u8; 3],
    x: i64,
    y: i64,
}

/// What a symbol is made of, as it stands.
struct Layers {
    k: usize,
    rich: bool,
    lit: bool,
    disc: Option<Part>,
    rays: Option<Part>,
    centre: (f64, f64),
    stars: Vec<Part>,
    cloud: Option<Part>,
    back: Option<Part>,
    through: Option<Glow>,
    drop: Option<Part>,
    flake: Option<Part>,
    bolt: Option<Part>,
    bars: Vec<Part>,
    corona: Option<Part>,
}

/// _part: `image` cut to its content plus a little room, premultiplied; None for a layer with
/// nothing on it.
fn part(image: &Image) -> Result<Option<Part>> {
    let margin = 2;
    let Some((l, t, r, b)) = image.getbbox(true) else {
        return Ok(None);
    };
    let (w, h) = (image.width() as i64, image.height() as i64);
    let (left, top) = ((l as i64 - margin).max(0), (t as i64 - margin).max(0));
    let (right, bottom) = ((r as i64 + margin).min(w), (b as i64 + margin).min(h));
    let image = image.crop(left, top, right, bottom).convert(Mode::RGBa)?;
    Ok(Some(Part {
        image,
        x: left,
        y: top,
    }))
}

/// _anchored: a picture of its own (a drop, a crystal) put by the point (ax, ay) on it.
fn anchored(image: &Image, ax: f64, ay: f64) -> Result<Option<Part>> {
    if image.getbbox(true).is_none() {
        return Ok(None);
    }
    Ok(Some(Part {
        image: image.convert(Mode::RGBa)?,
        x: -(lp_py::round(ax) as i64),
        y: -(lp_py::round(ay) as i64),
    }))
}

fn rgb(c: [i64; 4]) -> Rgb {
    art::levels_of(c)
}

/// _drop: rain's drop or drizzle's, put by the middle of its top.
fn drop_part(k: usize, kind: &str, colour: [i64; 4], lit: bool) -> Result<Option<Part>> {
    let (length, top, foot) = match (lit, kind == "drizzle") {
        (true, false) => RAIN,
        (true, true) => DRIZZLE,
        (false, false) => RAIN_FLAT,
        (false, true) => DRIZZLE_FLAT,
    };
    let s = art::px(k);
    let (top, foot) = (top.max(0.8 / s), foot.max(2.2 / s));
    let faint = if kind == "drizzle" { 0.35 } else { 0.1 };
    let image = art::drop(k, length, top, foot, rgb(colour), lit, faint)?;
    anchored(&image, image.width() as f64 / 2.0, (1.0 + foot / 2.0) * s)
}

/// _flake: a snow crystal, put by its middle: branched (the second look, where it is big enough
/// to show) or three plain strokes crossing.
fn flake_part(k: usize, colour: [i64; 4], branched: bool) -> Result<Option<Part>> {
    let size = FLAKE * art::px(k);
    let side = size + 4.0;
    let fine = branched && size >= 11.0;
    let width = 0.9f64.max(size * if fine { 0.10 } else { 0.13 });
    let image = art::flake(side, size, width, rgb(colour), fine)?;
    anchored(
        &image,
        image.width() as f64 / 2.0,
        image.height() as f64 / 2.0,
    )
}

/// _band_rows: where fog's `count` bands lie, top to foot.
fn band_rows(count: usize) -> Vec<f64> {
    let step = (BAND_FOOT - BAND_TOP) / (count.max(2) - 1) as f64;
    (0..count).map(|i| BAND_TOP + i as f64 * step).collect()
}

/// _through: what of `light` (behind a cloud) shows through the cloud: its glow, wide and near,
/// as much of it as frosted glass lets through.
fn through(light: &Image, colour: [i64; 4], k: usize, strength: f64) -> Result<Option<Glow>> {
    let a = art::Plane::of_levels(&light.getchannel(3)?, |b| f32::from(b) / 255.0);
    let radius = 1.0f64.max(k as f64 * 0.035);
    let wide = art::blurred(&a, radius)?;
    let near = art::blurred(&a, 0.6f64.max(radius * 0.28))?;
    let (w1, w2, strength) = (art::f(0.6), art::f(0.4), art::f(strength));
    let seen = art::Plane {
        w: a.w,
        h: a.h,
        v: wide
            .v
            .iter()
            .zip(&near.v)
            .map(|(&p, &q)| (w1 * p + w2 * q) * strength)
            .collect(),
    };
    let alpha = seen.levels()?;
    let Some((l, t, r, b)) = alpha.getbbox(true) else {
        return Ok(None);
    };
    let c = art::lighter(rgb(colour), 0.25).map(|v| (v * 255.0 + 0.5) as u8);
    Ok(Some(Glow {
        alpha: alpha.crop(l as i64, t as i64, r as i64, b as i64),
        colour: c,
        x: l as i64,
        y: t as i64,
    }))
}

/// _made: the layers of a kind of weather as a still frame shows them, as frosted glass (`lit`)
/// or flat.
fn made(kind: &str, night: bool, k: usize, c: &Colours, rich: bool, lit: bool) -> Result<Layers> {
    let mut layers = Layers {
        k,
        rich,
        lit,
        disc: None,
        rays: None,
        centre: (k as f64 / 2.0, k as f64 / 2.0),
        stars: vec![],
        cloud: None,
        back: None,
        through: None,
        drop: None,
        flake: None,
        bolt: None,
        bars: vec![],
        corona: None,
    };
    let cloud =
        |colour: [i64; 4], dark: bool, scale: f64, dx: f64, dy: f64| -> Result<Option<Part>> {
            let cov = art::cover("cloud", k, scale, dx, dy)?;
            if !lit {
                return part(&art::flat(&cov, rgb(colour))?);
            }
            let lift = if dark { 0.45 } else { 0.78 };
            part(&art::glass(&cov, rgb(colour), k, lift, 1.0, 0.9)?)
        };
    if let Some([cx, cy, r, inner, outer, width]) = sun_of(kind) {
        let (light, colour, strength);
        if night {
            let (name, scale, dx, dy) = moon_of(kind);
            let (image, _cov) = art::moon(k, name, rgb(c.cloud), lit, scale, dx, dy)?;
            layers.disc = part(&image)?;
            let big = rich && k >= SMALL && kind == "clear";
            let extra: &[(f64, f64, f64)] = if big { &STARS_BIG } else { &[] };
            for &(x, y, r) in stars_of(kind).iter().chain(extra) {
                if let Some(p) = part(&art::star(k, x, y, r, rgb(c.cloud), lit)?)? {
                    layers.stars.push(p);
                }
            }
            (light, colour, strength) = (image, c.cloud, 0.42);
        } else {
            let core = art::sun_core(k, cx, cy, r, rgb(c.sun), lit)?;
            layers.disc = part(&core)?;
            layers.rays = part(&art::rays(k, cx, cy, inner, outer, width, rgb(c.sun), lit)?)?;
            layers.centre = (art::at(k, cx), art::at(k, cy));
            let mut seen = core.clone();
            if rich {
                let halo = art::corona(k, cx, cy, r, rgb(c.sun))?;
                layers.corona = part(&halo)?;
                seen = alpha_composite(&halo, &core)?;
            }
            (light, colour, strength) = (seen, c.sun, 0.78);
        }
        if let Some((scale, dx, dy)) = sky_cloud(kind) {
            layers.cloud = cloud(c.cloud, false, scale, dx, dy)?;
            if lit && layers.cloud.is_some() {
                layers.through = through(&light, colour, k, strength)?;
            }
        }
        return Ok(layers);
    }
    if kind == "fog" {
        layers.cloud = cloud(c.cloud, false, 1.0, 0.0, FOG_LIFT)?;
        let mut bands = BANDS.to_vec();
        if rich && k >= SMALL {
            bands.push(BAND_BIG);
        }
        let rows = band_rows(bands.len());
        for (&(x0, x1, dense), &y) in bands.iter().zip(&rows) {
            // (soft veils in the second look; the first's are crisp)
            let band = art::band(k, x0, x1, y, BAND_WIDTH, rgb(c.cloud), dense, lit && rich)?;
            if let Some(p) = part(&band)? {
                layers.bars.push(p);
            }
        }
        return Ok(layers);
    }
    let storm = kind == "storm";
    if kind == "overcast" || storm {
        // (behind a storm's dark cloud a lighter one, that the two are told apart)
        let back = if storm {
            let [r, g, b] = light::mix(
                [c.dark[0], c.dark[1], c.dark[2]],
                [c.cloud[0], c.cloud[1], c.cloud[2]],
                0.78,
            );
            [r, g, b, 255]
        } else {
            c.dark
        };
        let cov = art::cover("back", k, 1.0, 0.0, if storm { LIFT } else { 0.0 })?;
        let image = if lit {
            art::glass(&cov, rgb(back), k, 0.42, 0.96, 0.9)?
        } else {
            art::flat(&cov, rgb(back))?
        };
        layers.back = part(&image)?;
    }
    let lift = if kind == "overcast" { 0.0 } else { LIFT };
    layers.cloud = if storm {
        cloud(c.dark, true, 1.0, 0.0, lift)?
    } else {
        cloud(c.cloud, false, 1.0, 0.0, lift)?
    };
    if kind == "rain" || (storm && rich && k >= SMALL) {
        layers.drop = drop_part(k, "rain", c.rain, lit)?;
    } else if kind == "drizzle" {
        layers.drop = drop_part(k, "drizzle", c.rain, lit)?;
    } else if kind == "snow" {
        layers.flake = flake_part(k, c.snow, rich)?;
    }
    if storm {
        layers.bolt = part(&art::bolt(k, rgb(c.sun), lit, true, LIFT)?)?;
    }
    Ok(layers)
}

/// A layer as it lies on the picture (RGBA), and its corner there.
struct Placed {
    image: Image,
    x: i64,
    y: i64,
}

/// _moved: `image` shifted by (dx, dy) (bilinear), as it is where the shift is under a fiftieth.
fn moved(image: &Image, dx: f64, dy: f64) -> Result<Image> {
    if dx.abs() < 0.02 && dy.abs() < 0.02 {
        return Ok(image.clone());
    }
    let (w, h) = (image.width() as f64, image.height() as f64);
    let s = 1.0;
    let matrix = [
        s,
        0.0,
        (1.0 - s) * w / 2.0 - dx,
        0.0,
        s,
        (1.0 - s) * h / 2.0 - dy,
    ];
    Ok(image.transform(
        image.size(),
        Transform::Affine(matrix),
        Filter::Bilinear,
        None,
    )?)
}

/// _fade_table: Image.point's table that scales all four bands of a premultiplied picture by
/// `share`, in steps of 1/64.
fn fade_table(share: f64) -> Vec<i64> {
    let q = ((share * 64.0 + 0.5) as i64).clamp(0, 64);
    let one: Vec<i64> = (0..256).map(|v| v * q / 64).collect();
    one.iter().copied().cycle().take(1024).collect()
}

/// symbols.compose with no time: the symbol standing, in its resting places, on a clear picture
/// `layers.k` px square.
fn compose(layers: &Layers, kind: &str) -> Result<Image> {
    let k = layers.k;
    let at = |v: f64| art::at(k, v);
    let mut out = Image::blank(Mode::RGBA, k, k);
    // place: a layer whose corner lies at (x, y) (fractions shifted in), faded to `share`
    let place = |part: Option<&Part>, x: f64, y: f64, share: f64| -> Result<Option<Placed>> {
        let Some(part) = part else { return Ok(None) };
        if share <= 0.01 {
            return Ok(None);
        }
        let (px, py) = (x + part.x as f64, y + part.y as f64);
        let (xi, yi) = (px.floor() as i64, py.floor() as i64);
        let (w, h) = (part.image.width() as i64, part.image.height() as i64);
        if xi >= k as i64 || yi >= k as i64 || xi + w <= 0 || yi + h <= 0 {
            return Ok(None);
        }
        let mut image = moved(&part.image, px - xi as f64, py - yi as f64)?;
        if share < 0.999 {
            image = image.point(&fade_table(share))?;
        }
        Ok(Some(Placed {
            image: image.convert(Mode::RGBA)?,
            x: xi,
            y: yi,
        }))
    };
    let lay = |out: &mut Image, placed: &Option<Placed>| -> Result<()> {
        if let Some(p) = placed {
            let source = (
                (-p.x).max(0),
                (-p.y).max(0),
                p.image.width() as i64,
                p.image.height() as i64,
            );
            out.alpha_composite_at(&p.image, (p.x.max(0), p.y.max(0)), source)?;
        }
        Ok(())
    };
    // where: how much `placed` covers of the box (x, y, w, h) of the picture (L)
    let covered = |p: &Placed, x: i64, y: i64, w: i64, h: i64| -> Result<Image> {
        let a = p.image.getchannel(3)?;
        Ok(a.crop(x - p.x, y - p.y, x - p.x + w, y - p.y + h))
    };
    // (the corona at its mean, the stars at their brightest, the rays unturned)
    lay(
        &mut out,
        &place(layers.corona.as_ref(), 0.0, 0.0, 0.6 + 0.4 * 0.5)?,
    )?;
    if let Some(rays) = &layers.rays {
        let centre = (
            layers.centre.0 - rays.x as f64,
            layers.centre.1 - rays.y as f64,
        );
        let turned = rays
            .image
            .rotate(0.0, Filter::Bicubic, false, Some(centre), None, None)?
            .convert(Mode::RGBA)?;
        let whole = (0, 0, turned.width() as i64, turned.height() as i64);
        out.alpha_composite_at(&turned, (rays.x, rays.y), whole)?;
    }
    for star in &layers.stars {
        lay(&mut out, &place(Some(star), 0.0, 0.0, 0.25 + 0.75 * 1.0)?)?;
    }
    lay(&mut out, &place(layers.disc.as_ref(), 0.0, 0.0, 1.0)?)?;
    for bar in &layers.bars {
        lay(&mut out, &place(Some(bar), 0.0, 0.0, 1.0)?)?;
    }
    // what falls comes out from under the clouds, so it goes in before them
    if let Some(drop) = &layers.drop {
        if kind == "rain" || kind == "storm" {
            for (x, down) in LANES.iter().zip([5.0, 11.0, 6.0]) {
                lay(&mut out, &place(Some(drop), at(*x), at(FALL + down), 1.0)?)?;
            }
        } else {
            for x in LANES {
                for j in 0..2 {
                    lay(
                        &mut out,
                        &place(Some(drop), at(x), at(FALL + 7.0 + 9.0 * j as f64), 1.0)?,
                    )?;
                }
            }
        }
    }
    if let Some(flake) = &layers.flake {
        for (x, y, turn) in SNOW_LANES {
            let crystal = if layers.rich {
                spun(flake, turn)?
            } else {
                flake.clone()
            };
            lay(&mut out, &place(Some(&crystal), at(x), at(y), 1.0)?)?;
        }
    }
    let cloud = place(layers.cloud.as_ref(), 0.0, 0.0, 1.0)?;
    let behind = place(layers.back.as_ref(), 0.0, 0.0, 1.0)?;
    lay(&mut out, &behind)?;
    if let (true, Some(behind), Some(front)) = (layers.lit, &behind, &cloud) {
        // the cloud in front shades the one behind it, softly, a little below and to the right
        let reach = (lp_py::round(k as f64 * 0.03) as i64).max(1);
        let pad = 3 * reach;
        let (w, h) = (
            front.image.width() as i64 + 2 * pad,
            front.image.height() as i64 + 2 * pad,
        );
        let mut shade = Image::blank(Mode::L, w as usize, h as usize);
        shade.paste(&front.image.getchannel(3)?, pad, pad, None)?;
        let shade = shade.gaussian_blur(reach as f32, reach as f32)?;
        let x = front.x - pad + lp_py::round(0.006 * k as f64) as i64;
        let y = front.y - pad + lp_py::round(0.012 * k as f64) as i64;
        let table: Vec<i64> = (0..256).map(|v| v * 30 / 100).collect();
        let shade = chops::multiply(&shade, &covered(behind, x, y, w, h)?)?.point(&table)?;
        let mut dark = Image::blank(Mode::RGBA, w as usize, h as usize);
        dark.putalpha(&shade)?;
        lay(&mut out, &Some(Placed { image: dark, x, y }))?;
    }
    lay(&mut out, &cloud)?;
    if let (Some(glow), Some(front)) = (&layers.through, &cloud) {
        // the sun (or the moon) behind the cloud: its glow shows through where the cloud is
        let (w, h) = (glow.alpha.width() as i64, glow.alpha.height() as i64);
        let [r, g, b] = glow.colour;
        let mut light = Image::new(Mode::RGBA, w as usize, h as usize, &[r, g, b, 255]);
        light.putalpha(&chops::multiply(
            &glow.alpha,
            &covered(front, glow.x, glow.y, w, h)?,
        )?)?;
        out.alpha_composite_at(&light, (glow.x, glow.y), (0, 0, w, h))?;
    }
    lay(&mut out, &place(layers.bolt.as_ref(), 0.0, 0.0, 1.0)?)?;
    Ok(out)
}

/// _spun: the snow crystal turned by `angle` degrees, in steps of three degrees.
fn spun(flake: &Part, angle: f64) -> Result<Part> {
    let key = ((angle / 3.0).floor() as i64).rem_euclid(20);
    let image = flake
        .image
        .rotate((key * 3) as f64, Filter::Bicubic, false, None, None, None)?;
    Ok(Part {
        image,
        x: flake.x,
        y: flake.y,
    })
}

/// A still symbol of a kind of weather: `k` px square, in a look, lit or flat.
fn still(kind: &str, night: bool, k: usize, c: &Colours, pack: &str, lit: bool) -> Result<Image> {
    let layers = made(kind, night, k, c, pack == "vivid", lit)?;
    compose(&layers, kind)
}

/// For the comparisons: a still symbol of a kind of weather, `k` px square, its colours (cloud,
/// dark, sun, rain, snow), in a look, lit or flat.
pub fn check_symbol(
    kind: &str,
    night: bool,
    k: usize,
    colours: [[i64; 4]; 5],
    pack: &str,
    lit: bool,
) -> std::result::Result<Image, String> {
    let [cloud, dark, sun, rain, snow] = colours;
    let c = Colours {
        cloud,
        dark,
        sun,
        rain,
        snow,
    };
    still(kind, night, k, &c, pack, lit).map_err(|e| e.0)
}

impl Renderer {
    /// renderer.pack: the look of a symbol: its own, else the one the renderer has.
    pub(crate) fn pack(&self, w: &Dict) -> String {
        let own = widget::text(w, "pack");
        if PACKS.contains(&own) {
            own.to_string()
        } else {
            self.symbols.clone()
        }
    }

    /// symbols.colours: the widget's colour for clouds and the moon (white where it has none),
    /// a darker cloud's a mix of it and the ground, the palette's warning colour for the sun and
    /// bolts, its accent for rain and its text colour for snow.
    pub(crate) fn symbol_colours(&self, w: &Dict) -> Result<Colours> {
        let own = self.colour_of(w, "color")?.unwrap_or([255; 4]);
        let palette = &self.theme.palette;
        let ground = light::role_colour(palette, "ground");
        let [r, g, b] = light::mix([own[0], own[1], own[2]], ground, 0.55);
        let solid = |c: [i64; 3]| [c[0], c[1], c[2], 255];
        Ok(Colours {
            cloud: own,
            dark: [r, g, b, own[3]],
            sun: solid(light::role_colour(palette, "warning")),
            rain: solid(light::role_colour(palette, "info")),
            snow: solid(light::role_colour(palette, "text")),
        })
    }

    /// symbols.draw: a symbol widget standing: the weather of its code sensor, flat or lit.
    pub(crate) fn symbol_piece(&self, w: &Dict, snapshot: &Snapshot, lit: bool) -> Result<Piece> {
        let k = widget::int(w, "size", 64).max(8);
        let sensor = widget::text(w, "sensor");
        let kind = kind_of(&snapshot.value(sensor));
        let night = night_at(snapshot, sensor);
        let image = self.symbol_image(w, kind, night, k, lit)?;
        Ok(Piece::new(
            image,
            widget::int(w, "x", 0),
            widget::int(w, "y", 0),
        ))
    }

    /// LitIcon.draw's piece: the weather icon `name` as its symbol, lit, standing.
    pub(crate) fn lit_icon_piece(&self, w: &Dict, name: &str) -> Result<Piece> {
        let (kind, night) = match name {
            "sun" => ("clear", false),
            "moon" => ("clear", true),
            "partly" => ("partly", false),
            "cloud" => ("overcast", false),
            other => (other, false),
        };
        let image = self.symbol_image(w, kind, night, widget::int(w, "size", 0), true)?;
        Ok(Piece::new(
            image,
            widget::int(w, "x", 0),
            widget::int(w, "y", 0),
        ))
    }

    fn symbol_image(&self, w: &Dict, kind: &str, night: bool, k: i64, lit: bool) -> Result<Image> {
        if k > 4096 {
            return Err(Failed("a symbol too large".into()));
        }
        still(
            kind,
            night,
            k as usize,
            &self.symbol_colours(w)?,
            &self.pack(w),
            lit,
        )
    }
}
