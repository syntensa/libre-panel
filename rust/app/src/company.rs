//! What outlives a panel's loop: the services' host and the services that run. The loop of
//! `libre-panel run` has its own; the background app's is the app's, so that the services go
//! on while the panel is paused (service.py's BackgroundApp: its PluginHost and
//! ServiceManager).

use std::sync::{Arc, Mutex};
use std::time::Instant;

use lp_config::Config;
use lp_services::host::PluginHost;
use lp_services::manager::Manager;

fn say(message: &str) {
    crate::logs::say("libre_panel.services", message);
}

/// What else follows config.toml (the background app's keyboard shortcuts).
pub type Also = Box<dyn Fn(&Config) + Send>;

pub struct Company {
    /// when the host's clock began: the loops count from it too
    pub began: Instant,
    pub host: Arc<PluginHost>,
    manager: Mutex<Manager>,
    /// the services config.toml enables that this build lacks, as they were last said
    lacking: Mutex<Vec<String>>,
    also: Mutex<Option<Also>>,
}

impl Company {
    pub fn new() -> Arc<Company> {
        let began = Instant::now();
        let host = PluginHost::new(Box::new(move || began.elapsed().as_secs_f64()));
        let manager = Manager::new(&host, crate::services::parts());
        Arc::new(Company {
            began,
            host,
            manager: Mutex::new(manager),
            lacking: Mutex::new(Vec::new()),
            also: Mutex::new(None),
        })
    }

    /// What follows config.toml beside the services.
    pub fn set_also(&self, also: Option<Also>) {
        if let Ok(mut held) = self.also.lock() {
            *held = also;
        }
    }

    /// The services config.toml enables, started, stopped or started anew as it says; those
    /// this build does not have yet are said once.
    pub fn follow(&self, config: &Config) {
        let parts = crate::services::parts();
        // (the plugins' services, run in the plugin host where plugins are installed)
        let settings = config
            .path
            .as_deref()
            .and_then(std::path::Path::parent)
            .map_or_else(lp_config::dirs::config_dir, std::path::Path::to_path_buf);
        let plugins = match crate::plugins::shared(&settings) {
            Some(Ok(host)) => host.names("services"),
            _ => Vec::new(),
        };
        if !plugins.is_empty() {
            self.plugin_services(&settings, config);
        }
        let mut enabled: Vec<String> = Vec::new();
        let mut lacking = Vec::new();
        for name in &config.services.enabled {
            match parts.contains_key(name.as_str()) || plugins.contains(name) {
                true => enabled.push(name.clone()),
                false => lacking.push(name.clone()),
            }
        }
        if parts.contains_key("script") {
            enabled.push("script".into()); // (the tests' own)
        }
        if let Ok(mut said) = self.lacking.lock() {
            if lacking != *said {
                for name in &lacking {
                    // (ServiceManager's words for one nobody brings)
                    say(&format!("service {}: not installed", lp_py::repr(name)));
                }
                *said = lacking;
            }
        }
        if let Ok(mut manager) = self.manager.lock() {
            manager.apply(&enabled, &crate::services::options_of(config));
            for note in manager.notes() {
                say(&note);
            }
        }
        if let Ok(also) = self.also.lock() {
            if let Some(also) = also.as_ref() {
                also(config);
            }
        }
    }

    /// The plugins' services handed to the manager, once.
    fn plugin_services(&self, settings: &std::path::Path, config: &Config) {
        let Ok(mut manager) = self.manager.lock() else {
            return;
        };
        if manager.has_remote() {
            return;
        }
        if let Some(Ok(host)) = crate::plugins::shared(settings) {
            let remote =
                crate::plugins::RemoteServices::new(host, &self.host, config.path.as_deref());
            manager.set_remote(Box::new(remote));
        }
    }

    /// Each service config.toml enables and its state (`running`, `failed: ...`, `not
    /// installed`, `stopped`), as the manager has them.
    pub fn states(&self) -> indexmap::IndexMap<String, String> {
        self.manager
            .lock()
            .map(|m| m.state.clone())
            .unwrap_or_default()
    }

    /// A running service's summary for its card; None where it says nothing.
    pub fn summary(&self, name: &str) -> Option<lp_py::Value> {
        self.manager.lock().ok()?.summary_of(name)
    }

    /// A running service's status for its console page; None where it does not run.
    pub fn status(&self, name: &str) -> Option<lp_py::Value> {
        self.manager.lock().ok()?.status_of(name)
    }

    /// Stops an enabled service and starts it again; its new state.
    pub fn restart(&self, name: &str) -> Result<String, String> {
        let mut manager = self.manager.lock().map_err(|_| "the services are stuck")?;
        let done = manager.restart(name);
        for note in manager.notes() {
            say(&note);
        }
        done
    }

    /// Every service stopped, the last started first.
    pub fn stop_services(&self) {
        if let Ok(mut manager) = self.manager.lock() {
            manager.stop();
            for note in manager.notes() {
                say(&note);
            }
        }
    }

    pub fn close(&self) {
        self.host.close();
    }
}
