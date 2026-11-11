//! Whether the person is away from the computer (presence.py): it is locked, or its monitor
//! is off. Windows tells the session window (winsession.rs), logind tells on Linux
//! (logind.rs). The panels then take the brightness of `[device] away_brightness` (0: dark)
//! where `dark_when_away` says so (night::brightness_now), and their own again when the
//! person is back. A reason is said by whoever notices it; the person is away while one of
//! them holds.

use std::collections::BTreeSet;
use std::sync::Mutex;

/// the session is locked (Win+L, or by itself)
pub const LOCKED: &str = "locked";
/// the monitor went off (its power plan, or by hand; Windows tells)
#[cfg_attr(not(windows), allow(dead_code))]
pub const DISPLAY_OFF: &str = "display-off";

static REASONS: Mutex<BTreeSet<&'static str>> = Mutex::new(BTreeSet::new());

/// set_away: `reason` holds now (`on`) or no longer does.
pub fn set_away(reason: &'static str, on: bool) {
    let Ok(mut reasons) = REASONS.lock() else {
        return;
    };
    let before = !reasons.is_empty();
    match on {
        true => reasons.insert(reason),
        false => reasons.remove(reason),
    };
    let now = !reasons.is_empty();
    if now != before {
        let how = if now { "away" } else { "back" };
        crate::logs::log(
            crate::logs::Level::Info,
            "libre_panel.presence",
            &format!("the person is {how} ({reason})"),
        );
    }
}

/// away: whether one of the reasons holds.
pub fn away() -> bool {
    REASONS.lock().is_ok_and(|reasons| !reasons.is_empty())
}
