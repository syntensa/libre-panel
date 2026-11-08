//! Libre Panel's renderer (render/ of the Python app, docs/RUST.md R4): the frames of a theme,
//! widget by widget, to the bit; so far the texts readings become, the ground and the cards.

mod agent;
mod analog;
mod bar;
mod calendar;
pub mod compose;
pub mod countdown;
pub mod cover;
pub mod download;
pub mod effects;
pub mod facets;
pub mod fan;
mod finish;
pub mod formatting;
mod gauge;
mod graph;
pub mod icons;
mod image;
mod light;
mod list;
mod lit;
mod metric;
pub mod moon;
pub mod np;
pub mod piece;
mod rect;
pub mod renderer;
pub mod shapes;
mod slot;
pub mod snapshot;
mod sunpath;
pub mod symbols;
mod text;
pub mod weather_art;
pub mod widget;

pub use graph::still_range as graph_range;
pub use list::forget_fans_run;
pub use piece::{Failed, Piece};
pub use renderer::{Options, Renderer, Unported};
pub use snapshot::{Moment, Reading, Snapshot};
