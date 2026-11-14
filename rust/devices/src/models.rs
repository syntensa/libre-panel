//! The catalog of known panels (devices/models.py): sizes, native resolutions, USB ids, what
//! their driver can do. Native resolution is given in portrait, as the panels report it.

use lp_py::{Dict, Value};

pub use crate::table::{MODELS, PROTOCOLS};

/// A panel's size in inches as the table gives it: Python keeps 5 an int and 8.8 a float, and
/// writes them so (to_dict: `5`, `8.8`).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Inch {
    Int(i64),
    Float(f64),
}

impl Inch {
    pub fn get(self) -> f64 {
        match self {
            Inch::Int(n) => n as f64,
            Inch::Float(f) => f,
        }
    }

    fn value(self) -> Value {
        match self {
            Inch::Int(n) => n.into(),
            Inch::Float(f) => f.into(),
        }
    }
}

/// One panel model (PanelModel).
#[derive(Debug)]
pub struct PanelModel {
    pub id: &'static str,
    pub vendor: &'static str,
    pub name: &'static str,
    pub size_inch: Inch,
    pub native_width: i64,
    pub native_height: i64,
    pub protocol: &'static str,
    /// "rect" or "round"
    pub shape: &'static str,
    pub usb_ids: &'static [(u16, u16)],
    pub serial_numbers: &'static [&'static str],
    /// "supported", "unverified" or "planned"
    pub driver: &'static str,
    pub notes: &'static str,
    pub limits: &'static str,
    /// pixels of the framebuffer (native, portrait) behind the bezel: top, right, bottom, left
    pub hidden: [i64; 4],
    /// the strips a portrait frame keeps clear at its edges, where an owner saw them otherwise
    pub portrait_clear: Option<[i64; 4]>,
    pub video: bool,
    pub video_range: &'static str,
    pub video_range_seen: bool,
}

const EDGES: [&str; 4] = ["top", "right", "bottom", "left"];

impl PanelModel {
    /// The frame's size in "portrait" or "landscape".
    pub fn size(&self, orientation: &str) -> (i64, i64) {
        let (short, long) = if self.native_width <= self.native_height {
            (self.native_width, self.native_height)
        } else {
            (self.native_height, self.native_width)
        };
        if orientation == "landscape" {
            (long, short)
        } else {
            (short, long)
        }
    }

    /// The hidden strips at the edges of a frame in `orientation` (top, right, bottom, left).
    pub fn hidden_edges(&self, orientation: &str) -> [i64; 4] {
        let [top, right, bottom, left] = self.hidden;
        if orientation == "landscape" {
            return [right, bottom, left, top];
        }
        if let Some(clear) = self.portrait_clear {
            return clear;
        }
        [bottom, left, top, right]
    }

    /// `Turing 2.1" round`: the name, the size as Python's `:g` writes it, the shape.
    pub fn label(&self) -> String {
        let shape = if self.shape == "round" { " round" } else { "" };
        format!(
            "{} {}\"{shape}",
            self.name,
            lp_py::format_g(self.size_inch.get(), 6)
        )
    }

    /// to_dict(): every field, the label, the USB ids as "1cbe:0016", the frame sizes, the
    /// hidden strips by orientation and the protocol's name.
    pub fn to_dict(&self) -> Value {
        let mut d = Dict::default();
        let strs = |items: &[&str]| Value::List(items.iter().map(|&s| s.into()).collect());
        let ints = |items: &[i64]| Value::List(items.iter().map(|&n| n.into()).collect());
        let edges = |values: [i64; 4]| {
            Value::Dict(
                EDGES
                    .iter()
                    .zip(values)
                    .map(|(&e, v)| (e.to_string(), v.into()))
                    .collect(),
            )
        };
        d.insert("id".into(), self.id.into());
        d.insert("vendor".into(), self.vendor.into());
        d.insert("name".into(), self.name.into());
        d.insert("size_inch".into(), self.size_inch.value());
        d.insert("native_width".into(), self.native_width.into());
        d.insert("native_height".into(), self.native_height.into());
        d.insert("protocol".into(), self.protocol.into());
        d.insert("shape".into(), self.shape.into());
        let usb = self
            .usb_ids
            .iter()
            .map(|(v, p)| Value::from(format!("{v:04x}:{p:04x}")));
        d.insert("usb_ids".into(), Value::List(usb.collect()));
        d.insert("serial_numbers".into(), strs(self.serial_numbers));
        d.insert("driver".into(), self.driver.into());
        d.insert("notes".into(), self.notes.into());
        d.insert("limits".into(), self.limits.into());
        let hidden: Dict = ["landscape", "portrait"]
            .iter()
            .map(|&o| (o.to_string(), edges(self.hidden_edges(o))))
            .collect();
        d.insert("hidden".into(), Value::Dict(hidden));
        d.insert(
            "portrait_clear".into(),
            self.portrait_clear.map_or(Value::None, |c| ints(&c)),
        );
        d.insert("video".into(), self.video.into());
        d.insert("video_range".into(), self.video_range.into());
        d.insert("video_range_seen".into(), self.video_range_seen.into());
        d.insert("label".into(), self.label().into());
        let (lw, lh) = self.size("landscape");
        let (pw, ph) = self.size("portrait");
        d.insert("landscape".into(), ints(&[lw, lh]));
        d.insert("portrait".into(), ints(&[pw, ph]));
        let protocol = PROTOCOLS.iter().find(|(k, _)| *k == self.protocol);
        d.insert(
            "protocol_name".into(),
            protocol.map_or(self.protocol, |(_, name)| name).into(),
        );
        Value::Dict(d)
    }
}

/// find_model: the model of this id, if there is one.
pub fn find_model(id: &str) -> Option<&'static PanelModel> {
    MODELS.iter().find(|m| m.id == id)
}

/// orientation_of: "landscape" for a frame wider than high, else "portrait".
pub fn orientation_of(width: i64, height: i64) -> &'static str {
    if width > height {
        "landscape"
    } else {
        "portrait"
    }
}

/// model_for_size: the first model whose native resolution is this, either way round.
pub fn model_for_size(width: i64, height: i64) -> Option<&'static PanelModel> {
    let wanted = (width.min(height), width.max(height));
    MODELS.iter().find(|m| {
        (
            m.native_width.min(m.native_height),
            m.native_width.max(m.native_height),
        ) == wanted
    })
}

/// models_for_usb: the models a device of these USB ids can be (several serial panels share a
/// bridge chip); a serial number narrows them down where one matches.
pub fn models_for_usb(vid: u16, pid: u16, serial: Option<&str>) -> Vec<&'static PanelModel> {
    let matches: Vec<&PanelModel> = MODELS
        .iter()
        .filter(|m| m.usb_ids.contains(&(vid, pid)))
        .collect();
    if let Some(serial) = serial.filter(|s| !s.is_empty()) {
        let exact: Vec<&PanelModel> = matches
            .iter()
            .copied()
            .filter(|m| m.serial_numbers.contains(&serial))
            .collect();
        if !exact.is_empty() {
            return exact;
        }
    }
    matches
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_model_s_label_and_sizes() {
        let turing = find_model("turing-2.1").expect("the 2.1\" round");
        assert_eq!(turing.label(), "Turing 2.1\" round");
        assert_eq!(turing.size("landscape"), (480, 480));
        assert_eq!(orientation_of(1920, 480), "landscape");
        assert!(find_model("nope").is_none());
    }
}
