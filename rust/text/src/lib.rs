//! lp-text: letters as Pillow 13.0 draws them (docs/RUST.md, stage R2), to the bit. A [`Font`]
//! is Pillow's FreeTypeFont: FreeType's face at Pillow's size, laid out as `_imagingft.c` lays
//! text out (its basic layout here: FreeType's advances, and its kerning as Pillow adds it, a
//! 64th of what the font says), measured as it measures (`getlength`, `getbbox` and their
//! anchors) and drawn as it draws (`getmask2`: FreeType's bitmaps blended into a mask, stroked
//! with FreeType's stroker, colour glyphs unpremultiplied). [`text`] is ImageDraw's text on top
//! (ImageText's lines, anchors and alignment, the ink through the mask).
//!
//! FreeType itself stays FreeType (a library of C, loaded where the app runs: [`ft`]).

mod ft;
mod raqm;
mod render;
pub mod text;

pub use raqm::set_locale_from_environment;

use std::ffi::{c_int, c_ulong, CStr};
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use lp_pixels::Image;

/// ImageFont.MAX_STRING_LENGTH: longer texts are refused.
pub const MAX_STRING_LENGTH: usize = 1_000_000;

/// What kind of error Pillow raises for it (the app catches some kinds and not others).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// OSError: FreeType's errors, a bitmap of a kind Pillow cannot blend
    Os,
    /// ValueError: a bad anchor, size or string, an image too large
    Value,
    /// KeyError: direction, language or features asked of the basic layout
    Key,
    /// DecompressionBombError: a mask larger than Pillow makes
    Bomb,
    /// RuntimeError: a text libraqm cannot lay out (more than one paragraph)
    Runtime,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error {
    pub kind: Kind,
    pub message: String,
}

impl Error {
    pub(crate) fn new(kind: Kind, message: impl Into<String>) -> Error {
        Error {
            kind,
            message: message.into(),
        }
    }

    fn freetype(code: c_int) -> Error {
        Error::new(Kind::Os, ft::message(code))
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for Error {}

impl From<lp_pixels::Error> for Error {
    fn from(e: lp_pixels::Error) -> Error {
        Error::new(Kind::Value, e.0)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

/// ImageFont.Layout.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Layout {
    Basic,
    Raqm,
}

/// core.HAVE_RAQM: whether text can be laid out as libraqm lays it out (HarfBuzz and FriBiDi
/// found where the app runs).
pub fn have_raqm() -> bool {
    raqm::available()
}

/// The HarfBuzz that shapes the text, where there is one.
pub fn harfbuzz_version() -> Option<String> {
    raqm::harfbuzz_version()
}

/// The FreeType that draws the letters: (major, minor, patch).
pub fn freetype_version() -> Result<(i32, i32, i32)> {
    let ft = ft::ft().map_err(|e| Error::new(Kind::Os, e))?;
    Ok(ft.version)
}

/// One glyph of a laid-out text, in 26.6 (GlyphInfo).
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct GlyphInfo {
    pub index: u32,
    pub x_offset: i32,
    pub x_advance: i32,
    pub y_offset: i32,
    pub y_advance: i32,
    #[allow(dead_code)] // (the character a glyph is of; libraqm's clusters, as Pillow keeps them)
    pub cluster: u32,
}

/// C's long as a 64-bit number (FreeType's FT_Long and FT_Pos: 32 bits on Windows).
#[allow(clippy::unnecessary_cast)]
pub(crate) fn long(x: std::ffi::c_long) -> i64 {
    x as i64
}

/// PIXEL: a 26.6 coordinate rounded to the nearest whole pixel.
pub(crate) fn pixel(x: i64) -> i64 {
    ((x + 32) & -64) >> 6
}

/// FreeType's load flags for a mode: mono hinting for "1", colour for "RGBA".
pub(crate) fn load_flags(mode: &str) -> c_int {
    let mut flags = ft::LOAD_DEFAULT;
    if mode == "1" {
        flags |= ft::LOAD_TARGET_MONO;
    }
    if mode == "RGBA" {
        flags |= ft::LOAD_COLOR;
    }
    flags
}

/// The box a text's ink and pen line lie in, and the anchor's point in it
/// (bounding_box_and_anchors): getsize's ((width, height), (x_offset, y_offset)).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Extent {
    pub width: i64,
    pub height: i64,
    pub x_offset: i32,
    pub y_offset: i64,
}

#[derive(Clone)]
enum Source {
    Path(PathBuf),
    Bytes(Arc<[u8]>),
}

/// ImageFont.FreeTypeFont: a face of a font file at a size.
pub struct Font {
    face: ft::Face,
    source: Source,
    size: f64,
    index: i64,
    encoding: String,
    layout: Layout,
}

// SAFETY: the face is this Font's alone, used by whoever holds the Font (FreeType's faces are
// for one thread at a time, as Pillow's are)
unsafe impl Send for Font {}

impl Drop for Font {
    fn drop(&mut self) {
        if let Ok(ft) = ft::ft() {
            ft.done(self.face);
        }
    }
}

impl fmt::Debug for Font {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let source = match &self.source {
            Source::Path(path) => path.display().to_string(),
            Source::Bytes(bytes) => format!("{} bytes", bytes.len()),
        };
        write!(f, "Font({source}, {}, {})", self.size, self.index)
    }
}

/// The size as Python writes it in Pillow's message: a whole number as the int it mostly is.
fn size_text(size: f64) -> String {
    if size.fract() == 0.0 && size.abs() < 1e16 {
        format!("{size:.0}")
    } else {
        format!("{size}")
    }
}

impl Font {
    /// FreeTypeFont(path, size, index, encoding, layout_engine): `layout` None is Pillow's
    /// default (libraqm where there is one); libraqm asked for where there is none is the basic
    /// layout.
    pub fn open(
        path: impl AsRef<Path>,
        size: f64,
        index: i64,
        encoding: &str,
        layout: Option<Layout>,
    ) -> Result<Font> {
        let path = path.as_ref();
        // FreeType opens no file whose name is not ASCII on Windows: Pillow reads it first
        if cfg!(windows) && !path.to_str().is_some_and(|p| p.is_ascii()) {
            let bytes = std::fs::read(path).map_err(|e| Error::new(Kind::Os, e.to_string()))?;
            let mut font = Font::from_bytes(bytes, size, index, encoding, layout)?;
            font.source = Source::Path(path.to_path_buf());
            return Ok(font);
        }
        Font::make(
            Source::Path(path.to_path_buf()),
            size,
            index,
            encoding,
            layout,
        )
    }

    /// FreeTypeFont(BytesIO(bytes), size, index, encoding, layout_engine).
    pub fn from_bytes(
        bytes: Vec<u8>,
        size: f64,
        index: i64,
        encoding: &str,
        layout: Option<Layout>,
    ) -> Result<Font> {
        Font::make(Source::Bytes(bytes.into()), size, index, encoding, layout)
    }

    fn make(
        source: Source,
        size: f64,
        index: i64,
        encoding: &str,
        layout: Option<Layout>,
    ) -> Result<Font> {
        if size <= 0.0 || size.is_nan() {
            let message = format!("font size must be greater than 0, not {}", size_text(size));
            return Err(Error::new(Kind::Value, message));
        }
        let layout = match layout {
            Some(Layout::Raqm) | None if have_raqm() => Layout::Raqm,
            _ => Layout::Basic,
        };
        let ft = ft::ft().map_err(|e| Error::new(Kind::Os, e))?;
        let face = match &source {
            Source::Path(path) => {
                let name = c_path(path)?;
                ft.face(Some(&name), None, index, size as f32, encoding)
            }
            Source::Bytes(bytes) => ft.face(None, Some(bytes), index, size as f32, encoding),
        }
        .map_err(Error::freetype)?;
        Ok(Font {
            face,
            source,
            size,
            index,
            encoding: encoding.to_string(),
            layout,
        })
    }

    /// font_variant: this font at another size (or index, encoding, layout).
    pub fn variant(
        &self,
        size: Option<f64>,
        index: Option<i64>,
        encoding: Option<&str>,
        layout: Option<Layout>,
    ) -> Result<Font> {
        Font::make(
            self.source.clone(),
            size.unwrap_or(self.size),
            index.unwrap_or(self.index),
            encoding.unwrap_or(&self.encoding),
            Some(layout.unwrap_or(self.layout)),
        )
    }

    pub fn size(&self) -> f64 {
        self.size
    }

    pub fn index(&self) -> i64 {
        self.index
    }

    pub fn layout(&self) -> Layout {
        self.layout
    }

    /// The file the font was opened from (None for one made of bytes).
    pub fn path(&self) -> Option<&Path> {
        match &self.source {
            Source::Path(path) => Some(path),
            Source::Bytes(_) => None,
        }
    }

    fn face(&self) -> &ft::FaceRec {
        // SAFETY: a live face, this Font's
        unsafe { &*self.face }
    }

    fn metrics(&self) -> &ft::SizeMetrics {
        // SAFETY: FT_Request_Size gave the face its size
        unsafe { &(*self.face().size).metrics }
    }

    /// getname: the family and the style, as the font names them.
    pub fn getname(&self) -> (Option<String>, Option<String>) {
        let text = |p: *const std::ffi::c_char| {
            // SAFETY: FreeType's names are C strings that live with the face
            (!p.is_null()).then(|| unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned())
        };
        (text(self.face().family_name), text(self.face().style_name))
    }

    /// getmetrics: the ascent and the descent at this size, in pixels.
    pub fn getmetrics(&self) -> (i64, i64) {
        let m = self.metrics();
        (pixel(long(m.ascender)), -pixel(long(m.descender)))
    }

    /// font.height, font.x_ppem, font.y_ppem and font.glyphs.
    pub fn height(&self) -> i64 {
        pixel(long(self.metrics().height))
    }

    pub fn ppem(&self) -> (u16, u16) {
        (self.metrics().x_ppem, self.metrics().y_ppem)
    }

    pub fn glyphs(&self) -> i64 {
        long(self.face().num_glyphs)
    }

    /// text_layout: the text's glyphs, laid out as Pillow lays them out.
    pub(crate) fn layout_text(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
    ) -> Result<Vec<GlyphInfo>> {
        match self.layout {
            Layout::Raqm => raqm::layout(self.face, text, direction, features, language),
            Layout::Basic => self.layout_basic(text, mode, direction, features, language),
        }
    }

    /// text_layout_fallback: each character's glyph with FreeType's advance at this size; the
    /// kerning of a pair, where the font has it, added to the advance of the first of them in
    /// whole pixels (to a 26.6 number: a 64th of it, as Pillow does).
    fn layout_basic(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
    ) -> Result<Vec<GlyphInfo>> {
        if features.is_some() || direction.is_some() || language.is_some() {
            return Err(Error::new(
                Kind::Key,
                "setting text direction, language or font features is not supported without \
                 libraqm",
            ));
        }
        let ft = ft::ft().map_err(|e| Error::new(Kind::Os, e))?;
        let kerning = self.face().face_flags & ft::FACE_FLAG_KERNING != 0;
        let flags = load_flags(mode);
        let mut glyphs: Vec<GlyphInfo> = Vec::with_capacity(text.len());
        let mut last = 0;
        for ch in text.chars() {
            let code = u32::from(ch);
            // SAFETY: a live face
            let index = unsafe { (ft.get_char_index)(self.face, c_ulong::from(code)) };
            // SAFETY: a live face
            let error = unsafe { (ft.load_glyph)(self.face, index, flags) };
            if error != 0 {
                return Err(Error::freetype(error));
            }
            if kerning && last != 0 && index != 0 {
                let mut delta = ft::Vector::default();
                // SAFETY: a live face; delta is filled
                let found = unsafe {
                    (ft.get_kerning)(self.face, last, index, ft::KERNING_DEFAULT, &mut delta)
                };
                if found == 0 {
                    let before = glyphs.last_mut().expect("the glyph before");
                    before.x_advance = before.x_advance.wrapping_add(pixel(long(delta.x)) as i32);
                    before.y_advance = before.y_advance.wrapping_add(pixel(long(delta.y)) as i32);
                }
            }
            // SAFETY: FT_Load_Glyph filled the face's slot
            let advance = unsafe { (*self.face().glyph).metrics.hori_advance };
            glyphs.push(GlyphInfo {
                index,
                x_offset: 0,
                x_advance: advance as i32,
                y_offset: 0,
                y_advance: 0,
                cluster: code,
            });
            last = index;
        }
        Ok(glyphs)
    }

    /// bounding_box_and_anchors: the box of the glyphs' boxes (FreeType's, grid-fitted) and
    /// of the pen's line from 0 to its end, and where the anchor puts the text's origin.
    pub(crate) fn extent(
        &self,
        anchor: Option<&str>,
        horizontal: bool,
        glyphs: &[GlyphInfo],
        flags: c_int,
    ) -> Result<Extent> {
        let ft = ft::ft().map_err(|e| Error::new(Kind::Os, e))?;
        let (mut position, mut x_min, mut x_max, mut y_min, mut y_max) = (0i64, 0, 0, 0, 0);
        for g in glyphs {
            let (px, py);
            if horizontal {
                px = pixel(position + i64::from(g.x_offset)) as i32;
                py = pixel(i64::from(g.y_offset)) as i32;
                position += i64::from(g.x_advance);
                x_max = x_max.max(pixel(position));
            } else {
                px = pixel(i64::from(g.x_offset)) as i32;
                py = pixel(position + i64::from(g.y_offset)) as i32;
                position += i64::from(g.y_advance);
                y_min = y_min.min(pixel(position));
            }
            // SAFETY: a live face
            let error = unsafe { (ft.load_glyph)(self.face, g.index, flags) };
            if error != 0 {
                return Err(Error::freetype(error));
            }
            let mut glyph: ft::Glyph = std::ptr::null_mut();
            // SAFETY: the face's slot, just loaded; glyph is filled
            let error = unsafe { (ft.get_glyph)(self.face().glyph, &mut glyph) };
            if error != 0 {
                return Err(Error::freetype(error));
            }
            let mut bbox = ft::BBox::default();
            // SAFETY: a glyph FT_Get_Glyph made, done here
            unsafe {
                (ft.glyph_get_cbox)(glyph, ft::GLYPH_BBOX_PIXELS, &mut bbox);
                (ft.done_glyph)(glyph);
            }
            x_max = x_max.max(long(bbox.x_max) + i64::from(px));
            x_min = x_min.min(long(bbox.x_min) + i64::from(px));
            y_max = y_max.max(long(bbox.y_max) + i64::from(py));
            y_min = y_min.min(long(bbox.y_min) + i64::from(py));
        }
        let anchor = anchor.unwrap_or(if horizontal { "la" } else { "lt" });
        let bad = || {
            Err(Error::new(
                Kind::Value,
                format!("bad anchor specified: {anchor}"),
            ))
        };
        let a = anchor.as_bytes();
        if a.len() != 2 {
            return bad();
        }
        let (mut x_anchor, mut y_anchor) = (0i64, 0i64);
        if !glyphs.is_empty() {
            let m = self.metrics();
            let (ascender, descender) = (long(m.ascender), long(m.descender));
            if horizontal {
                x_anchor = match a[0] {
                    b'l' => 0,
                    b'm' => pixel(position / 2),
                    b'r' => pixel(position),
                    _ => return bad(),
                };
                y_anchor = match a[1] {
                    b'a' => pixel(ascender),
                    b't' => y_max,
                    b'm' => pixel((ascender + descender) / 2),
                    b's' => 0,
                    b'b' => y_min,
                    b'd' => pixel(descender),
                    _ => return bad(),
                };
            } else {
                x_anchor = match a[0] {
                    b'l' => x_min,
                    b'm' => (x_min + x_max) / 2,
                    b'r' => x_max,
                    b's' => 0,
                    _ => return bad(),
                };
                y_anchor = match a[1] {
                    b't' => 0,
                    b'm' => pixel(position / 2),
                    b'b' => pixel(position),
                    _ => return bad(),
                };
            }
        }
        Ok(Extent {
            width: x_max - x_min,
            height: y_max - y_min,
            x_offset: (-x_anchor + x_min) as i32,
            y_offset: y_anchor - y_max,
        })
    }

    /// font.getlength: the advance of the whole text along its line, in 26.6.
    pub fn length_units(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
    ) -> Result<i32> {
        let horizontal = direction != Some("ttb");
        let glyphs = self.layout_text(text, mode, direction, features, language)?;
        Ok(glyphs.iter().fold(0i32, |length, g| {
            if horizontal {
                length.wrapping_add(g.x_advance)
            } else {
                length.wrapping_sub(g.y_advance)
            }
        }))
    }

    /// font.getsize: ((width, height), (x_offset, y_offset)) of the text from the anchor.
    pub fn getsize(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        anchor: Option<&str>,
    ) -> Result<((i64, i64), (i32, i64))> {
        let horizontal = direction != Some("ttb");
        let glyphs = self.layout_text(text, mode, direction, features, language)?;
        let e = self.extent(anchor, horizontal, &glyphs, load_flags(mode))?;
        Ok(((e.width, e.height), (e.x_offset, e.y_offset)))
    }

    /// FreeTypeFont.getlength: the text's advance in pixels (a fraction where it is one).
    pub fn getlength(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
    ) -> Result<f64> {
        check_length(text)?;
        let units = self.length_units(text, mode, direction, features, language)?;
        Ok(f64::from(units) / 64.0)
    }

    /// FreeTypeFont.getbbox: (left, top, right, bottom) of the text from the anchor, the
    /// stroke's width around it.
    #[allow(clippy::too_many_arguments)]
    pub fn getbbox(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        stroke_width: f64,
        anchor: Option<&str>,
    ) -> Result<(f64, f64, f64, f64)> {
        check_length(text)?;
        let ((w, h), (x, y)) = self.getsize(text, mode, direction, features, language, anchor)?;
        let (left, top) = (f64::from(x) - stroke_width, y as f64 - stroke_width);
        let (width, height) = (w as f64 + 2.0 * stroke_width, h as f64 + 2.0 * stroke_width);
        Ok((left, top, left + width, top + height))
    }

    /// FreeTypeFont.getmask2: the text drawn into a mask (L, or for "RGBA" the colours in RGB
    /// and the mask in A), and where its corner lies from the anchor. `ink` is the colour's
    /// int as ImageDraw has it (for the colour glyphs' foreground), `start` the fraction of a
    /// pixel the text starts at.
    #[allow(clippy::too_many_arguments)]
    pub fn getmask2(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        stroke_width: f64,
        anchor: Option<&str>,
        ink: i64,
        start: (f64, f64),
        stroke_filled: bool,
    ) -> Result<(Image, (i32, i64))> {
        check_length(text)?;
        render::render(
            self,
            render::Ask {
                text,
                mode,
                direction,
                features,
                language,
                stroke_width: stroke_width as f32,
                stroke_filled,
                anchor,
                ink,
                start: (start.0 as f32, start.1 as f32),
            },
        )
    }
}

/// _string_length_check.
fn check_length(text: &str) -> Result<()> {
    if text.len() > MAX_STRING_LENGTH && text.chars().count() > MAX_STRING_LENGTH {
        return Err(Error::new(Kind::Value, "too many characters in string"));
    }
    Ok(())
}

/// The file's name as FreeType opens it: the bytes of the name (UTF-8 on Windows, where the
/// name is ASCII by then).
fn c_path(path: &Path) -> Result<std::ffi::CString> {
    #[cfg(unix)]
    let bytes = {
        use std::os::unix::ffi::OsStrExt;
        path.as_os_str().as_bytes().to_vec()
    };
    #[cfg(not(unix))]
    let bytes = path.to_string_lossy().as_bytes().to_vec();
    std::ffi::CString::new(bytes).map_err(|_| Error::new(Kind::Value, "embedded null byte"))
}
