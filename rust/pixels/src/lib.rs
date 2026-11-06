//! Pillow's image operations as Libre Panel uses them, to the bit (docs/RUST.md, stage R1).
//!
//! Each operation does what Pillow 12 does, Python's side (PIL/Image.py) and C's
//! (libImaging) together: the same integer arithmetic, the same rounding, the same edge cases.
//! `lp-check` runs them for tests/test_rust_pixels.py, which compares them with Pillow's on
//! thousands of random cases, byte for byte.
//!
//! Images hold their pixels as Pillow's `tobytes()` gives them: L one byte a pixel, RGB three,
//! RGBA and RGBa (premultiplied) four, row by row. Pillow keeps RGB in four bytes inside (the
//! fourth always 255); where that shows in what it computes, the code here says so.

mod bands;
mod bbox;
mod composite;
mod convert;
mod paste;
mod point;

pub use bands::merge;
pub use composite::alpha_composite;
pub use point::round_half_even;

use std::fmt;

/// An image's mode (Pillow's names).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Mode {
    L,
    RGB,
    RGBA,
    /// RGBA with its colours multiplied by its alpha (Pillow's "RGBa")
    RGBa,
}

impl Mode {
    pub fn name(self) -> &'static str {
        match self {
            Mode::L => "L",
            Mode::RGB => "RGB",
            Mode::RGBA => "RGBA",
            Mode::RGBa => "RGBa",
        }
    }

    pub fn from_name(name: &str) -> Option<Mode> {
        Some(match name {
            "L" => Mode::L,
            "RGB" => Mode::RGB,
            "RGBA" => Mode::RGBA,
            "RGBa" => Mode::RGBa,
            _ => return None,
        })
    }

    /// The bands of a pixel, as its bytes in `tobytes()`.
    pub fn bands(self) -> usize {
        match self {
            Mode::L => 1,
            Mode::RGB => 3,
            Mode::RGBA | Mode::RGBa => 4,
        }
    }

    /// Whether it has an alpha band (the last).
    pub fn has_alpha(self) -> bool {
        matches!(self, Mode::RGBA | Mode::RGBa)
    }
}

impl fmt::Display for Mode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

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

/// An image of 8-bit bands.
#[derive(Clone, PartialEq, Eq)]
pub struct Image {
    mode: Mode,
    width: usize,
    height: usize,
    data: Vec<u8>,
}

impl fmt::Debug for Image {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Image({} {}x{})", self.mode, self.width, self.height)
    }
}

impl Image {
    /// Image.new(mode, size, colour): every pixel ``colour`` (one value a band).
    pub fn new(mode: Mode, width: usize, height: usize, colour: &[u8]) -> Image {
        assert_eq!(
            colour.len(),
            mode.bands(),
            "a colour of {} bands for {mode}",
            colour.len()
        );
        let mut data = Vec::with_capacity(width * height * mode.bands());
        for _ in 0..width * height {
            data.extend_from_slice(colour);
        }
        Image {
            mode,
            width,
            height,
            data,
        }
    }

    /// An image of nothing: every band 0 (Pillow's ImagingNew).
    pub fn blank(mode: Mode, width: usize, height: usize) -> Image {
        Image {
            mode,
            width,
            height,
            data: vec![0; width * height * mode.bands()],
        }
    }

    /// Image.frombytes(mode, size, data): ``data`` as `tobytes()` gives it.
    pub fn from_bytes(mode: Mode, width: usize, height: usize, data: Vec<u8>) -> Result<Image> {
        if data.len() != width * height * mode.bands() {
            return error("not enough image data");
        }
        Ok(Image {
            mode,
            width,
            height,
            data,
        })
    }

    pub fn mode(&self) -> Mode {
        self.mode
    }

    pub fn width(&self) -> usize {
        self.width
    }

    pub fn height(&self) -> usize {
        self.height
    }

    pub fn size(&self) -> (usize, usize) {
        (self.width, self.height)
    }

    pub fn bands(&self) -> usize {
        self.mode.bands()
    }

    /// The pixels as `tobytes()` gives them.
    pub fn as_bytes(&self) -> &[u8] {
        &self.data
    }

    pub fn as_bytes_mut(&mut self) -> &mut [u8] {
        &mut self.data
    }

    pub fn into_bytes(self) -> Vec<u8> {
        self.data
    }

    pub fn row(&self, y: usize) -> &[u8] {
        let n = self.width * self.bands();
        &self.data[y * n..(y + 1) * n]
    }

    pub fn row_mut(&mut self, y: usize) -> &mut [u8] {
        let n = self.width * self.bands();
        &mut self.data[y * n..(y + 1) * n]
    }

    pub fn pixel(&self, x: usize, y: usize) -> &[u8] {
        let b = self.bands();
        let at = (y * self.width + x) * b;
        &self.data[at..at + b]
    }

    /// Image.crop(box), the box in whole pixels: what lies outside the image is 0 in every band
    /// (Crop.c fills the new image with zeros where the box reaches beyond it).
    pub fn crop(&self, x0: i64, y0: i64, x1: i64, y1: i64) -> Image {
        let w = (x1 - x0).max(0) as usize;
        let h = (y1 - y0).max(0) as usize;
        let mut out = Image::blank(self.mode, w, h);
        let b = self.bands();
        let (sx0, sx1) = (x0.max(0), x1.min(self.width as i64));
        let (sy0, sy1) = (y0.max(0), y1.min(self.height as i64));
        if sx0 < sx1 && sy0 < sy1 {
            let n = (sx1 - sx0) as usize * b;
            for sy in sy0..sy1 {
                let from = (sy as usize * self.width + sx0 as usize) * b;
                let to = ((sy - y0) as usize * w + (sx0 - x0) as usize) * b;
                out.data[to..to + n].copy_from_slice(&self.data[from..from + n]);
            }
        }
        out
    }
}

/// Pillow's integer helpers (libImaging/ImagingUtils.h).
pub(crate) mod ints {
    /// SHIFTFORDIV255: a / 255 for a up to 255 * 255 + 255, rounded down after the 128 added.
    #[inline(always)]
    pub fn shift_div255(a: u32) -> u32 {
        ((a >> 8) + a) >> 8
    }

    /// DIV255: a / 255 rounded.
    #[inline(always)]
    pub fn div255(a: u32) -> u32 {
        shift_div255(a + 128)
    }

    /// MULDIV255: a * b / 255 rounded.
    #[inline(always)]
    pub fn muldiv255(a: u32, b: u32) -> u32 {
        div255(a * b)
    }

    /// BLEND: in1 and in2 mixed by mask (0: in1, 255: in2).
    #[inline(always)]
    pub fn blend(mask: u32, in1: u32, in2: u32) -> u8 {
        div255(in1 * (255 - mask) + in2 * mask) as u8
    }

    /// PREBLEND: in2 over in1 where in2 is premultiplied by mask.
    #[inline(always)]
    pub fn preblend(mask: u32, in1: u32, in2: u32) -> u8 {
        (muldiv255(in1, 255 - mask) + in2) as u8
    }

    /// CLIP8.
    #[inline(always)]
    pub fn clip8(v: i64) -> u8 {
        v.clamp(0, 255) as u8
    }
}
