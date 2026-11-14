//! The theme a panel shows when config.toml names none (theme/defaults.py): Lumen on a bar (the
//! 8.8" and 9.2", 1920x480) and on a round panel Lumen has a version for (480x480: Libre
//! Default's corners would be cut off there), Libre Default on every other panel and where the
//! panel is not known. The panel a config is for, and the theme of its normal page, go with
//! the config (docs/RUST.md, R9).

use lp_devices::models::PanelModel;

use crate::tables::DEFAULT_THEME;
use crate::versions::{theme_for, Places};

pub const BAR_THEME: &str = "lumen";
/// a bar is at least this many times as long as it is high
pub const BAR_RATIO: f64 = 3.5;

/// is_bar: whether the panel's landscape frame is 3.5 times as long as it is high or more
/// (1920x480, whatever the bezel hides; the 12.3"'s 1920x720 is not).
pub fn is_bar(model: Option<&PanelModel>) -> bool {
    let Some(model) = model else {
        return false;
    };
    let (long, short) = model.size("landscape");
    long as f64 >= BAR_RATIO * short as f64
}

/// _round_lumen: a round panel Lumen has a version for.
fn round_lumen(model: Option<&PanelModel>, places: &Places) -> bool {
    let Some(model) = model.filter(|m| m.shape == "round") else {
        return false;
    };
    theme_for(
        BAR_THEME,
        "landscape",
        Some(model.size("landscape")),
        places,
    ) != BAR_THEME
}

/// default_theme: the theme a config without a `theme` shows on `model`.
pub fn default_theme(model: Option<&PanelModel>, places: &Places) -> &'static str {
    if is_bar(model) || round_lumen(model, places) {
        BAR_THEME
    } else {
        DEFAULT_THEME
    }
}
