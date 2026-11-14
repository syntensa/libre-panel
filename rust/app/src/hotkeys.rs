//! Keyboard shortcuts for the whole PC (hotkeys.py): a key combination does one thing of the
//! background app, whatever window is in front (pause the panel, its brightness, open the
//! app, the next scene, the timer, the microphone's mute).
//!
//! config.toml's `[hotkeys]` names them, `pause = "Ctrl+Alt+P"`. Windows registers them with
//! the system (`RegisterHotKey`); on Linux with X11 the X server grabs them for Libre Panel.
//! Wayland lets no program take keys for itself: there they are not offered. A combination
//! that another program has taken already is said so, not taken from it.

use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use indexmap::IndexMap;
use lp_config::hotkeys::{parse, Combo, ACTIONS};
use lp_i18n::t;
use lp_py::{Dict, Value};

/// percent a shortcut turns the brightness up or down
pub const BRIGHTNESS_STEP: i64 = 10;
/// what a backend reports for a combination another program has
pub const TAKEN: &str = "taken";
/// how long a start waits for the combinations to be taken
const READY_S: f64 = 3.0;

/// What a pressed shortcut is told to.
pub type Fire = Arc<dyn Fn(&str) + Send + Sync>;
/// The combinations to hold, by their action.
pub type Combos = IndexMap<String, Combo>;

/// support: whether this system lets Libre Panel take keys for itself, and why not.
pub fn support() -> (bool, String) {
    if cfg!(windows) {
        return (true, String::new());
    }
    if cfg!(target_os = "linux") {
        let set = |name: &str| std::env::var_os(name).is_some_and(|value| !value.is_empty());
        let wayland = std::env::var("XDG_SESSION_TYPE").is_ok_and(|kind| kind == "wayland");
        if wayland || (set("WAYLAND_DISPLAY") && !set("DISPLAY")) {
            return (false, t("Wayland lets no program take keys for itself."));
        }
        if !set("DISPLAY") {
            return (false, t("There is no X display."));
        }
        return (true, String::new());
    }
    (false, t("Not on this system."))
}

/// What a backend's thread and its owner share: what is wrong with each combination, and
/// whether they are taken.
#[derive(Default)]
struct Held {
    errors: Mutex<IndexMap<String, String>>,
    ready: (Mutex<bool>, Condvar),
}

impl Held {
    fn say(&self, action: &str, error: &str) {
        if let Ok(mut errors) = self.errors.lock() {
            errors.insert(action.to_string(), error.to_string());
        }
    }

    fn ready(&self) {
        if let Ok(mut ready) = self.ready.0.lock() {
            *ready = true;
            self.ready.1.notify_all();
        }
    }

    fn wait(&self) {
        if let Ok(ready) = self.ready.0.lock() {
            let wait = Duration::from_secs_f64(READY_S);
            let _ready = self
                .ready
                .1
                .wait_timeout_while(ready, wait, |ready| !*ready);
        }
    }

    fn errors(&self) -> IndexMap<String, String> {
        self.errors.lock().map(|e| e.clone()).unwrap_or_default()
    }
}

/// Holds the combinations on a thread of its own and tells the pressed ones.
pub trait Backend: Send {
    /// What is wrong with each combination ("": nothing; TAKEN: another program has it).
    fn errors(&self) -> IndexMap<String, String>;
    fn stop(&mut self);
}

/// How a backend is made of the combinations and of what to tell.
pub type Make = Box<dyn Fn(Combos, Fire) -> Box<dyn Backend> + Send + Sync>;

// -- Windows ------------------------------------------------------------------------------------

/// win_key: the virtual-key code of a key.
#[cfg(any(windows, test))]
pub fn win_key(key: &str) -> u32 {
    const KEYS: [(&str, u32); 13] = [
        ("Space", 0x20),
        ("PageUp", 0x21),
        ("PageDown", 0x22),
        ("End", 0x23),
        ("Home", 0x24),
        ("Left", 0x25),
        ("Up", 0x26),
        ("Right", 0x27),
        ("Down", 0x28),
        ("Insert", 0x2D),
        ("Delete", 0x2E),
        ("Pause", 0x13),
        ("ScrollLock", 0x91),
    ];
    if let Some((_, code)) = KEYS.iter().find(|(name, _)| *name == key) {
        return *code;
    }
    match key.strip_prefix('F').and_then(|n| n.parse::<u32>().ok()) {
        // (F1 is 0x70)
        Some(number) if key.len() > 1 => 0x6F + number,
        // A to Z and 0 to 9 are their own codes
        _ => key.chars().next().map_or(0, u32::from),
    }
}

/// win_mods: the modifiers of a combination as RegisterHotKey takes them, without repeats
/// while the keys are held.
#[cfg(any(windows, test))]
pub fn win_mods(combo: &Combo) -> u32 {
    const MOD_NOREPEAT: u32 = 0x4000;
    let bit = |name: &&str| match *name {
        "Alt" => 0x1,
        "Ctrl" => 0x2,
        "Shift" => 0x4,
        "Win" => 0x8,
        _ => 0,
    };
    combo.mods.iter().map(bit).sum::<u32>() | MOD_NOREPEAT
}

#[cfg(windows)]
pub mod windows {
    use std::sync::atomic::{AtomicU32, Ordering};
    use std::sync::Arc;
    use std::thread::JoinHandle;

    use indexmap::IndexMap;
    use windows_sys::Win32::Foundation::GetLastError;
    use windows_sys::Win32::System::Threading::GetCurrentThreadId;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{RegisterHotKey, UnregisterHotKey};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetMessageW, PeekMessageW, PostThreadMessageW, MSG, WM_HOTKEY, WM_QUIT,
    };

    use super::{win_key, win_mods, Backend, Combos, Fire, Held, TAKEN};

    const ERROR_HOTKEY_ALREADY_REGISTERED: u32 = 1409;

    pub struct Windows {
        held: Arc<Held>,
        thread_id: Arc<AtomicU32>,
        /// (the tests' own: press() posts as Windows does)
        #[cfg_attr(not(test), allow(dead_code))]
        actions: Vec<String>,
        thread: Option<JoinHandle<()>>,
    }

    fn run(combos: &Combos, fire: &Fire, held: &Held, thread_id: &AtomicU32) {
        // SAFETY: the calls of a thread's own message queue, with a MSG of this thread's.
        unsafe {
            thread_id.store(GetCurrentThreadId(), Ordering::Relaxed);
            let mut msg: MSG = std::mem::zeroed();
            // (the thread's queue, for stop())
            PeekMessageW(&mut msg, std::ptr::null_mut(), 0, 0, 0);
            let mut ids: IndexMap<usize, &str> = IndexMap::new();
            for (number, (action, combo)) in combos.iter().enumerate() {
                let number = number + 1;
                let taken = RegisterHotKey(
                    std::ptr::null_mut(),
                    number as i32,
                    win_mods(combo),
                    win_key(&combo.key),
                );
                if taken != 0 {
                    ids.insert(number, action);
                    held.say(action, "");
                } else {
                    let error = match GetLastError() {
                        ERROR_HOTKEY_ALREADY_REGISTERED => TAKEN.to_string(),
                        code => format!("Windows error {code}"),
                    };
                    held.say(action, &error);
                }
            }
            held.ready();
            while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {
                if msg.message == WM_HOTKEY {
                    if let Some(action) = ids.get(&msg.wParam) {
                        fire(action);
                    }
                }
            }
            for number in ids.keys() {
                UnregisterHotKey(std::ptr::null_mut(), *number as i32);
            }
        }
    }

    impl Windows {
        pub fn start(combos: Combos, fire: Fire) -> Windows {
            let (held, thread_id) = (Arc::new(Held::default()), Arc::new(AtomicU32::new(0)));
            let actions = combos.keys().cloned().collect();
            let (shared, id) = (Arc::clone(&held), Arc::clone(&thread_id));
            let thread = std::thread::Builder::new().name("hotkeys".into());
            let thread = thread.spawn(move || run(&combos, &fire, &shared, &id));
            held.wait();
            Windows {
                held,
                thread_id,
                actions,
                thread: thread.ok(),
            }
        }

        fn post(&self, message: u32, wparam: usize) {
            let thread_id = self.thread_id.load(Ordering::Relaxed);
            if thread_id != 0 {
                // SAFETY: a message for the thread's queue; no pointers in it.
                unsafe {
                    PostThreadMessageW(thread_id, message, wparam, 0);
                }
            }
        }

        /// As Windows says it when the keys of `action` are pressed (the tests').
        #[cfg(test)]
        pub fn press(&self, action: &str) {
            if let Some(at) = self.actions.iter().position(|name| name == action) {
                self.post(WM_HOTKEY, at + 1);
            }
        }
    }

    impl Backend for Windows {
        fn errors(&self) -> IndexMap<String, String> {
            self.held.errors()
        }

        fn stop(&mut self) {
            self.post(WM_QUIT, 0);
            if self.thread_id.load(Ordering::Relaxed) != 0 {
                if let Some(thread) = self.thread.take() {
                    let _ = thread.join();
                }
            }
        }
    }
}

// -- X11 ----------------------------------------------------------------------------------------

/// x11_keysym: the X server's symbol of a key (XK.string_to_keysym of its name there).
#[cfg(any(test, all(unix, not(target_os = "macos"))))]
pub fn x11_keysym(key: &str) -> Option<u32> {
    const NAMED: [(&str, u32); 13] = [
        ("Space", 0x20),
        ("Home", 0xFF50),
        ("Left", 0xFF51),
        ("Up", 0xFF52),
        ("Right", 0xFF53),
        ("Down", 0xFF54),
        ("PageUp", 0xFF55),
        ("PageDown", 0xFF56),
        ("End", 0xFF57),
        ("Insert", 0xFF63),
        ("Delete", 0xFFFF),
        ("Pause", 0xFF13),
        ("ScrollLock", 0xFF14),
    ];
    if let Some((_, symbol)) = NAMED.iter().find(|(name, _)| *name == key) {
        return Some(*symbol);
    }
    let mut letters = key.chars();
    match (letters.next(), letters.next()) {
        // (a letter's symbol is the small letter's)
        (Some(one), None) if one.is_ascii_alphanumeric() => {
            Some(u32::from(one.to_ascii_lowercase()))
        }
        (Some('F'), Some(_)) => {
            let number = key[1..]
                .parse::<u32>()
                .ok()
                .filter(|n| (1..=24).contains(n))?;
            Some(0xFFBE + number - 1)
        }
        _ => None,
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
pub mod x11 {
    use std::os::fd::AsRawFd;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::thread::JoinHandle;

    use indexmap::IndexMap;
    use lp_py::Value;
    use x11rb::connection::Connection;
    use x11rb::protocol::xproto::{ConnectionExt, GrabMode, ModMask};
    use x11rb::protocol::{ErrorKind, Event};
    use x11rb::rust_connection::{ReplyError, RustConnection};
    use x11rb::wrapper::ConnectionExt as _;

    use super::{x11_keysym, Backend, Combos, Fire, Held, TAKEN};

    const SHIFT: u16 = 1;
    const LOCK: u16 = 2;
    const CONTROL: u16 = 4;
    const MOD1: u16 = 8;
    const MOD2: u16 = 16;
    const MOD4: u16 = 64;
    const RELEVANT: u16 = SHIFT | CONTROL | MOD1 | MOD4;
    /// Caps and Num Lock: a combination is taken with each of them on and off
    const LOCKS: [u16; 4] = [0, LOCK, MOD2, LOCK | MOD2];

    /// keysym_to_keycode as python-xlib has it: the key that has the symbol in its earliest
    /// column, the lowest of them; None where the keyboard has none.
    pub fn keycode_of(conn: &RustConnection, symbol: u32) -> Option<u8> {
        let (first, last) = (conn.setup().min_keycode, conn.setup().max_keycode);
        let count = last.checked_sub(first)?.checked_add(1)?;
        let mapping = conn.get_keyboard_mapping(first, count).ok()?.reply().ok()?;
        let width = usize::from(mapping.keysyms_per_keycode).max(1);
        let keys = mapping.keysyms.chunks(width).enumerate();
        let found = keys.filter_map(|(key, symbols)| {
            let column = symbols.iter().position(|s| *s == symbol && *s != 0)?;
            Some((column, key))
        });
        let (_, key) = found.min()?;
        u8::try_from(key).ok()?.checked_add(first)
    }

    pub struct X11 {
        held: Arc<Held>,
        stopping: Arc<AtomicBool>,
        thread: Option<JoinHandle<()>>,
    }

    fn mask_of(mods: &[&str]) -> u16 {
        let bit = |name: &&str| match *name {
            "Shift" => SHIFT,
            "Ctrl" => CONTROL,
            "Alt" => MOD1,
            "Win" => MOD4,
            _ => 0,
        };
        mods.iter().map(bit).sum()
    }

    fn run(combos: &Combos, fire: &Fire, held: &Held, stopping: &AtomicBool) {
        let (conn, screen) = match RustConnection::connect(None) {
            Ok(connected) => connected,
            // no X server to talk to (a display that went away)
            Err(why) => {
                for action in combos.keys() {
                    held.say(action, &why.to_string());
                }
                held.ready();
                return;
            }
        };
        let root = conn.setup().roots[screen].root;
        let mut grabs: IndexMap<(u8, u16), &str> = IndexMap::new();
        for (action, combo) in combos {
            let key = x11_keysym(&combo.key).and_then(|symbol| keycode_of(&conn, symbol));
            let Some(key) = key else {
                let key = [("key", Value::Str(combo.key.clone()))];
                let said = lp_i18n::t_format("This keyboard has no {key}.", &key);
                held.say(action, &said.unwrap_or_default());
                continue;
            };
            let mask = mask_of(&combo.mods);
            let asked: Vec<_> = (LOCKS.iter())
                .filter_map(|lock| {
                    let mods = ModMask::from(mask | lock);
                    (conn.grab_key(true, root, mods, key, GrabMode::ASYNC, GrabMode::ASYNC)).ok()
                })
                .collect();
            let refused = asked.into_iter().fold(false, |refused, cookie| {
                let access = matches!(
                    cookie.check(),
                    Err(ReplyError::X11Error(error)) if error.error_kind == ErrorKind::Access
                );
                refused || access
            });
            if refused {
                for lock in LOCKS {
                    let _ = conn.ungrab_key(key, root, ModMask::from(mask | lock));
                }
                held.say(action, TAKEN);
            } else {
                grabs.insert((key, mask), action);
                held.say(action, "");
            }
        }
        let _ = conn.sync();
        held.ready();
        while !stopping.load(Ordering::Relaxed) {
            let mut waits = libc::pollfd {
                fd: conn.stream().as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            // SAFETY: one pollfd of this thread's, for a fifth of a second at most.
            unsafe {
                libc::poll(&mut waits, 1, 200);
            }
            loop {
                match conn.poll_for_event() {
                    Ok(Some(Event::KeyPress(press))) => {
                        let state = u16::from(press.state) & RELEVANT;
                        if let Some(action) = grabs.get(&(press.detail, state)) {
                            fire(action);
                        }
                    }
                    Ok(Some(_)) => {}
                    Ok(None) => break,
                    // (the display went away: nothing more comes)
                    Err(_) => return,
                }
            }
        }
        for (key, mask) in grabs.keys() {
            for lock in LOCKS {
                let _ = conn.ungrab_key(*key, root, ModMask::from(mask | lock));
            }
        }
        let _ = conn.sync();
    }

    impl X11 {
        pub fn start(combos: Combos, fire: Fire) -> X11 {
            let (held, stopping) = (Arc::new(Held::default()), Arc::new(AtomicBool::new(false)));
            let (shared, stop) = (Arc::clone(&held), Arc::clone(&stopping));
            let thread = std::thread::Builder::new().name("hotkeys".into());
            let thread = thread.spawn(move || run(&combos, &fire, &shared, &stop));
            held.wait();
            X11 {
                held,
                stopping,
                thread: thread.ok(),
            }
        }
    }

    impl Backend for X11 {
        fn errors(&self) -> IndexMap<String, String> {
            self.held.errors()
        }

        fn stop(&mut self) {
            self.stopping.store(true, Ordering::Relaxed);
            if let Some(thread) = self.thread.take() {
                let _ = thread.join();
            }
        }
    }
}

/// The system's own way to hold combinations.
pub fn backend() -> Make {
    #[cfg(windows)]
    return Box::new(|combos, fire| Box::new(windows::Windows::start(combos, fire)));
    #[cfg(all(unix, not(target_os = "macos")))]
    return Box::new(|combos, fire| Box::new(x11::X11::start(combos, fire)));
    #[cfg(not(any(windows, all(unix, not(target_os = "macos")))))]
    return Box::new(|_, _| Box::new(Nothing));
}

/// Where no combination can be held.
#[cfg(not(any(windows, all(unix, not(target_os = "macos")))))]
struct Nothing;

#[cfg(not(any(windows, all(unix, not(target_os = "macos")))))]
impl Backend for Nothing {
    fn errors(&self) -> IndexMap<String, String> {
        IndexMap::new()
    }

    fn stop(&mut self) {}
}

// -- the shortcuts ------------------------------------------------------------------------------

#[derive(Default)]
struct Now {
    wanted: Combos,
    /// what config.toml names that is no combination
    wrong: IndexMap<String, String>,
    current: Option<Box<dyn Backend>>,
}

/// The shortcuts the background app holds: `apply` the ones config.toml names, and `act` is
/// called (on a thread of its own) when one is pressed.
pub struct Hotkeys {
    fire: Fire,
    make: Make,
    supported: fn() -> (bool, String),
    now: Mutex<Now>,
}

impl Hotkeys {
    pub fn new(act: Fire, make: Make, supported: fn() -> (bool, String)) -> Hotkeys {
        let fire: Fire = Arc::new(move |action: &str| {
            eprintln!("libre-panel: shortcut: {action}");
            let (act, action) = (Arc::clone(&act), action.to_string());
            let thread = std::thread::Builder::new().name("hotkey-action".into());
            let _ = thread.spawn(move || act(&action));
        });
        Hotkeys {
            fire,
            make,
            supported,
            now: Mutex::new(Now::default()),
        }
    }

    /// As when the keys of `action` are pressed.
    pub fn press(&self, action: &str) {
        (self.fire)(action);
    }

    /// apply: hold the combinations config.toml's `[hotkeys]` names (the same as now:
    /// nothing).
    pub fn apply(&self, named: &IndexMap<String, String>) {
        let (mut combos, mut wrong) = (Combos::new(), IndexMap::new());
        for (action, text) in named {
            if !ACTIONS.contains(&action.as_str()) || text.is_empty() {
                continue;
            }
            match parse(text) {
                Ok(combo) => drop(combos.insert(action.clone(), combo)),
                Err(why) => drop(wrong.insert(action.clone(), why.msg)),
            }
        }
        let Ok(mut now) = self.now.lock() else {
            return;
        };
        now.wrong = wrong;
        if combos == now.wanted && (now.current.is_some() || combos.is_empty()) {
            return;
        }
        if let Some(mut current) = now.current.take() {
            current.stop();
        }
        now.wanted = combos.clone();
        if !combos.is_empty() && (self.supported)().0 {
            now.current = Some((self.make)(combos, Arc::clone(&self.fire)));
        }
    }

    /// state: for the app: whether shortcuts can be had here, and each one with what is
    /// wrong.
    pub fn state(&self) -> Dict {
        let (supported, why) = (self.supported)();
        let mut keys = Dict::default();
        if let Ok(now) = self.now.lock() {
            let errors = now.current.as_ref().map(|c| c.errors()).unwrap_or_default();
            let entry = |combo: String, error: String| {
                let mut entry = Dict::default();
                entry.insert("combo".into(), Value::Str(combo));
                entry.insert("error".into(), Value::Str(error));
                Value::Dict(entry)
            };
            for (action, combo) in &now.wanted {
                let error = errors.get(action).cloned().unwrap_or_default();
                keys.insert(action.clone(), entry(combo.to_string(), error));
            }
            for (action, text) in &now.wrong {
                keys.insert(action.clone(), entry(String::new(), text.clone()));
            }
        }
        let mut state = Dict::default();
        state.insert("supported".into(), Value::Bool(supported));
        state.insert("why".into(), Value::Str(why));
        state.insert("keys".into(), Value::Dict(keys));
        state
    }

    pub fn close(&self) {
        if let Ok(mut now) = self.now.lock() {
            if let Some(mut current) = now.current.take() {
                current.stop();
            }
            now.wanted = Combos::new();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn named(pairs: &[(&str, &str)]) -> IndexMap<String, String> {
        let pairs = pairs.iter();
        pairs
            .map(|(action, keys)| ((*action).to_string(), (*keys).to_string()))
            .collect()
    }

    fn wait_for(done: impl Fn() -> bool, seconds: f64) -> bool {
        let until = std::time::Instant::now() + Duration::from_secs_f64(seconds);
        while std::time::Instant::now() < until {
            if done() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        done()
    }

    struct Fake(Arc<AtomicUsize>);

    impl Backend for Fake {
        fn errors(&self) -> IndexMap<String, String> {
            named(&[("open", TAKEN)])
        }

        fn stop(&mut self) {
            self.0.fetch_add(1, Ordering::Relaxed);
        }
    }

    #[test]
    fn the_combinations_are_held_again_only_when_they_change() {
        let (made, stopped) = (Arc::new(AtomicUsize::new(0)), Arc::new(AtomicUsize::new(0)));
        let (makes, stops) = (Arc::clone(&made), Arc::clone(&stopped));
        let make: Make = Box::new(move |_, _| {
            makes.fetch_add(1, Ordering::Relaxed);
            Box::new(Fake(Arc::clone(&stops)))
        });
        let done = Arc::new(Mutex::new(Vec::new()));
        let does = Arc::clone(&done);
        let act: Fire = Arc::new(move |action: &str| does.lock().unwrap().push(action.to_string()));
        let keys = Hotkeys::new(act, make, || (true, String::new()));
        let first = named(&[
            ("pause", "ctrl+alt+p"),
            ("open", "Ctrl+Alt+O"),
            ("timer", "p"),
            ("nothing", "Ctrl+Alt+X"),
            ("mic_mute", ""),
        ]);
        keys.apply(&first);
        keys.apply(&first);
        assert_eq!(
            (
                made.load(Ordering::Relaxed),
                stopped.load(Ordering::Relaxed)
            ),
            (1, 0)
        );
        let state = keys.state();
        let Some(Value::Dict(told)) = state.get("keys") else {
            panic!("no keys");
        };
        let entry = |action: &str, field: &str| match &told[action] {
            Value::Dict(entry) => entry[field].str(),
            _ => String::new(),
        };
        assert_eq!(told.len(), 3);
        assert_eq!(
            (entry("pause", "combo"), entry("pause", "error")),
            ("Ctrl+Alt+P".into(), "".into())
        );
        assert_eq!(entry("open", "error"), TAKEN);
        assert_eq!(entry("timer", "combo"), "");
        assert!(entry("timer", "error").contains("Ctrl, Alt"));
        keys.press("pause");
        assert!(wait_for(|| *done.lock().unwrap() == ["pause"], 5.0));
        keys.apply(&named(&[("pause", "Ctrl+Alt+Q")]));
        assert_eq!(
            (
                made.load(Ordering::Relaxed),
                stopped.load(Ordering::Relaxed)
            ),
            (2, 1)
        );
        keys.apply(&named(&[]));
        assert_eq!(
            (
                made.load(Ordering::Relaxed),
                stopped.load(Ordering::Relaxed)
            ),
            (2, 2)
        );
        keys.close();
        assert_eq!(stopped.load(Ordering::Relaxed), 2);
    }

    #[test]
    fn without_support_nothing_is_held_and_it_says_why() {
        let make: Make = Box::new(|_, _| panic!("nothing is to be held"));
        let act: Fire = Arc::new(|_: &str| {});
        let keys = Hotkeys::new(act, make, || (false, "Not here.".into()));
        keys.apply(&named(&[("pause", "Ctrl+Alt+P")]));
        let state = keys.state();
        assert_eq!(state["supported"], Value::Bool(false));
        assert_eq!(state["why"].str(), "Not here.");
        assert!(matches!(&state["keys"], Value::Dict(keys) if keys.len() == 1));
    }

    #[test]
    fn the_keys_have_their_codes() {
        let code = |keys: &str| {
            let combo = parse(keys).unwrap();
            (
                win_mods(&combo),
                win_key(&combo.key),
                x11_keysym(&combo.key),
            )
        };
        assert_eq!(code("Ctrl+Alt+P"), (0x4003, 0x50, Some(0x70)));
        assert_eq!(code("win+shift+7"), (0x400C, 0x37, Some(0x37)));
        assert_eq!(code("F13"), (0x4000, 0x7C, Some(0xFFCA)));
        assert_eq!(code("ctrl+f1"), (0x4002, 0x70, Some(0xFFBE)));
        assert_eq!(code("Alt+PageDown"), (0x4001, 0x22, Some(0xFF56)));
        assert_eq!(code("ScrollLock"), (0x4000, 0x91, Some(0xFF14)));
        assert_eq!(code("Ctrl+F"), (0x4002, 0x46, Some(0x66)));
    }

    /// Windows: the system registers it, and its message comes through the loop.
    #[cfg(windows)]
    #[test]
    fn on_windows_a_registered_shortcut_reaches_the_action() {
        let done = Arc::new(Mutex::new(Vec::new()));
        let does = Arc::clone(&done);
        let fire: Fire =
            Arc::new(move |action: &str| does.lock().unwrap().push(action.to_string()));
        let mut combos = Combos::new();
        combos.insert("open".into(), parse("Ctrl+Alt+Shift+F23").unwrap());
        let mut held = windows::Windows::start(combos, fire);
        let error = held.errors().get("open").cloned();
        if error.as_deref() != Some("") {
            eprintln!("this desktop takes no shortcuts: {error:?}");
            held.stop();
            return;
        }
        held.press("open"); // (as Windows sends it when the keys are pressed)
        assert!(wait_for(|| *done.lock().unwrap() == ["open"], 5.0));
        held.stop();
    }

    /// X11 (an Xvfb of the test's own): a pressed shortcut arrives, one another program has
    /// is said so, and what was held is let go.
    #[cfg(target_os = "linux")]
    #[test]
    fn on_x11_a_pressed_shortcut_arrives_and_one_taken_is_said() {
        use x11rb::connection::Connection;
        use x11rb::protocol::xproto::{ConnectionExt, GrabMode, ModMask};
        use x11rb::protocol::xtest::ConnectionExt as _;
        use x11rb::rust_connection::RustConnection;
        use x11rb::wrapper::ConnectionExt as _;

        let number =
            (140..200).find(|n| !std::path::Path::new(&format!("/tmp/.X{n}-lock")).exists());
        let Some(number) = number else {
            return;
        };
        let name = format!(":{number}");
        // -noreset: an X server starts over when its last client goes, and one that connects
        // in that moment is turned away
        let server = std::process::Command::new("Xvfb")
            .args([
                &name,
                "-screen",
                "0",
                "320x240x24",
                "-nolisten",
                "tcp",
                "-noreset",
            ])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();
        let Ok(mut server) = server else {
            eprintln!("needs Xvfb");
            return;
        };
        let socket = format!("/tmp/.X11-unix/X{number}");
        if !wait_for(|| std::path::Path::new(&socket).exists(), 10.0) {
            let _ = server.kill();
            let _ = server.wait();
            eprintln!("Xvfb did not start");
            return;
        }
        // (the backend connects to DISPLAY; no other test of this program reads it)
        std::env::set_var("DISPLAY", &name);
        let connect = || RustConnection::connect(Some(&name)).unwrap();
        let code = |conn: &RustConnection, symbol: u32| x11::keycode_of(conn, symbol).unwrap();
        let control = ModMask::CONTROL | ModMask::M1;

        // another program holds Ctrl+Alt+O already
        let (other, screen) = connect();
        let root = other.setup().roots[screen].root;
        let o = code(&other, 0x6F);
        other
            .grab_key(true, root, control, o, GrabMode::ASYNC, GrabMode::ASYNC)
            .unwrap()
            .check()
            .unwrap();

        let done = Arc::new(Mutex::new(Vec::new()));
        let does = Arc::clone(&done);
        let fire: Fire =
            Arc::new(move |action: &str| does.lock().unwrap().push(action.to_string()));
        let mut combos = Combos::new();
        for (action, keys) in [
            ("pause", "Ctrl+Alt+P"),
            ("open", "Ctrl+Alt+O"),
            ("brightness_up", "Pause"),
            ("brightness_down", "F13"),
        ] {
            combos.insert(action.into(), parse(keys).unwrap());
        }
        let mut held = x11::X11::start(combos, fire);
        let errors = held.errors();
        assert_eq!(errors["pause"], "");
        assert_eq!(errors["open"], TAKEN);
        assert_eq!(errors["brightness_down"], "This keyboard has no F13."); // (Xvfb's)

        // the keys pressed one after the other and let go the other way round (XTest)
        let press = |symbols: &[u32]| {
            let (conn, screen) = connect();
            let root = conn.setup().roots[screen].root;
            let codes: Vec<u8> = symbols.iter().map(|symbol| code(&conn, *symbol)).collect();
            for key in &codes {
                conn.xtest_fake_input(2, *key, 0, root, 0, 0, 0).unwrap();
            }
            for key in codes.iter().rev() {
                conn.xtest_fake_input(3, *key, 0, root, 0, 0, 0).unwrap();
            }
            conn.sync().unwrap();
        };
        let (control_l, alt_l, p, pause) = (0xFFE3, 0xFFE9, 0x70, 0xFF13);
        press(&[control_l, alt_l, p]);
        assert!(wait_for(|| *done.lock().unwrap() == ["pause"], 5.0));
        press(&[p]); // without the modifiers it is typing, not the shortcut
        press(&[pause]);
        assert!(wait_for(
            || *done.lock().unwrap() == ["pause", "brightness_up"],
            5.0
        ));
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(*done.lock().unwrap(), ["pause", "brightness_up"]);
        held.stop();
        drop(other);

        // let go: another program may have them now
        let (again, screen) = connect();
        let root = again.setup().roots[screen].root;
        let p = code(&again, p);
        again
            .grab_key(true, root, control, p, GrabMode::ASYNC, GrabMode::ASYNC)
            .unwrap()
            .check()
            .unwrap();
        drop(again);
        let _ = server.kill();
        let _ = server.wait();
    }
}
