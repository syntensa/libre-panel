//! The services config.toml enables, started, stopped and kept in step with it
//! (plugins/host.py's ServiceManager and apply_options).

use std::sync::mpsc::channel;
use std::sync::Arc;
use std::time::Duration;

use indexmap::IndexMap;
use lp_py::{Dict, Value};

use crate::host::{PluginHost, ServiceHost};

/// Settings that are no more: let be, their values unread (config.GONE's of the services).
pub const GONE: [&str; 2] = ["services.agents.cloud", "services.agents.cloud_token"];

/// A service: a long-running helper such as a game mode. `start` must return quickly (start
/// your own threads); `stop` must end them within a few seconds.
pub trait Service: Send {
    fn start(&mut self) -> Result<(), String> {
        Ok(())
    }

    fn stop(&mut self) {}

    /// What the service is doing now, for its card in the app, as data the card words; None
    /// says nothing.
    fn summary(&self) -> Option<Value> {
        None
    }

    /// What the service is doing in full, for its console page (the autopilot's rule and
    /// signals); None for a service without one.
    fn status(&self) -> Option<Value> {
        None
    }
}

/// An option of a service: its name, its type (int, number, bool, string, list) and its
/// default.
pub type Schema = Vec<(&'static str, &'static str, Value)>;

/// A kind of service: its options, and how one is made of its host and its options.
pub struct Part {
    pub options: fn() -> Schema,
    pub make: fn(ServiceHost, Dict) -> Result<Box<dyn Service>, String>,
}

/// apply_options: the service's defaults, overridden by what config.toml sets (types
/// checked); the options it does not know are named in `notes`.
pub fn apply_options(
    schema: &Schema,
    given: &Dict,
    place: &str,
    notes: &mut Vec<String>,
) -> Result<Dict, String> {
    let mut result: Dict = schema
        .iter()
        .map(|(key, _, default)| ((*key).to_string(), default.clone()))
        .collect();
    for (key, value) in given {
        if GONE.contains(&format!("{place}.{key}").as_str()) {
            continue;
        }
        let Some((_, kind, _)) = schema.iter().find(|(known, _, _)| known == key) else {
            notes.push(format!(
                "{place}: unknown option {} ignored",
                lp_py::repr(key)
            ));
            continue;
        };
        let fits = matches!(
            (*kind, value),
            ("int", Value::Int(_))
                | ("number", Value::Int(_) | Value::Float(_))
                | ("bool", Value::Bool(_))
                | ("string", Value::Str(_))
                | ("list", Value::List(_))
        );
        if !fits {
            return Err(format!(
                "{place}.{key}: expected {kind}, got {}",
                value.repr()
            ));
        }
        result.insert(key.clone(), value.clone());
    }
    Ok(result)
}

/// Services that run elsewhere (the plugins', in the plugin host): started, stopped and
/// kept in step with config.toml there, their options checked there.
pub trait Remote: Send {
    /// The services it has, by name.
    fn names(&self) -> Vec<String>;
    /// Those of them config.toml enables, with their options: each one's state after.
    fn apply(
        &mut self,
        enabled: &[String],
        options: &IndexMap<String, Dict>,
    ) -> IndexMap<String, String>;
    /// One stopped and started again: its new state.
    fn restart(&mut self, name: &str) -> Result<String, String>;
    /// What a running one is doing now; None for one that does not run or says nothing.
    fn summary(&self, name: &str) -> Option<Value>;
    /// All of them stopped.
    fn stop(&mut self);
}

/// How long a service may take to stop.
const STOP_SECONDS: f64 = 5.0;

/// Starts the services enabled in config.toml, and keeps them in step with it.
pub struct Manager {
    host: Arc<PluginHost>,
    parts: IndexMap<&'static str, Part>,
    /// each service that runs, and the options it was given
    running: IndexMap<String, (Box<dyn Service>, Dict)>,
    /// "running", "stopped", "not installed", "failed: ..."
    pub state: IndexMap<String, String>,
    /// enabled services and their options
    wanted: IndexMap<String, Dict>,
    notes: Vec<String>,
    /// the plugins' services, run in the plugin host
    remote: Option<Box<dyn Remote>>,
    /// the names it has
    remote_names: Vec<String>,
}

impl Manager {
    pub fn new(host: &Arc<PluginHost>, parts: IndexMap<&'static str, Part>) -> Manager {
        Manager {
            host: Arc::clone(host),
            parts,
            running: IndexMap::new(),
            state: IndexMap::new(),
            wanted: IndexMap::new(),
            notes: Vec::new(),
            remote: None,
            remote_names: Vec::new(),
        }
    }

    /// The services the plugins bring, run where they are (the plugin host).
    pub fn set_remote(&mut self, remote: Box<dyn Remote>) {
        self.remote_names = remote.names();
        self.remote = Some(remote);
    }

    /// Whether the plugins' services were handed over.
    pub fn has_remote(&self) -> bool {
        self.remote.is_some()
    }

    fn is_remote(&self, name: &str) -> bool {
        !self.parts.contains_key(name) && self.remote_names.iter().any(|known| known == name)
    }

    /// Starts, stops or restarts services so they match `enabled` and their `options`.
    pub fn apply(&mut self, enabled: &[String], options: &IndexMap<String, Dict>) {
        let wanted: IndexMap<String, Dict> = enabled
            .iter()
            .map(|name| (name.clone(), options.get(name).cloned().unwrap_or_default()))
            .collect();
        self.wanted = wanted.clone();
        let remote: Vec<String> = enabled
            .iter()
            .filter(|name| self.is_remote(name))
            .cloned()
            .collect();
        let wanted: IndexMap<String, Dict> = wanted
            .into_iter()
            .filter(|(name, _)| !self.is_remote(name))
            .collect();
        let gone: Vec<String> = self.running.keys().cloned().collect();
        for name in gone.iter().filter(|name| !wanted.contains_key(*name)) {
            self.stop_one(name);
        }
        for (name, given) in &wanted {
            if let Some((_, before)) = self.running.get(name) {
                if same(before, given) {
                    continue;
                }
                self.stop_one(name); // its options changed
            }
            self.start_one(name, given);
        }
        let states = match self.remote.as_mut() {
            Some(running) => running.apply(&remote, options),
            None => IndexMap::new(),
        };
        self.state
            .retain(|name, _| wanted.contains_key(name) || remote.contains(name));
        for (name, state) in states {
            if remote.contains(&name) {
                self.state.insert(name, state);
            }
        }
    }

    /// Stops an enabled service and starts it again; its new state.
    pub fn restart(&mut self, name: &str) -> Result<String, String> {
        let Some(given) = self.wanted.get(name).cloned() else {
            let named = lp_py::repr(name);
            return Err(format!("service {named} is not enabled in config.toml"));
        };
        if self.is_remote(name) {
            let state = match self.remote.as_mut() {
                Some(running) => running.restart(name)?,
                None => "not installed".to_string(),
            };
            self.state.insert(name.to_string(), state.clone());
            return Ok(state);
        }
        if self.running.contains_key(name) {
            self.stop_one(name);
        }
        self.start_one(name, &given);
        Ok(self.state.get(name).cloned().unwrap_or_default())
    }

    fn start_one(&mut self, name: &str, given: &Dict) {
        let named = lp_py::repr(name);
        let Some(part) = self.parts.get(name) else {
            self.state.insert(name.to_string(), "not installed".into());
            self.notes.push(format!("service {named}: not installed"));
            return;
        };
        let place = format!("services.{name}");
        let made = apply_options(&(part.options)(), given, &place, &mut self.notes)
            .and_then(|options| (part.make)(ServiceHost::new(&self.host, name), options));
        let mut service = match made {
            Ok(service) => service,
            Err(why) => {
                self.state
                    .insert(name.to_string(), format!("failed: {why}"));
                self.notes
                    .push(format!("service {named} not started: {why}"));
                return;
            }
        };
        if let Err(why) = service.start() {
            // (what it published or asked for before it failed goes with it)
            self.host.forget(name);
            self.state
                .insert(name.to_string(), format!("failed: {why}"));
            self.notes
                .push(format!("service {named} failed to start: {why}"));
            return;
        }
        self.running
            .insert(name.to_string(), (service, given.clone()));
        self.state.insert(name.to_string(), "running".into());
        self.notes.push(format!("service {named} started"));
    }

    fn stop_one(&mut self, name: &str) {
        let Some((mut service, _)) = self.running.shift_remove(name) else {
            return;
        };
        // ("running" until its stop() has returned, or it took too long)
        let (done, wait) = channel();
        let worker = std::thread::Builder::new()
            .name(format!("stop-{name}"))
            .spawn(move || {
                service.stop();
                let _ = done.send(());
            });
        let waited = wait.recv_timeout(Duration::from_secs_f64(STOP_SECONDS));
        if worker.is_err() || waited.is_err() {
            let named = lp_py::repr(name);
            let note = format!("service {named} did not stop within {STOP_SECONDS:.0} s");
            self.notes.push(note);
        }
        self.host.forget(name);
        self.state.insert(name.to_string(), "stopped".into());
    }

    /// Stops every service, the last started first.
    pub fn stop(&mut self) {
        // (before the services stop and lose their listeners)
        self.host.emit(crate::host::Event::Quit, true);
        let names: Vec<String> = self.running.keys().rev().cloned().collect();
        for name in names {
            self.stop_one(&name);
        }
        if let Some(running) = self.remote.as_mut() {
            running.stop();
        }
    }

    /// What each running service is doing now.
    pub fn summaries(&self) -> IndexMap<String, Value> {
        let running = self.running.iter();
        let mut found: IndexMap<String, Value> = running
            .filter_map(|(name, (service, _))| Some((name.clone(), service.summary()?)))
            .collect();
        if let Some(remote) = self.remote.as_ref() {
            for (name, state) in &self.state {
                if state == "running" && self.is_remote(name) {
                    if let Some(said) = remote.summary(name) {
                        found.insert(name.clone(), said);
                    }
                }
            }
        }
        found
    }

    /// What a running service is doing now (its card's summary); None for one that does not
    /// run or says nothing.
    pub fn summary_of(&self, name: &str) -> Option<Value> {
        if self.is_remote(name) {
            return self.remote.as_ref()?.summary(name);
        }
        self.running.get(name)?.0.summary()
    }

    /// A running service's status for its console page; None for one that does not run or
    /// has none.
    pub fn status_of(&self, name: &str) -> Option<Value> {
        self.running.get(name)?.0.status()
    }

    /// What the manager noted for the log since it was last asked.
    pub fn notes(&mut self) -> Vec<String> {
        std::mem::take(&mut self.notes)
    }
}

/// Whether two tables of options are the same (as Python's == of the dicts).
fn same(a: &Dict, b: &Dict) -> bool {
    a.len() == b.len()
        && a.iter()
            .all(|(key, value)| b.get(key).is_some_and(|other| other.repr() == value.repr()))
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;
    use crate::host::Scope;

    static STOPPED: AtomicUsize = AtomicUsize::new(0);

    struct Probe {
        host: ServiceHost,
        fail: bool,
    }

    impl Service for Probe {
        fn start(&mut self) -> Result<(), String> {
            self.host.publish("probe.value", Value::int(1), "", "Probe");
            self.host.show_theme("slate", None, 0, Scope::Any);
            self.host.set_mode(Some("game"), None);
            match self.fail {
                true => Err("no luck".into()),
                false => Ok(()),
            }
        }

        fn stop(&mut self) {
            STOPPED.fetch_add(1, Ordering::SeqCst);
        }
    }

    fn parts() -> IndexMap<&'static str, Part> {
        let mut parts = IndexMap::new();
        parts.insert(
            "probe",
            Part {
                options: || vec![("fail", "bool", Value::Bool(false))],
                make: |host, options| {
                    let fail = options.get("fail").is_some_and(Value::truthy);
                    Ok(Box::new(Probe { host, fail }))
                },
            },
        );
        parts
    }

    #[test]
    fn services_follow_the_settings_and_leave_nothing_behind() {
        let host = PluginHost::new(Box::new(|| 0.0));
        let mut manager = Manager::new(&host, parts());
        let enabled = vec!["probe".to_string(), "nope".to_string()];
        manager.apply(&enabled, &IndexMap::new());
        assert_eq!(manager.state["probe"], "running");
        assert_eq!(manager.state["nope"], "not installed");
        assert_eq!(host.theme().as_deref(), Some("slate"));
        assert_eq!(host.mode().as_deref(), Some("game"));
        assert!(host.published().contains_key("probe.value"));

        // the same settings: let be; other options: stopped and started anew
        manager.apply(&enabled, &IndexMap::new());
        assert_eq!(STOPPED.load(Ordering::SeqCst), 0);
        let mut options = IndexMap::new();
        let mut given = Dict::default();
        given.insert("fail".into(), Value::Bool(true));
        options.insert("probe".to_string(), given.clone());
        manager.apply(&enabled, &options);
        assert_eq!(STOPPED.load(Ordering::SeqCst), 1);
        assert_eq!(manager.state["probe"], "failed: no luck");
        assert!(host.published().is_empty() && host.theme().is_none() && host.mode().is_none());

        // a wrong type is said, and the service not started
        given.insert("fail".into(), Value::int(1));
        options.insert("probe".to_string(), given);
        manager.apply(&enabled, &options);
        assert_eq!(
            manager.state["probe"],
            "failed: services.probe.fail: expected bool, got 1"
        );
        assert!(manager.restart("other").is_err());

        manager.apply(&enabled, &IndexMap::new());
        assert_eq!(manager.restart("probe").as_deref(), Ok("running"));
        assert_eq!(STOPPED.load(Ordering::SeqCst), 2);
        manager.apply(&[], &IndexMap::new());
        assert_eq!(STOPPED.load(Ordering::SeqCst), 3);
        assert!(manager.state.is_empty() && host.published().is_empty());
        manager.stop();
        host.close();
    }
}
