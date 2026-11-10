//! The pages of the panel that Libre Panel switches to by itself (modes.py): "media" while
//! music plays (Encore by default) and "idle" when nobody has touched the PC for some minutes
//! (Almanac).
//!
//! They are modes as game mode is one (`[modes.<name>]` in config.toml: theme, frame rate), set
//! by the main loop instead of a service, and only while no service has a mode of its own: a
//! game goes first. Each is off until it is switched on; "media" goes before "idle".

use indexmap::IndexMap;
use lp_config::Config;
use lp_render::Reading;

pub const MEDIA: &str = "media";
pub const IDLE: &str = "idle";
/// who sets the mode of a page, for the host (a service goes by its name)
pub const OWNER: &str = "pages";
const IDLE_MINUTES: f64 = 5.0;
/// a pause this long ends the music's page (not the gap between two tracks)
const MEDIA_LEAVE_S: f64 = 8.0;
/// how often the time without input is asked for
const IDLE_EVERY_S: f64 = 1.0;

/// Whether the page of `name` (a mode Libre Panel switches to itself) is switched on.
pub fn page_on(config: &Config, name: &str) -> bool {
    config.modes.get(name).is_some_and(|mode| mode.on)
}

/// The theme a mode's page shows: config.toml's, else the page's own (Encore for music).
pub fn page_theme(config: &Config, name: Option<&str>) -> Option<String> {
    let name = name.unwrap_or("");
    let own = config.modes.get(name).and_then(|mode| mode.theme.clone());
    own.filter(|theme| !theme.is_empty()).or(match name {
        MEDIA => Some("encore".into()),
        IDLE => Some("almanac".into()),
        _ => None,
    })
}

fn idle_minutes(config: &Config) -> f64 {
    let own = config.modes.get(IDLE).and_then(|mode| mode.minutes);
    own.filter(|minutes| *minutes != 0.0)
        .unwrap_or(IDLE_MINUTES)
}

/// Seconds since the last key or click on this PC; None where it cannot be known.
#[cfg(windows)]
pub fn idle_seconds() -> Option<f64> {
    use windows_sys::Win32::System::SystemInformation::GetTickCount;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};
    let mut info = LASTINPUTINFO {
        cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32,
        dwTime: 0,
    };
    // SAFETY: `info` is a LASTINPUTINFO with its size set, as the call asks.
    if unsafe { GetLastInputInfo(&mut info) } == 0 {
        return None;
    }
    // SAFETY: GetTickCount takes nothing and only reads the system's clock.
    let now = unsafe { GetTickCount() };
    Some(f64::from(now.wrapping_sub(info.dwTime)) / 1000.0) // (both wrap at 2³²)
}

/// How long the desktop has called this login session idle (logind's IdleHint, which GNOME,
/// KDE and others set after their own delay); None where it is not known.
#[cfg(not(windows))]
pub fn idle_seconds() -> Option<f64> {
    let session = std::env::var("XDG_SESSION_ID")
        .ok()
        .filter(|s| !s.is_empty())?;
    let out = std::process::Command::new("loginctl")
        .args([
            "show-session",
            &session,
            "-p",
            "IdleHint",
            "-p",
            "IdleSinceHintMonotonic",
        ])
        .stdin(std::process::Stdio::null())
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    let value = |name: &str| {
        text.lines()
            .filter_map(|line| line.split_once('='))
            .find(|(key, _)| *key == name)
            .map(|(_, value)| value.to_string())
    };
    if value("IdleHint")? != "yes" {
        return Some(0.0);
    }
    let since = match value("IdleSinceHintMonotonic") {
        None => 0.0,
        Some(micros) => {
            lp_py::int_of_str(&micros).and_then(|n| i64::try_from(n).ok())? as f64 / 1e6
        }
    };
    if since == 0.0 {
        return Some(0.0);
    }
    // (logind counts on CLOCK_MONOTONIC)
    let mut now = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: `now` is a timespec the call fills in.
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut now) } != 0 {
        return None;
    }
    Some((now.tv_sec as f64 + now.tv_nsec as f64 / 1e9 - since).max(0.0))
}

/// Which of the panel's own pages is due: music while a track plays (and for a short pause
/// after it), else the clock once the PC has been idle for the page's minutes.
pub struct PageSwitch {
    idle: Box<dyn Fn() -> Option<f64>>,
    played_at: Option<f64>,
    idle_s: Option<f64>,
    asked_at: Option<f64>,
    /// the readings the pages read (the media source runs only while somebody reads it)
    pub reads: Vec<&'static str>,
}

impl PageSwitch {
    pub fn new() -> PageSwitch {
        PageSwitch {
            idle: Box::new(idle_seconds),
            played_at: None,
            idle_s: None,
            asked_at: None,
            reads: Vec::new(),
        }
    }

    /// The page that is due at `now` (the loop's clock), of the readings.
    pub fn due(
        &mut self,
        config: &Config,
        readings: Option<&IndexMap<String, Reading>>,
        now: f64,
    ) -> Option<&'static str> {
        let (media, idle) = (page_on(config, MEDIA), page_on(config, IDLE));
        self.reads = match media {
            true => vec!["media.playing", "media.title"],
            false => Vec::new(),
        };
        if media {
            let playing = readings.and_then(|readings| readings.get("media.playing"));
            if playing.is_some_and(|playing| playing.value.to_f64() == Some(1.0)) {
                self.played_at = Some(now);
            }
            if self.played_at.is_some_and(|at| now - at < MEDIA_LEAVE_S) {
                return Some(MEDIA);
            }
        }
        if idle {
            if self.asked_at.is_none_or(|at| now - at >= IDLE_EVERY_S) {
                self.asked_at = Some(now);
                self.idle_s = (self.idle)();
            }
            if self
                .idle_s
                .is_some_and(|idle| idle >= idle_minutes(config) * 60.0)
            {
                return Some(IDLE);
            }
        }
        None
    }
}
