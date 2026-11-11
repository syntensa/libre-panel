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

use crate::{
    bomb_check, opened_check, opening, raise, Error, Exif, Frame, Kind, Picture, Raised, Result,
};

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
        .map_err(|e| Error::new(Raised::Unported, format!("libwebp: {e}")))
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

/// WebPImageFile._open (Image.open's: its errors as Image.open takes them, then the size
/// checked), then each frame `get_next` gives (as many as `want`), one after the other to
/// `each`; the count of frames the file has.
fn read(data: &[u8], want: usize, each: &mut Each) -> Result<usize> {
    if !accepts(data) {
        return Err(opening(Error::new(Raised::SyntaxError, "not a WEBP file")));
    }
    if let Some((w, h)) = header_size(&data[..data.len().min(30)]) {
        bomb_check(w as i64, h as i64)?;
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
        return raise(Raised::OSError, "could not create decoder object");
    }
    let result = (|| -> Result<usize> {
        // SAFETY: zeroed is a valid WebPAnimInfo; dec is a live decoder
        let mut info: AnimInfo = unsafe { std::mem::zeroed() };
        if unsafe { (lib.info)(dec, &mut info) } == 0 {
            return raise(Raised::OSError, "could not create decoder object");
        }
        let size = (info.canvas_width as usize, info.canvas_height as usize);
        // (ImageFile's check of the size the decoder gives, and Image.open's bomb check)
        opened_check(true, size.0 as i64, size.1 as i64)?;
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
                return raise(Raised::OSError, "failed to read next frame");
            }
            let canvas = unsafe { std::slice::from_raw_parts(buf, size.0 * size.1 * 4) }.to_vec();
            each(&decoded, all, canvas, timestamp)?;
        }
        Ok(all)
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
    first.map_or_else(|| raise(Raised::OSError, "failed to read next frame"), Ok)
}

/// Image.open's count of the frames (n_frames: the decoder's frame_count).
pub(crate) fn n_frames(data: &[u8]) -> Result<usize> {
    read(data, 0, &mut |_, _, _, _| Ok(()))
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
    .map(|_| ())
}

// -- writing an animation -----------------------------------------------------------------------

/// encode.h's and mux.h's interface versions (their major byte must match the library's).
const ENCODER_ABI: c_int = 0x0210;
const MUX_ABI: c_int = 0x0109;

#[repr(C)]
struct Config {
    lossless: c_int,
    quality: f32,
    method: c_int,
    image_hint: c_int,
    target_size: c_int,
    target_psnr: f32,
    /// segments, sns_strength, filter_strength, filter_sharpness, filter_type, autofilter,
    /// alpha_compression, alpha_filtering
    filters: [c_int; 8],
    alpha_quality: c_int,
    /// pass ... qmax
    rest: [c_int; 14],
}

#[repr(C)]
struct WebPPicture {
    use_argb: c_int,
    colorspace: c_int,
    width: c_int,
    height: c_int,
    y: *mut u8,
    u: *mut u8,
    v: *mut u8,
    y_stride: c_int,
    uv_stride: c_int,
    a: *mut u8,
    a_stride: c_int,
    pad1: [u32; 2],
    argb: *mut u32,
    argb_stride: c_int,
    pad2: [u32; 3],
    writer: *mut c_void,
    custom_ptr: *mut c_void,
    extra_info_type: c_int,
    extra_info: *mut u8,
    stats: *mut c_void,
    error_code: c_int,
    progress_hook: *mut c_void,
    user_data: *mut c_void,
    pad3: [u32; 3],
    pad4: *mut u8,
    pad5: *mut u8,
    pad6: [u32; 8],
    memory: *mut c_void,
    memory_argb: *mut c_void,
    pad7: [*mut c_void; 2],
}

#[repr(C)]
struct AnimOptions {
    bgcolor: u32,
    loop_count: c_int,
    minimize_size: c_int,
    kmin: c_int,
    kmax: c_int,
    allow_mixed: c_int,
    verbose: c_int,
    padding: [u32; 4],
}

#[repr(C)]
struct OutData {
    bytes: *mut u8,
    size: usize,
}

struct EncLib {
    _webp: libloading::Library,
    _mux: libloading::Library,
    config_init: unsafe extern "C" fn(*mut Config, c_int, f32, c_int) -> c_int,
    validate: unsafe extern "C" fn(*const Config) -> c_int,
    picture_init: unsafe extern "C" fn(*mut WebPPicture, c_int) -> c_int,
    picture_alloc: unsafe extern "C" fn(*mut WebPPicture) -> c_int,
    picture_free: unsafe extern "C" fn(*mut WebPPicture),
    free: unsafe extern "C" fn(*mut c_void),
    options_init: unsafe extern "C" fn(*mut AnimOptions, c_int) -> c_int,
    new: unsafe extern "C" fn(c_int, c_int, *const AnimOptions, c_int) -> *mut c_void,
    add: unsafe extern "C" fn(*mut c_void, *mut WebPPicture, c_int, *const Config) -> c_int,
    assemble: unsafe extern "C" fn(*mut c_void, *mut OutData) -> c_int,
    error: unsafe extern "C" fn(*mut c_void) -> *const std::ffi::c_char,
    delete: unsafe extern "C" fn(*mut c_void),
}

static ENC: OnceLock<std::result::Result<EncLib, String>> = OnceLock::new();

fn load_encoder() -> std::result::Result<EncLib, String> {
    let webp = match std::env::var("LP_LIBWEBP") {
        Ok(path) => open(&path)?,
        Err(_) => first(&["libwebp.so.7", "libwebp.dll", "libwebp.7.dylib"])?,
    };
    let mux = match std::env::var("LP_LIBWEBPMUX") {
        Ok(path) => open(&path)?,
        Err(_) => first(&["libwebpmux.so.3", "libwebpmux.dll", "libwebpmux.3.dylib"])?,
    };
    macro_rules! get {
        ($lib:expr, $name:literal) => {
            // SAFETY: the symbol is libwebp's function of that name and type
            *unsafe { $lib.get(concat!($name, "\0").as_bytes()) }.map_err(|e| e.to_string())?
        };
    }
    Ok(EncLib {
        config_init: get!(webp, "WebPConfigInitInternal"),
        validate: get!(webp, "WebPValidateConfig"),
        picture_init: get!(webp, "WebPPictureInitInternal"),
        picture_alloc: get!(webp, "WebPPictureAlloc"),
        picture_free: get!(webp, "WebPPictureFree"),
        free: get!(webp, "WebPFree"),
        options_init: get!(mux, "WebPAnimEncoderOptionsInitInternal"),
        new: get!(mux, "WebPAnimEncoderNewInternal"),
        add: get!(mux, "WebPAnimEncoderAdd"),
        assemble: get!(mux, "WebPAnimEncoderAssemble"),
        error: get!(mux, "WebPAnimEncoderGetError"),
        delete: get!(mux, "WebPAnimEncoderDelete"),
        _webp: webp,
        _mux: mux,
    })
}

/// The encoder made by WebPAnimEncoderNew and the picture it is handed, freed once.
struct Encoder<'a> {
    lib: &'a EncLib,
    enc: *mut c_void,
    frame: WebPPicture,
}

impl Drop for Encoder<'_> {
    fn drop(&mut self) {
        // SAFETY: both made below, freed once
        unsafe {
            (self.lib.picture_free)(&mut self.frame);
            (self.lib.delete)(self.enc);
        }
    }
}

impl Encoder<'_> {
    fn failed(&self) -> Error {
        // SAFETY: a live encoder; its message is a C string of its own
        let said = unsafe { std::ffi::CStr::from_ptr((self.lib.error)(self.enc)) };
        Error::new(Raised::OSError, said.to_string_lossy().into_owned())
    }
}

/// `frames[0].save(fp, "WEBP", save_all=True, append_images=frames[1:], loop=loop_count,
/// duration=duration, quality=quality, method=method)` as Pillow 13.0 writes an animation of
/// more than one picture (WebPImagePlugin._save_all, _webp.c): libwebp's animation encoder
/// with Pillow's defaults (a clear background, no minimising, key frames 3 to 5 apart, lossy,
/// no mixing, alpha at 100), each picture handed over as ARGB (RGB and RGBX opaque), the
/// last one's length by an empty picture at the end; no ICC, EXIF or XMP.
pub fn save_animation(
    frames: &[lp_pixels::Image],
    duration: i64,
    loop_count: i32,
    quality: f32,
    method: i32,
) -> Result<Vec<u8>> {
    let Some(first_frame) = frames.first() else {
        return raise(Raised::ValueError, "no pictures to write");
    };
    let (width, height) = first_frame.size();
    let lib = ENC
        .get_or_init(load_encoder)
        .as_ref()
        .map_err(|e| Error::new(Raised::Unported, format!("libwebp: {e}")))?;
    // SAFETY: zeroed structures are filled in by libwebp's own initialisers before use
    let mut options: AnimOptions = unsafe { std::mem::zeroed() };
    if unsafe { (lib.options_init)(&mut options, MUX_ABI) } == 0 {
        return raise(Raised::OSError, "failed to initialize encoder options");
    }
    options.bgcolor = 0;
    options.loop_count = loop_count;
    options.minimize_size = 0;
    options.kmin = 3;
    options.kmax = 5;
    options.allow_mixed = 0;
    options.verbose = 0;
    let (w, h) = (
        c_int::try_from(width).unwrap_or(0),
        c_int::try_from(height).unwrap_or(0),
    );
    if w <= 0 || h <= 0 {
        return raise(Raised::ValueError, "invalid canvas dimensions");
    }
    // SAFETY: as above
    let mut frame: WebPPicture = unsafe { std::mem::zeroed() };
    if unsafe { (lib.picture_init)(&mut frame, ENCODER_ABI) } == 0 {
        return raise(Raised::OSError, "could not create encoder object");
    }
    // SAFETY: options set up by its initialiser
    let enc = unsafe { (lib.new)(w, h, &options, MUX_ABI) };
    if enc.is_null() {
        return raise(Raised::OSError, "could not create encoder object");
    }
    let mut encoder = Encoder { lib, enc, frame };
    let mut timestamp: i64 = 0;
    for picture in frames {
        let mode = picture.mode();
        let opaque = match mode {
            lp_pixels::Mode::RGBA => false,
            lp_pixels::Mode::RGB => true,
            other => {
                return raise(
                    Raised::ValueError,
                    format!("cannot write mode {other} as WEBP"),
                )
            }
        };
        // SAFETY: as above
        let mut config: Config = unsafe { std::mem::zeroed() };
        if unsafe { (lib.config_init)(&mut config, 0, 75.0, ENCODER_ABI) } == 0 {
            return raise(Raised::OSError, "failed to initialize config!");
        }
        config.lossless = 0;
        config.quality = quality;
        config.alpha_quality = 100;
        config.method = method;
        if unsafe { (lib.validate)(&config) } == 0 {
            return raise(Raised::ValueError, "invalid configuration");
        }
        let (pw, ph) = picture.size();
        let frame = &mut encoder.frame;
        frame.width = c_int::try_from(pw).unwrap_or(0);
        frame.height = c_int::try_from(ph).unwrap_or(0);
        frame.use_argb = 1;
        // SAFETY: the picture set up by its initialiser; Alloc frees what it held before
        if unsafe { (lib.picture_alloc)(frame) } == 0 {
            return raise(Raised::OSError, "can't allocate picture frame");
        }
        let bands = picture.bands();
        let data = picture.as_bytes();
        let stride = frame.argb_stride as usize;
        for y in 0..ph {
            // SAFETY: Alloc made argb_stride * height pixels
            let row = unsafe { std::slice::from_raw_parts_mut(frame.argb.add(y * stride), pw) };
            for (x, out) in row.iter_mut().enumerate() {
                let p = &data[(y * pw + x) * bands..];
                let alpha = if opaque { 0xff } else { u32::from(p[3]) };
                *out = u32::from(p[2])
                    | (u32::from(p[1]) << 8)
                    | (u32::from(p[0]) << 16)
                    | (alpha << 24);
            }
        }
        let at = c_int::try_from(timestamp).unwrap_or(c_int::MAX);
        // SAFETY: a live encoder, a whole picture and a checked config
        if unsafe { (lib.add)(encoder.enc, &mut encoder.frame, at, &config) } == 0 {
            return Err(encoder.failed());
        }
        timestamp += duration;
    }
    let at = c_int::try_from(timestamp).unwrap_or(c_int::MAX);
    // SAFETY: an empty picture says how long the last lasts
    unsafe { (lib.add)(encoder.enc, std::ptr::null_mut(), at, std::ptr::null()) };
    let mut out = OutData {
        bytes: std::ptr::null_mut(),
        size: 0,
    };
    // SAFETY: a live encoder; what it hands back is libwebp's to free
    if unsafe { (lib.assemble)(encoder.enc, &mut out) } == 0 {
        return Err(encoder.failed());
    }
    // SAFETY: `size` bytes made by libwebp, copied and then freed once
    let file = unsafe { std::slice::from_raw_parts(out.bytes, out.size) }.to_vec();
    unsafe { (lib.free)(out.bytes.cast()) };
    Ok(file)
}
