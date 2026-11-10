//! Services and what they may do: the host between them and the main loop (plugins/host.py).
//!
//! A service runs in the background app and in `libre-panel run`. It talks to the panel only
//! through its [`ServiceHost`]: publish readings, states and images, show another theme for a
//! while, switch the mode (with its own frame rate), show a toast, and hear about events
//! (among them every new sample of the readings).

use std::collections::{BTreeSet, VecDeque};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;

use indexmap::IndexMap;
use lp_pixels::Image;
use lp_py::{Dict, Value};
use lp_render::overlays::Toast;
use lp_render::{Reading, Snapshot};

/// The events a service can hear about.
pub const EVENTS: [&str; 9] = [
    "panel-connected",
    "panel-lost",
    // back after the driver restarted it to clear a hung decoder
    "panel-restarted",
    "theme-changed",
    "mode-changed",
    // a new snapshot of the readings, about every refresh_ms
    "sample",
    // between two samples, readings that follow on every frame changed
    "fresh",
    // a keyboard shortcut of [hotkeys] was pressed, or its button in the app
    "shortcut",
    "quit",
];
/// the most toasts kept for the main loop to take
pub const MAX_TOASTS: usize = 20;

/// What happened, as its listeners are told.
#[derive(Clone, Debug)]
pub enum Event {
    PanelConnected,
    PanelLost,
    PanelRestarted,
    ThemeChanged {
        theme: String,
    },
    ModeChanged {
        mode: Option<String>,
    },
    Sample {
        snapshot: Arc<Snapshot>,
    },
    Fresh {
        snapshot: Arc<Snapshot>,
        keys: BTreeSet<String>,
    },
    Shortcut {
        action: String,
    },
    Quit,
}

impl Event {
    pub fn name(&self) -> &'static str {
        match self {
            Event::PanelConnected => "panel-connected",
            Event::PanelLost => "panel-lost",
            Event::PanelRestarted => "panel-restarted",
            Event::ThemeChanged { .. } => "theme-changed",
            Event::ModeChanged { .. } => "mode-changed",
            Event::Sample { .. } => "sample",
            Event::Fresh { .. } => "fresh",
            Event::Shortcut { .. } => "shortcut",
            Event::Quit => "quit",
        }
    }

    /// Delivered in a thread of their own, only the newest of each.
    fn newest(&self) -> bool {
        matches!(self, Event::Sample { .. } | Event::Fresh { .. })
    }
}

/// A transition: a name and, for one that takes some, its parameters.
#[derive(Clone, Debug, PartialEq)]
pub struct Transition {
    pub name: String,
    pub params: Option<Dict>,
}

impl Transition {
    pub fn named(name: &str) -> Transition {
        Transition {
            name: name.to_string(),
            params: None,
        }
    }
}

/// Which modes a theme request counts in.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Scope {
    /// every mode
    Any,
    /// one mode; None: only while no mode is on (an autopilot)
    Mode(Option<String>),
}

/// the message waits for the panel: it is not connected or paused
pub const NO_PANEL: &str = "no-panel";
/// the theme switches its kind off (`toast.off`)
pub const OFF_IN_THEME: &str = "theme";
/// the screen shows its kind itself
pub const LEFT_OUT: &str = "screen";

/// What `notify` says about a message: whether it will show, and if not, why. The answer is
/// that of the moment.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Delivery {
    pub shown: bool,
    pub reason: String,
}

impl Delivery {
    pub fn shown() -> Delivery {
        Delivery {
            shown: true,
            reason: String::new(),
        }
    }

    pub fn held(reason: &str) -> Delivery {
        Delivery {
            shown: false,
            reason: reason.to_string(),
        }
    }
}

type Callback = Arc<Mutex<Box<dyn FnMut(&Event) + Send>>>;
type Judge = Box<dyn Fn(&Toast) -> Delivery + Send + Sync>;
type Clock = Box<dyn Fn() -> f64 + Send + Sync>;

#[derive(Clone, Debug)]
struct Request {
    theme: String,
    priority: i64,
    asked: u64,
    scope: Scope,
}

#[derive(Default)]
struct State {
    /// value and the service that owns it: a stopped service leaves nothing behind
    published: IndexMap<String, (Reading, Option<String>)>,
    images: IndexMap<String, (Image, Option<String>)>,
    /// counts every change of the images: the main loop takes them anew when it differs
    images_state: u64,
    latest: Option<Arc<Snapshot>>,
    /// theme requests by service
    themes: IndexMap<String, Request>,
    asked: u64,
    /// the transition for the next switch
    transition: Option<Transition>,
    /// switches the main loop has not seen yet (a mode change with a transition is one even
    /// when the theme stays)
    switches: u64,
    /// (mode, the service that set it)
    mode: Option<(String, Option<String>)>,
    toasts: VecDeque<Toast>,
    listeners: IndexMap<&'static str, Vec<(Option<String>, Callback)>>,
    /// the readings each service reads
    wants: IndexMap<String, BTreeSet<String>>,
    /// the newest sample and fresh not yet delivered
    newest: IndexMap<&'static str, Event>,
    sampler_stop: bool,
    notes: Vec<String>,
}

impl State {
    /// The request on top, with who asked: the highest priority, then the latest.
    fn top_request(&self) -> Option<(&String, &Request)> {
        let mode = self.mode.as_ref().map(|(mode, _)| mode);
        self.themes
            .iter()
            .filter(|(_, request)| match &request.scope {
                Scope::Any => true,
                Scope::Mode(only) => only.as_ref() == mode,
            })
            .max_by_key(|(_, request)| (request.priority, request.asked))
    }

    fn top_theme(&self) -> Option<String> {
        self.top_request().map(|(_, request)| request.theme.clone())
    }
}

/// Shared by all services and the main loop (PluginHost).
pub struct PluginHost {
    state: Mutex<State>,
    ready: Condvar,
    /// the main loop's clock, which the time stamps of states come from
    clock: Clock,
    /// says whether a toast would show now (the main loop knows the theme, the screen and the
    /// panel); none while no loop runs
    judge: Mutex<Option<Judge>>,
    events: Mutex<Option<Sender<Option<Event>>>>,
    threads: Mutex<Vec<JoinHandle<()>>>,
    /// `<settings>/plugins-data`: where each service has a folder of its own
    data: Mutex<Option<std::path::PathBuf>>,
}

impl PluginHost {
    pub fn new(clock: Clock) -> Arc<PluginHost> {
        Arc::new(PluginHost {
            state: Mutex::new(State::default()),
            ready: Condvar::new(),
            clock,
            judge: Mutex::new(None),
            events: Mutex::new(None),
            threads: Mutex::new(Vec::new()),
            data: Mutex::new(None),
        })
    }

    fn with<T>(&self, work: impl FnOnce(&mut State) -> T) -> T {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        work(&mut state)
    }

    // -- for services ------------------------------------------------------------------------

    pub fn publish(&self, key: &str, reading: Reading, owner: Option<&str>) {
        self.with(|state| {
            let owner = owner.map(str::to_string);
            state.published.insert(key.to_string(), (reading, owner));
        });
    }

    /// A structured value (a dict or a list, None for none), kept as JSON text with the time
    /// it was published; an error for any other value.
    pub fn publish_state(
        &self,
        key: &str,
        value: &Value,
        owner: Option<&str>,
        label: &str,
    ) -> Result<(), String> {
        if !matches!(value, Value::None | Value::Dict(_) | Value::List(_)) {
            return Err(format!(
                "publish_state takes a dict, a list or None, not {} (publish takes a number or \
                 a text)",
                value.type_name()
            ));
        }
        let how = lp_py::json::Dumps {
            ensure_ascii: false,
            separators: Some((",".into(), ":".into())),
            ..Default::default()
        };
        let reading = Reading {
            label: label.to_string(),
            stamp: Some((self.clock)()),
            ..Reading::of(Value::Str(lp_py::json::dumps(value, &how)))
        };
        self.publish(key, reading, owner);
        Ok(())
    }

    /// Which readings `owner` reads; none takes them back.
    pub fn want(&self, owner: &str, keys: impl IntoIterator<Item = String>) {
        let keys: BTreeSet<String> = keys.into_iter().collect();
        self.with(|state| state.wants.insert(owner.to_string(), keys));
    }

    /// Every key (or pattern) a service reads.
    pub fn wanted(&self) -> BTreeSet<String> {
        self.with(|state| state.wants.values().flatten().cloned().collect())
    }

    pub fn unpublish(&self, key: &str) {
        self.with(|state| state.published.shift_remove(key));
    }

    pub fn publish_image(&self, key: &str, image: Option<Image>, owner: Option<&str>) {
        self.with(|state| {
            match image {
                None => {
                    state.images.shift_remove(key);
                }
                Some(image) => {
                    let owner = owner.map(str::to_string);
                    state.images.insert(key.to_string(), (image, owner));
                }
            }
            state.images_state += 1;
        });
    }

    /// A service asks for a theme (`name`), or takes its request back (None).
    pub fn request_theme(
        &self,
        name: Option<&str>,
        by: &str,
        transition: Option<Transition>,
        priority: i64,
        scope: Scope,
    ) {
        self.with(|state| {
            let before = state.top_theme();
            match name.filter(|name| !name.is_empty()) {
                Some(name) => {
                    let request = Request {
                        theme: name.to_string(),
                        priority,
                        asked: state.asked,
                        scope,
                    };
                    state.asked += 1;
                    state.themes.insert(by.to_string(), request);
                    let shown = lp_py::repr(name);
                    state
                        .notes
                        .push(format!("service {by}: shows theme {shown}"));
                }
                None => {
                    state.themes.shift_remove(by);
                    state
                        .notes
                        .push(format!("service {by}: restores the theme"));
                }
            }
            // (a request that shows nothing new plays nothing)
            if state.top_theme() != before {
                state.transition = transition;
            }
        });
    }

    pub fn set_mode(&self, name: Option<&str>, by: Option<&str>, transition: Option<Transition>) {
        let name = name.filter(|name| !name.is_empty());
        let changed = self.with(|state| {
            if name == state.mode.as_ref().map(|(mode, _)| mode.as_str()) {
                return false;
            }
            state.mode = name.map(|name| (name.to_string(), by.map(str::to_string)));
            // (else a theme request's own transition stays)
            if transition.is_some() {
                state.transition = transition;
                state.switches += 1;
            }
            state
                .notes
                .push(format!("mode: {}", name.unwrap_or("normal")));
            true
        });
        if changed {
            self.emit(
                Event::ModeChanged {
                    mode: name.map(str::to_string),
                },
                false,
            );
        }
    }

    /// The transition a service chose for the switch under way (once).
    pub fn take_transition(&self) -> Option<Transition> {
        self.with(|state| state.transition.take())
    }

    /// A service stopped: its readings, images, listeners, theme and mode go too.
    pub fn forget(&self, owner: &str) {
        let mode_ended = self.with(|state| {
            let theirs = |by: &Option<String>| by.as_deref() == Some(owner);
            state.published.retain(|_, (_, by)| !theirs(by));
            let images = state.images.len();
            state.images.retain(|_, (_, by)| !theirs(by));
            if state.images.len() != images {
                state.images_state += 1;
            }
            for listeners in state.listeners.values_mut() {
                listeners.retain(|(by, _)| !theirs(by));
            }
            state.themes.shift_remove(owner);
            let mode_ended = state.mode.as_ref().is_some_and(|(_, by)| theirs(by));
            if mode_ended {
                state.mode = None;
            }
            state.wants.shift_remove(owner);
            mode_ended
        });
        if mode_ended {
            self.emit(Event::ModeChanged { mode: None }, false);
        }
    }

    /// A short message for the panel; whether it will show, or why not.
    pub fn notify(&self, toast: Toast) -> Delivery {
        let judged = match self.judge.lock() {
            Ok(judge) => judge.as_ref().map(|judge| judge(&toast)),
            Err(_) => None,
        };
        self.with(|state| {
            if state.toasts.len() == MAX_TOASTS {
                state.toasts.pop_front();
            }
            state.toasts.push_back(toast);
        });
        judged.unwrap_or_else(|| Delivery::held(NO_PANEL))
    }

    /// Hears about `event` from now on; an error for an event that is none.
    pub fn listen(
        self: &Arc<Self>,
        event: &str,
        callback: Box<dyn FnMut(&Event) + Send>,
        owner: Option<&str>,
    ) -> Result<(), String> {
        let Some(event) = EVENTS.iter().find(|known| **known == event) else {
            return Err(format!(
                "unknown event {} (known: {})",
                lp_py::repr(event),
                EVENTS.join(", ")
            ));
        };
        self.with(|state| {
            let listener = (owner.map(str::to_string), Arc::new(Mutex::new(callback)));
            state.listeners.entry(event).or_default().push(listener);
        });
        let newest = matches!(*event, "sample" | "fresh");
        let Ok(mut threads) = self.threads.lock() else {
            return Ok(());
        };
        let Ok(mut events) = self.events.lock() else {
            return Ok(());
        };
        let running = |name: &str| {
            threads
                .iter()
                .any(|thread| thread.thread().name() == Some(name))
        };
        if newest && !running("plugin-samples") {
            self.with(|state| state.sampler_stop = false);
            let host = Arc::clone(self);
            let thread = std::thread::Builder::new()
                .name("plugin-samples".into())
                .spawn(move || host.sample_loop());
            threads.extend(thread.ok());
        } else if !newest && events.is_none() {
            let (sender, receiver) = channel();
            *events = Some(sender);
            let host = Arc::clone(self);
            let thread = std::thread::Builder::new()
                .name("plugin-events".into())
                .spawn(move || host.dispatch(&receiver));
            threads.extend(thread.ok());
        }
        Ok(())
    }

    // -- for the main loop -------------------------------------------------------------------

    /// Where the services keep their files: `<settings>/plugins-data`.
    pub fn set_data_dir(&self, folder: std::path::PathBuf) {
        if let Ok(mut data) = self.data.lock() {
            *data = Some(folder);
        }
    }

    /// A note for the log, of a service or the loop.
    pub fn note(&self, text: &str) {
        self.with(|state| state.notes.push(text.to_string()));
    }

    /// What the main loop says of a toast (none while no loop runs).
    pub fn set_judge(&self, judge: Option<Judge>) {
        if let Ok(mut held) = self.judge.lock() {
            *held = judge;
        }
    }

    /// The latest readings and history the panel was drawn from.
    pub fn latest(&self) -> Option<Arc<Snapshot>> {
        self.with(|state| state.latest.clone())
    }

    pub fn set_latest(&self, snapshot: Arc<Snapshot>) {
        self.with(|state| state.latest = Some(snapshot));
    }

    /// Published readings, merged into the snapshot like any sensor source.
    pub fn published(&self) -> IndexMap<String, Reading> {
        self.with(|state| {
            let readings = state.published.iter();
            readings
                .map(|(key, (reading, _))| (key.clone(), reading.clone()))
                .collect()
        })
    }

    pub fn images(&self) -> IndexMap<String, Image> {
        self.with(|state| {
            let images = state.images.iter();
            images
                .map(|(key, (image, _))| (key.clone(), image.clone()))
                .collect()
        })
    }

    /// A number that changes with the images.
    pub fn images_state(&self) -> u64 {
        self.with(|state| state.images_state)
    }

    /// The theme services asked for, if any: the highest priority, then the latest.
    pub fn theme(&self) -> Option<String> {
        self.with(|state| state.top_theme())
    }

    /// Who asked for the theme: a service's name (for the transition of its switch).
    pub fn theme_owner(&self) -> Option<String> {
        self.with(|state| state.top_request().map(|(by, _)| by.clone()))
    }

    pub fn mode(&self) -> Option<String> {
        self.with(|state| state.mode.as_ref().map(|(mode, _)| mode.clone()))
    }

    /// Who set the mode on now: a service's name, or "pages".
    pub fn mode_owner(&self) -> Option<String> {
        self.with(|state| state.mode.as_ref().and_then(|(_, by)| by.clone()))
    }

    /// The switches the main loop has not seen yet.
    pub fn switches(&self) -> u64 {
        self.with(|state| state.switches)
    }

    pub fn take_toasts(&self) -> Vec<Toast> {
        self.with(|state| state.toasts.drain(..).collect())
    }

    /// What the host noted for the log since it was last asked.
    pub fn notes(&self) -> Vec<String> {
        self.with(|state| std::mem::take(&mut state.notes))
    }

    /// Whether anybody listens for `event`.
    pub fn hears(&self, event: &str) -> bool {
        self.with(|state| state.listeners.get(event).is_some_and(|l| !l.is_empty()))
    }

    /// Tells the listeners, in the event thread (or here, with `wait`).
    pub fn emit(&self, event: Event, wait: bool) {
        let name = event.name();
        let heard = self.with(|state| {
            let heard = state.listeners.get(name).is_some_and(|l| !l.is_empty());
            if heard && !wait && event.newest() {
                let event = match (&event, state.newest.get(name)) {
                    // (what moved before is not lost)
                    (Event::Fresh { snapshot, keys }, Some(Event::Fresh { keys: held, .. })) => {
                        Event::Fresh {
                            snapshot: snapshot.clone(),
                            keys: held.union(keys).cloned().collect(),
                        }
                    }
                    _ => event.clone(),
                };
                // (one not yet delivered is replaced: the newest counts)
                state.newest.insert(name, event);
            }
            heard
        });
        if !heard {
            return;
        }
        if wait {
            self.deliver(&event);
        } else if event.newest() {
            self.ready.notify_all();
        } else if let Ok(events) = self.events.lock() {
            if let Some(sender) = events.as_ref() {
                let _ = sender.send(Some(event));
            }
        }
    }

    /// For the main loop, between two samples: `latest` (the readings of the sources that
    /// follow on every frame) went into `snapshot`, whose readings were `before`. Tells the
    /// `fresh` listeners which readings changed, if any did.
    pub fn fresh(
        &self,
        snapshot: Arc<Snapshot>,
        before: &IndexMap<String, Reading>,
        latest: &IndexMap<String, Reading>,
    ) {
        let heard = self.with(|state| state.listeners.get("fresh").is_some_and(|l| !l.is_empty()));
        if !heard {
            return;
        }
        let keys: BTreeSet<String> = latest
            .iter()
            .filter(|(key, reading)| before.get(*key) != Some(reading))
            .map(|(key, _)| key.clone())
            .collect();
        if !keys.is_empty() {
            self.emit(Event::Fresh { snapshot, keys }, false);
        }
    }

    fn deliver(&self, event: &Event) {
        let callbacks: Vec<Callback> = self.with(|state| {
            let listeners = state.listeners.get(event.name());
            listeners
                .into_iter()
                .flatten()
                .map(|(_, callback)| callback.clone())
                .collect()
        });
        for callback in callbacks {
            let told = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                if let Ok(mut callback) = callback.lock() {
                    callback(event);
                }
            }));
            if told.is_err() {
                let note = format!("a {} listener failed", event.name());
                self.with(|state| {
                    // (a sample comes ten times a second: each failure said once)
                    if state.notes.last() != Some(&note) {
                        state.notes.push(note);
                    }
                });
            }
        }
    }

    fn sample_loop(&self) {
        loop {
            let newest = {
                let Ok(mut state) = self.state.lock() else {
                    return;
                };
                while state.newest.is_empty() && !state.sampler_stop {
                    state = match self.ready.wait(state) {
                        Ok(state) => state,
                        Err(_) => return,
                    };
                }
                if state.sampler_stop {
                    return;
                }
                std::mem::take(&mut state.newest)
            };
            for event in newest.values() {
                self.deliver(event);
            }
        }
    }

    fn dispatch(&self, events: &Receiver<Option<Event>>) {
        while let Ok(Some(event)) = events.recv() {
            self.deliver(&event);
        }
    }

    /// Ends the event threads, once what is queued has been told.
    pub fn close(&self) {
        if let Ok(mut events) = self.events.lock() {
            if let Some(sender) = events.take() {
                let _ = sender.send(None);
            }
        }
        self.with(|state| state.sampler_stop = true);
        self.ready.notify_all();
        let threads = match self.threads.lock() {
            Ok(mut threads) => std::mem::take(&mut *threads),
            Err(_) => Vec::new(),
        };
        for thread in threads {
            let _ = thread.join();
        }
    }
}

/// What one service may do. Thread-safe; call it from any thread (ServiceHost).
#[derive(Clone)]
pub struct ServiceHost {
    hub: Arc<PluginHost>,
    pub name: String,
}

impl ServiceHost {
    pub fn new(hub: &Arc<PluginHost>, name: &str) -> ServiceHost {
        ServiceHost {
            hub: Arc::clone(hub),
            name: name.to_string(),
        }
    }

    /// The host all services share.
    pub fn hub(&self) -> &Arc<PluginHost> {
        &self.hub
    }

    /// `<settings>/plugins-data/<service>/`: the service's own files; none where the folder
    /// cannot be made.
    pub fn data_dir(&self) -> Option<std::path::PathBuf> {
        let root = self.hub.data.lock().ok()?.clone()?;
        let folder = root.join(&self.name);
        std::fs::create_dir_all(&folder).ok()?;
        Some(folder)
    }

    /// A line for the log.
    pub fn note(&self, text: &str) {
        self.hub.note(text);
    }

    /// The latest readings and history the panel was drawn from.
    pub fn snapshot(&self) -> Option<Arc<Snapshot>> {
        self.hub.latest()
    }

    /// A reading themes can show like any sensor (e.g. `game.fps`). It goes away when the
    /// service stops. It reaches the next frame, not only the next sample.
    pub fn publish(&self, key: &str, value: Value, unit: &str, label: &str) {
        let reading = Reading {
            unit: unit.to_string(),
            label: label.to_string(),
            ..Reading::of(value)
        };
        self.hub.publish(key, reading, Some(&self.name));
    }

    /// A structured value (a dict or a list, None for none) that screens, widget types and
    /// other services read; kept as JSON text with the time it was published.
    pub fn publish_state(&self, key: &str, value: &Value, label: &str) -> Result<(), String> {
        self.hub.publish_state(key, value, Some(&self.name), label)
    }

    pub fn unpublish(&self, key: &str) {
        self.hub.unpublish(key);
    }

    /// Says which readings the service reads (`media.*`, `audio.volume`): a source that works
    /// on demand then runs although no theme on the panel shows them. Replaces the keys of an
    /// earlier call; none takes them back.
    pub fn want(&self, keys: &[&str]) {
        self.hub
            .want(&self.name, keys.iter().map(|key| (*key).to_string()));
    }

    /// An image for screens and widgets (e.g. `media.cover`); None removes it.
    pub fn publish_image(&self, key: &str, image: Option<Image>) {
        self.hub.publish_image(key, image, Some(&self.name));
    }

    /// Shows another theme until `restore_theme` (config.toml is not changed). When several
    /// services ask for a theme, the highest `priority` wins, and among equals the last to
    /// ask. `scope` limits the request to one mode.
    pub fn show_theme(
        &self,
        name: &str,
        transition: Option<Transition>,
        priority: i64,
        scope: Scope,
    ) {
        self.hub
            .request_theme(Some(name), &self.name, transition, priority, scope);
    }

    /// Takes back this service's `show_theme` (another service's may show then).
    pub fn restore_theme(&self, transition: Option<Transition>) {
        self.hub
            .request_theme(None, &self.name, transition, 0, Scope::Any);
    }

    pub fn mode(&self) -> Option<String> {
        self.hub.mode()
    }

    /// Switches to a mode from `[modes.<name>]` (frame rate, theme); None ends it.
    pub fn set_mode(&self, name: Option<&str>, transition: Option<Transition>) {
        self.hub.set_mode(name, Some(&self.name), transition);
    }

    /// A short message on the panel. The answer says whether it will show or why not.
    pub fn notify(&self, mut toast: Toast) -> Delivery {
        toast.service = self.name.clone();
        self.hub.notify(toast)
    }

    /// Hears about an event; callbacks run in an event thread, and a slow `sample` or `fresh`
    /// callback is handed the newest one when it is free again, not every one.
    pub fn on(&self, event: &str, callback: Box<dyn FnMut(&Event) + Send>) -> Result<(), String> {
        self.hub.listen(event, callback, Some(&self.name))
    }
}
