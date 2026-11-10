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
        let mut enabled: Vec<String> = Vec::new();
        let mut lacking = Vec::new();
        for name in &config.services.enabled {
            match parts.contains_key(name.as_str()) {
                true => enabled.push(name.clone()),
                false => lacking.push(name.clone()),
            }
        }
        if parts.contains_key("script") {
            enabled.push("script".into()); // (the tests' own)
        }
        if let Ok(mut said) = self.lacking.lock() {
            if lacking != *said {
                if !lacking.is_empty() {
                    say(&format!(
                        "services not in this build yet: {}",
                        lacking.join(", ")
                    ));
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
