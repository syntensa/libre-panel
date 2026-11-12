//! Texts (render/renderer.py's _draw_text, _fit, _text_piece, _letters, _laid and _glyph): a
//! text in the theme's fonts (falling back to the system's for what a font lacks), made to fit
//! its width (a smaller font, or cut with "…"), drawn whole, or letter by letter where its
//! letters are spaced or its digits of equal width.

use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use lp_pixels::{Image, Mode};
use lp_py::Dict;
use lp_text::fallback::{cursive, FallbackFont};
use lp_text::text::TextFace;
use lp_text::text::{draw, Text};

use crate::compose;
use crate::facets::{self, Glyph, Style};
use crate::finish::{self, Finish};
use crate::piece::{Failed, Piece, Result};
use crate::renderer::Renderer;
use crate::shapes::{ink, Rgba};
use crate::widget;

/// The theme's font where a widget names none, and the app's where the theme names none.
pub const DEFAULT_FONT: &str = "builtin:Barlow-Medium";
pub(crate) const DIGITS: &str = "0123456789";

pub(crate) fn failed(e: lp_text::Error) -> Failed {
    Failed(e.to_string())
}

/// The anchor of a text aligned so (_ANCHORS).
fn anchor_of(align: &str) -> &'static str {
    match align {
        "center" => "ma",
        "right" => "ra",
        _ => "la",
    }
}

/// Where a letter's ink lies in its picture (getbbox: left, top, right, bottom); that it has
/// none; or, for a finish's letter, that it is not known (all of its picture is laid).
#[derive(Clone, Copy)]
pub(crate) enum Ink {
    At((usize, usize, usize, usize)),
    Blank,
    Unknown,
}

/// The letters' pictures kept (_glyphs): by font, character, ink, fraction of a pixel (to four
/// places), pad and height; the most recently used while they hold at most GLYPH_BYTES.
#[derive(Default)]
pub(crate) struct Glyphs {
    kept: HashMap<GlyphKey, KeptGlyph>,
    bytes: usize,
    clock: u64,
}

type GlyphKey = (usize, char, [u8; 4], u64, i64, i64);

/// A letter's picture kept, where its ink lies, its bytes and when it was last used; with its
/// font, so that no other font takes its place in memory while it is kept.
struct KeptGlyph {
    _font: Rc<FallbackFont>,
    image: Image,
    ink: Ink,
    bytes: usize,
    used: u64,
}

/// What the letters' pictures may hold (GLYPH_BYTES: a digit of 60 px is about 10 kB).
const GLYPH_BYTES: usize = 4 * 1024 * 1024;

impl Glyphs {
    fn get(&mut self, key: &GlyphKey) -> Option<(Image, Ink)> {
        self.clock += 1;
        let clock = self.clock;
        self.kept.get_mut(key).map(|kept| {
            kept.used = clock;
            (kept.image.clone(), kept.ink)
        })
    }

    fn put(&mut self, key: GlyphKey, font: Rc<FallbackFont>, image: Image, ink: Ink) {
        self.clock += 1;
        let bytes = image.as_bytes().len();
        if let Some(old) = self.kept.remove(&key) {
            self.bytes -= old.bytes;
        }
        // (the least recently used go while they would hold more; the newest always stays)
        while self.bytes + bytes > GLYPH_BYTES && !self.kept.is_empty() {
            let oldest = (self.kept.iter())
                .min_by_key(|(_, kept)| kept.used)
                .map(|(key, _)| *key);
            if let Some(oldest) = oldest {
                if let Some(gone) = self.kept.remove(&oldest) {
                    self.bytes -= gone.bytes;
                }
            }
        }
        self.bytes += bytes;
        let used = self.clock;
        let kept = KeptGlyph {
            _font: font,
            image,
            ink,
            bytes,
            used,
        };
        self.kept.insert(key, kept);
    }
}

/// The finishes' letters kept: by font, character, quarter pixel and style (None: a blank).
pub(crate) type FacetGlyphs = RefCell<HashMap<(usize, char, i64, Style), Option<Rc<Glyph>>>>;

/// A text laid out letter by letter (_Letters): its layer's size, where the layer goes, the
/// box, and each letter on the layer (None: a finish's blank) with where its ink lies in its
/// picture.
pub(crate) struct Letters {
    pub(crate) size: (usize, usize),
    pub(crate) left: i64,
    pub(crate) y: i64,
    pub(crate) hit: [i64; 4],
    pub(crate) glyphs: Vec<Option<(Image, i64, i64)>>,
    pub(crate) inks: Vec<Ink>,
}

impl Renderer {
    /// font: the font of `reference` (the theme's, then the app's, where it names none) at
    /// `size` (4 to 512), made once; the app's own where it cannot be opened (warned).
    pub(crate) fn font(&self, reference: &str, size: i64) -> Result<Rc<FallbackFont>> {
        let reference = if !reference.is_empty() {
            reference
        } else if !self.theme.font.is_empty() {
            self.theme.font.as_str()
        } else {
            DEFAULT_FONT
        };
        let size = size.clamp(4, 512);
        let key = (reference.to_string(), size);
        if let Some(found) = self.fonts.borrow().get(&key) {
            return Ok(found.clone());
        }
        let open = |path: &std::path::Path| FallbackFont::open(path, size as f64, 0, None);
        let mut font = None;
        let tried = match self.env.builtin_font_path(reference) {
            Some(path) => Some(open(&path).map_err(failed)),
            None => self.theme.root.as_ref().map(|root| {
                lp_theme::model::resolve_asset(root, &lp_py::Value::from(reference))
                    .map_err(|e| Failed(e.msg.clone()))
                    .and_then(|path| open(&path).map_err(failed))
            }),
        };
        match tried {
            Some(Ok(made)) => font = Some(made),
            Some(Err(e)) => self.warn(format!(
                "font {} could not be loaded ({e}); using the default font",
                lp_py::repr(reference)
            )),
            None => {}
        }
        if font.is_none() && reference != DEFAULT_FONT {
            let path = self
                .env
                .builtin_font_path(DEFAULT_FONT)
                .ok_or_else(|| Failed("the default font is missing".into()))?;
            font = Some(open(&path).map_err(failed)?);
        }
        // (Python takes Pillow's own default font where even the app's cannot be read)
        let font = Rc::new(font.ok_or_else(|| Failed("no font could be loaded".into()))?);
        self.fonts.borrow_mut().insert(key, font.clone());
        Ok(font)
    }

    /// font.getlength(text), as Pillow measures it.
    pub(crate) fn length(font: &FallbackFont, text: &str) -> Result<f64> {
        font.getlength(text, "", None, None, None).map_err(failed)
    }

    /// _digit_cell: the widest digit's advance.
    pub(crate) fn digit_cell(font: &FallbackFont) -> Result<f64> {
        let mut widest = f64::NEG_INFINITY;
        for d in DIGITS.chars() {
            let w = Self::length(font, &d.to_string())?;
            if widest == f64::NEG_INFINITY || w > widest {
                widest = w;
            }
        }
        Ok(widest)
    }

    /// _advance: a letter's advance.
    pub(crate) fn advance(font: &FallbackFont, ch: char) -> Result<f64> {
        Self::length(font, &ch.to_string())
    }

    /// _fit: the text and the font size that keep within the widget's max_width.
    pub(crate) fn fit(&self, w: &Dict, text: &str) -> Result<(String, i64)> {
        let size = widget::int(w, "font_size", 24);
        let limit = widget::int(w, "max_width", 0);
        if limit <= 0 || text.is_empty() || text.contains('\n') {
            return Ok((text.to_string(), size));
        }
        let spacing = widget::number(w, "letter_spacing", 0.0);
        let tabular = widget::flag(w, "tabular", false);
        let joined = cursive(text); // (such text is laid out whole: see text_piece)
        let font = self.font(widget::text(w, "font"), size)?;
        let width = |candidate: &str| -> Result<f64> {
            if joined {
                return Self::length(&font, candidate);
            }
            let cell = if tabular {
                Self::digit_cell(&font)?
            } else {
                0.0
            };
            let mut advances = Vec::new();
            for ch in candidate.chars() {
                advances.push(if tabular && DIGITS.contains(ch) {
                    cell
                } else {
                    Self::advance(&font, ch)?
                });
            }
            let gaps = candidate.chars().count().saturating_sub(1) as f64;
            Ok(lp_py::sum_floats(advances) + spacing * gaps)
        };
        let measured = width(text)?;
        if measured <= limit as f64 {
            return Ok((text.to_string(), size));
        }
        if widget::text(w, "fit") == "ellipsis" {
            let mut text: Vec<char> = text.chars().collect();
            let trimmed = |t: &[char]| -> String {
                let s: String = t.iter().collect();
                s.trim_end_matches(lp_py::isspace).to_string()
            };
            while text.len() > 1 && width(&(trimmed(&text) + "…"))? > limit as f64 {
                text.pop();
            }
            return Ok((trimmed(&text) + "…", size));
        }
        if measured.is_nan() {
            return Err(Failed("cannot convert float NaN to integer".into()));
        }
        let shrunk = (size.saturating_mul(limit) as f64 / measured).trunc() as i64;
        Ok((text.to_string(), shrunk.max(6)))
    }

    /// _text_piece: the text as a piece.
    pub(crate) fn text_piece(&self, w: &Dict, text: &str, colour: &str) -> Result<Piece> {
        let (text, size) = self.fit(w, text)?;
        if let Some(letters) = self.letters(w, &text, colour, size)? {
            let mut piece = Piece::new(self.laid(&letters)?, letters.left, letters.y);
            piece.hit = Some(letters.hit);
            return Ok(piece);
        }
        let font = self.font(widget::text(w, "font"), size)?;
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let anchor = anchor_of(widget::text(w, "align"));
        let measure = Text::new(&text, font.as_ref(), "L");
        let (bx0, by0, bx1, by1) = measure
            .get_bbox((x as f64, y as f64), Some(anchor), "left")
            .map_err(failed)?;
        let (x0, y0) = (bx0.floor() as i64, by0.floor() as i64);
        let width = (bx1.ceil() as i64 - x0 + 1).max(1);
        let height = (by1.ceil() as i64 - y0 + 1).max(1);
        if width.saturating_mul(height) > crate::renderer::MOST_PIXELS {
            return Err(Failed("a layer too large".into()));
        }
        let fill = ink(self.color(Some(colour), 1.0)?.unwrap_or([0, 0, 0, 0]));
        let mut layer = Image::blank(Mode::RGBA, width as usize, height as usize);
        let written = Text::new(&text, font.as_ref(), "RGBA");
        let at = ((x - x0) as f64, (y - y0) as f64);
        draw(&mut layer, at, &written, fill, None, Some(anchor), "left").map_err(failed)?;
        Ok(Piece::new(layer, x0, y0))
    }

    /// _letters: the text laid out letter by letter, for letter spacing, equal-width digits, a
    /// finish's own letters or a clock's digits that roll or fade (laid out so in a still frame
    /// too); None where it is drawn whole. `size` is the font size the text fits in.
    pub(crate) fn letters(
        &self,
        w: &Dict,
        text: &str,
        colour: &str,
        size: i64,
    ) -> Result<Option<Letters>> {
        let font = self.font(widget::text(w, "font"), size)?;
        let colour = self.color(Some(colour), 1.0)?;
        let fill = ink(colour.unwrap_or([0, 0, 0, 0]));
        let (x, y) = (widget::int(w, "x", 0), widget::int(w, "y", 0));
        let align = widget::text(w, "align");
        let spacing = widget::number(w, "letter_spacing", 0.0);
        let tabular = widget::flag(w, "tabular", false);
        if cursive(text) || text.contains('\n') {
            return Ok(None); // (letters that join: the whole text, as it is)
        }
        let digits = tabular && text.chars().any(|c| DIGITS.contains(c));
        let rolls = widget::optional(w, "digits").unwrap_or("jump") != "jump";
        // (a finish that letters a text of one line its own way: FacetText.lettering)
        let lettered = match finish::of(w) {
            Finish::Lettered(style) if facets::usable(&font) => Some(style),
            _ => None,
        };
        if spacing == 0.0 && !digits && !rolls && lettered.is_none() {
            return Ok(None);
        }
        let cell = Self::digit_cell(&font)?;
        let mut advances = Vec::new();
        for ch in text.chars() {
            let advance = if tabular && DIGITS.contains(ch) {
                cell
            } else {
                Self::advance(&font, ch)?
            };
            advances.push(advance + spacing);
        }
        let total = lp_py::sum_floats(advances.iter().copied()) - spacing;
        let total = if total > 1.0 { total } else { 1.0 }; // (Python's max(1.0, x))
        let (ascent, descent) = font.font.getmetrics();
        let start = match align {
            "center" => x as f64 - total / 2.0,
            "right" => x as f64 - total,
            _ => x as f64,
        };
        let margin = size.div_euclid(6).max(2);
        let (lw, lh) = (total.ceil() as i64 + 2 * margin, ascent + descent);
        let mut glyphs = Vec::new();
        let mut inks = Vec::new();
        let mut pen = margin as f64;
        for (ch, advance) in text.chars().zip(&advances) {
            let at = if tabular && DIGITS.contains(ch) {
                pen + (cell - Self::advance(&font, ch)?) / 2.0
            } else {
                pen
            };
            if let Some(style) = lettered {
                // (the finish's letter, at the pen's fraction of a pixel: `at % 1.0`)
                let placed = self.facet_glyph(&font, ch, colour, at - at.floor(), style)?;
                glyphs.push(placed.map(|(patch, dx, dy)| (patch, at.floor() as i64 + dx, dy)));
                inks.push(Ink::Unknown);
            } else {
                let (glyph, gx, glyph_ink) = self.glyph(&font, ch, fill, at, margin, lh)?;

                glyphs.push(Some((glyph, gx, 0)));
                inks.push(glyph_ink);
            }
            pen += advance;
        }
        let left = start.floor() as i64 - margin;
        let hit = [left + margin, y, total.ceil() as i64, lh];
        if lw < 0 || lh < 0 {
            return Err(Failed("Width and height must be >= 0".into()));
        }
        Ok(Some(Letters {
            size: (lw as usize, lh as usize),
            left,
            y,
            hit,
            glyphs,
            inks,
        }))
    }

    /// _glyph: `ch` drawn at `at` (its fraction of a pixel counts) on a picture `pad` px wider on
    /// each side, where its left edge goes, and where its ink lies in it (None: none): kept, so
    /// the next text with that character in that place costs no rendering (by the fraction to
    /// four places, as Python keeps it).
    fn glyph(
        &self,
        font: &Rc<FallbackFont>,
        ch: char,
        fill: [u8; 4],
        at: f64,
        pad: i64,
        h: i64,
    ) -> Result<(Image, i64, Ink)> {
        let whole = at.floor();
        let fraction = lp_py::round_digits(at - whole, 4);
        let key = (
            Rc::as_ptr(font) as usize,
            ch,
            fill,
            fraction.to_bits(),
            pad,
            h,
        );
        if let Some((image, ink)) = self.glyphs.borrow_mut().get(&key) {
            return Ok((image, whole as i64 - pad, ink));
        }
        let (image, left, ink) = self.drawn_glyph(font, ch, fill, at, pad, h)?;
        self.glyphs
            .borrow_mut()
            .put(key, Rc::clone(font), image.clone(), ink);
        Ok((image, left, ink))
    }

    /// _glyph, drawn.
    fn drawn_glyph(
        &self,
        font: &FallbackFont,
        ch: char,
        fill: [u8; 4],
        at: f64,
        pad: i64,
        h: i64,
    ) -> Result<(Image, i64, Ink)> {
        let whole = at.floor();
        let text = ch.to_string();
        let width = Self::length(font, &text)?.ceil() as i64 + 2 * pad + 1;
        if width < 0 || h < 0 {
            return Err(Failed("Width and height must be >= 0".into()));
        }
        let mut image = Image::blank(Mode::RGBA, width as usize, h as usize);
        let written = Text::new(&text, font, "RGBA");
        let start = (at - whole + pad as f64, 0.0);
        draw(&mut image, start, &written, fill, None, Some("la"), "left").map_err(failed)?;
        let found = image.getbbox(true).map_or(Ink::Blank, Ink::At);
        Ok((image, whole as i64 - pad, found))
    }

    /// facets.glyph: a finish's letter `ch` at `frac` of a pixel from the pen's (rounded to a
    /// quarter), in `fill`, and where it goes from the pen's pixel and the line's top; None for a
    /// blank. Its geometry built once per font, character, quarter and style.
    fn facet_glyph(
        &self,
        font: &Rc<FallbackFont>,
        ch: char,
        fill: Option<Rgba>,
        frac: f64,
        style: Style,
    ) -> Result<Option<(Image, i64, i64)>> {
        let (quarter, shift) = facets::quarter_of(frac);
        let key = (Rc::as_ptr(font) as usize, ch, quarter, style);
        let kept = self.facet_glyphs.borrow().get(&key).cloned();
        let geometry = match kept {
            Some(geometry) => geometry,
            None => {
                let built = facets::glyph_geometry(font, ch, quarter, style)?.map(Rc::new);
                self.facet_glyphs.borrow_mut().insert(key, built.clone());
                built
            }
        };
        let Some(glyph) = geometry else {
            return Ok(None);
        };
        // (Python's tuple(fill) of a colour that is none)
        let fill = fill.ok_or_else(|| Failed("'NoneType' object is not iterable".into()))?;
        let patch = facets::coloured(&glyph, fill, self.world)?;
        let (dx, dy) = facets::placed(&glyph, shift);
        Ok(Some((patch, dx, dy)))
    }

    /// _laid: the letters laid on a clear layer one after the other: one whose ink lies over
    /// no ink laid before goes on as it is (the same pixels), one whose ink meets ink is laid
    /// over it.
    fn laid(&self, letters: &Letters) -> Result<Image> {
        let (w, h) = letters.size;
        let mut layer = Image::blank(Mode::RGBA, w, h);
        let mut inked: Vec<(i64, i64, i64, i64)> = Vec::new();
        for (placed, found) in letters.glyphs.iter().zip(&letters.inks) {
            let Some((image, x, y)) = placed else {
                continue; // (a finish's blank)
            };
            let (x, y) = (*x, *y);
            let found = match found {
                Ink::At(found) => found,
                Ink::Blank => continue, // (a letter without ink)
                Ink::Unknown => {
                    // (a finish's letter: where it has ink is not known, so all of it)
                    compose::paste(&mut layer, image, x, y, false)?;
                    let (w, h) = (image.width() as i64, image.height() as i64);
                    inked.push((x, y, x + w, y + h));
                    continue;
                }
            };
            let (a, b) = (x + found.0 as i64, y + found.1 as i64);
            let (c, d) = (x + found.2 as i64, y + found.3 as i64);
            let inside = a >= 0 && b >= 0 && c <= w as i64 && d <= h as i64;
            let meets = inked
                .iter()
                .any(|&(p, q, r, t)| p < c && a < r && q < d && b < t);
            if inside && !meets {
                let ink_part = image.crop(
                    found.0 as i64,
                    found.1 as i64,
                    found.2 as i64,
                    found.3 as i64,
                );
                layer.paste(&ink_part, a, b, None)?;
            } else {
                compose::paste(&mut layer, image, x, y, false)?;
            }
            inked.push((a, b, c, d));
        }
        Ok(layer)
    }

    /// _draw_text: a title, in the panel's language where it asks, in capitals where it asks.
    pub(crate) fn text_of(&self, w: &Dict) -> String {
        let text = widget::text(w, "text");
        let text = if widget::flag(w, "translate", false) {
            lp_i18n::t(text)
        } else {
            text.to_string()
        };
        if widget::flag(w, "uppercase", false) {
            lp_py::upper(&text)
        } else {
            text
        }
    }
}
