//! The sun and the moon, computed: no internet, no extra package (sensors/sky.py).
//!
//! Sunrise and sunset follow the sunrise equation (as NOAA uses it, good to a minute or two)
//! for the place set in `[weather]` (latitude, longitude); the moon's phase needs no place.
//! Times are the computer's local time.

use std::f64::consts::PI;

use lp_py::datetime::DateTime;
use lp_py::num::float_rem;
use lp_py::zones::{fromtimestamp, Zone};
use lp_py::{round_digits, Value};
use lp_render::Reading;

use crate::lhm::Readings;

/// days from new moon to new moon
pub const SYNODIC_MONTH: f64 = 29.530588853;
/// a new moon (6 January 2000), as a Julian date
const NEW_MOON: f64 = 2451550.1;
/// 1 January 1970 as a Julian date
const UNIX_EPOCH: f64 = 2440587.5;
/// The moon's phases in order, an eighth of a month each, the named ones centred.
pub const MOON_PHASES: [&str; 8] = [
    "New moon",
    "Waxing crescent",
    "First quarter",
    "Waxing gibbous",
    "Full moon",
    "Waning gibbous",
    "Last quarter",
    "Waning crescent",
];

fn julian(t: f64) -> f64 {
    t / 86400.0 + UNIX_EPOCH
}

fn unix(julian: f64) -> f64 {
    (julian - UNIX_EPOCH) * 86400.0
}

/// Solar noon (Julian date), the sun's declination and its mean anomaly, in radians.
fn sun_path(day: &DateTime, longitude: f64) -> (f64, f64, f64) {
    let n = day.toordinal() - DateTime::date(2000, 1, 1).toordinal();
    let mean = n as f64 - longitude / 360.0;
    let anomaly = float_rem(357.5291 + 0.98560028 * mean, 360.0).to_radians();
    let mut centre = 1.9148 * anomaly.sin() + 0.02 * (2.0 * anomaly).sin();
    centre += 0.0003 * (3.0 * anomaly).sin();
    let ecliptic = float_rem(anomaly.to_degrees() + centre + 180.0 + 102.9372, 360.0).to_radians();
    let noon = 2451545.0 + mean + 0.0053 * anomaly.sin() - 0.0069 * (2.0 * ecliptic).sin();
    let declination = (ecliptic.sin() * 23.4397f64.to_radians().sin()).asin();
    (noon, declination, anomaly)
}

/// Sunrise, solar noon and sunset of a day (Unix times).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SunTimes {
    pub noon: f64,
    /// (rise, set); none in a polar night or a midnight sun
    pub day: Option<(f64, f64)>,
    /// in a midnight sun true, in a polar night false
    pub always: Option<bool>,
}

pub fn sun_times(day: &DateTime, latitude: f64, longitude: f64) -> SunTimes {
    let (noon, declination, _) = sun_path(day, longitude);
    let phi = latitude.to_radians();
    let mut below = phi.cos() * declination.cos();
    if below == 0.0 {
        below = 1e-9;
    }
    let cos_hour = ((-0.833f64).to_radians().sin() - phi.sin() * declination.sin()) / below;
    let mut out = SunTimes {
        noon: unix(noon),
        day: None,
        always: None,
    };
    if cos_hour > 1.0 {
        out.always = Some(false);
    } else if cos_hour < -1.0 {
        out.always = Some(true);
    } else {
        let half = cos_hour.acos().to_degrees() / 360.0;
        out.day = Some((unix(noon - half), unix(noon + half)));
    }
    out
}

/// How high the sun stands, in degrees (below the horizon: negative); none where the time
/// is none the computer's clock has.
pub fn sun_elevation(zone: &Zone, t: f64, latitude: f64, longitude: f64) -> Option<f64> {
    let (day, _) = fromtimestamp(zone, t)?;
    let (noon, declination, _) = sun_path(&day, longitude);
    let hour_angle = (360.0 * (julian(t) - noon)).to_radians();
    let phi = latitude.to_radians();
    let mut sine = phi.sin() * declination.sin();
    sine += phi.cos() * declination.cos() * hour_angle.cos();
    Some((-1.0f64).max(1.0f64.min(sine)).asin().to_degrees())
}

/// Where the moon is in its month: 0 new, 0.25 first quarter, 0.5 full, 0.75 last quarter.
pub fn moon_phase(t: f64) -> f64 {
    float_rem((julian(t) - NEW_MOON) / SYNODIC_MONTH, 1.0)
}

pub fn moon_name(phase: f64) -> &'static str {
    MOON_PHASES[((phase * 8.0 + 0.5) as i64).rem_euclid(8) as usize]
}

/// The time of day of a moment, "06:42"; none where the clock has no such time.
fn clock(zone: &Zone, t: f64) -> Option<Value> {
    let (moment, _) = fromtimestamp(zone, t)?;
    Some(Value::Str(format!(
        "{:02}:{:02}",
        moment.hour, moment.minute
    )))
}

/// The readings of the sky at `t` (a Unix time), for the place if there is one; none where
/// the computer's clock has no such time.
pub fn sky_readings(zone: &Zone, t: f64, place: Option<(f64, f64)>) -> Option<Readings> {
    let mut out = Readings::new();
    let mut put = |key: &str, value: Value, unit: &str, label: &str| {
        let reading = Reading {
            unit: unit.into(),
            label: label.into(),
            ..Reading::of(value)
        };
        out.insert(key.to_string(), reading);
    };
    let float = Value::Float;
    let phase = moon_phase(t);
    let lit = (1.0 - (2.0 * PI * phase).cos()) / 2.0 * 100.0;
    put(
        "moon.phase",
        float(round_digits(phase, 4)),
        "",
        "Moon phase",
    );
    put(
        "moon.illumination",
        float(round_digits(lit, 1)),
        "%",
        "Moon lit",
    );
    put("moon.name", Value::Str(moon_name(phase).into()), "", "Moon");
    let age = round_digits(phase * SYNODIC_MONTH, 1);
    put("moon.age", float(age), "d", "Moon age");
    let full = round_digits(float_rem(0.5 - phase, 1.0) * SYNODIC_MONTH, 1);
    put("moon.full_in", float(full), "d", "Full moon in");
    let new = round_digits(float_rem(1.0 - phase, 1.0) * SYNODIC_MONTH, 1);
    put("moon.new_in", float(new), "d", "New moon in");
    if let Some((latitude, longitude)) = place {
        let (today, _) = fromtimestamp(zone, t)?;
        let sun = sun_times(&today, latitude, longitude);
        put("sun.noon", clock(zone, sun.noon)?, "", "Solar noon");
        let elevation = sun_elevation(zone, t, latitude, longitude)?;
        put(
            "sun.elevation",
            float(round_digits(elevation, 1)),
            "°",
            "Sun",
        );
        if let Some((rise, down)) = sun.day {
            put("sun.rise", clock(zone, rise)?, "", "Sunrise");
            put("sun.set", clock(zone, down)?, "", "Sunset");
            let daylight = lp_py::num::round_float(down - rise).ok()?;
            put("sun.daylight", Value::Int(daylight), "s", "Daylight");
            let up = rise <= t && t <= down;
            put("sun.up", Value::from(i64::from(up)), "", "Sun up");
            let progress = match up {
                true => float(round_digits((t - rise) / (down - rise), 4)),
                false => Value::None,
            };
            put("sun.progress", progress, "", "Day passed");
            let next = DateTime::from_ordinal(today.toordinal() + 1)?;
            if let Some((rise, _)) = sun_times(&next, latitude, longitude).day {
                put("sun.next_rise", clock(zone, rise)?, "", "Next sunrise");
            }
        } else {
            // polar night or midnight sun
            let day = sun.always == Some(true);
            put("sun.up", Value::from(i64::from(day)), "", "Sun up");
            let daylight = if day { 86400 } else { 0 };
            put("sun.daylight", Value::from(daylight), "s", "Daylight");
        }
    }
    Some(out)
}

/// sun.* for the configured place, moon.* everywhere.
pub struct SkySource {
    place: Option<(f64, f64)>,
    /// when the sky was last computed, and what it was
    kept: Option<(f64, Readings)>,
}

impl SkySource {
    /// `place`: `[weather]`'s latitude and longitude, where both are set.
    pub fn new(place: Option<(f64, f64)>) -> SkySource {
        SkySource { place, kept: None }
    }

    pub fn read(&mut self) -> Readings {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0.0, |since| since.as_secs_f64());
        // (the sky moves slowly)
        if self.kept.as_ref().is_none_or(|(at, _)| now - at >= 20.0) {
            let found = lp_py::zones::local().and_then(|zone| sky_readings(zone, now, self.place));
            self.kept = Some((now, found.unwrap_or_default()));
        }
        self.kept
            .as_ref()
            .map(|(_, sky)| sky.clone())
            .unwrap_or_default()
    }
}
