//! Devices: a message when a drive, a USB device or a game controller comes or goes, when a
//! controller's battery runs low, when a drive is nearly full and when an SSD runs hot
//! (Windows) (services/devices.py).
//!
//! Only *changes* are told: what is plugged in when the service starts is taken in silently,
//! or a controller that has been connected for hours would pop up at every start. The panel
//! itself and the hubs are no news, and a USB stick is told once, not as a drive and as a USB
//! device. Four changes at once (the computer woke up, a hub reset) are taken in silently
//! too.
//!
//! Windows tells when a device comes or goes (`CM_Register_Notification`); the service looks
//! then, and a slow poll catches what was missed. Where that does not work it polls every
//! couple of seconds. Everything only reads: see `devices_windows`.

use std::collections::{BTreeSet, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use indexmap::IndexMap;
use lp_i18n::t;
use lp_py::{Dict, Value};
use lp_render::overlays::Toast;

use crate::cooling_text::{s, tf};
use crate::host::ServiceHost;
use crate::manager::{Part, Schema, Service};

/// plugged in or out; a theme switches the kind off with toast.off
const DEVICE: (&str, i64) = ("device", 1);
/// what is wrong (the notices' WARNING): its name, rank and seconds
const WARNING: (&str, i64, f64) = ("warning", 2, 5.0);
/// how long a message stays
const PLUGGED_S: f64 = 4.0;
const UNPLUGGED_S: f64 = 2.5;

// Seconds. Without Windows' notices drives and USB devices are looked at every DRIVE_TICK;
// with them, only to catch what was missed.
const DRIVE_TICK: f64 = 2.0;
const USB_TICK: f64 = 2.0;
const FALLBACK_TICK: f64 = 10.0;
/// between two looks while nothing is going on (the controllers' batteries)
const IDLE_TICK: f64 = 1.0;
/// while a new device settles
const BUSY_TICK: f64 = 0.25;
/// a plugged device brings a dozen notices: gather them, then look once
const WAKE_SETTLE: f64 = 0.15;
/// a wireless controller tells its battery a moment after it connects
const CONTROLLER_WAIT: f64 = 2.0;
/// one that does (or a wired one) is told after this long
const CONTROLLER_MIN_AGE: f64 = 0.5;
/// a drive or a controller may come this long before the USB device it is
const USB_WAIT: f64 = 4.0;
/// a USB device is told at the earliest this long after it appeared...
const USB_MIN_AGE: f64 = 0.6;
/// ...and once Windows has said nothing new for this long
const USB_QUIET: f64 = 1.0;
/// a drive told this close to a USB device is that device
const SAME_MOMENT: f64 = 0.2;
/// this many changes at once: the computer woke up, a hub reset
const MASS: usize = 4;
const STORAGE_TICK: f64 = 60.0;
/// °C below the limit the warning is armed again
const HEAT_HYSTERESIS: f64 = 5.0;
/// the limit of a drive that names none, if the options name none either
const DEFAULT_HEAT_C: f64 = 70.0;
/// a controller's battery type: wired (devices_windows.BATTERY_WIRED)
pub const BATTERY_WIRED: i64 = 1;

/// Vendors of phones, by USB vendor id: such a device gets the phone and the maker's name.
const PHONE_MAKERS: [(&str, &str); 15] = [
    ("05AC", "Apple"),
    ("04E8", "Samsung"),
    ("18D1", "Google"),
    ("2717", "Xiaomi"),
    ("2A70", "OnePlus"),
    ("12D1", "Huawei"),
    ("22B8", "Motorola"),
    ("0FCE", "Sony"),
    ("1004", "LG"),
    ("0BB4", "HTC"),
    ("2D95", "Vivo"),
    ("22D9", "Oppo"),
    ("1EBF", "Fairphone"),
    ("19D2", "ZTE"),
    ("2A45", "Meizu"),
];

/// size_text: a size as drives are sold (powers of 1000): 512 MB, 29.8 GB, 1.0 TB.
pub fn size_text(size: f64) -> String {
    let (tb, gb, mb) = (1000f64.powi(4), 1000f64.powi(3), 1000f64.powi(2));
    if size >= tb {
        format!("{:.1} TB", size / tb)
    } else if size >= 10.0 * gb {
        format!("{:.0} GB", size / gb)
    } else if size >= gb {
        format!("{:.1} GB", size / gb)
    } else {
        format!("{:.0} MB", size / mb)
    }
}

/// panel_ids: `VID:PID` (upper case) of every panel Libre Panel drives, so that the panel's
/// own USB device never makes a message, whichever model is plugged in.
pub fn panel_ids() -> HashSet<String> {
    let ids = lp_devices::models::MODELS
        .iter()
        .flat_map(|model| model.usb_ids);
    ids.map(|(vid, pid)| format!("{vid:04X}:{pid:04X}"))
        .collect()
}

/// parse_ignored: the `usb_ignore` option: `"1234:abcd"` each, as the set of ids in upper
/// case.
pub fn parse_ignored(raw: &[Value], report: &mut dyn FnMut(String)) -> HashSet<String> {
    let usb_id = |text: &str| {
        let bytes = text.as_bytes();
        bytes.len() == 9
            && bytes[4] == b':'
            && (bytes.iter().enumerate()).all(|(at, b)| at == 4 || b.is_ascii_hexdigit())
    };
    let mut found = HashSet::new();
    for entry in raw {
        match entry.as_str().map(lp_py::strip).filter(|id| usb_id(id)) {
            Some(id) => {
                found.insert(id.to_ascii_uppercase());
            }
            None => report(format!(
                "usb_ignore: {} is no USB id, write it as VID:PID, e.g. 1a86:7523",
                entry.repr()
            )),
        }
    }
    found
}

// -- what happened ---------------------------------------------------------------------

/// Something worth a message. `what` is one of `drive-in`, `drive-out`, `controller-in`,
/// `controller-out`, `controller-low`, `usb-in`, `usb-out`, `space` (a drive is nearly full)
/// and `heat` (an SSD is hot); `key` names the device (the same device again replaces its
/// message); `data` has the facts.
#[derive(Clone, Debug, PartialEq)]
pub struct Event {
    pub what: &'static str,
    pub key: String,
    pub data: Dict,
}

fn event(what: &'static str, key: String, first: Vec<(&str, Value)>, more: &Dict) -> Event {
    let mut data: Dict = first.into_iter().map(|(k, v)| (k.to_string(), v)).collect();
    for (key, value) in more {
        data.insert(key.clone(), value.clone());
    }
    Event { what, key, data }
}

/// What is there: the drives by letter, the controllers by slot, the USB devices by their
/// key and the storage by letter (`devices_windows`' readers, or made-up ones).
pub trait Readers: Send {
    fn drives(&self) -> IndexMap<String, Dict>;
    fn storage(&self) -> IndexMap<String, Dict>;
    fn controllers(&self) -> IndexMap<i64, Dict>;
    fn usb(&self, ignore: &HashSet<String>) -> IndexMap<String, Dict>;
}

/// What the watcher looks at, of the service's options.
#[derive(Clone, Debug, PartialEq)]
pub struct Watching {
    pub drives: bool,
    pub usb: bool,
    pub controllers: bool,
    pub storage: bool,
    pub min_free_gb: f64,
    pub min_free_percent: f64,
    /// (as the options have it: a whole number stays one in what is told)
    pub ssd_warn_c: Value,
}

impl Watching {
    pub fn of(options: &Dict) -> Watching {
        let on = |key: &str| options.get(key).is_some_and(Value::truthy);
        let number = |key: &str| options.get(key).and_then(Value::to_f64).unwrap_or(0.0);
        Watching {
            drives: on("drives"),
            usb: on("usb"),
            controllers: on("controllers"),
            storage: on("storage"),
            min_free_gb: number("min_free_gb"),
            min_free_percent: number("min_free_percent"),
            ssd_warn_c: options
                .get("ssd_warn_c")
                .cloned()
                .unwrap_or(Value::Float(0.0)),
        }
    }
}

fn number(entry: &Dict, key: &str) -> Option<f64> {
    match entry.get(key)? {
        Value::Bool(on) => Some(f64::from(u8::from(*on))),
        value @ (Value::Int(_) | Value::Float(_)) => value.to_f64(),
        _ => None,
    }
}

/// Looks at the drives, controllers, USB devices and storage and says what changed.
///
/// `step(now, readers)` is one look and gives the events of it; the service calls it from
/// its thread, a test calls it with the time it likes. Nothing is told for what is there at
/// the first look.
pub struct Watcher {
    options: Watching,
    ignore: HashSet<String>,
    /// Windows tells when devices come and go: the poll is only a net
    pub events: bool,
    /// when Windows last told
    pub last_notice: f64,
    drives: Option<IndexMap<String, Dict>>,
    drives_due: f64,
    controllers: Option<IndexMap<i64, Dict>>,
    /// controllers that were told as connected
    connected: BTreeSet<i64>,
    /// new controllers and since when they stand there
    waiting: IndexMap<i64, f64>,
    /// controllers whose weak battery has been told
    weak: BTreeSet<i64>,
    usb: Option<IndexMap<String, Dict>>,
    usb_due: f64,
    usb_waiting: IndexMap<String, (f64, Dict)>,
    /// when a drive or a controller was last told
    told: f64,
    storage_due: f64,
    /// drives whose lack of space has been told
    full: HashSet<String>,
    /// drives whose heat has been told: "warn" or "crit"
    hot: IndexMap<String, &'static str>,
    out: Vec<Event>,
}

impl Watcher {
    pub fn new(options: Watching, ignore: HashSet<String>) -> Watcher {
        Watcher {
            options,
            ignore,
            events: false,
            last_notice: f64::NEG_INFINITY,
            drives: None,
            drives_due: 0.0,
            controllers: None,
            connected: BTreeSet::new(),
            waiting: IndexMap::new(),
            weak: BTreeSet::new(),
            usb: None,
            usb_due: 0.0,
            usb_waiting: IndexMap::new(),
            told: f64::NEG_INFINITY,
            storage_due: 0.0,
            full: HashSet::new(),
            hot: IndexMap::new(),
            out: Vec::new(),
        }
    }

    /// pause: how long to wait before the next look.
    pub fn pause(&self) -> f64 {
        match self.waiting.is_empty() && self.usb_waiting.is_empty() {
            true => IDLE_TICK,
            false => BUSY_TICK,
        }
    }

    /// hurry: Windows says something changed: look at the drives and the USB devices now.
    pub fn hurry(&mut self) {
        (self.drives_due, self.usb_due) = (0.0, 0.0);
    }

    pub fn step(&mut self, now: f64, readers: &dyn Readers) -> Vec<Event> {
        let o = self.options.clone();
        let tick = |tick: f64, events: bool| now + if events { FALLBACK_TICK } else { tick };
        if !o.drives {
            self.drives = None;
        } else if now >= self.drives_due {
            self.drives_due = tick(DRIVE_TICK, self.events);
            self.look_drives(readers.drives(), now);
        }
        match o.controllers {
            true => self.look_controllers(readers.controllers(), now),
            false => self.controllers = None,
        }
        // USB last: a drive and a controller of this look are told by now, and the second
        // message of a stick (it is a drive too) drops away.
        if o.usb {
            if now >= self.usb_due {
                self.usb_due = tick(USB_TICK, self.events);
                self.look_usb(readers.usb(&self.ignore), now);
            }
            self.deliver_usb(now);
        } else {
            self.usb = None;
            self.usb_waiting.clear();
        }
        if o.storage && now >= self.storage_due {
            self.storage_due = now + STORAGE_TICK;
            self.look_storage(readers.storage());
        }
        std::mem::take(&mut self.out)
    }

    fn tell(&mut self, event: Event, now: Option<f64>) {
        let device = event.what.starts_with("drive") || event.what.starts_with("controller");
        if let Some(now) = now.filter(|_| device) {
            self.told = now;
        }
        self.out.push(event);
    }

    // -- drives ------------------------------------------------------------------------

    fn look_drives(&mut self, found: IndexMap<String, Dict>, now: f64) {
        let Some(before) = self.drives.replace(found.clone()) else {
            return;
        };
        let came: Vec<&String> = found.keys().filter(|l| !before.contains_key(*l)).collect();
        let gone: Vec<&String> = before.keys().filter(|l| !found.contains_key(*l)).collect();
        if came.len() + gone.len() >= MASS {
            return; // the computer woke up: taken in silently
        }
        let told = |what: &'static str, letter: &String, entry: &Dict| {
            let key = format!("drive:{letter}");
            event(what, key, vec![("letter", s(letter))], entry)
        };
        for letter in came {
            self.tell(told("drive-in", letter, &found[letter]), Some(now));
        }
        for letter in gone {
            self.tell(told("drive-out", letter, &before[letter]), Some(now));
        }
    }

    // -- controllers ---------------------------------------------------------------------

    fn look_controllers(&mut self, found: IndexMap<i64, Dict>, now: f64) {
        let level = |c: &Dict| number(c, "level");
        let Some(before) = self.controllers.replace(found.clone()) else {
            self.connected = found.keys().copied().collect();
            // already weak at the start: not an event
            let weak = found
                .iter()
                .filter(|(_, c)| level(c).is_some_and(|l| l <= 1.0));
            self.weak = weak.map(|(slot, _)| *slot).collect();
            return;
        };
        for slot in found.keys() {
            if !before.contains_key(slot) && !self.connected.contains(slot) {
                self.waiting.entry(*slot).or_insert(now);
            }
        }
        let told = |what: &'static str, slot: i64, entry: &Dict| {
            let key = format!("controller:{slot}");
            event(what, key, vec![("slot", Value::int(slot))], entry)
        };
        for (slot, since) in self.waiting.clone() {
            let Some(controller) = found.get(&slot) else {
                self.waiting.shift_remove(&slot); // gone again at once: nothing to tell
                continue;
            };
            if now - since >= self.settle(controller) {
                self.waiting.shift_remove(&slot);
                self.connected.insert(slot);
                self.tell(told("controller-in", slot, controller), Some(now));
            }
        }
        for slot in self.connected.clone() {
            if !found.contains_key(&slot) {
                self.connected.remove(&slot);
                self.weak.remove(&slot);
                self.tell(told("controller-out", slot, &Dict::default()), Some(now));
            }
        }
        for (slot, c) in &found {
            let battery = number(c, "battery");
            let (true, Some(level)) = (
                self.connected.contains(slot) && (battery == Some(2.0) || battery == Some(3.0)),
                level(c),
            ) else {
                continue;
            };
            if level <= 1.0 && !self.weak.contains(slot) {
                self.weak.insert(*slot);
                self.tell(told("controller-low", *slot, c), Some(now));
            } else if level >= 2.0 {
                self.weak.remove(slot);
            }
        }
    }

    /// How long a new controller must stand there before it is told. With Windows' notices
    /// a wired one, or a wireless one that has told its battery, is told after half a
    /// second; a wireless one that has not gets two seconds.
    fn settle(&self, controller: &Dict) -> f64 {
        if !self.events {
            return CONTROLLER_WAIT;
        }
        let battery = number(controller, "battery");
        let charged = battery == Some(2.0) || battery == Some(3.0);
        let ready = battery == Some(BATTERY_WIRED as f64)
            || (charged && number(controller, "level").is_some());
        match ready {
            true => CONTROLLER_MIN_AGE,
            false => CONTROLLER_WAIT,
        }
    }

    // -- USB devices ---------------------------------------------------------------------

    fn look_usb(&mut self, found: IndexMap<String, Dict>, now: f64) {
        let Some(before) = self.usb.replace(found.clone()) else {
            return;
        };
        let came: Vec<&String> = found.keys().filter(|k| !before.contains_key(*k)).collect();
        let gone: Vec<&String> = before.keys().filter(|k| !found.contains_key(*k)).collect();
        if came.len() + gone.len() >= MASS {
            self.usb_waiting.clear(); // the computer woke up, or a hub reset: silently
            return;
        }
        for key in came {
            self.usb_waiting
                .insert(key.clone(), (now, found[key].clone()));
        }
        for key in gone {
            if self.usb_waiting.shift_remove(key).is_some() {
                continue; // gone again before it was told: nothing
            }
            if now - self.told < USB_WAIT {
                continue; // the drive or the controller that it was has said so
            }
            let told = event(
                "usb-out",
                format!("usb:{key}"),
                vec![("id", s(key))],
                &before[key],
            );
            self.tell(told, None);
        }
    }

    /// Tell the USB devices that have settled. A device brings a dozen notices; it is there
    /// for good when Windows has been quiet for a second.
    fn deliver_usb(&mut self, now: f64) {
        for (key, (since, entry)) in self.usb_waiting.clone() {
            let age = now - since;
            let quiet = age >= USB_MIN_AGE && now - self.last_notice >= USB_QUIET;
            if !(age >= USB_WAIT || (self.events && quiet)) {
                continue;
            }
            self.usb_waiting.shift_remove(&key);
            if !self.usb.as_ref().is_some_and(|usb| usb.contains_key(&key)) {
                continue;
            }
            // A stick is a drive too: if the drive was told with it or since, that said it.
            let near = if self.events { SAME_MOMENT } else { USB_TICK };
            if self.told >= since - near {
                continue;
            }
            let told = event(
                "usb-in",
                format!("usb:{key}"),
                vec![("id", s(&key))],
                &entry,
            );
            self.tell(told, None);
        }
    }

    // -- storage -------------------------------------------------------------------------

    fn look_storage(&mut self, found: IndexMap<String, Dict>) {
        let o = self.options.clone();
        self.full.retain(|letter| found.contains_key(letter));
        self.hot.retain(|letter, _| found.contains_key(letter));
        for (letter, drive) in &found {
            let Some(total) = number(drive, "total").filter(|total| *total != 0.0) else {
                continue;
            };
            let free = number(drive, "free").unwrap_or(0.0);
            let (least, share) = (
                o.min_free_gb * 1000f64.powi(3),
                total * o.min_free_percent / 100.0,
            );
            let limit = if share > least { share } else { least };
            if free < limit {
                if self.full.insert(letter.clone()) {
                    let first = vec![
                        ("letter", s(letter)),
                        ("critical", Value::Bool(free < limit / 3.0)),
                    ];
                    self.tell(
                        event("space", format!("storage:{letter}"), first, drive),
                        None,
                    );
                }
            } else if free > limit * 1.2 {
                // so that a value at the limit does not flicker
                self.full.remove(letter);
            }
            let Some(temp) = number(drive, "temp") else {
                continue;
            };
            let named = |key: &str| drive.get(key).filter(|value| value.truthy()).cloned();
            let hot_from = match o.ssd_warn_c.to_f64().is_some_and(|own| own > 0.0) {
                true => o.ssd_warn_c.clone(),
                false => named("warn").unwrap_or(Value::Float(DEFAULT_HEAT_C)),
            };
            let from = hot_from.to_f64().unwrap_or(DEFAULT_HEAT_C);
            let crit = named("crit").and_then(|crit| crit.to_f64());
            if temp >= from {
                let level = match crit.is_some_and(|crit| temp >= crit) {
                    true => "crit",
                    false => "warn",
                };
                let before = self.hot.get(letter).copied();
                if before.is_none() || (level == "crit" && before == Some("warn")) {
                    self.hot.insert(letter.clone(), level);
                    let first = vec![
                        ("letter", s(letter)),
                        ("critical", Value::Bool(level == "crit")),
                        ("limit", hot_from),
                    ];
                    self.tell(
                        event("heat", format!("ssdtemp:{letter}"), first, drive),
                        None,
                    );
                }
            } else if temp < from - HEAT_HYSTERESIS {
                self.hot.shift_remove(letter);
            }
        }
    }
}

// -- the messages ----------------------------------------------------------------------

fn share(part: f64, whole: f64) -> f64 {
    if whole == 0.0 {
        return 0.0;
    }
    let share = part / whole;
    let share = if share < 1.0 { share } else { 1.0 };
    if share > 0.0 {
        share
    } else {
        0.0
    }
}

/// "Wired" or the battery's state; empty where it is not known.
fn battery_text(controller: &Dict) -> String {
    let battery = number(controller, "battery");
    if battery == Some(BATTERY_WIRED as f64) {
        return t("Wired");
    }
    if battery != Some(2.0) && battery != Some(3.0) {
        return String::new();
    }
    match number(controller, "level") {
        Some(0.0) => t("Battery empty"),
        Some(1.0) => t("Battery low"),
        Some(2.0) => t("Battery medium"),
        Some(3.0) => t("Battery full"),
        _ => String::new(),
    }
}

/// describe: the message for an event: what `host.notify` is given.
pub fn describe(event: &Event) -> Toast {
    let d = &event.data;
    let named = |key: &str| d.get(key).filter(|value| value.truthy()).cloned();
    let of = |key: &str| d.get(key).cloned().unwrap_or(Value::None);
    let amount = |key: &str| number(d, key).unwrap_or(0.0);
    let letter = of("letter");
    let label = |text: String| -> Dict {
        let mut payload = Dict::default();
        payload.insert("label".into(), s(text));
        payload
    };
    let base = |text: String, icon: &str, seconds: f64, payload: Dict| Toast {
        icon: Some(icon.to_string()),
        seconds: Some(seconds),
        kind: DEVICE.0.to_string(),
        rank: DEVICE.1,
        payload,
        key: event.key.clone(),
        ..Toast::new(text)
    };
    let warning = |text: String, icon: &str, critical: bool, payload: Dict| Toast {
        icon: Some(icon.to_string()),
        level: if critical { "error" } else { "warning" }.to_string(),
        seconds: Some(WARNING.2),
        kind: WARNING.0.to_string(),
        rank: WARNING.1,
        payload,
        key: event.key.clone(),
        ..Toast::new(text)
    };
    let also = |key: &str| named(key).map_or(String::new(), |name| format!(" · {}", name.str()));
    match event.what {
        "drive-in" | "drive-out" => {
            let drive = named("name").or(named("label")).map_or_else(
                || tf("Drive {letter}:", &[("letter", letter.clone())]),
                |name| name.str(),
            );
            let said = tf(
                "{bus} drive {letter}:",
                &[("bus", of("bus")), ("letter", letter.clone())],
            );
            let mut payload = label(said);
            if event.what == "drive-out" {
                let text = tf("{name} removed", &[("name", s(drive))]);
                return base(text, "stick", UNPLUGGED_S, payload);
            }
            let mut text = drive.clone();
            if amount("total") != 0.0 {
                text = tf(
                    "{name} · {free} free of {total}",
                    &[
                        ("name", s(drive)),
                        ("free", s(size_text(amount("free")))),
                        ("total", s(size_text(amount("total")))),
                    ],
                );
                let used = share(amount("total") - amount("free"), amount("total"));
                payload.insert("progress".into(), Value::Float(used));
            }
            base(text, "stick", PLUGGED_S, payload)
        }
        "controller-in" | "controller-out" | "controller-low" => {
            let number_of = Value::Float(amount("slot") + 1.0);
            let number_of = match of("slot") {
                Value::Int(slot) => Value::Int(slot + 1),
                _ => number_of,
            };
            let mut payload = label(tf("Controller · player {number}", &[("number", number_of)]));
            if event.what == "controller-out" {
                return base(t("Disconnected"), "gamepad", UNPLUGGED_S, payload);
            }
            let battery = number(d, "battery");
            let level = number(d, "level");
            if let (true, Some(level)) = (battery == Some(2.0) || battery == Some(3.0), level) {
                payload.insert("progress".into(), Value::Float(level / 3.0));
            }
            if event.what == "controller-low" {
                let empty = level == Some(0.0);
                let text = t(if empty {
                    "Battery empty"
                } else {
                    "Battery low"
                });
                return Toast {
                    kind: DEVICE.0.to_string(),
                    ..warning(text, "gamepad", empty, payload)
                };
            }
            let battery = battery_text(d);
            let text = match battery.is_empty() {
                true => t("Connected"),
                false => format!("{} · {battery}", t("Connected")),
            };
            base(text, "gamepad", PLUGGED_S, payload)
        }
        "usb-in" | "usb-out" => {
            let vid = of("vid").str();
            let maker = PHONE_MAKERS.iter().find(|(id, _)| *id == vid);
            let said = match maker {
                Some((_, maker)) => tf("Phone · {maker}", &[("maker", s(*maker))]),
                None => t("USB device"),
            };
            let name = named("name")
                .map_or_else(|| format!("{vid}:{}", of("pid").str()), |name| name.str());
            let icon = if maker.is_some() { "phone" } else { "usb" };
            match event.what {
                "usb-out" => {
                    let text = tf("{name} disconnected", &[("name", s(name))]);
                    base(text, icon, UNPLUGGED_S, label(said))
                }
                _ => base(name, icon, PLUGGED_S, label(said)),
            }
        }
        "space" => {
            let text = tf(
                "Only {free} free",
                &[("free", s(size_text(amount("free"))))],
            );
            let said = tf("Drive {letter}:", &[("letter", letter)]);
            let mut payload = label(format!("{said}{}", also("name")));
            let used = share(amount("total") - amount("free"), amount("total"));
            payload.insert("progress".into(), Value::Float(used));
            warning(text, "disk", of("critical").truthy(), payload)
        }
        _ => {
            let text = tf(
                "{temp} °C · limit {limit} °C",
                &[
                    ("temp", s(format!("{:.0}", amount("temp")))),
                    ("limit", s(format!("{:.0}", amount("limit")))),
                ],
            );
            let said = tf("SSD {letter}:", &[("letter", letter)]);
            let mut payload = label(format!("{said}{}", also("name")));
            let crit = named("crit").and_then(|crit| crit.to_f64()).unwrap_or(90.0);
            let heat = share(amount("temp"), crit);
            payload.insert("progress".into(), Value::Float(heat));
            warning(text, "ssd", of("critical").truthy(), payload)
        }
    }
}

// -- the service -----------------------------------------------------------------------

/// Tells when a device came or went (`devices_windows`' notifier).
pub trait Notifier: Send {
    /// Starts telling; false where that does not work.
    fn start(&mut self, on_change: Box<dyn Fn() + Send + Sync>) -> bool;
    fn stop(&mut self);
}

struct Shared {
    host: ServiceHost,
    began: Instant,
    /// when Windows last told, as the bits of the seconds since the start
    noticed: AtomicU64,
    /// (woken, ended)
    state: Mutex<(bool, bool)>,
    wake: Condvar,
}

impl Shared {
    fn clock(&self) -> f64 {
        self.began.elapsed().as_secs_f64()
    }

    /// Waits up to `seconds` for `ready`; whether it came.
    fn wait(&self, seconds: f64, ready: fn(&(bool, bool)) -> bool) -> bool {
        let Ok(state) = self.state.lock() else {
            return true;
        };
        let wait = Duration::from_secs_f64(seconds);
        let waited = (self.wake).wait_timeout_while(state, wait, |state| !ready(state));
        waited.is_ok_and(|(state, _)| ready(&state))
    }

    fn ended(&self) -> bool {
        self.state.lock().map_or(true, |state| state.1)
    }

    fn set(&self, change: fn(&mut (bool, bool))) {
        if let Ok(mut state) = self.state.lock() {
            change(&mut state);
            self.wake.notify_all();
        }
    }

    fn run(
        &self,
        mut watcher: Watcher,
        readers: Box<dyn Readers>,
        mut notifier: Option<Box<dyn Notifier>>,
        noticed: Box<dyn Fn() + Send + Sync>,
    ) {
        watcher.events = notifier
            .as_mut()
            .is_some_and(|notifier| notifier.start(noticed));
        if !watcher.events {
            let said = "devices: no notices from Windows, looking every few seconds";
            self.host.note(said);
        }
        while !self.ended() {
            let woken = self.wait(watcher.pause(), |state| state.0 || state.1);
            if self.ended() {
                break;
            }
            if woken {
                // a dozen notices for one device: gather them
                if self.wait(WAKE_SETTLE, |state| state.1) {
                    break;
                }
                self.set(|state| state.0 = false);
                watcher.hurry();
            }
            watcher.last_notice = f64::from_bits(self.noticed.load(Ordering::Relaxed));
            for event in watcher.step(self.clock(), &*readers) {
                self.host.notify(describe(&event));
            }
        }
        if let Some(notifier) = &mut notifier {
            notifier.stop();
        }
    }
}

/// What the service's thread is started with.
type Parts = (Watcher, Box<dyn Readers>, Option<Box<dyn Notifier>>);

/// Messages for drives, USB devices and controllers that come and go, and for storage.
pub struct Devices {
    shared: Arc<Shared>,
    parts: Option<Parts>,
    thread: Option<JoinHandle<()>>,
}

fn options() -> Schema {
    vec![
        ("drives", "bool", Value::Bool(true)),
        ("usb", "bool", Value::Bool(true)),
        ("controllers", "bool", Value::Bool(true)),
        ("storage", "bool", Value::Bool(true)),
        ("usb_ignore", "list", Value::List(Vec::new())),
        ("min_free_gb", "number", Value::Float(25.0)),
        ("min_free_percent", "number", Value::Float(5.0)),
        ("ssd_warn_c", "number", Value::Float(0.0)),
    ]
}

/// The devices as a kind of service.
pub fn part() -> Part {
    Part {
        options,
        make: |host, options| {
            let mut notes = Vec::new();
            let raw = options
                .get("usb_ignore")
                .and_then(Value::as_list)
                .unwrap_or(&[]);
            let mut ignore = parse_ignored(raw, &mut |note| notes.push(note));
            for note in notes {
                host.note(&note);
            }
            ignore.extend(panel_ids());
            let watcher = Watcher::new(Watching::of(&options), ignore);
            let parts = crate::devices_windows::system()
                .map(|(readers, notifier)| (watcher, readers, Some(notifier)));
            Ok(Box::new(Devices {
                shared: Arc::new(Shared {
                    host,
                    began: Instant::now(),
                    noticed: AtomicU64::new(f64::NEG_INFINITY.to_bits()),
                    state: Mutex::new((false, false)),
                    wake: Condvar::new(),
                }),
                parts,
                thread: None,
            }))
        },
    }
}

impl Service for Devices {
    fn start(&mut self) -> Result<(), String> {
        let Some((watcher, readers, notifier)) = self.parts.take() else {
            self.shared.host.note("devices: this works on Windows only");
            return Ok(());
        };
        let shared = Arc::clone(&self.shared);
        let told = Arc::clone(&self.shared);
        // Windows says a device came or went (in a thread of Windows)
        let noticed = Box::new(move || {
            told.noticed
                .store(told.clock().to_bits(), Ordering::Relaxed);
            told.set(|state| state.0 = true);
        });
        let thread = std::thread::Builder::new().name("devices".into());
        self.thread = Some(
            thread
                .spawn(move || shared.run(watcher, readers, notifier, noticed))
                .map_err(|why| format!("the devices' thread did not start: {why}"))?,
        );
        Ok(())
    }

    fn stop(&mut self) {
        self.shared.set(|state| *state = (true, true));
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
