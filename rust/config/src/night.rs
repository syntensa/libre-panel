//! The panel's brightness by the time of day (config.py's night_now and brightness_now).

use lp_py::datetime::DateTime;

use crate::{time_of_day, DeviceConfig};

/// The times "sunset" and "sunrise" stand for where the sun's are not known (SUN_TIMES).
pub const SUN_TIMES: [(&str, &str); 2] = [("sunset", "19:00"), ("sunrise", "07:00")];

/// The sun of the day at the panel's place (sensors/sky.py's sun_times): whether it is night
/// or day all day (`always`: Some(true) for a polar night), else the minutes after midnight
/// it sets and rises at.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Sun {
    pub always_night: Option<bool>,
    pub set: i64,
    pub rise: i64,
}

/// night_now: whether it is night for the panel (`[device] night`, from `night_from` to
/// `night_to`; a night that goes over midnight too) at `now`. `sun`: the day's, where the
/// place is known.
pub fn night_now(device: &DeviceConfig, now: &DateTime, sun: Option<&Sun>) -> bool {
    if !device.night {
        return false;
    }
    let by_sun = |text: &str| SUN_TIMES.iter().find(|(name, _)| *name == text);
    let named = by_sun(&device.night_from).is_some() || by_sun(&device.night_to).is_some();
    let sun = sun.filter(|_| named);
    if let Some(always) = sun.and_then(|sun| sun.always_night) {
        return always;
    }
    let minute = |text: &str| match (by_sun(text), sun) {
        (Some((name, _)), Some(sun)) => Some(if *name == "sunset" { sun.set } else { sun.rise }),
        (Some((_, usual)), None) => time_of_day(usual),
        (None, _) => time_of_day(text),
    };
    let (Some(start), Some(end)) = (minute(&device.night_from), minute(&device.night_to)) else {
        return false;
    };
    if start == end {
        return false;
    }
    let minute_now = i64::from(now.hour) * 60 + i64::from(now.minute);
    if start < end {
        start <= minute_now && minute_now < end
    } else {
        minute_now >= start || minute_now < end
    }
}

/// brightness_now: the brightness the panel shows now: while the person is away the one for
/// then, at night the night's, else its own.
pub fn brightness_now(device: &DeviceConfig, now: &DateTime, away: bool, sun: Option<&Sun>) -> i64 {
    if away && device.dark_when_away {
        device.away_brightness
    } else if night_now(device, now, sun) {
        device.night_brightness
    } else {
        device.brightness
    }
}
