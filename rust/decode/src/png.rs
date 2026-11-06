//! PNG and APNG as Pillow 13.0's PngImagePlugin reads them: the file read one chunk after the
//! other as PngImageFile and PngStream read it (checksums checked while opening, up to the
//! image data; the chunks after it read once it is loaded, up to the next frame; a chunk put
//! back where a frame begins), the image data inflated and unfiltered as ZipDecode.c does it
//! (zlib-rs, a port of zlib, so that it stops where zlib stops), and its samples unpacked as
//! Pillow's raw modes unpack them: grey of 1, 2 and 4 bits spread to 0..255, 16 bits cut to
//! their high byte, I;16 kept.
//!
//! An APNG's frames are kept in the file's mode and laid on the last as Pillow lays them: the
//! disposal of the frame before (to zeros, or to the picture as it was), then the frame's
//! pixels in its place, or blended over (OP_OVER: each byte weighed by the frame's alpha, the
//! alpha too, as Pillow's paste weighs them).

use flate2::{Decompress, FlushDecompress, Status};

use crate::{error, Error, Exif, Frame, Kind, Picture, Result, Transparency};

const MAGIC: &[u8] = b"\x89PNG\r\n\x1a\n";

/// Image.MAX_IMAGE_PIXELS twice over: where Pillow refuses a picture as a bomb.
const BOMB: u64 = 2 * (1024 * 1024 * 1024 / 4 / 3);

/// ImageFile's decodermaxblock: what load_read is asked for at a time.
const MAX_BLOCK: usize = 65536;

/// MAX_TEXT_CHUNK (ImageFile.SAFEBLOCK) and MAX_TEXT_MEMORY.
const MAX_TEXT_CHUNK: usize = 1 << 20;
const MAX_TEXT_MEMORY: usize = 64 << 20;

/// Adam7 in ZipDecode.c's terms.
const OFFSET: [usize; 7] = [7, 3, 3, 1, 1, 0, 0];
const STARTING_COL: [usize; 7] = [0, 4, 0, 2, 0, 1, 0];
const STARTING_ROW: [usize; 7] = [0, 0, 4, 0, 2, 0, 1];
const COL_INCREMENT: [usize; 7] = [8, 8, 4, 4, 2, 2, 1];
const ROW_INCREMENT: [usize; 7] = [8, 8, 8, 4, 4, 2, 2];

/// APNG's dispose_op and blend_op values.
const OP_BACKGROUND: i64 = 1;
const OP_PREVIOUS: i64 = 2;
const OP_OVER: i64 = 1;

fn u32_at(b: &[u8], at: usize) -> u32 {
    u32::from_be_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

fn u16_at(b: &[u8], at: usize) -> u32 {
    u32::from(u16::from_be_bytes([b[at], b[at + 1]]))
}

pub(crate) fn crc32(kind: &[u8], data: &[u8]) -> u32 {
    let mut crc = !0u32;
    for &byte in kind.iter().chain(data) {
        crc ^= u32::from(byte);
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0xEDB8_8320
            } else {
                crc >> 1
            };
        }
    }
    !crc
}

/// The raw modes of _MODES: how a row's samples are packed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Raw {
    Bit,
    L2,
    L4,
    L,
    I16B,
    Rgb,
    Rgb16,
    P1,
    P2,
    P4,
    P,
    La,
    La16,
    Rgba,
    Rgba16,
}

impl Raw {
    /// _MODES: the mode and raw mode of a bit depth and colour type.
    fn of(depth: u8, colour: u8) -> Option<(Kind, Raw)> {
        Some(match (depth, colour) {
            (1, 0) => (Kind::Bilevel, Raw::Bit),
            (2, 0) => (Kind::L, Raw::L2),
            (4, 0) => (Kind::L, Raw::L4),
            (8, 0) => (Kind::L, Raw::L),
            (16, 0) => (Kind::I16, Raw::I16B),
            (8, 2) => (Kind::RGB, Raw::Rgb),
            (16, 2) => (Kind::RGB, Raw::Rgb16),
            (1, 3) => (Kind::P, Raw::P1),
            (2, 3) => (Kind::P, Raw::P2),
            (4, 3) => (Kind::P, Raw::P4),
            (8, 3) => (Kind::P, Raw::P),
            (8, 4) => (Kind::LA, Raw::La),
            (16, 4) => (Kind::RGBA, Raw::La16),
            (8, 6) => (Kind::RGBA, Raw::Rgba),
            (16, 6) => (Kind::RGBA, Raw::Rgba16),
            _ => return None,
        })
    }

    /// The unpacker's bits a pixel.
    fn bits(self) -> usize {
        match self {
            Raw::Bit | Raw::P1 => 1,
            Raw::L2 | Raw::P2 => 2,
            Raw::L4 | Raw::P4 => 4,
            Raw::L | Raw::P => 8,
            Raw::I16B | Raw::La => 16,
            Raw::Rgb => 24,
            Raw::La16 | Raw::Rgba => 32,
            Raw::Rgb16 => 48,
            Raw::Rgba16 => 64,
        }
    }

    /// The unpacker: `count` pixels of `src` (from its first bit) into `out`, in the mode's
    /// bytes (L, P and 1: one; LA: grey, alpha; I;16: little end first; RGB; RGBA).
    fn unpack(self, src: &[u8], count: usize, out: &mut [u8]) {
        let bits = |n: usize, b: usize| -> u8 {
            let per = 8 / b;
            (src[n / per] >> (8 - b * (n % per + 1))) & ((1u8 << b) - 1)
        };
        match self {
            Raw::Bit => (0..count).for_each(|x| out[x] = if bits(x, 1) != 0 { 255 } else { 0 }),
            Raw::L2 => (0..count).for_each(|x| out[x] = bits(x, 2) * 0x55),
            Raw::L4 => (0..count).for_each(|x| out[x] = bits(x, 4) * 0x11),
            Raw::P1 => (0..count).for_each(|x| out[x] = bits(x, 1)),
            Raw::P2 => (0..count).for_each(|x| out[x] = bits(x, 2)),
            Raw::P4 => (0..count).for_each(|x| out[x] = bits(x, 4)),
            Raw::L | Raw::P => out[..count].copy_from_slice(&src[..count]),
            Raw::I16B => {
                for (o, s) in out.chunks_exact_mut(2).zip(src.chunks_exact(2)).take(count) {
                    o.copy_from_slice(&[s[1], s[0]]);
                }
            }
            Raw::Rgb => out[..count * 3].copy_from_slice(&src[..count * 3]),
            Raw::Rgb16 => {
                for (o, s) in out.chunks_exact_mut(3).zip(src.chunks_exact(6)).take(count) {
                    o.copy_from_slice(&[s[0], s[2], s[4]]);
                }
            }
            Raw::La => out[..count * 2].copy_from_slice(&src[..count * 2]),
            Raw::La16 => {
                for (o, s) in out.chunks_exact_mut(4).zip(src.chunks_exact(4)).take(count) {
                    o.copy_from_slice(&[s[0], s[0], s[0], s[2]]);
                }
            }
            Raw::Rgba => out[..count * 4].copy_from_slice(&src[..count * 4]),
            Raw::Rgba16 => {
                for (o, s) in out.chunks_exact_mut(4).zip(src.chunks_exact(8)).take(count) {
                    o.copy_from_slice(&[s[0], s[2], s[4], s[6]]);
                }
            }
        }
    }
}

/// What Pillow keeps in its info that the reading and the conversions look at.
#[derive(Clone, Default)]
struct Info {
    transparency: Option<Transparency>,
    exif: Option<Exif>,
    raw_exif: Option<String>,
    xmp: Option<String>,
    xmp_bytes: Option<Vec<u8>>,
    /// info["interlace"]'s truth
    interlace: bool,
    bbox: Option<(u64, u64, u64, u64)>,
    duration: Option<f64>,
    disposal: Option<i64>,
    blend: Option<i64>,
    default_image: bool,
}

/// A tile: the extent the image data fills, where it begins.
#[derive(Clone, Copy)]
struct Tile {
    extent: (u64, u64, u64, u64),
    pos: usize,
}

/// What a chunk's handler did: read it, met image data or the end (EOFError), or none was
/// there (AttributeError).
enum Called {
    Read(Vec<u8>),
    Eof,
    Unknown,
}

/// PngStream and the file under it, as Python reads it (short at its end, never past it).
struct Stream<'a> {
    file: &'a [u8],
    at: usize,
    queue: Vec<(Vec<u8>, usize, usize)>,
    size: (u64, u64),
    mode: Option<(Kind, Raw)>,
    info: Info,
    palette: Option<Vec<u8>>,
    tile: Option<Tile>,
    n_frames: Option<u32>,
    seq_num: Option<u32>,
    text_memory: usize,
}

impl<'a> Stream<'a> {
    fn read(&mut self, n: usize) -> &'a [u8] {
        let from = self.at.min(self.file.len());
        let to = from.saturating_add(n).min(self.file.len());
        self.at = to;
        &self.file[from..to]
    }

    /// ImageFile._safe_read: all of it, or "Truncated File Read".
    fn safe_read(&mut self, n: usize) -> Result<&'a [u8]> {
        let s = self.read(n);
        if s.len() < n {
            return error("Truncated File Read");
        }
        Ok(s)
    }

    /// ChunkStream.read: the next chunk's type, where its data begins, its length (a chunk put
    /// back first). Err for what Python raises as struct.error and SyntaxError.
    fn head(&mut self) -> std::result::Result<(Vec<u8>, usize, usize), String> {
        if let Some((cid, pos, length)) = self.queue.pop() {
            self.at = pos;
            return Ok((cid, pos, length));
        }
        let s = self.read(8);
        let cid = s.get(4..).unwrap_or(&[]).to_vec();
        let pos = self.at;
        if s.len() < 4 {
            return Err("unpack requires a buffer of 4 bytes".into());
        }
        let length = u32_at(s, 0) as usize;
        let word = |c: &u8| c.is_ascii_alphanumeric() || *c == b'_';
        if cid.len() < 4 || !cid.iter().all(word) {
            return Err(format!("broken PNG file (chunk {cid:?})"));
        }
        Ok((cid, pos, length))
    }

    fn push(&mut self, cid: Vec<u8>, pos: usize, length: usize) {
        self.queue.push((cid, pos, length));
    }

    /// ChunkStream.crc (while opening): the checksum after the data, checked.
    fn crc(&mut self, cid: &[u8], data: &[u8]) -> Result<()> {
        let s = self.read(4);
        if s.len() < 4 {
            return error("broken PNG file (incomplete checksum)");
        }
        if u32_at(s, 0) != crc32(cid, data) {
            return error("broken PNG file (bad header checksum)");
        }
        Ok(())
    }

    /// zlib's data inflated as _safe_zlib_decompress inflates it (decompress(s, MAX_TEXT_CHUNK)):
    /// Err(true) where input is left once that much is out (a ValueError), Err(false) where
    /// zlib fails (zlib.error).
    fn text_inflate(data: &[u8]) -> std::result::Result<Vec<u8>, bool> {
        let mut z = Decompress::new(true);
        let mut out = vec![0u8; MAX_TEXT_CHUNK];
        let mut made = 0usize;
        loop {
            let (used, before) = (z.total_in() as usize, z.total_out());
            let status = z
                .decompress(&data[used..], &mut out[made..], FlushDecompress::None)
                .map_err(|_| false)?;
            made = z.total_out() as usize;
            let left = data.len() - z.total_in() as usize;
            if status == Status::StreamEnd || left == 0 {
                out.truncate(made);
                return Ok(out);
            }
            if made == MAX_TEXT_CHUNK {
                return Err(true);
            }
            if z.total_out() == before && z.total_in() as usize == used {
                out.truncate(made);
                return Ok(out);
            }
        }
    }

    fn text_memory(&mut self, n: usize) -> Result<()> {
        self.text_memory += n;
        if self.text_memory > MAX_TEXT_MEMORY {
            return error("Too much memory used in text chunks");
        }
        Ok(())
    }

    /// A text chunk's keyword and value, as PngStream keeps them where they matter here.
    fn keep_text(&mut self, key: &[u8], value: TextValue) -> Result<()> {
        let key: String = key.iter().map(|&c| char::from(c)).collect();
        match (key.as_str(), value) {
            ("exif", TextValue::Bytes(v)) => self.info.exif = Some(Exif::Bytes(v)),
            ("exif", TextValue::Text(v)) => {
                self.info.exif = Some(Exif::Text {
                    empty: v.is_empty(),
                })
            }
            ("Raw profile type exif", v) => self.info.raw_exif = Some(v.text()),
            ("XML:com.adobe.xmp", v) => self.info.xmp = Some(v.text()),
            ("interlace", v) => self.info.interlace = !v.is_empty(),
            ("default_image", v) => self.info.default_image = !v.is_empty(),
            ("transparency" | "bbox" | "duration" | "disposal" | "blend", _) => {
                // (Pillow keeps the text in their place and fails on it later, each in its own
                // way: refused here)
                return error(format!(
                    "a text chunk named {key}, which Pillow reads as its own"
                ));
            }
            _ => {}
        }
        Ok(())
    }

    /// PngStream.call: the chunk's handler.
    fn call(&mut self, cid: &[u8], pos: usize, length: usize) -> Result<Called> {
        let read = match cid {
            b"IHDR" => {
                let s = self.safe_read(length)?;
                if length < 13 {
                    return error("Truncated IHDR chunk");
                }
                self.size = (u64::from(u32_at(s, 0)), u64::from(u32_at(s, 4)));
                if let Some(mode) = Raw::of(s[8], s[9]) {
                    self.mode = Some(mode);
                }
                if s[12] != 0 {
                    self.info.interlace = true;
                }
                if s[11] != 0 {
                    return error("unknown filter category");
                }
                s
            }
            b"IDAT" => {
                let extent = match self.info.bbox {
                    Some(b) => b,
                    None => {
                        if self.n_frames.is_some() {
                            self.info.default_image = true;
                        }
                        (0, 0, self.size.0, self.size.1)
                    }
                };
                self.tile = Some(Tile { extent, pos });
                return Ok(Called::Eof);
            }
            b"IEND" => return Ok(Called::Eof),
            b"PLTE" => {
                let s = self.safe_read(length)?;
                if self.kind() == Some(Kind::P) {
                    self.palette = Some(s.to_vec());
                }
                s
            }
            b"tRNS" => {
                let s = self.safe_read(length)?;
                let i16 = |at: usize| u16_at(s, at);
                match self.kind() {
                    Some(Kind::P) => {
                        // _simple_palette: one 0 among 255s
                        let zeros = s.iter().filter(|&&v| v == 0).count();
                        self.info.transparency =
                            Some(if zeros == 1 && s.iter().all(|&v| v == 0 || v == 255) {
                                Transparency::Index(s.iter().position(|&v| v == 0).unwrap() as u32)
                            } else {
                                Transparency::Alphas(s.to_vec())
                            });
                    }
                    Some(k @ (Kind::Bilevel | Kind::L | Kind::I16 | Kind::RGB)) => {
                        if length < if k == Kind::RGB { 6 } else { 2 } {
                            return error("Truncated tRNS chunk");
                        }
                        self.info.transparency = Some(match k {
                            Kind::Bilevel => Transparency::Grey(if i16(0) != 0 { 255 } else { 0 }),
                            Kind::RGB => Transparency::Colour(i16(0), i16(2), i16(4)),
                            _ => Transparency::Grey(i16(0)),
                        });
                    }
                    _ => {}
                }
                s
            }
            b"gAMA" | b"cHRM" | b"sRGB" | b"pHYs" => {
                let s = self.safe_read(length)?;
                let least = match cid {
                    b"gAMA" => 4,
                    b"cHRM" => 32,
                    b"sRGB" => 1,
                    _ => 9,
                };
                if length < least {
                    return error("a truncated chunk");
                }
                s
            }
            b"iCCP" => {
                let s = self.safe_read(length)?;
                let i = s.iter().position(|&c| c == 0).map_or(-1, |i| i as i64);
                let Some(&method) = s.get((i + 1) as usize) else {
                    return error("index out of range");
                };
                if method != 0 {
                    return error("Unknown compression method in iCCP chunk");
                }
                if let Err(true) = Self::text_inflate(s.get((i + 2) as usize..).unwrap_or(&[])) {
                    return error("Decompressed data too large for PngImagePlugin.MAX_TEXT_CHUNK");
                }
                s
            }
            b"tEXt" => {
                let s = self.safe_read(length)?;
                let (k, v) = split(s);
                if !k.is_empty() {
                    let value = if k == b"exif" {
                        TextValue::Bytes(v.to_vec())
                    } else {
                        TextValue::Text(latin(v))
                    };
                    self.text_memory(v.len())?;
                    self.keep_text(k, value)?;
                }
                s
            }
            b"zTXt" => {
                let s = self.safe_read(length)?;
                let (k, v) = split(s);
                if v.first().copied().unwrap_or(0) != 0 {
                    return error("Unknown compression method in zTXt chunk");
                }
                let v = match Self::text_inflate(v.get(1..).unwrap_or(&[])) {
                    Ok(v) => v,
                    Err(true) => return error("Decompressed data too large"),
                    Err(false) => Vec::new(),
                };
                if !k.is_empty() {
                    self.text_memory(v.len())?;
                    self.keep_text(k, TextValue::Text(latin(&v)))?;
                }
                s
            }
            b"iTXt" => {
                let s = self.safe_read(length)?;
                self.itxt(s)?;
                s
            }
            b"eXIf" => {
                let s = self.safe_read(length)?;
                let mut v = b"Exif\0\0".to_vec();
                v.extend_from_slice(s);
                self.info.exif = Some(Exif::Bytes(v));
                s
            }
            b"acTL" => {
                let s = self.safe_read(length)?;
                if length < 8 {
                    return error("APNG contains truncated acTL chunk");
                }
                if self.n_frames.is_some() {
                    self.n_frames = None; // ("Invalid APNG, will use default PNG image")
                } else {
                    let n = u32_at(s, 0);
                    if n != 0 && n <= 0x8000_0000 {
                        self.n_frames = Some(n);
                    }
                }
                s
            }
            b"fcTL" => {
                let s = self.safe_read(length)?;
                if length < 26 {
                    return error("APNG contains truncated fcTL chunk");
                }
                let seq = u32_at(s, 0);
                if self
                    .seq_num
                    .map_or(seq != 0, |n| u64::from(n) + 1 != u64::from(seq))
                {
                    return error("APNG contains frame sequence errors");
                }
                self.seq_num = Some(seq);
                let (w, h) = (u64::from(u32_at(s, 4)), u64::from(u32_at(s, 8)));
                let (px, py) = (u64::from(u32_at(s, 12)), u64::from(u32_at(s, 16)));
                if px + w > self.size.0 || py + h > self.size.1 {
                    return error("APNG contains invalid frames");
                }
                self.info.bbox = Some((px, py, px + w, py + h));
                let (num, den) = (u16_at(s, 20), u16_at(s, 22));
                let den = if den == 0 { 100 } else { den };
                self.info.duration = Some(f64::from(num) / f64::from(den) * 1000.0);
                self.info.disposal = Some(i64::from(s[24]));
                self.info.blend = Some(i64::from(s[25]));
                s
            }
            b"fdAT" => {
                if length < 4 {
                    return error("APNG contains truncated fDAT chunk");
                }
                let s = self.safe_read(4)?;
                let seq = u32_at(s, 0);
                if self
                    .seq_num
                    .is_none_or(|n| u64::from(n) + 1 != u64::from(seq))
                {
                    return error("APNG contains frame sequence errors");
                }
                self.seq_num = Some(seq);
                return self.call(b"IDAT", pos + 4, length - 4);
            }
            _ => return Ok(Called::Unknown),
        };
        Ok(Called::Read(read.to_vec()))
    }

    fn itxt(&mut self, s: &[u8]) -> Result<()> {
        let Some(i) = s.iter().position(|&c| c == 0) else {
            return Ok(());
        };
        let (k, r) = (&s[..i], &s[i + 1..]);
        if r.len() < 2 {
            return Ok(());
        }
        let (cf, cm, r) = (r[0], r[1], &r[2..]);
        let mut parts = r.splitn(3, |&c| c == 0);
        let (Some(lang), Some(tk), Some(v)) = (parts.next(), parts.next(), parts.next()) else {
            return Ok(());
        };
        let v = if cf != 0 {
            if cm != 0 {
                return Ok(());
            }
            match Self::text_inflate(v) {
                Ok(v) => v,
                Err(true) => return error("Decompressed data too large"),
                Err(false) => return Ok(()),
            }
        } else {
            v.to_vec()
        };
        if k == b"XML:com.adobe.xmp" {
            self.info.xmp_bytes = Some(v.clone());
        }
        if std::str::from_utf8(lang).is_err() || std::str::from_utf8(tk).is_err() {
            return Ok(());
        }
        let Ok(text) = String::from_utf8(v) else {
            return Ok(());
        };
        self.text_memory(text.chars().count())?;
        self.keep_text(k, TextValue::Text(text))
    }

    fn kind(&self) -> Option<Kind> {
        self.mode.map(|(k, _)| k)
    }
}

/// A text chunk's value: bytes (tEXt's "exif") or text.
enum TextValue {
    Bytes(Vec<u8>),
    Text(String),
}

impl TextValue {
    fn is_empty(&self) -> bool {
        match self {
            TextValue::Bytes(b) => b.is_empty(),
            TextValue::Text(t) => t.is_empty(),
        }
    }

    fn text(self) -> String {
        match self {
            TextValue::Bytes(b) => latin(&b),
            TextValue::Text(t) => t,
        }
    }
}

fn latin(b: &[u8]) -> String {
    b.iter().map(|&c| char::from(c)).collect()
}

/// `s.split(b"\0", 1)`, the whole as the key where there is no 0.
fn split(s: &[u8]) -> (&[u8], &[u8]) {
    match s.iter().position(|&c| c == 0) {
        Some(i) => (&s[..i], &s[i + 1..]),
        None => (s, &[]),
    }
}

/// A picture as Pillow's C side holds a PNG's: its mode's bytes a pixel, the palette of P.
#[derive(Clone)]
struct Canvas {
    kind: Kind,
    w: usize,
    h: usize,
    px: Vec<u8>,
}

impl Canvas {
    fn new(kind: Kind, w: usize, h: usize) -> Canvas {
        Canvas {
            kind,
            w,
            h,
            px: vec![0; w * h * kind.bytes()],
        }
    }

    fn crop(&self, (x0, y0, x1, y1): (usize, usize, usize, usize)) -> Canvas {
        let n = self.kind.bytes();
        let mut out = Canvas::new(self.kind, x1 - x0, y1 - y0);
        for y in y0..y1 {
            let from = (y * self.w + x0) * n;
            let to = ((y - y0) * out.w) * n;
            out.px[to..to + out.w * n].copy_from_slice(&self.px[from..from + out.w * n]);
        }
        out
    }

    /// ImagingPaste of a picture of the box's size (within this one), with a mask's alphas.
    fn paste(&mut self, src: &Canvas, at: (usize, usize), alphas: Option<&[u8]>) {
        let n = self.kind.bytes();
        for y in 0..src.h {
            let out = ((at.1 + y) * self.w + at.0) * n;
            let row = &mut self.px[out..out + src.w * n];
            let from = &src.px[y * src.w * n..(y + 1) * src.w * n];
            match alphas {
                None => row.copy_from_slice(from),
                Some(a) => {
                    let pixels = row.chunks_exact_mut(n).zip(from.chunks_exact(n));
                    for (x, (o, i)) in pixels.enumerate() {
                        match a[y * src.w + x] {
                            0 => {}
                            255 => o.copy_from_slice(i),
                            m => {
                                for (o, &i) in o.iter_mut().zip(i) {
                                    let tmp = u32::from(*o) * (255 - u32::from(m))
                                        + u32::from(i) * u32::from(m)
                                        + 128;
                                    *o = (((tmp >> 8) + tmp) >> 8) as u8;
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/// PngImageFile as the plugin keeps it between its frames.
struct Png<'a> {
    png: Stream<'a>,
    kind: Kind,
    raw: Raw,
    size: (usize, usize),
    n_frames: usize,
    animated: bool,
    /// the palette (P), 256 entries as the C palette holds it, once put to the picture
    palette: Vec<[u8; 4]>,
    im: Option<Canvas>,
    prev: Option<Canvas>,
    prepare_idat: usize,
    idat: usize,
    frame: usize,
    dispose_op: Option<i64>,
    blend_op: Option<i64>,
    dispose_extent: Option<(u64, u64, u64, u64)>,
    tile: Option<Tile>,
    /// the loads with info["interlace"] (load_prepare adds 1 to decoderconfig each time, and
    /// the decoder takes only one)
    interlaced_loads: usize,
}

fn eq(v: Option<i64>, n: i64) -> bool {
    v == Some(n)
}

impl<'a> Png<'a> {
    /// _open: the chunks up to the image data, their checksums checked; an APNG's first
    /// frame sought.
    fn open(file: &'a [u8]) -> Result<Png<'a>> {
        if !file.starts_with(MAGIC) {
            return error("not a PNG file");
        }
        let mut png = Stream {
            file,
            at: 8,
            queue: Vec::new(),
            size: (0, 0),
            mode: None,
            info: Info::default(),
            palette: None,
            tile: None,
            n_frames: None,
            seq_num: None,
            text_memory: 0,
        };
        let (cid, length) = loop {
            let (cid, pos, length) = png.head().map_err(Error)?;
            let data = match png.call(&cid, pos, length)? {
                Called::Eof => break (cid, length),
                Called::Read(s) => s,
                Called::Unknown => png.safe_read(length)?.to_vec(),
            };
            png.crc(&cid, &data)?;
        };
        let Some((kind, raw)) = png.mode else {
            return error("cannot identify image file (no mode)");
        };
        let size = (png.size.0 as usize, png.size.1 as usize);
        if png.size.0.max(1) * png.size.1.max(1) > BOMB {
            return error("decompression bomb");
        }
        let prepare_idat = if cid == b"fdAT" { length - 4 } else { length };
        let mut n_frames = png.n_frames.unwrap_or(1) as usize;
        let apng = png.n_frames.is_some();
        if apng && png.info.default_image {
            n_frames += 1;
        }
        let mut out = Png {
            png,
            kind,
            raw,
            size,
            n_frames,
            animated: n_frames > 1,
            palette: Vec::new(),
            im: None,
            prev: None,
            prepare_idat,
            idat: 0,
            frame: 0,
            dispose_op: None,
            blend_op: None,
            dispose_extent: None,
            tile: None,
            interlaced_loads: 0,
        };
        out.tile = out.png.tile;
        if apng {
            out.seek_first();
        }
        out.animated = out.n_frames > 1;
        Ok(out)
    }

    /// _seek(0) of an APNG (what its first frame's fcTL says, if one came before the data).
    fn seek_first(&mut self) {
        self.prev = None;
        self.dispose_op = self.png.info.disposal;
        self.blend_op = self.png.info.blend;
        if let Some(b) = self.png.info.bbox {
            self.dispose_extent = Some(b);
        }
        self.frame = 0;
        if self.prev.is_none() && eq(self.dispose_op, OP_PREVIOUS) {
            self.dispose_op = Some(OP_BACKGROUND);
        }
    }

    /// The palette as Image.load puts it to the picture (putpalette: at most 256 entries).
    fn realize(&mut self) -> Result<()> {
        if self.kind != Kind::P || !self.palette.is_empty() {
            return Ok(());
        }
        let mut entries = vec![[0u8, 0, 0, 255]; 256];
        if let Some(p) = &self.png.palette {
            if p.len() / 3 > 256 {
                return error("invalid palette size");
            }
            for (e, rgb) in entries.iter_mut().zip(p.chunks_exact(3)) {
                e[..3].copy_from_slice(rgb);
            }
        }
        self.palette = entries;
        Ok(())
    }

    /// load_read: the frame's data, `n` bytes at most and one chunk at a time; the chunks
    /// after a data chunk followed (IDAT, fdAT, DDAT), stopped at any other (put back).
    fn load_read(&mut self, n: usize) -> Result<&'a [u8]> {
        while self.idat == 0 {
            self.png.read(4); // (the checksum)
            let (cid, pos, length) = self.png.head().map_err(Error)?;
            if !matches!(&cid[..], b"IDAT" | b"DDAT" | b"fdAT") {
                self.png.push(cid, pos, length);
                return Ok(&[]);
            }
            if &cid[..] == b"fdAT" {
                self.png.call(&cid, pos, length)?;
                self.idat = length - 4;
            } else {
                self.idat = length;
            }
        }
        let n = if n == 0 { self.idat } else { n.min(self.idat) };
        self.idat -= n;
        Ok(self.png.read(n))
    }

    /// ImageFile.load of the tile: load_prepare, the data decoded as ZipDecode does, then
    /// load_end (which reads on to the next frame or the end) and the decoder's error after.
    fn load(&mut self) -> Result<()> {
        let Some(tile) = self.tile.take() else {
            return if self.im.is_some() {
                Ok(())
            } else {
                error("cannot load this image")
            };
        };
        // load_prepare
        let interlaced = self.png.info.interlace;
        self.idat = self.prepare_idat;
        if self.im.is_none() {
            self.im = Some(Canvas::new(self.kind, self.size.0, self.size.1));
        }
        self.realize()?;
        if interlaced {
            self.interlaced_loads += 1;
            if self.interlaced_loads > 1 {
                return error("function takes at most 3 arguments"); // (zip_decoder's TypeError)
            }
        }
        self.png.at = tile.pos;
        let decoded = self.decode(tile, interlaced);
        // (an error reading the data is raised at once; the decoder's own after load_end)
        let failed = match decoded {
            Err(Fail::Now(e)) => return Err(e),
            Err(Fail::After(e)) => Some(e),
            Ok(()) => None,
        };
        self.load_end()?;
        match failed {
            Some(e) => Err(e),
            None => Ok(()),
        }
    }

    /// The tile decoded: ZipDecode.c fed what load_read gives it.
    fn decode(&mut self, tile: Tile, interlaced: bool) -> std::result::Result<(), Fail> {
        let (x0, y0, x1, y1) = tile.extent;
        let im = self.im.as_ref().expect("the picture");
        if x1 <= x0 || y1 <= y0 || x1 > im.w as u64 || y1 > im.h as u64 {
            return Err(Fail::Now(Error("tile cannot extend outside image".into())));
        }
        let (xoff, yoff) = (x0 as usize, y0 as usize);
        let (xsize, ysize) = ((x1 - x0) as usize, (y1 - y0) as usize);
        let raw = self.raw;
        let bits = raw.bits();
        let bytes = (bits * xsize).div_ceil(8);
        let n = self.kind.bytes();
        let bpp = bits.div_ceil(8);
        let row_len_of =
            |pass: usize| ((xsize + OFFSET[pass]) / COL_INCREMENT[pass] * bits).div_ceil(8);
        let mut z = Decompress::new(true);
        let mut buffer = vec![0u8; bytes + 1];
        let mut previous = vec![0u8; bytes + 1];
        let mut last_output = 0usize;
        let mut pass = 0usize;
        let mut y = if interlaced { STARTING_ROW[0] } else { 0 };
        let mut row_len = if interlaced { row_len_of(0) } else { bytes };
        let broken = |what: &str| Fail::After(Error(format!("{what} when reading image file")));
        loop {
            let s = self.load_read(MAX_BLOCK).map_err(Fail::Now)?;
            if s.is_empty() {
                return Err(Fail::Now(Error("image file is truncated".into())));
            }
            let mut input = s;
            while !input.is_empty() {
                let want = row_len + 1;
                let before_in = z.total_in();
                let before_out = z.total_out();
                let status = match z.decompress(
                    input,
                    &mut buffer[last_output..want],
                    FlushDecompress::None,
                ) {
                    Ok(status) => status,
                    // (Z_NEED_DICT is no error to ZipDecode.c: no output, more wanted)
                    Err(e) if e.needs_dictionary().is_some() => Status::Ok,
                    Err(_) => return Err(broken("broken data stream")),
                };
                input = &input[(z.total_in() - before_in) as usize..];
                let got = last_output + (z.total_out() - before_out) as usize;
                if status == Status::BufError {
                    return Err(broken("bad configuration"));
                }
                if got < want {
                    // (more wanted: the rest of this input counts as read, as ZipDecode.c
                    // returns all of it)
                    last_output = got;
                    break;
                }
                // the predictor
                match buffer[0] {
                    0 => {}
                    1 => {
                        for i in bpp + 1..=row_len {
                            buffer[i] = buffer[i].wrapping_add(buffer[i - bpp]);
                        }
                    }
                    2 => {
                        for i in 1..=row_len {
                            buffer[i] = buffer[i].wrapping_add(previous[i]);
                        }
                    }
                    3 => {
                        for i in 1..=row_len.min(bpp) {
                            buffer[i] = buffer[i].wrapping_add(previous[i] / 2);
                        }
                        for i in bpp + 1..=row_len {
                            let avg = (u32::from(buffer[i - bpp]) + u32::from(previous[i])) / 2;
                            buffer[i] = buffer[i].wrapping_add(avg as u8);
                        }
                    }
                    4 => {
                        for i in 1..=row_len.min(bpp) {
                            buffer[i] = buffer[i].wrapping_add(previous[i]);
                        }
                        for i in bpp + 1..=row_len {
                            let (a, b, c) = (
                                i32::from(buffer[i - bpp]),
                                i32::from(previous[i]),
                                i32::from(previous[i - bpp]),
                            );
                            let (pa, pb, pc) =
                                ((b - c).abs(), (a - c).abs(), (a + b - 2 * c).abs());
                            let p = if pa <= pb && pa <= pc {
                                a
                            } else if pb <= pc {
                                b
                            } else {
                                c
                            };
                            buffer[i] = buffer[i].wrapping_add(p as u8);
                        }
                    }
                    _ => return Err(broken("unrecognized data stream contents")),
                }
                // the pixels into the picture
                let im = self.im.as_mut().expect("the picture");
                let stride = im.w * n;
                if interlaced {
                    // (at the picture's own rows and columns: ZipDecode.c leaves the tile's
                    // place out here)
                    let mut col = STARTING_COL[pass];
                    let row = &buffer[1..];
                    if bits >= 8 {
                        let mut i = 0;
                        while i < row_len {
                            let at = y * stride + col * n;
                            raw.unpack(&row[i..], 1, &mut im.px[at..at + n]);
                            col += COL_INCREMENT[pass];
                            i += bpp;
                        }
                    } else {
                        let row_bits = (xsize + OFFSET[pass]) / COL_INCREMENT[pass] * bits;
                        let mut i = 0;
                        while i < row_bits {
                            let byte = row[i / 8] << (i % 8);
                            let at = y * stride + col * n;
                            raw.unpack(&[byte], 1, &mut im.px[at..at + n]);
                            col += COL_INCREMENT[pass];
                            i += bits;
                        }
                    }
                    y += ROW_INCREMENT[pass];
                    while y >= ysize || row_len == 0 {
                        pass += 1;
                        if pass == 7 {
                            y = ysize;
                            break;
                        }
                        y = STARTING_ROW[pass];
                        row_len = row_len_of(pass);
                        buffer.fill(0);
                    }
                } else {
                    let at = (y + yoff) * stride + xoff * n;
                    raw.unpack(&buffer[1..], xsize, &mut im.px[at..at + xsize * n]);
                    y += 1;
                }
                last_output = 0;
                if y >= ysize || status == Status::StreamEnd {
                    return Ok(());
                }
                std::mem::swap(&mut buffer, &mut previous);
            }
        }
    }

    /// load_end: the rest of the data chunk, then the chunks after it up to the end, or up to
    /// the next frame's fcTL (put back); an APNG frame blended over the picture before it.
    fn load_end(&mut self) -> Result<()> {
        if self.idat != 0 {
            let n = self.idat;
            self.png.read(n);
        }
        loop {
            self.png.read(4); // (the checksum)
            let Ok((cid, pos, length)) = self.png.head() else {
                break;
            };
            if &cid[..] == b"IEND" {
                break;
            }
            if &cid[..] == b"fcTL" && self.animated {
                self.prepare_idat = 0;
                self.png.push(cid, pos, length);
                break;
            }
            match self.png.call(&cid, pos, length)? {
                Called::Eof => {
                    let length = if &cid[..] == b"fdAT" {
                        length - 4
                    } else {
                        length
                    };
                    self.png.safe_read(length)?;
                }
                Called::Unknown => {
                    self.png.safe_read(length)?;
                }
                Called::Read(_) => {}
            }
        }
        if !self.animated {
            return Ok(());
        }
        let prev_has = self.prev.as_ref().is_some_and(|p| p.w * p.h > 0);
        if prev_has && eq(self.blend_op, OP_OVER) {
            let Some((x0, y0, x1, y1)) = self.dispose_extent else {
                return error("no extent to blend the frame in");
            };
            let b = (x0 as usize, y0 as usize, x1 as usize, y1 as usize);
            let im = self.im.as_ref().expect("the picture");
            let updated = im.crop(b);
            let alphas = self.mask(&updated)?;
            let mut prev = self.prev.take().expect("the picture before");
            prev.paste(&updated, (b.0, b.1), Some(&alphas));
            self.im = Some(prev.clone());
            self.prev = Some(prev);
        }
        Ok(())
    }

    /// The mask load_end blends a frame by: its alpha in RGBA (convert_transparent for RGB
    /// with a transparency, the palette's alphas for P with one, else as convert gives it).
    fn mask(&self, part: &Canvas) -> Result<Vec<u8>> {
        let t = &self.png.info.transparency;
        let px = &part.px;
        let count = part.w * part.h;
        Ok(match (part.kind, t) {
            (Kind::RGB, Some(Transparency::Colour(r, g, b))) => {
                let key = [(r & 0xff) as u8, (g & 0xff) as u8, (b & 0xff) as u8];
                px.chunks_exact(3)
                    .map(|p| if p == key { 0 } else { 255 })
                    .collect()
            }
            (Kind::P, t) => {
                let mut alphas: Vec<u8> = self.palette.iter().map(|e| e[3]).collect();
                match t {
                    Some(Transparency::Alphas(a)) => {
                        if a.len() > 256 {
                            return error("palette index out of range");
                        }
                        alphas[..a.len()].copy_from_slice(a);
                    }
                    Some(Transparency::Index(i)) => match alphas.get_mut(*i as usize) {
                        Some(a) => *a = 0,
                        None => return error("palette index out of range"),
                    },
                    _ => {}
                }
                px.iter().map(|&i| alphas[usize::from(i)]).collect()
            }
            (Kind::LA, _) => px.chunks_exact(2).map(|p| p[1]).collect(),
            (Kind::RGBA, _) => px.chunks_exact(4).map(|p| p[3]).collect(),
            (Kind::I16, _) => {
                return error("an I;16 frame blended over (Pillow's paste misreads it)")
            }
            _ => vec![255; count],
        })
    }

    /// _seek(n) of an APNG after its first frame: the last frame's disposal, then the chunks
    /// read up to this frame's data. Ok(false) where the file has no such frame (EOFError).
    fn seek(&mut self, n: usize) -> Result<bool> {
        let dispose = match (self.dispose_op, self.dispose_extent) {
            (op, Some((x0, y0, x1, y1))) if eq(op, OP_BACKGROUND) => Some(Canvas::new(
                self.kind,
                (x1 - x0) as usize,
                (y1 - y0) as usize,
            )),
            (op, Some(b)) if eq(op, OP_PREVIOUS) && self.prev.is_some() => {
                let p = self.prev.as_ref().expect("the picture before");
                Some(p.crop((b.0 as usize, b.1 as usize, b.2 as usize, b.3 as usize)))
            }
            (op, None) if eq(op, OP_BACKGROUND) || (eq(op, OP_PREVIOUS) && self.prev.is_some()) => {
                return error("no extent to dispose of");
            }
            _ => None,
        };
        self.load()?;
        if let (Some(d), Some(b)) = (dispose, self.dispose_extent) {
            if d.w * d.h > 0 {
                let im = self.im.as_mut().expect("the picture");
                im.paste(&d, (b.0 as usize, b.1 as usize), None);
            }
        }
        self.prev = self.im.clone();
        if self.prepare_idat != 0 {
            let skip = self.prepare_idat;
            self.png.safe_read(skip)?;
            self.prepare_idat = 0;
        }
        let mut frame_start = false;
        loop {
            self.png.read(4); // (the checksum)
            let Ok((cid, pos, length)) = self.png.head() else {
                break;
            };
            if &cid[..] == b"IEND" {
                return Ok(false);
            }
            if &cid[..] == b"fcTL" {
                if frame_start {
                    return error("APNG missing frame data");
                }
                frame_start = true;
            }
            match self.png.call(&cid, pos, length)? {
                Called::Eof => {
                    let mut length = length;
                    if &cid[..] == b"fdAT" {
                        length -= 4;
                        if frame_start {
                            self.prepare_idat = length;
                            break;
                        }
                    }
                    self.png.safe_read(length)?;
                }
                Called::Unknown => {
                    self.png.safe_read(length)?;
                }
                Called::Read(_) => {}
            }
        }
        self.frame = n;
        self.tile = self.png.tile;
        self.dispose_op = self.png.info.disposal;
        self.blend_op = self.png.info.blend;
        if self.tile.is_none() {
            return Ok(false);
        }
        if let Some(b) = self.png.info.bbox {
            self.dispose_extent = Some(b);
        }
        if self.prev.is_none() && eq(self.dispose_op, OP_PREVIOUS) {
            self.dispose_op = Some(OP_BACKGROUND);
        }
        Ok(true)
    }

    /// The picture as it stands, with what Pillow's info says of it.
    fn picture(&self) -> Picture {
        let im = self.im.as_ref().expect("the picture");
        let info = &self.png.info;
        Picture {
            kind: self.kind,
            width: im.w,
            height: im.h,
            data: im.px.clone(),
            palette: (self.kind == Kind::P).then(|| self.palette.clone()),
            transparency: info.transparency.clone(),
            exif: info.exif.clone(),
            raw_exif: info.raw_exif.clone(),
            xmp: info.xmp.clone(),
            xmp_bytes: info.xmp_bytes.clone(),
        }
    }
}

/// Why the decoding stopped: an error raised at once (reading the file), or the decoder's own,
/// raised after load_end.
enum Fail {
    Now(Error),
    After(Error),
}

/// The picture's first frame, as Image.open and load give it.
pub fn decode(file: &[u8]) -> Result<Picture> {
    let mut png = Png::open(file)?;
    png.load()?;
    Ok(png.picture())
}

/// Every frame as the app reads an animation (an APNG's; a PNG's one), each with its
/// info["duration"] and converted to RGBA.
pub fn frames(file: &[u8]) -> Result<Vec<Frame>> {
    let mut png = Png::open(file)?;
    png.load()?;
    let mut out = Vec::new();
    let rgba = |p: &Png| -> Result<lp_pixels::Image> { p.picture().convert(lp_pixels::Mode::RGBA) };
    out.push(Frame {
        duration: png.png.info.duration,
        image: rgba(&png)?,
    });
    for n in 1..png.n_frames {
        if !png.seek(n)? {
            break;
        }
        let duration = png.png.info.duration;
        png.load()?;
        out.push(Frame {
            duration,
            image: rgba(&png)?,
        });
    }
    Ok(out)
}
