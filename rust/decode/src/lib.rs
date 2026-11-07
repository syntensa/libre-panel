//! Pictures from their files as Pillow 13.0 decodes them (docs/RUST.md, stage R1): each in the
//! mode Pillow opens it in, with its palette, its transparency and its EXIF orientation as
//! Pillow reads them, and converted to RGBA, RGB or L as Image.convert converts it (the app
//! converts every picture it opens, most to RGBA).

mod exif;
mod gif;
mod jpeg;
mod png;
mod webp;

pub use exif::orientation;
pub use gif::Frame;
/// A picture saved as a JPEG as Pillow saves it (the panels that take JPEGs).
pub use jpeg::save as save_jpeg;

use lp_pixels::{thumbnail_size, Filter, Image, Mode};
use std::fmt;

/// What Pillow would refuse, with its reason.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error(pub String);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

pub type Result<T> = std::result::Result<T, Error>;

pub(crate) fn error<T>(text: impl Into<String>) -> Result<T> {
    Err(Error(text.into()))
}

/// The mode Pillow opens a picture in (of those its decoders give the app).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// "1": one value a pixel, 0 or 255
    Bilevel,
    L,
    /// "LA": a grey and an alpha a pixel
    LA,
    /// "P": an index into the palette a pixel
    P,
    RGB,
    RGBA,
    /// "I;16": a 16-bit grey a pixel (kept as two bytes, little end first)
    I16,
    /// "CMYK" (a JPEG's of four layers)
    Cmyk,
}

impl Kind {
    /// The bytes a pixel takes here.
    pub fn bytes(self) -> usize {
        match self {
            Kind::Bilevel | Kind::L | Kind::P => 1,
            Kind::LA | Kind::I16 => 2,
            Kind::RGB => 3,
            Kind::RGBA | Kind::Cmyk => 4,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Kind::Bilevel => "1",
            Kind::L => "L",
            Kind::LA => "LA",
            Kind::P => "P",
            Kind::RGB => "RGB",
            Kind::RGBA => "RGBA",
            Kind::I16 => "I;16",
            Kind::Cmyk => "CMYK",
        }
    }
}

/// A picture's info["transparency"] as Pillow reads it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Transparency {
    /// P: the one index that is clear
    Index(u32),
    /// P: an alpha for each of the first indexes
    Alphas(Vec<u8>),
    /// 1, L, I;16: the grey that is clear (only its low byte counts in convert_transparent)
    Grey(u32),
    /// RGB: the colour that is clear (the same)
    Colour(u32, u32, u32),
}

/// info["exif"] as Pillow keeps it: bytes (eXIf, or a tEXt chunk named "exif"), or text (a
/// zTXt or iTXt named so), on which getexif fails unless it is empty.
#[derive(Clone, Debug)]
pub enum Exif {
    Bytes(Vec<u8>),
    Text {
        empty: bool,
    },
    /// a JPEG's EXIF that _read_dpi_from_exif failed on while opening: getexif gives none
    /// after, and reads no XMP
    Spoilt,
}

/// A decoded picture: its pixels in the mode Pillow gives them, its palette (P: 256 entries
/// of RGBA, as Pillow's C palette holds them) and what Pillow keeps in its info.
#[derive(Clone, Debug)]
pub struct Picture {
    pub kind: Kind,
    pub width: usize,
    pub height: usize,
    pub data: Vec<u8>,
    pub palette: Option<Vec<[u8; 4]>>,
    pub transparency: Option<Transparency>,
    /// info["exif"]: the EXIF data (with or without its "Exif\0\0")
    pub exif: Option<Exif>,
    /// info["Raw profile type exif"] (a PNG's text by ImageMagick)
    pub raw_exif: Option<String>,
    /// info["XML:com.adobe.xmp"]
    pub xmp: Option<String>,
    /// info["xmp"] (an iTXt's bytes, where its text is not UTF-8 too)
    pub xmp_bytes: Option<Vec<u8>>,
    /// Python's mode says L while the pixels are P's, read through the file's palette (a GIF's
    /// first frame whose own colour table is the grey ramp: Image.load puts the file's palette
    /// on the L picture, which makes it P underneath). So the picture as Image.open leaves it
    /// converts as an L would ask and a P would do (convert("RGBA") with a transparency
    /// refused, convert("L") the P picture itself), and resizes only by NEAREST; a copy of it
    /// (exif_transpose's) is P through and through.
    pub l_over_p: bool,
}

/// The `n` pixels in `mode`, each from its RGBA in `px`: RGB its first three, L its grey (a
/// grey kind's own, the others' weighed), RGBA all four with the transparency's `key` made
/// clear.
#[inline(always)]
fn lay(
    n: usize,
    mode: Mode,
    key: Option<[u8; 4]>,
    grey_kind: bool,
    px: impl Iterator<Item = [u8; 4]>,
) -> Vec<u8> {
    let mut out = vec![0u8; n * mode.bands()];
    match mode {
        Mode::RGB => {
            for (o, p) in out.chunks_exact_mut(3).zip(px) {
                o.copy_from_slice(&p[..3]);
            }
        }
        Mode::L => {
            for (o, p) in out.iter_mut().zip(px) {
                *o = if grey_kind {
                    p[0]
                } else {
                    grey(p[0], p[1], p[2])
                };
            }
        }
        _ => match key {
            None => {
                for (o, p) in out.chunks_exact_mut(4).zip(px) {
                    o.copy_from_slice(&p);
                }
            }
            Some(key) => {
                for (o, mut p) in out.chunks_exact_mut(4).zip(px) {
                    if p == key {
                        p[3] = 0;
                    }
                    o.copy_from_slice(&p);
                }
            }
        },
    }
    out
}

/// ITU-R 601-2's grey as Convert.c weighs it (L24 >> 16).
fn grey(r: u8, g: u8, b: u8) -> u8 {
    ((u32::from(r) * 19595 + u32::from(g) * 38470 + u32::from(b) * 7471 + 0x8000) >> 16) as u8
}

impl Picture {
    /// The EXIF orientation Pillow finds for it (getexif: its EXIF, else its raw profile, and
    /// where neither names one, its XMP); 1 where there is none.
    pub fn orientation(&self) -> Result<u32> {
        let exif = match (&self.exif, &self.raw_exif) {
            (Some(Exif::Bytes(e)), _) => Some(e.clone()),
            (Some(Exif::Text { empty: false }), _) => {
                return error("startswith first arg must be str"); // (Exif.load of text)
            }
            (Some(Exif::Text { empty: true }), _) => None,
            (Some(Exif::Spoilt), _) => return Ok(1),
            (None, Some(raw)) => Some(exif::from_raw_profile(raw)?),
            (None, None) => None,
        };
        if let Some(found) = exif.as_deref().map(orientation).transpose()?.flatten() {
            return Ok(found);
        }
        // XMP's text, else (where it is empty or none) its bytes
        let xmp = match &self.xmp {
            Some(text) if !text.is_empty() => Some(text.as_bytes()),
            _ => self.xmp_bytes.as_deref(),
        };
        Ok(xmp.and_then(exif::xmp_orientation).unwrap_or(1))
    }

    /// Image.resize(to, filter, box, reducing_gap) in the picture's own mode, as
    /// Image.thumbnail calls it (nothing where it has that size already): "1" and "P" by
    /// NEAREST whatever the filter, "LA" with its grey multiplied by its alpha as RGBA's
    /// colours are (the same bytes as through RGBA), CMYK's four bands as they are, I;16 in 16
    /// bits.
    fn resized(
        &self,
        to: (usize, usize),
        filter: Filter,
        bounds: Option<(f64, f64, f64, f64)>,
        gap: Option<f64>,
    ) -> Result<Picture> {
        if to == (self.width, self.height) {
            return Ok(self.clone());
        }
        if self.l_over_p && filter != Filter::Nearest {
            // (asked as of an L, done by the P picture, which has no other filter)
            return error("image has wrong mode");
        }
        let (w, h) = (self.width, self.height);
        let fail = |e: lp_pixels::Error| Error(e.to_string());
        let resize = |mode: Mode, data: Vec<u8>, filter: Filter| -> Result<Image> {
            Image::from_bytes(mode, w, h, data)
                .and_then(|im| im.resize(to.0, to.1, filter, bounds, gap))
                .map_err(fail)
        };
        let data = match self.kind {
            Kind::Bilevel | Kind::P => resize(Mode::L, self.data.clone(), Filter::Nearest)?,
            Kind::L => resize(Mode::L, self.data.clone(), filter)?,
            Kind::RGB => resize(Mode::RGB, self.data.clone(), filter)?,
            Kind::RGBA => resize(Mode::RGBA, self.data.clone(), filter)?,
            // (RGBa: four bands resized as they are, none multiplied by another)
            Kind::Cmyk => resize(Mode::RGBa, self.data.clone(), filter)?,
            Kind::LA => {
                let rgba = self
                    .data
                    .chunks_exact(2)
                    .flat_map(|p| [p[0], p[0], p[0], p[1]]);
                let out = resize(Mode::RGBA, rgba.collect(), filter)?;
                let la = out.as_bytes().chunks_exact(4).flat_map(|p| [p[0], p[3]]);
                return Ok(self.with_data(to, la.collect()));
            }
            Kind::I16 => {
                let out = lp_pixels::resize_16(&self.data, w, h, to.0, to.1, filter, bounds, gap);
                return Ok(self.with_data(to, out.map_err(fail)?));
            }
        };
        Ok(self.with_data(to, data.into_bytes()))
    }

    /// The picture with other pixels, of `size`.
    fn with_data(&self, size: (usize, usize), data: Vec<u8>) -> Picture {
        Picture {
            kind: self.kind,
            width: size.0,
            height: size.1,
            data,
            palette: self.palette.clone(),
            transparency: self.transparency.clone(),
            exif: self.exif.clone(),
            raw_exif: self.raw_exif.clone(),
            xmp: self.xmp.clone(),
            xmp_bytes: self.xmp_bytes.clone(),
            // (a resized picture is a new one, in the pixels' own mode)
            l_over_p: false,
        }
    }

    /// Image.convert(mode) to RGBA, RGB or L, with the transparency as Pillow takes it
    /// (convert_transparent for 1, L, I;16 and RGB to RGBA; the palette's alphas for P). RGBa,
    /// which the app opens nothing in, is refused.
    pub fn convert(&self, mode: Mode) -> Result<Image> {
        if self.l_over_p {
            if mode == Mode::RGBA && self.transparency.is_some() {
                return error("conversion from P to RGBA not supported in convert_transparent");
            }
            if mode == Mode::L {
                // (mode == self.mode: Pillow's copy, which is the P picture)
                return error("convert(\"L\") gives the picture in P");
            }
        }
        self.convert_pixels(mode)
    }

    /// Image.convert(mode) as the pixels' own mode does it (a copy's: exif_transpose's).
    fn convert_pixels(&self, mode: Mode) -> Result<Image> {
        if mode == Mode::RGBa {
            return error("pictures open in RGBA, RGB or L");
        }
        let (w, h) = (self.width, self.height);
        let n = w * h;
        let src = &self.data;
        // the RGBA each pixel converts to (alpha 255), as Convert.c's converters make it
        let palette = self.palette_for(mode)?;
        // convert_transparent's key (rgbT2a: each value's low byte, the alpha 255)
        let key = match (&self.transparency, self.kind, mode) {
            (Some(Transparency::Grey(t)), Kind::Bilevel | Kind::L | Kind::I16, Mode::RGBA) => {
                let t = (t & 0xff) as u8;
                Some([t, t, t, 255])
            }
            (Some(Transparency::Colour(r, g, b)), Kind::RGB, Mode::RGBA) => {
                Some([(r & 0xff) as u8, (g & 0xff) as u8, (b & 0xff) as u8, 255])
            }
            _ => None,
        };
        // (to L a grey kind keeps its grey; the others are weighed)
        let grey_kind = matches!(self.kind, Kind::L | Kind::Bilevel | Kind::I16 | Kind::LA);
        let out = match self.kind {
            Kind::Bilevel => lay(
                n,
                mode,
                key,
                grey_kind,
                src.iter().map(|&v| {
                    let v = if v != 0 { 255 } else { 0 };
                    [v, v, v, 255]
                }),
            ),
            Kind::L => lay(n, mode, key, grey_kind, src.iter().map(|&v| [v, v, v, 255])),
            Kind::LA => lay(
                n,
                mode,
                key,
                grey_kind,
                src.chunks_exact(2).map(|p| [p[0], p[0], p[0], p[1]]),
            ),
            Kind::P => {
                // (the 256 entries as an array: an index of a byte needs no check)
                let mut table = [[0u8, 0, 0, 255]; 256];
                for (t, p) in table.iter_mut().zip(&palette) {
                    *t = *p;
                }
                lay(
                    n,
                    mode,
                    key,
                    grey_kind,
                    src.iter().map(|&v| table[usize::from(v)]),
                )
            }
            Kind::RGB => lay(
                n,
                mode,
                key,
                grey_kind,
                src.chunks_exact(3).map(|p| [p[0], p[1], p[2], 255]),
            ),
            Kind::RGBA => lay(
                n,
                mode,
                key,
                grey_kind,
                src.chunks_exact(4).map(|p| [p[0], p[1], p[2], p[3]]),
            ),
            Kind::I16 => lay(
                n,
                mode,
                key,
                grey_kind,
                src.chunks_exact(2).map(|p| {
                    // (I16_RGB, and I;16 to L, which RGBA goes through: above 255 white)
                    let v = if p[1] == 0 { p[0] } else { 255 };
                    [v, v, v, 255]
                }),
            ),
            Kind::Cmyk => lay(
                n,
                mode,
                key,
                grey_kind,
                src.chunks_exact(4).map(|p| {
                    // cmyk2rgb (and to L through RGB, as convert goes where it has no way)
                    let k = 255 - u32::from(p[3]);
                    let ink = |c: u8| {
                        let tmp = u32::from(c) * k + 128;
                        (k - (((tmp >> 8) + tmp) >> 8)) as u8
                    };
                    [ink(p[0]), ink(p[1]), ink(p[2]), 255]
                }),
            ),
        };
        Image::from_bytes(mode, w, h, out).map_err(|e| Error(e.to_string()))
    }

    /// The palette as a conversion to `mode` reads it: for RGBA with the alphas of its
    /// transparency put in (putpalettealphas, putpalettealpha, which refuse alphas beyond the
    /// 256 entries), for RGB and L opaque.
    fn palette_for(&self, mode: Mode) -> Result<Vec<[u8; 4]>> {
        let mut palette = self
            .palette
            .clone()
            .unwrap_or_else(|| vec![[0, 0, 0, 255]; 256]);
        match (&self.transparency, mode) {
            (_, Mode::RGB | Mode::L) => {
                for entry in &mut palette {
                    entry[3] = 255;
                }
            }
            (Some(Transparency::Alphas(a)), _) => {
                if a.len() > 256 {
                    return error("palette index out of range");
                }
                for (entry, &alpha) in palette.iter_mut().zip(a) {
                    entry[3] = alpha;
                }
            }
            (Some(Transparency::Index(i)), _) => match palette.get_mut(*i as usize) {
                Some(entry) => entry[3] = 0,
                None => return error("palette index out of range"),
            },
            _ => {}
        }
        Ok(palette)
    }
}

fn is_gif(data: &[u8]) -> bool {
    data.starts_with(b"GIF87a") || data.starts_with(b"GIF89a")
}

fn is_jpeg(data: &[u8]) -> bool {
    data.starts_with(b"\xff\xd8\xff")
}

/// The picture in `data` (its file's bytes), as Image.open reads it and loads its first frame.
pub fn decode(data: &[u8]) -> Result<Picture> {
    decode_draft(data, None)
}

/// The same after draft(mode, size): a JPEG decoded at the scale of 1/2, 1/4 or 1/8 that still
/// covers `size` (JpegImageFile.draft; "L" read as grey by libjpeg where the file is RGB);
/// other pictures as they are.
pub fn decode_draft(data: &[u8], draft: Option<(Option<Kind>, (usize, usize))>) -> Result<Picture> {
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return png::decode(data);
    }
    if is_gif(data) {
        return gif::decode(data);
    }
    if is_jpeg(data) {
        return jpeg::decode_picture(data, draft);
    }
    if webp::accepts(data) {
        return webp::decode(data);
    }
    error("cannot identify image file")
}

/// Image.thumbnail(size, filter, reducing_gap) on a file just opened, before anything else is
/// asked of it (media.py makes a track's cover small so: Image.open, thumbnail((512, 512)),
/// convert("RGBA")). A JPEG is drafted first where there is a reducing_gap (libjpeg decoding
/// it at 1/2, 1/4 or 1/8, the resize then from the box JpegImageFile.draft gives); every
/// picture is resized in its file's own mode, its palette, transparency and EXIF kept for what
/// comes after.
pub fn thumbnail(
    data: &[u8],
    size: (f64, f64),
    filter: Filter,
    reducing_gap: Option<f64>,
) -> Result<Picture> {
    let fail = |e: lp_pixels::Error| Error(e.to_string());
    if !is_jpeg(data) {
        let picture = decode(data)?;
        return match thumbnail_size(picture.width, picture.height, size).map_err(fail)? {
            Some(to) => picture.resized(to, filter, None, reducing_gap),
            None => Ok(picture),
        };
    }
    let opened = jpeg::opened_size(data)?;
    let Some(to) = thumbnail_size(opened.0, opened.1, size).map_err(fail)? else {
        return decode(data);
    };
    let Some(gap) = reducing_gap else {
        return decode(data)?.resized(to, filter, None, None);
    };
    // draft(None, (int(size[0] * reducing_gap), int(size[1] * reducing_gap))), which divides
    // the size by it as Python's // does: by 0 refused, by less than 0 a scale of 1 (as the
    // size itself gives)
    let want = ((size.0 * gap) as i64, (size.1 * gap) as i64);
    if want.0 == 0 || want.1 == 0 {
        return error("integer division or modulo by zero");
    }
    let want = if want.0 < 0 || want.1 < 0 {
        opened
    } else {
        (want.0 as usize, want.1 as usize)
    };
    let scale = jpeg::draft_scale(opened, want) as f64;
    let picture = jpeg::decode_picture(data, Some((None, want)))?;
    let bounds = (0.0, 0.0, opened.0 as f64 / scale, opened.1 as f64 / scale);
    picture.resized(to, filter, Some(bounds), Some(gap))
}

/// Every frame of an animated picture as the app reads one (render/moving.py's
/// read_animation: each frame's duration and its picture in RGBA): GIF's, APNG's (a PNG's
/// one).
pub fn frames(data: &[u8]) -> Result<Vec<Frame>> {
    if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        return png::frames(data);
    }
    if is_gif(data) {
        return gif::frames(data);
    }
    if is_jpeg(data) {
        return jpeg::frames(data);
    }
    if webp::accepts(data) {
        return webp::frames(data);
    }
    error("cannot identify image file")
}

/// What the app makes of a picture it opens: ImageOps.exif_transpose(picture).convert(mode).
pub fn open(data: &[u8], mode: Mode) -> Result<Image> {
    opened(&decode(data)?, mode)
}

/// The same of a picture decoded (after a draft, say).
pub fn opened(picture: &Picture, mode: Mode) -> Result<Image> {
    let orientation = picture.orientation()?;
    // (exif_transpose makes a copy, in the pixels' own mode)
    let converted = picture.convert_pixels(mode)?;
    Ok(converted.exif_transposed(orientation.min(u32::from(u16::MAX)) as u16))
}
