//! The capture program on Wayland (wayland_feed.py: `libre-panel wayland-feed`, started by
//! wayland.rs): it asks the desktop for the screen, holds its session while it runs, and writes
//! the panel's frames to its output, raw (BGRx), fitted to the panel's size, at the panel's
//! frames a second. Nothing else goes to its output: what it says goes to its error output.
//!
//! 1. **The stream.** GNOME: Mutter's screen cast, a session with a monitor by its connector
//!    (`RecordMonitor`) or a monitor of the panel's own (`RecordVirtual`); started, its stream
//!    says its PipeWire node. Elsewhere: the screen-sharing portal: a session, the sources (a
//!    monitor, a window, a virtual output; the dialog asks, its choice kept for the next time:
//!    `persist_mode`), started; the PipeWire node of the stream and the PipeWire connection the
//!    portal opens for it (a file descriptor).
//! 2. **The pictures.** `gst-launch-1.0`: `pipewiresrc` on that node, converted, cropped and
//!    scaled as `--fit` says, at `--fps`, to this program's output. It ends with this program.
//! 3. **The end.** When GStreamer ends, the session is stopped; when the desktop closes the
//!    session (the person stopped sharing), GStreamer is ended; either way this program ends.

// (Wayland is Linux's: elsewhere what talks to its desktop goes unused)
#![cfg_attr(not(target_os = "linux"), allow(dead_code))]

/// say: what the program says, on its error output.
pub fn say(text: &str) {
    eprintln!("wayland-feed: {text}");
}

/// cover_crop: `(left, right, top, bottom)` cropping `stream` to `size`'s proportions,
/// centred.
pub fn cover_crop(stream: (i64, i64), size: (i64, i64)) -> (i64, i64, i64, i64) {
    let (sw, sh) = stream;
    let (width, height) = size;
    if sw * height > sh * width {
        // wider: off the sides
        let keep = (sh as f64 * width as f64 / height as f64).round_ties_even() as i64;
        let left = (sw - keep).div_euclid(2);
        return (left, sw - keep - left, 0, 0);
    }
    let keep = (sw as f64 * height as f64 / width as f64).round_ties_even() as i64;
    let top = (sh - keep).div_euclid(2);
    (0, 0, top, sh - keep - top)
}

/// gst_command: gst-launch-1.0's pipeline: the stream of `node` (through `fd` where the portal
/// opened it; `sized`: asked at the panel's size, Mutter's monitor of its own) to raw BGRx
/// frames of `size` on its output, `fps` a second, fitted as `fit` says.
#[allow(clippy::too_many_arguments)]
pub fn gst_command(
    gst: &str,
    node: u32,
    fd: Option<i32>,
    stream: Option<(i64, i64)>,
    size: (i64, i64),
    fps: i64,
    fit: &str,
    sized: bool,
) -> Vec<String> {
    let (width, height) = size;
    let mut source = vec![
        "pipewiresrc".to_string(),
        format!("path={node}"),
        "do-timestamp=true".into(),
        "keepalive-time=1000".into(),
        "always-copy=true".into(),
    ];
    if let Some(fd) = fd {
        source.push(format!("fd={fd}"));
    }
    let mut parts = vec![source];
    if sized {
        parts.push(vec![format!("video/x-raw,width={width},height={height}")]);
    }
    parts.push(vec!["videoconvert".into()]);
    if let (true, Some(stream)) = (fit == "cover", stream) {
        let (left, right, top, bottom) = cover_crop(stream, size);
        parts.push(vec![
            "videocrop".into(),
            format!("left={left}"),
            format!("right={right}"),
            format!("top={top}"),
            format!("bottom={bottom}"),
        ]);
    }
    let borders = if fit == "contain" { "true" } else { "false" };
    parts.push(vec!["videoscale".into(), format!("add-borders={borders}")]);
    parts.push(vec![format!(
        "video/x-raw,format=BGRx,width={width},height={height},pixel-aspect-ratio=1/1"
    )]);
    parts.push(vec!["videorate".into()]);
    parts.push(vec![format!("video/x-raw,framerate={fps}/1")]);
    parts.push(vec!["fdsink".into(), "fd=1".into(), "sync=false".into()]);
    let mut command = vec![gst.to_string(), "-q".into()];
    for (n, part) in parts.into_iter().enumerate() {
        if n > 0 {
            command.push("!".into());
        }
        command.extend(part);
    }
    command
}

/// The program's options (wayland_feed.py's add_arguments).
pub struct Asked {
    pub kind: String,
    pub id: String,
    pub size: (i64, i64),
    pub fps: i64,
    pub fit: String,
    pub cursor: bool,
}

/// The options, or what is wrong with them and the exit code argparse gives.
pub fn asked(args: &[String]) -> Result<Asked, (String, i32)> {
    let usage = |why: String| (why, 2);
    let (mut kind, mut id, mut size, mut fps, mut fit, mut cursor) = (
        None,
        crate::wayland::ASK.to_string(),
        None,
        30,
        "contain".to_string(),
        true,
    );
    let mut rest = args.iter();
    while let Some(arg) = rest.next() {
        let mut value = |name: &str| {
            rest.next()
                .cloned()
                .ok_or_else(|| usage(format!("argument {name}: expected one argument")))
        };
        match arg.as_str() {
            "--kind" => {
                let given = value("--kind")?;
                if !["monitor", "virtual", "window"].contains(&given.as_str()) {
                    return Err(usage(format!(
                        "argument --kind: invalid choice: '{given}' (choose from 'monitor', \
                         'virtual', 'window')"
                    )));
                }
                kind = Some(given);
            }
            "--id" => id = value("--id")?,
            "--size" => size = Some(value("--size")?),
            "--fps" => {
                let given = value("--fps")?;
                fps = given
                    .parse()
                    .map_err(|_| usage(format!("argument --fps: invalid int value: '{given}'")))?;
            }
            "--fit" => {
                let given = value("--fit")?;
                if !["contain", "cover", "stretch"].contains(&given.as_str()) {
                    return Err(usage(format!(
                        "argument --fit: invalid choice: '{given}' (choose from 'contain', \
                         'cover', 'stretch')"
                    )));
                }
                fit = given;
            }
            "--no-cursor" => cursor = false,
            other => return Err(usage(format!("unrecognized arguments: {other}"))),
        }
    }
    let kind = kind.ok_or_else(|| usage("the following arguments are required: --kind".into()))?;
    let size = size.ok_or_else(|| usage("the following arguments are required: --size".into()))?;
    let lowered = size.to_lowercase();
    let parts: Vec<&str> = lowered.split('x').collect();
    let parsed = match parts[..] {
        [w, h] => w.trim().parse().ok().zip(h.trim().parse().ok()),
        _ => None,
    };
    let Some(size) = parsed else {
        return Err(("--size: WIDTHxHEIGHT".into(), 2));
    };
    Ok(Asked {
        kind,
        id,
        size,
        fps,
        fit,
        cursor,
    })
}

/// run: the program; its exit code (2: wrong options or no GStreamer, 3: the desktop gave no
/// screen, 1: another failure, said).
pub fn run(args: &[String]) -> i32 {
    let asked = match asked(args) {
        Ok(asked) => asked,
        Err((why, code)) => {
            if why.starts_with("--size") {
                say(&why);
            } else {
                eprintln!("usage: libre-panel wayland-feed --kind {{monitor,virtual,window}} [--id ID] --size SIZE [--fps FPS] [--fit {{contain,cover,stretch}}] [--no-cursor]");
                eprintln!("libre-panel wayland-feed: error: {why}");
            }
            return code;
        }
    };
    let Some(gst) = crate::wayland::gstreamer() else {
        say("no gst-launch-1.0");
        return 2;
    };
    #[cfg(target_os = "linux")]
    {
        linux::run(&asked, &gst.to_string_lossy())
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (asked, gst);
        say("the screen of Wayland is for Linux");
        2
    }
}

#[cfg(target_os = "linux")]
mod linux {
    use std::cell::RefCell;
    use std::rc::Rc;
    use std::time::{Duration, Instant};

    use dbus::arg::{PropMap, RefArg, Variant};
    use dbus::message::MatchRule;
    use dbus::strings::Path;

    use super::{gst_command, say, Asked};
    use crate::wayland::bus::{self, Connection};
    use crate::wayland::{ASK, MUTTER_CAST, PORTAL};

    const MUTTER: &str = MUTTER_CAST;
    /// the dialog waits on the person
    const DIALOG_S: f64 = 300.0;
    const ANSWER_S: f64 = 10.0;
    /// the portal's restore tokens, by kind, beside config.toml
    const TOKENS: &str = "wayland-screencast.json";

    /// What went wrong: the desktop gave no stream (Refused, said for the log), or another
    /// failure.
    pub enum Failed {
        Refused(String),
        Other(String),
    }

    impl From<dbus::Error> for Failed {
        fn from(why: dbus::Error) -> Failed {
            Failed::Other(format!(
                "{}: {}",
                why.name().unwrap_or("DBusError"),
                why.message().unwrap_or("")
            ))
        }
    }

    fn seconds(s: f64) -> Duration {
        Duration::from_secs_f64(s)
    }

    /// A stream: its PipeWire node, the PipeWire connection the portal opened for it, its
    /// size where the portal says it, and the session that holds it.
    type Stream = (u32, Option<i32>, Option<(i64, i64)>, Session);

    /// A session the program holds: its object, and whether it is Mutter's (Stop) or the
    /// portal's (Close).
    pub struct Session {
        path: Path<'static>,
        mutter: bool,
    }

    /// The first signal `rule` matches, waiting up to `timeout` after `then` is done (the
    /// match set up before, so that the signal cannot come too early).
    fn signal_after<T, F>(
        connection: &Connection,
        rule: MatchRule<'static>,
        timeout: f64,
        then: F,
    ) -> Result<T, Failed>
    where
        T: dbus::arg::ReadAll + 'static,
        F: FnOnce() -> Result<(), Failed>,
    {
        let got: Rc<RefCell<Option<T>>> = Rc::new(RefCell::new(None));
        let keep = got.clone();
        let token = connection.add_match(rule, move |body: T, _, _| {
            *keep.borrow_mut() = Some(body);
            false
        })?;
        let done = then();
        if let Err(why) = done {
            let _ = connection.remove_match(token);
            return Err(why);
        }
        let until = Instant::now() + seconds(timeout);
        while got.borrow().is_none() {
            let left = until.saturating_duration_since(Instant::now());
            if left.is_zero() {
                let _ = connection.remove_match(token);
                return Err(Failed::Other("TimeoutError: no answer in time".into()));
            }
            connection.process(left.min(seconds(0.5)))?;
        }
        let body = got.borrow_mut().take();
        body.ok_or_else(|| Failed::Other("no answer".into()))
    }

    /// mutter_stream: GNOME: `(node, None, None, session)`: a monitor by its connector, or the
    /// panel's own.
    pub fn mutter_stream(
        connection: &Connection,
        kind: &str,
        connector: &str,
        cursor: bool,
    ) -> Result<Stream, Failed> {
        let cast = connection.with_proxy(MUTTER, "/org/gnome/Mutter/ScreenCast", seconds(ANSWER_S));
        let (session_path,): (Path<'static>,) =
            cast.method_call(MUTTER, "CreateSession", (PropMap::new(),))?;
        let session = connection.with_proxy(MUTTER, session_path.clone(), seconds(ANSWER_S));
        let mut options = PropMap::new();
        // (1: drawn into the picture)
        options.insert(
            "cursor-mode".into(),
            Variant(Box::new(u32::from(cursor)) as Box<dyn RefArg>),
        );
        let interface = format!("{MUTTER}.Session");
        let (stream_path,): (Path<'static>,) = if kind == "virtual" {
            session.method_call(interface.as_str(), "RecordVirtual", (options,))?
        } else {
            session.method_call(interface.as_str(), "RecordMonitor", (connector, options))?
        };
        let rule = MatchRule::new_signal(format!("{MUTTER}.Stream"), "PipeWireStreamAdded")
            .with_path(stream_path);
        let (node,): (u32,) = signal_after(connection, rule, ANSWER_S, || {
            session.method_call::<(), _, _, _>(interface.as_str(), "Start", ())?;
            Ok(())
        })?;
        let session = Session {
            path: session_path,
            mutter: true,
        };
        Ok((node, None, None, session))
    }

    fn tokens_file() -> std::path::PathBuf {
        lp_config::dirs::config_dir().join(TOKENS)
    }

    fn restore_token(kind: &str) -> String {
        let text = std::fs::read_to_string(tokens_file()).unwrap_or_default();
        match lp_py::json::loads(&text) {
            Ok(lp_py::Value::Dict(known)) => match known.get(kind) {
                Some(lp_py::Value::Str(token)) => token.clone(),
                Some(other) => lp_py::format::str_of(other),
                None => String::new(),
            },
            _ => String::new(),
        }
    }

    fn keep_token(kind: &str, token: &str) {
        let path = tokens_file();
        let text = std::fs::read_to_string(&path).unwrap_or_default();
        let mut known = match lp_py::json::loads(&text) {
            Ok(lp_py::Value::Dict(known)) => known,
            _ => lp_py::Dict::default(),
        };
        known.insert(kind.into(), lp_py::Value::Str(token.into()));
        let text = lp_py::json::dumps(&lp_py::Value::Dict(known), &lp_py::json::Dumps::default());
        let written = path
            .parent()
            .map_or(Ok(()), std::fs::create_dir_all)
            .and_then(|()| std::fs::write(&path, text));
        if let Err(why) = written {
            say(&format!("the desktop's choice is not kept ({why})"));
        }
    }

    fn variant<T: RefArg + 'static>(value: T) -> Variant<Box<dyn RefArg>> {
        Variant(Box::new(value))
    }

    /// One request of the portal: `call` with its handle token, answered by the Response
    /// signal on the request's object (watched before the call): its results, or Refused.
    fn portal_request<F>(
        connection: &Connection,
        sender: &str,
        count: &mut u32,
        timeout: f64,
        call: F,
    ) -> Result<PropMap, Failed>
    where
        F: FnOnce(&str) -> Result<(), dbus::Error>,
    {
        *count += 1;
        let token = format!("libre_panel_{}_{count}", std::process::id());
        let path = format!("/org/freedesktop/portal/desktop/request/{sender}/{token}");
        let rule = MatchRule::new_signal("org.freedesktop.portal.Request", "Response")
            .with_path(Path::new(path).map_err(Failed::Other)?);
        let (code, results): (u32, PropMap) = signal_after(connection, rule, timeout, || {
            call(&token).map_err(Failed::from)
        })?;
        match code {
            0 => Ok(results),
            1 => Err(Failed::Refused("cancelled".into())),
            code => Err(Failed::Refused(format!("refused ({code})"))),
        }
    }

    /// portal_stream: elsewhere: `(node, fd, size of the stream, session)` through the portal;
    /// the dialog asks the person which source, unless a kept choice (restore token) answers.
    pub fn portal_stream(
        connection: &Connection,
        kind: &str,
        cursor: bool,
    ) -> Result<Stream, Failed> {
        let portal =
            connection.with_proxy(PORTAL, "/org/freedesktop/portal/desktop", seconds(ANSWER_S));
        let interface = "org.freedesktop.portal.ScreenCast";
        let sender = connection
            .unique_name()
            .trim_start_matches(':')
            .replace('.', "_");
        let mut count = 0u32;
        let results = portal_request(connection, &sender, &mut count, ANSWER_S, |token| {
            let mut options = PropMap::new();
            options.insert("handle_token".into(), variant(token.to_string()));
            options.insert(
                "session_handle_token".into(),
                variant(format!("{token}_session")),
            );
            portal.method_call::<(Path<'static>,), _, _, _>(
                interface,
                "CreateSession",
                (options,),
            )?;
            Ok(())
        })?;
        let session_text = results
            .get("session_handle")
            .and_then(|value| value.0.as_str().map(str::to_string))
            .ok_or_else(|| Failed::Other("KeyError: 'session_handle'".into()))?;
        let session_path = Path::new(session_text).map_err(Failed::Other)?;
        let types = match kind {
            "monitor" => 1u32,
            "window" => 2,
            _ => 4,
        };
        let kept = restore_token(kind);
        portal_request(connection, &sender, &mut count, ANSWER_S, |token| {
            let mut choose = PropMap::new();
            choose.insert("types".into(), variant(types));
            choose.insert("multiple".into(), variant(false));
            choose.insert("cursor_mode".into(), variant(if cursor { 2u32 } else { 1 }));
            choose.insert("persist_mode".into(), variant(2u32));
            if !kept.is_empty() {
                choose.insert("restore_token".into(), variant(kept.clone()));
            }
            choose.insert("handle_token".into(), variant(token.to_string()));
            portal.method_call::<(Path<'static>,), _, _, _>(
                interface,
                "SelectSources",
                (session_path.clone(), choose),
            )?;
            Ok(())
        })?;
        let results = portal_request(connection, &sender, &mut count, DIALOG_S, |token| {
            let mut options = PropMap::new();
            options.insert("handle_token".into(), variant(token.to_string()));
            portal.method_call::<(Path<'static>,), _, _, _>(
                interface,
                "Start",
                (session_path.clone(), "", options),
            )?;
            Ok(())
        })?;
        let (node, size) =
            first_stream(&results).ok_or_else(|| Failed::Refused("no stream".into()))?;
        if let Some(token) = results
            .get("restore_token")
            .and_then(|value| value.0.as_str().map(str::to_string))
            .filter(|token| !token.is_empty())
        {
            keep_token(kind, &token);
        }
        let (remote,): (dbus::arg::OwnedFd,) = portal.method_call(
            interface,
            "OpenPipeWireRemote",
            (session_path.clone(), PropMap::new()),
        )?;
        let session = Session {
            path: session_path,
            mutter: false,
        };
        Ok((node, Some(remote.into_fd()), size, session))
    }

    /// The node and size of the first stream of Start's results (`streams`: a(ua{sv})).
    fn first_stream(results: &PropMap) -> Option<(u32, Option<(i64, i64)>)> {
        let streams = results.get("streams")?;
        let mut streams = streams.0.as_iter()?;
        // (the variant holds the array: its first item is the list itself where it is one)
        let first = streams.next()?;
        let stream = match first.as_iter() {
            Some(mut inner) if first.arg_type() == dbus::arg::ArgType::Array => inner.next()?,
            _ => first,
        };
        let mut fields = stream.as_iter()?;
        let node = u32::try_from(fields.next()?.as_u64()?).ok()?;
        let properties = fields.next()?;
        let mut size = None;
        if let Some(mut pairs) = properties.as_iter() {
            while let (Some(key), Some(value)) = (pairs.next(), pairs.next()) {
                if key.as_str() == Some("size") {
                    let mut wh = value.as_iter()?;
                    let inner = wh.next()?;
                    let (w, h) = match inner.as_iter() {
                        Some(mut pair) => (pair.next()?.as_i64()?, pair.next()?.as_i64()?),
                        None => (inner.as_i64()?, wh.next()?.as_i64()?),
                    };
                    size = Some((w, h));
                }
            }
        }
        Some((node, size))
    }

    /// _with_parent: GStreamer ends with this program (a signal when its parent dies).
    fn with_parent(command: &mut std::process::Command, fd: Option<i32>) {
        use std::os::unix::process::CommandExt;
        // SAFETY: only async-signal-safe calls between fork and exec
        unsafe {
            command.pre_exec(move || {
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL);
                if let Some(fd) = fd {
                    // (the portal's descriptor goes to GStreamer: not closed on exec)
                    let flags = libc::fcntl(fd, libc::F_GETFD);
                    if flags >= 0 {
                        libc::fcntl(fd, libc::F_SETFD, flags & !libc::FD_CLOEXEC);
                    }
                }
                Ok(())
            });
        }
    }

    pub fn run(asked: &Asked, gst: &str) -> i32 {
        let connection = match bus::connect() {
            Ok(connection) => connection,
            Err(why) => {
                say(&format!("DBusError: {why}"));
                return 1;
            }
        };
        let mut session = None;
        let ended = (|| -> Result<i32, Failed> {
            let gnome = bus::has_name(&connection, MUTTER).map_err(Failed::Other)?;
            let mine = asked.kind == "virtual" || (asked.kind == "monitor" && asked.id != ASK);
            let ((node, fd, stream, got), sized) = if gnome && mine {
                (
                    mutter_stream(&connection, &asked.kind, &asked.id, asked.cursor)?,
                    asked.kind == "virtual",
                )
            } else {
                (
                    portal_stream(&connection, &asked.kind, asked.cursor)?,
                    false,
                )
            };
            session = Some(got);
            let command = gst_command(
                gst, node, fd, stream, asked.size, asked.fps, &asked.fit, sized,
            );
            let shown = stream.map_or(String::new(), |(w, h)| format!(" ({w}x{h})"));
            say(&format!("stream {node}{shown}"));
            let mut process = std::process::Command::new(&command[0]);
            process.args(&command[1..]);
            with_parent(&mut process, fd);
            let feeding = process
                .spawn()
                .map_err(|why| Failed::Other(why.to_string()));
            if let Some(fd) = fd {
                // SAFETY: the descriptor is this program's own, given to GStreamer above
                unsafe { libc::close(fd) };
            }
            let feeding = feeding?;
            let path = session.as_ref().map(|s| s.path.clone()).unwrap_or_default();
            until_ended(&connection, path, feeding)
        })();
        let code = match ended {
            Ok(code) => code,
            Err(Failed::Refused(why)) => {
                say(&format!("the desktop gave no screen: {why}"));
                3
            }
            Err(Failed::Other(why)) => {
                say(&why);
                1
            }
        };
        close(&connection, session.as_ref());
        code
    }

    fn until_ended(
        connection: &Connection,
        session: Path<'static>,
        mut feeding: std::process::Child,
    ) -> Result<i32, Failed> {
        let closed = Rc::new(RefCell::new(false));
        let mark = closed.clone();
        let mut rule = MatchRule::new();
        rule.msg_type = Some(dbus::message::MessageType::Signal);
        rule.member = Some("Closed".into());
        rule.path = Some(session);
        let added = connection.add_match(rule, move |(): (), _, _| {
            *mark.borrow_mut() = true;
            false
        });
        let watched = (|| -> Result<(), Failed> {
            added?;
            while feeding
                .try_wait()
                .map_err(|why| Failed::Other(why.to_string()))?
                .is_none()
            {
                connection.process(seconds(1.0))?;
                if *closed.borrow() {
                    say("the desktop stopped sharing the screen");
                    break;
                }
            }
            Ok(())
        })();
        if feeding.try_wait().ok().flatten().is_none() {
            let _ = feeding.kill();
        }
        let _ = feeding.wait();
        watched.map(|()| 0)
    }

    fn close(connection: &Connection, session: Option<&Session>) {
        let Some(session) = session else {
            return;
        };
        let (bus_name, interface, method) = if session.mutter {
            (MUTTER, format!("{MUTTER}.Session"), "Stop")
        } else {
            (
                PORTAL,
                "org.freedesktop.portal.Session".to_string(),
                "Close",
            )
        };
        let proxy = connection.with_proxy(bus_name, session.path.clone(), seconds(2.0));
        // (the session ends with the connection anyway)
        let _ = proxy.method_call::<(), _, _, _>(interface.as_str(), method, ());
    }
}

#[cfg(test)]
mod tests {
    use super::{cover_crop, gst_command};

    #[test]
    fn gstreamers_pipeline_fits_the_stream() {
        assert_eq!(cover_crop((1920, 1080), (1920, 720)), (0, 0, 180, 180));
        assert_eq!(cover_crop((1280, 720), (480, 480)), (280, 280, 0, 0));
        let pipeline = gst_command(
            "gst",
            77,
            Some(5),
            Some((1920, 1080)),
            (1920, 720),
            50,
            "cover",
            false,
        )
        .join(" ");
        assert!(pipeline.starts_with("gst -q pipewiresrc path=77 ") && pipeline.contains(" fd=5 "));
        assert!(pipeline.contains(
            "videocrop left=0 right=0 top=180 bottom=180 ! videoscale add-borders=false"
        ));
        assert!(pipeline.ends_with("video/x-raw,framerate=50/1 ! fdsink fd=1 sync=false"));
        let own = gst_command("gst", 42, None, None, (1920, 720), 30, "contain", true).join(" ");
        assert!(own.contains("! video/x-raw,width=1920,height=720 ! videoconvert"));
        assert!(own.contains("add-borders=true"));
    }
}
