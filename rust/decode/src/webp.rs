//! WebP as Pillow 13.0 reads it (WebPImagePlugin.py, _webp.c): every WebP, still or animated,
//! through libwebp's animation decoder (WebPAnimDecoderNew with its defaults: each frame the
//! whole canvas in RGBA, laid on the last by libwebp itself), its mode RGBA where
//! WebPGetFeatures finds alpha and else RGB (the canvas read as RGBX), its EXIF and XMP from
//! the demuxer.
//!
//! libwebp stays a library of C (docs/RUST.md), loaded where the app runs: LP_LIBWEBPDEMUX
//! and LP_LIBWEBP name its two files, else the system's.

use std::ffi::{c_int, c_void};
use std::sync::OnceLock;

use crate::{error, Error, Exif, Frame, Kind, Picture, Result};

/// Pillow's MAX_IMAGE_PIXELS twice: a size beyond it is refused as a decompression bomb.
const BOMB: u64 = 2 * (1024 * 1024 * 1024 / 4 / 3);

/// decode.h's and demux.h's interface versions (their major byte must match the library's).
const DECODER_ABI: c_int = 0x0210;
const DEMUX_ABI: c_int = 0x0107;

#[repr(C)]
struct Features {
    width: c_int,
    height: c_int,
    has_alpha: c_int,
    has_animation: c_int,
    format: c_int,
    pad: [u32; 5],
}

#[repr(C)]
struct Data {
    bytes: *const u8,
    size: usize,
}

#[repr(C)]
struct AnimInfo {
    canvas_width: u32,
    canvas_height: u32,
    loop_count: u32,
    bgcolor: u32,
    frame_count: u32,
    pad: [u32; 4],
}

#[repr(C)]
struct ChunkIterator {
    chunk_num: c_int,
    num_chunks: c_int,
    chunk: Data,
    pad: [u32; 6],
    private: *mut c_void,
}

struct Lib {
    _webp: libloading::Library,
    _demux: libloading::Library,
    features: unsafe extern "C" fn(*const u8, usize, *mut Features, c_int) -> c_int,
    new: unsafe extern "C" fn(*const Data, *const c_void, c_int) -> *mut c_void,
    info: unsafe extern "C" fn(*const c_void, *mut AnimInfo) -> c_int,
    next: unsafe extern "C" fn(*mut c_void, *mut *mut u8, *mut c_int) -> c_int,
    delete: unsafe extern "C" fn(*mut c_void),
    demuxer: unsafe extern "C" fn(*const c_void) -> *const c_void,
    chunk: unsafe extern "C" fn(*const c_void, *const u8, c_int, *mut ChunkIterator) -> c_int,
    release: unsafe extern "C" fn(*mut ChunkIterator),
}

static LIB: OnceLock<std::result::Result<Lib, String>> = OnceLock::new();

fn open(name: &str) -> std::result::Result<libloading::Library, String> {
    // SAFETY: libwebp's initialisers run no code beyond its own
    unsafe { libloading::Library::new(name) }.map_err(|e| e.to_string())
}

fn first(names: &[&str]) -> std::result::Result<libloading::Library, String> {
    let mut last = String::from("no name");
    for name in names {
        match open(name) {
            Ok(lib) => return Ok(lib),
            Err(e) => last = e,
        }
    }
    Err(last)
}

fn load() -> std::result::Result<Lib, String> {
    let webp = match std::env::var("LP_LIBWEBP") {
        Ok(path) => open(&path)?,
        Err(_) => first(&["libwebp.so.7", "libwebp.dll", "libwebp.7.dylib"])?,
    };
    let demux = match std::env::var("LP_LIBWEBPDEMUX") {
        Ok(path) => open(&path)?,
        Err(_) => first(&[
            "libwebpdemux.so.2",
            "libwebpdemux.dll",
            "libwebpdemux.2.dylib",
        ])?,
    };
    macro_rules! get {
        ($lib:expr, $name:literal) => {
            // SAFETY: the symbol is libwebp's function of that name and type
            *unsafe { $lib.get(concat!($name, "\0").as_bytes()) }.map_err(|e| e.to_string())?
        };
    }
    Ok(Lib {
        features: get!(webp, "WebPGetFeaturesInternal"),
        new: get!(demux, "WebPAnimDecoderNewInternal"),
        info: get!(demux, "WebPAnimDecoderGetInfo"),
        next: get!(demux, "WebPAnimDecoderGetNext"),
        delete: get!(demux, "WebPAnimDecoderDelete"),
        demuxer: get!(demux, "WebPAnimDecoderGetDemuxer"),
        chunk: get!(demux, "WebPDemuxGetChunk"),
        release: get!(demux, "WebPDemuxReleaseChunkIterator"),
        _webp: webp,
        _demux: demux,
    })
}

fn lib() -> Result<&'static Lib> {
    LIB.get_or_init(load)
        .as_ref()
        .map_err(|e| Error(format!("libwebp: {e}")))
}

/// _accept: RIFF, WEBP, and one of VP8's three kinds.
pub(crate) fn accepts(data: &[u8]) -> bool {
    data.starts_with(b"RIFF")
        && data.get(8..12) == Some(b"WEBP")
        && matches!(data.get(12..15), Some(b"VP8"))
        && matches!(data.get(15), Some(b' ' | b'X' | b'L'))
}

/// _get_size: the canvas's size from the first chunk's header, where it says it (and its
/// header is whole).
fn header_size(s: &[u8]) -> Option<(u64, u64)> {
    let le16 = |at: usize| u64::from(u16::from_le_bytes([s[at], s[at + 1]]));
    let le24 =
        |at: usize| u64::from(s[at]) | u64::from(s[at + 1]) << 8 | u64::from(s[at + 2]) << 16;
    match s.get(12..16)? {
        b"VP8 " if s.len() >= 30 && &s[23..26] == b"\x9d\x01\x2a" => {
            Some((le16(26) & 0x3fff, le16(28) & 0x3fff))
        }
        b"VP8X" if s.len() >= 30 => Some((le24(24) + 1, le24(27) + 1)),
        b"VP8L" if s.len() >= 25 && s[20] == 0x2f => {
            let bits = le24(21) | u64::from(s[24]) << 24;
            Some(((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1))
        }
        _ => None,
    }
}

/// What the animation decoder says of the picture: the mode (RGBA, or RGB where there is no
/// alpha), its canvas's size and the EXIF and XMP chunks.
struct Decoded {
    kind: Kind,
    size: (usize, usize),
    exif: Option<Vec<u8>>,
    xmp: Option<Vec<u8>>,
}

/// A frame's canvas in RGBA and its timestamp, and the count of frames the file has.
type Each<'a> = dyn FnMut(&Decoded, usize, Vec<u8>, i32) -> Result<()> + 'a;

/// WebPImageFile._open, then each frame `get_next` gives (as many as `want`), one after the
/// other to `each`.
fn read(data: &[u8], want: usize, each: &mut Each) -> Result<()> {
    if !accepts(data) {
        return error("not a WEBP file");
    }
    if let Some((w, h)) = header_size(&data[..data.len().min(30)]) {
        if w.max(1) * h.max(1) > BOMB {
            return error("decompression bomb");
        }
    }
    let lib = lib()?;
    // SAFETY: zeroed is a valid WebPBitstreamFeatures (all numbers)
    let mut features: Features = unsafe { std::mem::zeroed() };
    // SAFETY: data and features are valid for the call
    let sniffed = unsafe { (lib.features)(data.as_ptr(), data.len(), &mut features, DECODER_ABI) };
    let kind = if sniffed == 0 && features.has_alpha == 0 {
        Kind::RGB
    } else {
        Kind::RGBA
    };
    let source = Data {
        bytes: data.as_ptr(),
        size: data.len(),
    };
    // SAFETY: the decoder copies nothing it does not own; `data` outlives it (deleted below)
    let dec = unsafe { (lib.new)(&source, std::ptr::null(), DEMUX_ABI) };
    if dec.is_null() {
        return error("could not create decoder object");
    }
    let result = (|| -> Result<()> {
        // SAFETY: zeroed is a valid WebPAnimInfo; dec is a live decoder
        let mut info: AnimInfo = unsafe { std::mem::zeroed() };
        if unsafe { (lib.info)(dec, &mut info) } == 0 {
            return error("could not create decoder object");
        }
        let size = (info.canvas_width as usize, info.canvas_height as usize);
        let chunk = |name: &[u8; 4]| -> Option<Vec<u8>> {
            // SAFETY: the demuxer is the decoder's, alive with it; the iterator is released
            unsafe {
                let demux = (lib.demuxer)(dec);
                let mut iter: ChunkIterator = std::mem::zeroed();
                if (lib.chunk)(demux, name.as_ptr(), 1, &mut iter) == 0 {
                    return None;
                }
                let bytes = std::slice::from_raw_parts(iter.chunk.bytes, iter.chunk.size).to_vec();
                (lib.release)(&mut iter);
                (!bytes.is_empty()).then_some(bytes)
            }
        };
        let decoded = Decoded {
            kind,
            size,
            exif: chunk(b"EXIF"),
            xmp: chunk(b"XMP "),
        };
        let all = info.frame_count as usize;
        for _ in 0..all.min(want) {
            let mut buf: *mut u8 = std::ptr::null_mut();
            let mut timestamp: c_int = 0;
            // SAFETY: dec is live; buf is the decoder's canvas, read before the next call
            if unsafe { (lib.next)(dec, &mut buf, &mut timestamp) } == 0 || buf.is_null() {
                return error("failed to read next frame");
            }
            let canvas = unsafe { std::slice::from_raw_parts(buf, size.0 * size.1 * 4) }.to_vec();
            each(&decoded, all, canvas, timestamp)?;
        }
        Ok(())
    })();
    // SAFETY: made by WebPAnimDecoderNewInternal, deleted once
    unsafe { (lib.delete)(dec) };
    result
}

fn picture(
    kind: Kind,
    size: (usize, usize),
    canvas: Vec<u8>,
    exif: Option<Vec<u8>>,
    xmp: Option<Vec<u8>>,
) -> Picture {
    let data = match kind {
        Kind::RGB => canvas
            .chunks_exact(4)
            .flat_map(|p| [p[0], p[1], p[2]])
            .collect(),
        _ => canvas,
    };
    Picture {
        kind,
        width: size.0,
        height: size.1,
        data,
        palette: None,
        transparency: None,
        exif: exif.map(Exif::Bytes),
        raw_exif: None,
        xmp: None,
        xmp_bytes: xmp,
        l_over_p: false,
    }
}

/// The first frame as Image.open loads it.
pub(crate) fn decode(data: &[u8]) -> Result<Picture> {
    let mut first = None;
    read(data, 1, &mut |d, _, canvas, _| {
        first = Some(picture(
            d.kind,
            d.size,
            canvas,
            d.exif.clone(),
            d.xmp.clone(),
        ));
        Ok(())
    })?;
    first.map_or_else(|| error("failed to read next frame"), Ok)
}

/// Every frame as the app reads an animation, one after the other to `each` with the count of
/// them (n_frames). The app reads a frame's duration before the
/// frame is loaded, and a WebP's is set only by its loading (seek takes the last one away):
/// so the first frame's is its own, the later ones' none.
pub(crate) fn each_frame(
    data: &[u8],
    each: &mut dyn FnMut(usize, Frame) -> Result<()>,
) -> Result<()> {
    let mut n = 0;
    read(data, usize::MAX, &mut |d, count, canvas, timestamp| {
        let image = picture(d.kind, d.size, canvas, None, None).convert(lp_pixels::Mode::RGBA)?;
        let duration = (n == 0).then_some(f64::from(timestamp));
        n += 1;
        each(count, Frame { duration, image })
    })
}
