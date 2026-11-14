//! Finishes (render/finish.py): how a widget's shape is drawn, in the same place and size. A
//! widget names one (its "finish"); a finish draws the kinds of widget it is made for, and of
//! those the ones it accepts; any other widget is drawn flat, as Python draws it.

use lp_py::{Dict, Value};

use crate::facets::Style;
use crate::widget;

/// The finish a widget is drawn with, as finish.py's attach finds it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Finish {
    /// the renderer's own drawing (flat, or a finish that does not take the widget)
    Flat,
    /// lit.py's LitText: a text's or a metric's glow is light
    LitText,
    /// lit.py's LitBar: a bar's light in its milled groove
    LitBar,
    /// lit.py's LitGauge: a ring's arc of light in its milled groove
    LitGauge,
    /// lit.py's LitIcon: a weather icon as its weather's symbol, lit
    LitIcon,
    /// symbols.py's LitSymbol: a weather symbol as frosted glass lit from above
    LitSymbol,
    /// lit.py's LitGraph: a graph's line as light, its halo under it
    LitGraph,
    /// download.py's LitDownloads: a download card's progress light in its groove
    LitDownloads,
    /// facet.py's FacetBar: a raised bar with bevelled ends in a milled track
    FacetBar,
    /// facet.py's FacetGauge: a raised ring with bevelled ends in a milled track
    FacetGauge,
    /// facet.py's FacetGraph: a graph's line as a bevelled ridge, its halo under it
    FacetGraph,
    /// facet.py's FacetText and AcrylicText: the text's letters the finish's own
    Lettered(Style),
}

/// lit.py's WEATHER_ICONS: the icons the lit finish draws as weather symbols.
const WEATHER_ICONS: [&str; 9] = [
    "weather", "sun", "moon", "partly", "cloud", "fog", "rain", "snow", "storm",
];

/// finish.get(name, kind) and that finish's accepts(widget): the finishes the app brings are
/// lit (lit.py, symbols.py, download.py), facet and acrylic (facet.py).
pub(crate) fn of(w: &Dict) -> Finish {
    let Some(Value::Str(name)) = w.get("finish") else {
        return Finish::Flat;
    };
    match (name.as_str(), widget::text(w, "type")) {
        ("lit", "text" | "metric") => Finish::LitText,
        ("lit", "bar") => Finish::LitBar,
        ("lit", "gauge") => Finish::LitGauge,
        // (LitIcon accepts the weather's icons alone: the others stay line icons)
        ("lit", "icon") if !WEATHER_ICONS.contains(&widget::text(w, "icon")) => Finish::Flat,
        ("lit", "icon") => Finish::LitIcon,
        ("lit", "symbol") => Finish::LitSymbol,
        ("lit", "graph") => Finish::LitGraph,
        ("lit", "downloads") => Finish::LitDownloads,
        ("facet", "gauge") => Finish::FacetGauge,
        ("facet", "graph") => Finish::FacetGraph,
        ("facet", "bar") => Finish::FacetBar,
        ("facet", "text" | "metric" | "clock" | "weather" | "countdown") => {
            Finish::Lettered(Style::Metal)
        }
        ("acrylic", "text" | "metric" | "clock" | "weather" | "countdown") => {
            Finish::Lettered(Style::Acrylic)
        }
        _ => Finish::Flat,
    }
}

/// The widget as its finish keeps it (Finish.widget, when a renderer starts): a lit text's
/// without the renderer's glow and fading, which its light and its letters' fading take the
/// place of; a lit graph's glow in its line's halo, not round the whole graph.
pub(crate) fn kept(mut w: Dict) -> Dict {
    match of(&w) {
        Finish::LitText => {
            let glow = w.get("glow").cloned().unwrap_or(Value::Float(0.0));
            let opacity = w.get("opacity").cloned().unwrap_or(Value::Float(1.0));
            w.insert("glow".into(), Value::Float(0.0));
            w.insert("opacity".into(), Value::Float(1.0));
            w.insert(LIGHT.into(), glow);
            w.insert(OPACITY.into(), opacity);
        }
        Finish::LitGraph => {
            let glow = w.get("glow").cloned().unwrap_or(Value::Float(0.0));
            w.insert("glow".into(), Value::Float(0.0));
            w.insert(HALO.into(), glow);
        }
        _ => {}
    }
    w
}

/// Where a lit text keeps its own glow and opacity.
pub(crate) const LIGHT: &str = "\0light";
pub(crate) const OPACITY: &str = "\0opacity";
/// Where a lit graph keeps its glow, for its line's halo.
pub(crate) const HALO: &str = "\0halo";
