//! The window's program: the app in a window of its own until it is closed (window.py's
//! run_window, `libre-panel window URL`), holding `window.lock` from its first moment to its
//! last, answering the other starts on a local port.

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::Path;
use std::time::{Duration, Instant};

use lp_py::{Dict, Value};

use crate::access;
use crate::instance::{InstanceLock, NotLocked};
use crate::window::{
    focus_window, open_in_browser, parts, port_of, say, view_url, ANSWER_S, RUNNING_FILENAME,
};

const TITLE: &str = "Libre Panel";
const MIN_SIZE: (u32, u32) = (1100, 700);
const DEFAULT_SIZE: (u32, u32) = (1440, 900);
/// the app's background: no white flash while it loads
const BACKGROUND: (u8, u8, u8, u8) = (0x0A, 0x0E, 0x13, 0xFF);
/// held by the window's process, opening or open
const LOCK_FILENAME: &str = "window.lock";
/// how long a second start waits for a window that is still opening
const OPENING_S: f64 = 30.0;
/// its size, kept for the next time
const STATE_FILENAME: &str = "window-state.json";
/// WebView2's processes lighter (Windows): what its browser engine is started with, each in
/// Microsoft's list of WebView2's browser flags or among Chromium's switches; the features
/// wry turns off by default are kept off (one --disable-features: Chromium reads the last)
#[cfg_attr(not(windows), allow(dead_code))]
const BROWSER_ARGUMENTS: &str = "--renderer-process-limit=1 \
     --disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection,\
     SpareRendererForSitePerProcess --disk-cache-size=1 --disable-gpu-shader-disk-cache";
/// the person's own arguments for WebView2, where set: kept as they are
#[cfg_attr(not(windows), allow(dead_code))]
const WEBVIEW2_ARGUMENTS: &str = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS";
/// The page's side of the window: window.pywebview.api.<name>(...) posts the call, and the page
/// hears "pywebviewready" once it may call (gtk_window.py's _BRIDGE_JS).
const BRIDGE_JS: &str = r#"(() => {
  const post = (name, args) => {
    window.ipc.postMessage(JSON.stringify({ name, args }));
    return Promise.resolve(null);
  };
  const api = {};
  for (const name of ["set_dirty", "set_title", "close"]) api[name] = (...args) => post(name, args);
  window.pywebview = { api };
  const ready = () => window.dispatchEvent(new CustomEvent("pywebviewready"));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else setTimeout(ready, 0);
})();"#;

/// local_url: only Libre Panel's own server is opened in its window.
fn local_url(url: &str) -> bool {
    parts(url).is_some_and(|(scheme, host, _)| {
        scheme.eq_ignore_ascii_case("http") && ["127.0.0.1", "localhost"].contains(&host)
    })
}

/// _wait_for_window: another window process holds the lock: its window is open or opening.
/// It is brought to the front as soon as it answers; false when it has not after OPENING_S.
fn wait_for_window(settings: &Path, url: &str, view: Option<&str>, token: Option<&str>) -> bool {
    let until = Instant::now() + Duration::from_secs_f64(OPENING_S);
    loop {
        if focus_window(settings, url, view, token) {
            return true;
        }
        if Instant::now() >= until {
            return false;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

/// load_state: the window's size and whether it was maximised, as it was last time.
fn load_state(settings: &Path) -> (u32, u32, bool) {
    let text = std::fs::read_to_string(settings.join(STATE_FILENAME)).unwrap_or_default();
    let state = match lp_py::json::loads(&text) {
        Ok(Value::Dict(state)) => state,
        _ => Dict::default(),
    };
    let whole = |key: &str| match state.get(key) {
        Some(Value::Int(n)) => n.to_string().parse::<i64>().ok(),
        _ => None,
    };
    let (width, height) = match (whole("width"), whole("height")) {
        (Some(w), Some(h)) => (w, h),
        _ => (i64::from(DEFAULT_SIZE.0), i64::from(DEFAULT_SIZE.1)),
    };
    let clamp = |n: i64, least: u32| u32::try_from(n.max(i64::from(least))).unwrap_or(least);
    let maximized = matches!(state.get("maximized"), Some(Value::Bool(true)));
    (
        clamp(width, MIN_SIZE.0),
        clamp(height, MIN_SIZE.1),
        maximized,
    )
}

fn save_state(settings: &Path, (width, height, maximized): (u32, u32, bool)) {
    let mut state = Dict::default();
    state.insert("width".into(), Value::int(i64::from(width)));
    state.insert("height".into(), Value::int(i64::from(height)));
    state.insert("maximized".into(), Value::Bool(maximized));
    let text = lp_py::json::dumps(&Value::Dict(state), &lp_py::json::Dumps::default());
    let path = settings.join(STATE_FILENAME);
    let temporary = settings.join(format!("{STATE_FILENAME}.tmp"));
    let kept = std::fs::create_dir_all(settings)
        .and_then(|()| std::fs::write(&temporary, text))
        .and_then(|()| std::fs::rename(&temporary, &path));
    if let Err(why) = kept {
        say(&format!("window size not kept: {why}"));
    }
}

/// _register: the file that lets other starts find this window, readable by this user only.
fn register(settings: &Path, port: u16, token: &str, url: &str) {
    let mut info = Dict::default();
    info.insert("pid".into(), Value::int(i64::from(std::process::id())));
    info.insert("port".into(), Value::int(i64::from(port)));
    info.insert("token".into(), Value::Str(token.into()));
    info.insert("url".into(), Value::Str(url.into()));
    let text = lp_py::json::dumps(&Value::Dict(info), &lp_py::json::Dumps::default());
    let path = settings.join(RUNNING_FILENAME);
    let temporary = settings.join(format!("{RUNNING_FILENAME}.tmp"));
    let _ = std::fs::remove_file(&temporary);
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let written = std::fs::create_dir_all(settings)
        .and_then(|()| options.open(&temporary))
        .and_then(|mut file| file.write_all(text.as_bytes()))
        .and_then(|()| std::fs::rename(&temporary, &path));
    if let Err(why) = written {
        say(&format!(
            "the window could not write {}: {why}",
            path.display()
        ));
    }
}

fn unregister(settings: &Path) {
    let path = settings.join(RUNNING_FILENAME);
    let mine = std::fs::read_to_string(&path)
        .ok()
        .and_then(|text| lp_py::json::loads(&text).ok())
        .is_some_and(|info| match info {
            Value::Dict(info) => {
                info.get("pid").and_then(Value::to_f64) == Some(f64::from(std::process::id()))
            }
            _ => false,
        });
    if mine {
        let _ = std::fs::remove_file(path);
    }
}

/// What another start asks of the window.
#[derive(Debug)]
pub enum Asked {
    Focus(String),
    Close,
}

/// _Requests: answers the other starts, `focus <token> <url>` and `close <token>`: a request is
/// answered as soon as it is understood, and what it asks is handed to the window.
fn answer(listener: TcpListener, token: String, hand: impl Fn(Asked) + Send + 'static) {
    std::thread::Builder::new()
        .name("window-requests".into())
        .spawn(move || {
            for link in listener.incoming() {
                let Ok(mut link) = link else { continue };
                let _ = link.set_read_timeout(Some(Duration::from_secs_f64(ANSWER_S)));
                let mut line = String::new();
                let read = BufReader::new(&link).take_line(&mut line);
                if read.is_err() {
                    continue;
                }
                let words: Vec<&str> = line.split_whitespace().collect();
                let given = words.get(1).copied();
                let asked = match words.first() {
                    _ if !access::matches(given, &token) => None,
                    Some(&"close") => Some(Asked::Close),
                    Some(&"focus") if words.len() == 3 && local_url(words[2]) => {
                        Some(Asked::Focus(words[2].to_string()))
                    }
                    _ => None,
                };
                let known = asked.is_some();
                let _ = link.write_all(if known { b"ok\n" } else { b"no\n" });
                if let Some(asked) = asked {
                    hand(asked);
                }
            }
        })
        .map(drop)
        .unwrap_or_else(|why| say(&format!("the window answers no other start: {why}")));
}

/// A line read, at most 4096 bytes of it.
trait TakeLine {
    fn take_line(self, line: &mut String) -> std::io::Result<usize>;
}

impl<R: std::io::Read> TakeLine for BufReader<R> {
    fn take_line(self, line: &mut String) -> std::io::Result<usize> {
        let mut limited = BufReader::new(std::io::Read::take(self, 4096));
        limited.read_line(line)
    }
}

/// run_window: the app shown in a window on this (main) thread until it is closed. An open
/// window is brought to the front instead; where no window can open, the browser opens.
pub fn run_window(settings: &Path, url: &str, view: Option<&str>) -> i32 {
    let (base, inside) = access::split_token(url);
    let target = match view {
        Some(view) => view_url(&base, Some(view)),
        None => base.clone(),
    };
    if !local_url(&target) {
        say(&format!(
            "refusing to open {target} in Libre Panel's window"
        ));
        return 2;
    }
    let inherited = access::inherited_token(); // (taken out of the environment in any case)
    let token = inside
        .or(inherited)
        .or_else(|| access::read_token(settings, port_of(&base)));
    let lock = match InstanceLock::acquire_named(settings, LOCK_FILENAME) {
        Ok(lock) => lock,
        Err(NotLocked::Running(_)) => {
            // another window is open, or opening: that one, never a second
            if !wait_for_window(settings, &base, view, token.as_deref()) {
                say("the open window does not answer: the app opens in the browser");
                open_in_browser(&access::with_token(&target, token.as_deref()));
            }
            return 0;
        }
        Err(NotLocked::Failed(why)) => {
            say(&format!("the window's lock: {why}"));
            return 1;
        }
    };
    // (a window of a Libre Panel before the lock)
    if focus_window(settings, &base, view, token.as_deref()) {
        return 0;
    }
    let listener = match TcpListener::bind(("127.0.0.1", 0)) {
        Ok(listener) => listener,
        Err(why) => {
            say(&format!("the window's port: {why}"));
            return 1;
        }
    };
    let port = listener.local_addr().map_or(0, |at| at.port());
    let secret = match access::token_urlsafe(16) {
        Ok(secret) => secret,
        Err(why) => {
            say(&format!("the window's token: {why}"));
            return 1;
        }
    };
    register(settings, port, &secret, &target); // (names the page, not the access token)
    let shown = access::with_token(&target, token.as_deref());
    let code = native::show(settings, &shown, listener, secret);
    unregister(settings);
    drop(lock);
    code
}

#[cfg(any(windows, target_os = "linux"))]
mod native {
    //! The window itself: tao's window with wry's web view in it.

    use std::cell::Cell;
    use std::net::TcpListener;
    use std::path::Path;
    use std::rc::Rc;

    use tao::dpi::LogicalSize;
    use tao::event::{Event, WindowEvent};
    use tao::event_loop::{ControlFlow, EventLoopBuilder};
    use tao::window::WindowBuilder;
    use wry::{NewWindowResponse, WebContext, WebViewBuilder};

    use super::{
        answer, load_state, local_url, save_state, Asked, BACKGROUND, BRIDGE_JS, MIN_SIZE, TITLE,
    };
    use crate::access;
    use crate::window::{open_in_browser, say};

    /// What the page and the other starts ask of the window's thread.
    #[derive(Debug)]
    enum Told {
        Asked(Asked),
        Dirty(bool),
        Title(String),
        Close,
        /// a page of the app's own asked for in a window of its own: its route, here
        Route(String),
    }

    /// The question before closing a window with changes that are not saved.
    fn may_close() -> bool {
        let question = lp_i18n::t("Close the window? Changes that are not saved are lost.");
        let dialog = rfd::MessageDialog::new()
            .set_title(TITLE)
            .set_description(question)
            .set_level(rfd::MessageLevel::Warning);
        // (Windows: a task dialog of the common controls the program's manifest names)
        let close = lp_i18n::t("Close");
        let buttons = rfd::MessageButtons::OkCancelCustom(close.clone(), lp_i18n::t("Cancel"));
        match dialog.set_buttons(buttons).show() {
            rfd::MessageDialogResult::Custom(chosen) => chosen == close,
            rfd::MessageDialogResult::Ok => true,
            _ => false,
        }
    }

    /// The window's icon (Linux; on Windows the program's own).
    fn icon() -> Option<tao::window::Icon> {
        if cfg!(windows) {
            return None;
        }
        let data = crate::home::Home::find().ok()?.data;
        let path = data.join("editor").join("static").join("icon.png");
        let bytes = std::fs::read(path).ok()?;
        let picture = lp_decode::open(&bytes, lp_pixels::Mode::RGBA).ok()?;
        let (width, height) = picture.size();
        let (width, height) = (u32::try_from(width).ok()?, u32::try_from(height).ok()?);
        tao::window::Icon::from_rgba(picture.as_bytes().to_vec(), width, height).ok()
    }

    pub fn show(settings: &Path, url: &str, listener: TcpListener, secret: String) -> i32 {
        let (width, height, maximized) = load_state(settings);
        let mut event_loop = EventLoopBuilder::<Told>::with_user_event().build();
        let mut builder = WindowBuilder::new()
            .with_title(TITLE)
            .with_inner_size(LogicalSize::new(width, height))
            .with_min_inner_size(LogicalSize::new(MIN_SIZE.0, MIN_SIZE.1))
            .with_maximized(maximized);
        if let Some(icon) = icon() {
            builder = builder.with_window_icon(Some(icon));
        }
        let window = match builder.build(&event_loop) {
            Ok(window) => window,
            Err(why) => {
                say(&format!(
                    "the window did not open: {why}: the app opens in the browser"
                ));
                open_in_browser(url);
                return 0;
            }
        };
        let proxy = event_loop.create_proxy();
        let told = proxy.clone();
        answer(listener, secret, move |asked| {
            let _ = told.send_event(Told::Asked(asked));
        });
        let mut context = WebContext::new(Some(settings.join("window-data")));
        let page = proxy.clone();
        let routed = proxy.clone();
        #[cfg_attr(not(windows), allow(unused_mut))]
        let mut builder = WebViewBuilder::new_with_web_context(&mut context)
            .with_url(url)
            .with_background_color(BACKGROUND)
            .with_initialization_script(BRIDGE_JS)
            .with_clipboard(true)
            .with_ipc_handler(move |request| {
                let Ok(lp_py::Value::Dict(call)) = lp_py::json::loads(request.body()) else {
                    return;
                };
                let first = match call.get("args") {
                    Some(lp_py::Value::List(args)) => args.first().cloned(),
                    _ => None,
                };
                let asked = match (call.get("name").and_then(lp_py::Value::as_str), first) {
                    (Some("set_dirty"), Some(dirty)) => Told::Dirty(dirty.truthy()),
                    (Some("set_title"), Some(lp_py::Value::Str(title))) => Told::Title(title),
                    (Some("close"), _) => Told::Close,
                    _ => return,
                };
                let _ = page.send_event(asked);
            })
            // links to elsewhere open in the browser, the app stays in the window
            .with_navigation_handler(|to| {
                let ours = local_url(&to) || to.starts_with("about:") || to.starts_with("blob:");
                if !ours {
                    open_in_browser(&to);
                }
                ours
            })
            .with_new_window_req_handler(move |to, _| {
                // the app's own pages stay in its window (the browser has not its access
                // token: it would say so); only links to elsewhere open in the browser
                if local_url(&to) {
                    let _ = routed.send_event(Told::Route(to));
                } else {
                    open_in_browser(&to);
                }
                NewWindowResponse::Deny
            })
            // "Export" saves a theme file where the person chooses
            .with_download_started_handler(|_, path| {
                let name = path
                    .file_name()
                    .map(|name| name.to_string_lossy().into_owned());
                let mut dialog = rfd::FileDialog::new().set_title(lp_i18n::t("Save"));
                if let Some(name) = name {
                    dialog = dialog.set_file_name(name);
                }
                match dialog.save_file() {
                    Some(chosen) => {
                        *path = chosen;
                        true
                    }
                    None => false,
                }
            });
        #[cfg(windows)]
        {
            use wry::WebViewBuilderExtWindows;
            // (an elevated program does not read WebView2's variable: the arguments go in here)
            if std::env::var_os(super::WEBVIEW2_ARGUMENTS).is_none() {
                builder = builder.with_additional_browser_args(super::BROWSER_ARGUMENTS);
            }
        }
        #[cfg(target_os = "linux")]
        let made = {
            use tao::platform::unix::WindowExtUnix;
            use wry::WebViewBuilderExtUnix;
            match window.default_vbox() {
                Some(vbox) => builder.build_gtk(vbox),
                None => Err(wry::Error::MessageSender),
            }
        };
        #[cfg(windows)]
        let made = builder.build(&window);
        let webview = match made {
            Ok(webview) => webview,
            Err(why) => {
                say(&format!(
                    "the app cannot show in a window here ({why}): it opens in the browser"
                ));
                open_in_browser(url);
                return 0;
            }
        };
        let (page, token) = access::split_token(url);
        let shown_page = Rc::new(std::cell::RefCell::new((page, token)));
        let dirty = Cell::new(false);
        let mut state = (width, height, maximized);
        let mut minimized = false;
        use tao::platform::run_return::EventLoopExtRunReturn;
        event_loop.run_return(|event, _, flow| {
            *flow = ControlFlow::Wait;
            let mut close = false;
            match event {
                Event::WindowEvent {
                    event: WindowEvent::CloseRequested,
                    ..
                } => close = !dirty.get() || may_close(),
                Event::WindowEvent {
                    event: WindowEvent::Resized(_),
                    ..
                } => {
                    let now_minimized = window.is_minimized();
                    if now_minimized != minimized {
                        // (WebView2 holds less while it is minimized)
                        #[cfg(windows)]
                        {
                            use wry::{MemoryUsageLevel, WebViewExtWindows};
                            let level = match now_minimized {
                                true => MemoryUsageLevel::Low,
                                false => MemoryUsageLevel::Normal,
                            };
                            let _ = webview.set_memory_usage_level(level);
                        }
                        minimized = now_minimized;
                    }
                    state.2 = window.is_maximized();
                    if !state.2 && !minimized {
                        let size = window.inner_size().to_logical::<u32>(window.scale_factor());
                        state.0 = size.width.max(MIN_SIZE.0);
                        state.1 = size.height.max(MIN_SIZE.1);
                    }
                }
                Event::UserEvent(Told::Dirty(now)) => dirty.set(now),
                Event::UserEvent(Told::Route(to)) => {
                    // (a route of the page: shown here; any other page of the app stays as
                    // the page links to it, never loaded anew without its token)
                    if let Some((_, route)) = to.split_once('#') {
                        let hash = lp_py::json::dumps(
                            &lp_py::Value::Str(format!("#{route}")),
                            &lp_py::json::Dumps::default(),
                        );
                        let _ = webview.evaluate_script(&format!("location.hash = {hash}"));
                    }
                }
                Event::UserEvent(Told::Title(title)) => {
                    let title: String = title.split_whitespace().collect::<Vec<_>>().join(" ");
                    let title: String = title.chars().take(200).collect();
                    window.set_title(if title.is_empty() { TITLE } else { &title });
                }
                Event::UserEvent(Told::Close) | Event::UserEvent(Told::Asked(Asked::Close)) => {
                    close = true;
                }
                Event::UserEvent(Told::Asked(Asked::Focus(to))) => {
                    let (page, token) = access::split_token(&to);
                    let mut shown = shown_page.borrow_mut();
                    // a token of its own: another run of the app, whose page is loaded anew
                    let again =
                        matches!((&token, &shown.1), (Some(new), Some(known)) if new != known);
                    let same = super::view_url(&page, None) == super::view_url(&shown.0, None);
                    if same && to.contains('#') && !again {
                        // the same server: the page goes to the route
                        let route = to.split_once('#').map_or("", |(_, route)| route);
                        let hash = lp_py::json::dumps(
                            &lp_py::Value::Str(format!("#{route}")),
                            &lp_py::json::Dumps::default(),
                        );
                        let _ = webview.evaluate_script(&format!("location.hash = {hash}"));
                    } else if page != shown.0 || again {
                        let _ = webview.load_url(&to);
                    }
                    shown.0 = page;
                    if token.is_some() {
                        shown.1 = token;
                    }
                    if window.is_minimized() {
                        window.set_minimized(false);
                    }
                    window.set_visible(true);
                    window.set_focus();
                }
                _ => {}
            }
            if close {
                *flow = ControlFlow::Exit;
            }
        });
        save_state(settings, state);
        0
    }
}

#[cfg(not(any(windows, target_os = "linux")))]
mod native {
    use std::net::TcpListener;
    use std::path::Path;

    pub fn show(_: &Path, url: &str, _: TcpListener, _: String) -> i32 {
        super::open_in_browser(url);
        0
    }
}
