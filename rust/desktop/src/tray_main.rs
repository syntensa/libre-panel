//! libre-panel-tray: Libre Panel's tray icon (tray.py's Tray), `libre-panel-tray URL`, started
//! by `libre-panel tray` with the app's token in its environment. It shows what the app makes
//! (rust/app's tray.rs): once a second it asks for the icon's state, its tooltip and the menu
//! (`GET /api/tray`), and tells what is chosen (`POST /api/tray`); a click on the icon opens
//! the app, as the menu's first item does. It ends when the app no longer answers.

// (no console window of its own on Windows)
#![cfg_attr(windows, windows_subsystem = "windows")]

#[allow(dead_code)]
#[path = "../../app/src/access.rs"]
mod access;
#[allow(dead_code)]
#[path = "../../app/src/logs.rs"]
mod logs;
#[allow(dead_code)]
#[path = "../../app/src/window.rs"]
mod window;

/// What logs.rs takes from the app's autostart.rs.
mod autostart {
    /// The person's own folder.
    pub fn user_home() -> std::path::PathBuf {
        let named = match cfg!(windows) {
            true => std::env::var_os("USERPROFILE"),
            false => std::env::var_os("HOME"),
        };
        named.map(std::path::PathBuf::from).unwrap_or_default()
    }
}

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

use lp_py::{Dict, Value};
use tao::event::{Event, StartCause};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tray_icon::menu::{
    CheckMenuItem, IsMenuItem, Menu, MenuEvent, MenuItem, PredefinedMenuItem, Submenu,
};
use tray_icon::{Icon, MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};

/// how often the app is asked how things stand (tray.py's POLL_S)
const POLL_S: f64 = 1.0;
/// the app is gone once it did not answer this many times running
const GONE_AFTER: u32 = 3;
/// the icon's size, as tray.py draws it
const ICON_SIZE: u32 = 64;

fn say(message: &str) {
    eprintln!("libre-panel: {message}");
}

/// The way to the app's server.
#[derive(Clone)]
struct Link {
    port: u16,
    token: String,
}

impl Link {
    /// One request; the answer's status and body (None where the server does not answer).
    fn ask(&self, method: &str, path: &str, body: &[u8]) -> Option<(u16, Vec<u8>)> {
        let address = std::net::SocketAddr::from(([127, 0, 0, 1], self.port));
        let mut link = TcpStream::connect_timeout(&address, Duration::from_secs(5)).ok()?;
        link.set_read_timeout(Some(Duration::from_secs(10))).ok()?;
        let head = format!(
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nX-Libre-Panel: {}\r\n\
             Content-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            self.port,
            self.token,
            body.len()
        );
        link.write_all(head.as_bytes()).ok()?;
        link.write_all(body).ok()?;
        let mut answer = Vec::new();
        link.read_to_end(&mut answer).ok()?;
        let end = answer.windows(4).position(|w| w == b"\r\n\r\n")?;
        let status = String::from_utf8_lossy(&answer[..end]);
        let code = status.split_whitespace().nth(1)?.parse().ok()?;
        Some((code, answer[end + 4..].to_vec()))
    }

    fn state(&self) -> Option<Dict> {
        match self.ask("GET", "/api/tray", b"")? {
            (200, body) => match lp_py::json::loads(&String::from_utf8_lossy(&body)).ok()? {
                Value::Dict(state) => Some(state),
                _ => None,
            },
            _ => None,
        }
    }

    fn icon(&self, state: &str) -> Option<Icon> {
        let path = format!("/api/tray/icon?state={state}&size={ICON_SIZE}");
        let (200, png) = self.ask("GET", &path, b"")? else {
            return None;
        };
        let picture = lp_decode::open(&png, lp_pixels::Mode::RGBA).ok()?;
        let (width, height) = picture.size();
        let (width, height) = (u32::try_from(width).ok()?, u32::try_from(height).ok()?);
        Icon::from_rgba(picture.as_bytes().to_vec(), width, height).ok()
    }

    /// An item chosen, told in the background (the menu never waits for the app).
    fn choose(&self, id: String) {
        let link = self.clone();
        std::thread::spawn(move || {
            let mut chosen = Dict::new();
            chosen.insert("id".into(), Value::Str(id));
            let body = lp_py::json::dumps(&Value::Dict(chosen), &lp_py::json::Dumps::default());
            if link.ask("POST", "/api/tray", body.as_bytes()).is_none() {
                say("the app did not hear what was chosen in the tray's menu");
            }
        });
    }
}

/// What the event loop is told.
enum Told {
    /// how things stand now, and the icon for its state where that changed
    Shown(Box<Dict>, Option<Icon>),
    /// an item of the menu chosen
    Chosen(String),
    /// the icon clicked
    Clicked,
    /// the app no longer answers
    Gone,
}

fn text_of(item: &Dict, key: &str) -> String {
    match item.get(key) {
        Some(Value::Str(text)) => text.clone(),
        _ => String::new(),
    }
}

/// The items of a menu as the app lists them, made for the menu: those without an id of their
/// own get one that does nothing.
fn items(list: &[Value], unnamed: &mut usize) -> Vec<Box<dyn IsMenuItem>> {
    let mut made: Vec<Box<dyn IsMenuItem>> = Vec::new();
    for item in list {
        let Value::Dict(item) = item else { continue };
        if matches!(item.get("separator"), Some(Value::Bool(true))) {
            made.push(Box::new(PredefinedMenuItem::separator()));
            continue;
        }
        let text = text_of(item, "text");
        let enabled = !matches!(item.get("enabled"), Some(Value::Bool(false)));
        let id = match item.get("id") {
            Some(Value::Str(id)) => id.clone(),
            _ => {
                *unnamed += 1;
                format!("-{unnamed}")
            }
        };
        if let Some(Value::List(inner)) = item.get("items") {
            let inside = items(inner, unnamed);
            let refs: Vec<&dyn IsMenuItem> = inside.iter().map(|i| i.as_ref()).collect();
            match Submenu::with_id_and_items(id, &text, enabled, &refs) {
                Ok(submenu) => made.push(Box::new(submenu)),
                Err(why) => say(&format!("the tray's menu: {why}")),
            }
            continue;
        }
        match item.get("checked") {
            Some(Value::Bool(on)) => {
                made.push(Box::new(CheckMenuItem::with_id(
                    id, &text, enabled, *on, None,
                )));
            }
            _ => made.push(Box::new(MenuItem::with_id(id, &text, enabled, None))),
        }
    }
    made
}

fn menu(list: &[Value]) -> Menu {
    let menu = Menu::new();
    let made = items(list, &mut 0);
    let refs: Vec<&dyn IsMenuItem> = made.iter().map(|i| i.as_ref()).collect();
    if let Err(why) = menu.append_items(&refs) {
        say(&format!("the tray's menu: {why}"));
    }
    menu
}

/// The menu's default: what a click on the icon does.
fn default_of(state: &Dict) -> Option<String> {
    let Some(Value::List(list)) = state.get("menu") else {
        return None;
    };
    list.iter().find_map(|item| match item {
        Value::Dict(item) if matches!(item.get("default"), Some(Value::Bool(true))) => {
            Some(text_of(item, "id"))
        }
        _ => None,
    })
}

fn main() {
    let Some(url) = std::env::args().nth(1) else {
        eprintln!("usage: libre-panel-tray URL");
        std::process::exit(2);
    };
    let (base, inside) = access::split_token(&url);
    let Some(port) = window::port_of(&base) else {
        eprintln!("libre-panel-tray: not an address of the app: {base}");
        std::process::exit(2);
    };
    let settings = lp_config::dirs::config_dir();
    let token = inside
        .or_else(access::inherited_token)
        .or_else(|| access::read_token(&settings, Some(port)))
        .unwrap_or_default();
    let link = Link { port, token };
    let Some(first) = link.state() else {
        say("no tray icon: the app does not answer");
        std::process::exit(1);
    };

    let mut event_loop = EventLoopBuilder::<Told>::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let told = proxy.clone();
    MenuEvent::set_event_handler(Some(move |chosen: MenuEvent| {
        let _ = told.send_event(Told::Chosen(chosen.id.0));
    }));
    let told = proxy.clone();
    TrayIconEvent::set_event_handler(Some(move |event: TrayIconEvent| {
        if let TrayIconEvent::Click {
            button: MouseButton::Left,
            button_state: MouseButtonState::Up,
            ..
        } = event
        {
            let _ = told.send_event(Told::Clicked);
        }
    }));
    // how things stand, asked once a second
    let asking = link.clone();
    let told = proxy;
    std::thread::spawn(move || {
        let (mut last, mut shown_state, mut missed) = (String::new(), String::new(), 0);
        loop {
            std::thread::sleep(Duration::from_secs_f64(POLL_S));
            let Some(state) = asking.state() else {
                missed += 1;
                if missed >= GONE_AFTER {
                    let _ = told.send_event(Told::Gone);
                    return;
                }
                continue;
            };
            missed = 0;
            let text = lp_py::json::dumps(&Value::Dict(state.clone()), &Default::default());
            if text == last {
                continue;
            }
            last = text;
            let now = text_of(&state, "state");
            let icon = (now != shown_state).then(|| asking.icon(&now)).flatten();
            if icon.is_some() {
                shown_state = now;
            }
            if told.send_event(Told::Shown(Box::new(state), icon)).is_err() {
                return;
            }
        }
    });

    let mut tray: Option<TrayIcon> = None;
    let mut current = first;
    let mut code = 0;
    use tao::platform::run_return::EventLoopExtRunReturn;
    event_loop.run_return(|event, _, flow| {
        *flow = ControlFlow::Wait;
        match event {
            Event::NewEvents(StartCause::Init) => {
                let list = match current.get("menu") {
                    Some(Value::List(list)) => list.clone(),
                    _ => Vec::new(),
                };
                let built = TrayIconBuilder::new()
                    .with_tooltip(text_of(&current, "title"))
                    .with_menu(Box::new(menu(&list)))
                    .with_menu_on_left_click(false);
                let built = match link.icon(&text_of(&current, "state")) {
                    Some(icon) => built.with_icon(icon),
                    None => built,
                };
                match built.build() {
                    Ok(icon) => {
                        tray = Some(icon);
                        say("tray icon ready");
                    }
                    Err(why) => {
                        say(&format!(
                            "no tray icon ({why}); Libre Panel keeps running without one"
                        ));
                        code = 1;
                        *flow = ControlFlow::Exit;
                    }
                }
            }
            Event::UserEvent(Told::Shown(state, icon)) => {
                let Some(tray) = &tray else { return };
                if state.get("menu") != current.get("menu") {
                    if let Some(Value::List(list)) = state.get("menu") {
                        tray.set_menu(Some(Box::new(menu(list))));
                    }
                }
                let title = text_of(&state, "title");
                if title != text_of(&current, "title") {
                    let _ = tray.set_tooltip(Some(title));
                }
                if let Some(icon) = icon {
                    let _ = tray.set_icon(Some(icon));
                }
                current = *state;
            }
            Event::UserEvent(Told::Chosen(id)) => {
                if !id.starts_with('-') {
                    link.choose(id);
                }
            }
            Event::UserEvent(Told::Clicked) => {
                if let Some(id) = default_of(&current) {
                    link.choose(id);
                }
            }
            Event::UserEvent(Told::Gone) => *flow = ControlFlow::Exit,
            _ => {}
        }
    });
    drop(tray);
    std::process::exit(code);
}
