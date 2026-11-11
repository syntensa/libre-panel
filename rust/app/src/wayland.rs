//! The screen on Wayland (wayland.py): no program may look at it; the desktop shares it through
//! PipeWire, asked over D-Bus, and GStreamer (`gst-launch-1.0` with `pipewiresrc`, the
//! desktop's own) reads the stream.
//!
//! - **GNOME** (Mutter's screen cast, `org.gnome.Mutter.ScreenCast`): a monitor by its
//!   connector (listed through `org.gnome.Mutter.DisplayConfig`: [`monitors`]), or a monitor of
//!   the panel's own (`RecordVirtual`: GNOME makes it as the capture starts, at the panel's
//!   size, and takes it away as it ends); no dialog.
//! - **Any other desktop** (KDE, Sway, Hyprland ...) through the screen-sharing portal
//!   (`org.freedesktop.portal.ScreenCast`): the desktop's dialog asks which monitor or window,
//!   or offers a new virtual output. The choice is kept (`persist_mode`): the next time it asks
//!   no more.
//!
//! A capture runs in a program of its own (wayland_feed.rs: `libre-panel wayland-feed`): it
//! holds the desktop's session for as long as it runs, starts GStreamer on the stream and
//! writes the panel's frames, raw and fitted, to its output, which is ffmpeg's input.

// (Wayland is Linux's: elsewhere what talks to its desktop goes unused)
#![cfg_attr(not(target_os = "linux"), allow(dead_code))]

use lp_py::{Dict, Value};

use crate::capture::{Capture, Monitor};
use crate::mirror::{MirrorError, Source};

pub const MUTTER_CAST: &str = "org.gnome.Mutter.ScreenCast";
pub const MUTTER_DISPLAY: &str = "org.gnome.Mutter.DisplayConfig";
pub const PORTAL: &str = "org.freedesktop.portal.Desktop";
/// the id of a monitor or window the desktop's dialog asks for
pub const ASK: &str = "ask";
/// how long a plain answer may take
pub const ANSWER_S: f64 = 2.0;

fn say(message: &str) {
    crate::logs::log(crate::logs::Level::Info, "libre_panel.wayland", message);
}

/// gstreamer: `gst-launch-1.0`, where it is (its pipewiresrc comes with the desktop's PipeWire).
pub fn gstreamer() -> Option<std::path::PathBuf> {
    crate::capture::which("gst-launch-1.0")
}

#[cfg(target_os = "linux")]
pub mod bus {
    //! The session bus, through libdbus.

    use std::time::Duration;

    use dbus::blocking::LocalConnection;

    pub type Connection = LocalConnection;

    /// _connection: the session bus.
    pub fn connect() -> Result<Connection, String> {
        LocalConnection::new_session().map_err(|why| why.to_string())
    }

    /// has_name: whether `name` has an owner on the bus.
    pub fn has_name(connection: &Connection, name: &str) -> Result<bool, String> {
        let proxy = connection.with_proxy(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            Duration::from_secs_f64(super::ANSWER_S),
        );
        let (owned,): (bool,) = proxy
            .method_call("org.freedesktop.DBus", "NameHasOwner", (name,))
            .map_err(|why| why.to_string())?;
        Ok(owned)
    }
}

/// desktop: which ways there are here: (`gnome`: Mutter's screen cast, `portal`).
pub fn desktop() -> (bool, bool) {
    #[cfg(target_os = "linux")]
    {
        let connection = match bus::connect() {
            Ok(connection) => connection,
            Err(why) => {
                say(&format!("wayland: no session bus ({why})"));
                return (false, false);
            }
        };
        let gnome = bus::has_name(&connection, MUTTER_CAST);
        let portal = bus::has_name(&connection, PORTAL);
        match (gnome, portal) {
            (Ok(gnome), Ok(portal)) => (gnome, portal),
            (Err(why), _) | (_, Err(why)) => {
                say(&format!("wayland: the session bus did not answer ({why})"));
                (false, false)
            }
        }
    }
    #[cfg(not(target_os = "linux"))]
    (false, false)
}

/// virtual_state: for the app (extend.state): a monitor of the panel's own is there for the
/// asking where GNOME or the portal is, with GStreamer; `missing` says what is not.
pub fn virtual_state() -> Dict {
    let mut told = Dict::new();
    let (ready, missing, gnome) = if gstreamer().is_none() {
        (false, "gstreamer", None)
    } else {
        match desktop() {
            (false, false) => (false, "portal", None),
            (gnome, _) => (true, "", Some(gnome)),
        }
    };
    told.insert("ready".into(), Value::Bool(ready));
    told.insert("missing".into(), Value::Str(missing.into()));
    if let Some(gnome) = gnome {
        told.insert("gnome".into(), Value::Bool(gnome));
    }
    told
}

/// monitors: GNOME's monitors (DisplayConfig.GetCurrentState): connector, place, size (its
/// mode's pixels), the main one; none on other desktops (their dialog lists them).
pub fn monitors() -> Vec<Monitor> {
    #[cfg(target_os = "linux")]
    {
        match gnome_state() {
            Ok(Some(found)) => found,
            Ok(None) => Vec::new(),
            Err(why) => {
                say(&format!("wayland: GNOME's monitors not listed ({why})"));
                Vec::new()
            }
        }
    }
    #[cfg(not(target_os = "linux"))]
    Vec::new()
}

#[cfg(target_os = "linux")]
type Connector = (String, String, String, String);
#[cfg(target_os = "linux")]
type Mode = (String, i32, i32, f64, f64, Vec<f64>, dbus::arg::PropMap);
#[cfg(target_os = "linux")]
type Physical = (Connector, Vec<Mode>, dbus::arg::PropMap);
#[cfg(target_os = "linux")]
type Logical = (i32, i32, f64, u32, bool, Vec<Connector>, dbus::arg::PropMap);

#[cfg(target_os = "linux")]
fn gnome_state() -> Result<Option<Vec<Monitor>>, String> {
    use std::time::Duration;

    let Ok(connection) = bus::connect() else {
        return Ok(None);
    };
    if !bus::has_name(&connection, MUTTER_DISPLAY)? {
        return Ok(None);
    }
    let proxy = connection.with_proxy(
        MUTTER_DISPLAY,
        "/org/gnome/Mutter/DisplayConfig",
        Duration::from_secs_f64(ANSWER_S),
    );
    let (_serial, physical, logical, _props): (
        u32,
        Vec<Physical>,
        Vec<Logical>,
        dbus::arg::PropMap,
    ) = proxy
        .method_call(MUTTER_DISPLAY, "GetCurrentState", ())
        .map_err(|why| why.to_string())?;
    Ok(Some(gnome_monitors(&physical, &logical)))
}

/// gnome_monitors: the monitors of DisplayConfig's state: each logical monitor's place and its
/// monitor's current mode, by connector.
#[cfg(target_os = "linux")]
fn gnome_monitors(physical: &[Physical], logical: &[Logical]) -> Vec<Monitor> {
    use dbus::arg::RefArg;

    let mut sizes: Vec<(String, (i64, i64, String))> = Vec::new();
    for ((connector, _vendor, _product, _serial), modes, props) in physical {
        let current = modes.iter().find(|mode| {
            mode.6
                .get("is-current")
                .and_then(|value| value.0.as_u64())
                .is_some_and(|on| on != 0)
        });
        let Some(current) = current else {
            continue;
        };
        let name = props
            .get("display-name")
            .and_then(|value| value.0.as_str().map(str::to_string))
            .filter(|name| !name.is_empty())
            .unwrap_or_else(|| connector.clone());
        let size = (i64::from(current.1), i64::from(current.2), name);
        match sizes.iter_mut().find(|(known, _)| known == connector) {
            Some(known) => known.1 = size,
            None => sizes.push((connector.clone(), size)),
        }
    }
    let mut found = Vec::new();
    for (x, y, _scale, _transform, primary, monitors_of, _props) in logical {
        for (connector, ..) in monitors_of {
            if let Some((_, (width, height, name))) = sizes.iter().find(|(c, _)| c == connector) {
                found.push(Monitor {
                    id: connector.clone(),
                    name: name.clone(),
                    x: i64::from(*x),
                    y: i64::from(*y),
                    width: *width,
                    height: *height,
                    primary: *primary,
                    // (Mutter's own, as RecordVirtual's)
                    virtual_: connector.starts_with("Meta-"),
                });
            }
        }
    }
    found
}

/// feeder_command: how the app starts its own capture program (wayland_feed.rs).
pub fn feeder_command() -> Vec<String> {
    let program = std::env::current_exe().unwrap_or_default();
    vec![
        program.to_string_lossy().into_owned(),
        "wayland-feed".to_string(),
    ]
}

/// capture_of: ffmpeg's input for `source` here: raw frames of `size` (the panel's) from the
/// capture program, which fits them as `source.fit` says.
pub fn capture_of(
    source: &Source,
    fps: i64,
    size: Option<(usize, usize)>,
) -> Result<Capture, MirrorError> {
    if gstreamer().is_none() {
        return Err(MirrorError(
            "on Wayland a panel shows the screen through GStreamer (gst-launch-1.0 and its \
             PipeWire plugin): install them"
                .into(),
        ));
    }
    let Some((width, height)) = size else {
        return Err(MirrorError("the panel's size is not known".into()));
    };
    let ident = if source.id.is_empty() {
        ASK
    } else {
        &source.id
    };
    let mut command = feeder_command();
    command.extend(
        [
            "--kind",
            &source.kind,
            "--id",
            ident,
            "--size",
            &format!("{width}x{height}"),
            "--fps",
            &fps.to_string(),
            "--fit",
            &source.fit,
        ]
        .map(str::to_string),
    );
    if !source.cursor {
        command.push("--no-cursor".into());
    }
    let inputs = [
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr0",
        "-video_size",
        &format!("{width}x{height}"),
        "-framerate",
        &fps.to_string(),
        "-i",
        "pipe:0",
    ]
    .map(str::to_string)
    .to_vec();
    let label = if source.name.is_empty() {
        source.kind.clone()
    } else {
        source.name.clone()
    };
    Ok(Capture {
        inputs,
        feeder: command,
        label,
        ..Capture::default()
    })
}
