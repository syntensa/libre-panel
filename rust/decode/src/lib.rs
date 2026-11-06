//! Pictures from their files as Pillow 13.0 decodes them (docs/RUST.md, stage R1): each in the
//! mode Pillow opens it in, with its palette, its transparency and its EXIF orientation as
//! Pillow reads them, and converted to RGBA, RGB or L as Image.convert converts it (the app
//! converts every picture it opens, most to RGBA).

mod exif;
mod gif;
mod jpeg;
mod png;

pub use exif::orientation;
pub use gif::Frame;

use lp_pixels::{Image, Mode};
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

    /// Image.convert(mode) to RGBA, RGB or L, with the transparency as Pillow takes it
    /// (convert_transparent for 1, L, I;16 and RGB to RGBA; the palette's alphas for P). RGBa,
    /// which the app opens nothing in, is refused.
    pub fn convert(&self, mode: Mode) -> Result<Image> {
        if mode == Mode::RGBa {
            return error("pictures open in RGBA, RGB or L");
        }
        let (w, h) = (self.width, self.height);
        let n = w * h;
        let out_bands = mode.bands();
        let mut out = Vec::with_capacity(n * out_bands);
        let src = &self.data;
        // the RGBA each pixel converts to (alpha 255), as Convert.c's converters make it
        let palette = self.palette_for(mode)?;
        let rgba = |i: usize| -> [u8; 4] {
            match self.kind {
                Kind::Bilevel => {
                    let v = if src[i] != 0 { 255 } else { 0 };
                    [v, v, v, 255]
                }
                Kind::L => [src[i], src[i], src[i], 255],
                Kind::LA => [src[2 * i], src[2 * i], src[2 * i], src[2 * i + 1]],
                Kind::P => palette[usize::from(src[i])],
                Kind::RGB => [src[3 * i], src[3 * i + 1], src[3 * i + 2], 255],
                Kind::RGBA => [src[4 * i], src[4 * i + 1], src[4 * i + 2], src[4 * i + 3]],
                Kind::I16 => {
                    // (I16_RGB, and I;16 to L, which RGBA goes through: above 255 white)
                    let v = if src[2 * i + 1] == 0 { src[2 * i] } else { 255 };
                    [v, v, v, 255]
                }
                Kind::Cmyk => {
                    // cmyk2rgb (and to L through RGB, as convert goes where it has no way)
                    let k = 255 - u32::from(src[4 * i + 3]);
                    let ink = |c: u8| {
                        let tmp = u32::from(c) * k + 128;
                        (k - (((tmp >> 8) + tmp) >> 8)) as u8
                    };
                    [
                        ink(src[4 * i]),
                        ink(src[4 * i + 1]),
                        ink(src[4 * i + 2]),
                        255,
                    ]
                }
            }
        };
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
        for i in 0..n {
            let mut px = rgba(i);
            match mode {
                Mode::RGB => out.extend_from_slice(&px[..3]),
                Mode::L => out.push(match self.kind {
                    Kind::L | Kind::Bilevel | Kind::I16 | Kind::LA => px[0],
                    _ => grey(px[0], px[1], px[2]),
                }),
                _ => {
                    if key == Some(px) {
                        px[3] = 0;
                    }
                    out.extend_from_slice(&px);
                }
            }
        }
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
    error("cannot identify image file")
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
    error("cannot identify image file")
}

/// What the app makes of a picture it opens: ImageOps.exif_transpose(picture).convert(mode).
pub fn open(data: &[u8], mode: Mode) -> Result<Image> {
    opened(&decode(data)?, mode)
}

/// The same of a picture decoded (after a draft, say).
pub fn opened(picture: &Picture, mode: Mode) -> Result<Image> {
    let orientation = picture.orientation()?;
    let converted = picture.convert(mode)?;
    Ok(converted.exif_transposed(orientation.min(u32::from(u16::MAX)) as u16))
}
