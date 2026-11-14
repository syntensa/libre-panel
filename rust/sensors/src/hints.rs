//! What to do on Windows when no CPU or GPU temperature or power arrives
//! (sensors/librehardwaremonitor.py's ring0_steps, hardware_hint and user_hint): for the
//! command line with the technical words, and one sentence in the person's language for the
//! tray and the editor.

use lp_i18n::t;

use crate::lhm::Readings;
use crate::service::LhmStatus;

/// What on Windows only LibreHardwareMonitor gives.
pub const HARDWARE_KEYS: [&str; 4] = ["cpu.temp", "gpu.temp", "cpu.power", "gpu.power"];
/// the driver LibreHardwareMonitor reads the CPU through
pub const PAWNIO_URL: &str = "https://pawnio.eu";
/// What sets up the sensor service, which reads the CPU with the rights it needs (and PawnIO).
pub const SERVICE_STEP: &str = "set up the sensor service (Unlock all sensors in the app, or as \
                                administrator: libre-panel system-setup --service --pawnio)";

/// The number a version starts with (`re.match(r"\d+", version)`), where it starts with one.
fn major(version: &str) -> Option<u128> {
    let digits: Vec<u32> = version.chars().map_while(lp_py::decimal).collect();
    if digits.is_empty() {
        return None;
    }
    Some(digits.iter().fold(0u128, |n, d| {
        n.saturating_mul(10).saturating_add(u128::from(*d))
    }))
}

/// Whether the installed PawnIO driver is older than 2 (a version that is not said is not).
fn old_pawnio(version: Option<&str>) -> bool {
    major(version.unwrap_or("")).is_some_and(|n| n < 2)
}

/// ring0_steps: what stands between the CPU's sensors and Libre Panel, from what is known.
/// `unlock`: this download can set up the sensor service, which brings both.
pub fn ring0_steps(status: &LhmStatus, unlock: bool) -> Vec<String> {
    if unlock && status.elevated == Some(false) {
        return vec![SERVICE_STEP.to_string()];
    }
    let mut steps = Vec::new();
    if status.elevated == Some(false) {
        steps.push(
            "run Libre Panel as administrator (libre-panel autostart enable --elevated)".into(),
        );
    }
    match &status.pawnio {
        None => steps.push(format!("install the PawnIO driver ({PAWNIO_URL})")),
        Some(version) if old_pawnio(Some(version)) => steps.push(format!(
            "update the PawnIO driver to version 2 or newer ({PAWNIO_URL})"
        )),
        Some(_) => {}
    }
    steps
}

/// hardware_hint: what to do on Windows (`windows`) when no CPU or GPU temperature or power
/// arrives; none when it does, and elsewhere. `status` says how LibreHardwareMonitor is read;
/// `unlock`, whether this download can set up the sensor service.
pub fn hardware_hint(
    readings: &Readings,
    providers: &[String],
    url: &str,
    windows: bool,
    status: Option<&LhmStatus>,
    unlock: bool,
) -> Option<String> {
    if !windows || HARDWARE_KEYS.iter().any(|key| readings.contains_key(*key)) {
        return None;
    }
    let mut text = "No CPU or GPU temperature or power readings. ".to_string();
    if let Some(status) = status.filter(|status| !status.service.is_empty()) {
        return Some(format!(
            "{text}The sensor service does not answer: {}.",
            status.service
        ));
    }
    if let Some(status) = status.filter(|status| status.mode == "service") {
        if !status.error.is_empty() {
            return Some(format!(
                "{text}The sensor service does not answer: {}.",
                status.error
            ));
        }
        let steps = ring0_steps(status, false);
        if steps.is_empty() {
            return Some(format!(
                "{text}The sensor service runs, but LibreHardwareMonitor finds no sensors it \
                 supports."
            ));
        }
        return Some(format!(
            "{text}The sensor service runs, but the CPU's sensors are out of reach: {}.",
            steps.join(" and ")
        ));
    }
    if let Some(status) = status.filter(|status| status.mode == "inprocess") {
        if !status.error.is_empty() {
            let said = format!(
                "{text}LibreHardwareMonitor could not start inside Libre Panel: {}",
                status.error
            );
            return Some(if unlock {
                format!("{said}. Instead: {SERVICE_STEP}.")
            } else {
                said
            });
        }
        let steps = ring0_steps(status, unlock);
        if steps.is_empty() {
            return Some(format!(
                "{text}LibreHardwareMonitor runs inside Libre Panel, but finds no sensors it \
                 supports."
            ));
        }
        return Some(format!(
            "{text}LibreHardwareMonitor runs inside Libre Panel, but the CPU's sensors are out \
             of reach: {}.",
            steps.join(" and ")
        ));
    }
    text.push_str(&format!(
        "On Windows these come from LibreHardwareMonitor: run it as administrator and turn on \
         Options -> Remote Web Server -> Run (Libre Panel then reads {url})."
    ));
    if !providers.iter().any(|name| name == "librehardwaremonitor") {
        text.push_str(" Add \"librehardwaremonitor\" to providers under [sensors] in config.toml.");
    }
    if let Some(status) = status.filter(|status| !status.error.is_empty()) {
        // (it tried to start inside Libre Panel first)
        text.push_str(&format!(
            " (Inside Libre Panel it could not start: {}.)",
            status.error
        ));
        if unlock {
            text.push_str(&format!(" Or {SERVICE_STEP}."));
        }
    }
    Some(text)
}

/// user_hint: one sentence for a person who sees "--" where Windows' temperatures and power
/// should be: what is the matter and what to do about it, in their language. Empty when all
/// is well, elsewhere, while LibreHardwareMonitor is still starting (`ready` false) and for
/// what nobody can change.
pub fn user_hint(
    readings: &Readings,
    windows: bool,
    status: Option<&LhmStatus>,
    ready: bool,
    unlock: bool,
) -> String {
    if !windows || !ready {
        return String::new();
    }
    let nothing = !HARDWARE_KEYS.iter().any(|key| readings.contains_key(*key));
    let has_temp = readings.contains_key("cpu.temp");
    let unlocks = || {
        t(
            "CPU temperature, power and fans need the sensor service: choose “Unlock all \
           sensors” on Start.",
        )
    };
    if let Some(status) = status {
        if !status.service.is_empty() || (status.mode == "service" && !status.error.is_empty()) {
            if has_temp {
                return String::new();
            }
            return t(
                "CPU temperature and power are missing: the sensor service does not answer. \
                 Set it up again in Settings → Sensors, or restart Windows.",
            );
        }
        if !status.error.is_empty() {
            // (it did not start inside Libre Panel)
            if !nothing {
                return String::new(); // the web server has them
            }
            if unlock {
                return unlocks();
            }
            return t(
                "Temperatures and power are missing: their reader did not start. Reinstall \
                 Libre Panel; the log says why.",
            );
        }
    }
    let Some(status) = status.filter(|s| s.mode == "inprocess" || s.mode == "service") else {
        if !nothing {
            return String::new();
        }
        return t(
            "Temperatures and power need LibreHardwareMonitor: start it as administrator and \
             turn on Options > Remote Web Server > Run.",
        );
    };
    if status.ring0 != Some(false) || has_temp {
        return String::new(); // in reach, unknown, or filled in
    }
    let (admin, missing, old) = (
        status.elevated == Some(false),
        status.pawnio.is_none(),
        old_pawnio(status.pawnio.as_deref()),
    );
    if admin && unlock {
        return unlocks();
    }
    if admin && (missing || old) {
        return t(
            "CPU temperature and power need administrator rights and the PawnIO driver \
             (pawnio.eu).",
        );
    }
    if admin {
        return t(
            "CPU temperature and power need administrator rights: run Libre Panel as \
             administrator.",
        );
    }
    if missing {
        return t("CPU temperature and power need the PawnIO driver: install it from pawnio.eu.");
    }
    if old {
        return t("CPU temperature and power need PawnIO 2 or newer: update it from pawnio.eu.");
    }
    String::new()
}
