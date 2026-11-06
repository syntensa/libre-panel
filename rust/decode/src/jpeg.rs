//! JPEG as Pillow 13.0 reads it (JpegImagePlugin.py, MpoImagePlugin.py, JpegDecode.c): its
//! markers read as _open reads them (the mode from the frame's layers; the EXIF, XMP and the
//! MP index kept as Pillow keeps them), then the pixels decoded by libjpeg-turbo itself, the
//! library Pillow decodes with, set up as JpegDecode.c sets it up (the output colour space of
//! the raw mode, a scale where draft asked for one, a source that suspends at the data's end,
//! which Pillow takes as a truncated file).
//!
//! libjpeg-turbo stays a library of C (docs/RUST.md): it is loaded where the app runs, the
//! file LP_LIBJPEG names or else the system's libjpeg of the 6.2 interface (Pillow's). Its
//! errors unwind out of it (error_exit, as Pillow's longjmp leaves it).

use std::ffi::{c_int, c_long, c_uint, c_void};
use std::panic::{self, AssertUnwindSafe};
use std::sync::OnceLock;

use crate::{error, Error, Exif, Frame, Kind, Picture, Result};

/// Pillow's MAX_IMAGE_PIXELS twice: a size beyond it is refused as a decompression bomb.
const BOMB: u64 = 2 * (1024 * 1024 * 1024 / 4 / 3);

#[cfg(windows)]
type Boolean = u8;
#[cfg(not(windows))]
type Boolean = c_int;

const JCS_GRAYSCALE: c_int = 1;
const JCS_CMYK: c_int = 4;
const JCS_EXT_RGBX: c_int = 7;
const JPEG_SUSPENDED: c_int = 0;
const JPEG_HEADER_TABLES_ONLY: c_int = 2;

/// jpeg_error_mgr (jpeglib.h); its methods nullable (None) until jpeg_std_error fills them.
#[repr(C)]
struct ErrorMgr {
    error_exit: Option<unsafe extern "C-unwind" fn(*mut Decompress)>,
    emit_message: Option<unsafe extern "C-unwind" fn(*mut Decompress, c_int)>,
    output_message: Option<unsafe extern "C-unwind" fn(*mut Decompress)>,
    format_message: Option<unsafe extern "C-unwind" fn(*mut Decompress, *mut u8)>,
    reset_error_mgr: Option<unsafe extern "C-unwind" fn(*mut Decompress)>,
    msg_code: c_int,
    msg_parm: [u8; 80],
    trace_level: c_int,
    num_warnings: c_long,
    jpeg_message_table: *const *const u8,
    last_jpeg_message: c_int,
    addon_message_table: *const *const u8,
    first_addon_message: c_int,
    last_addon_message: c_int,
}

/// jpeg_source_mgr, with what JpegDecode.c's JPEGSOURCE adds.
#[repr(C)]
struct Source {
    next_input_byte: *const u8,
    bytes_in_buffer: usize,
    init_source: unsafe extern "C-unwind" fn(*mut Decompress),
    fill_input_buffer: unsafe extern "C-unwind" fn(*mut Decompress) -> Boolean,
    skip_input_data: unsafe extern "C-unwind" fn(*mut Decompress, c_long),
    resync_to_restart: unsafe extern "C-unwind" fn(*mut Decompress, c_int) -> Boolean,
    term_source: unsafe extern "C-unwind" fn(*mut Decompress),
    skip: c_long,
}

/// jpeg_decompress_struct of the 6.2 interface (JPEG_LIB_VERSION 62), as libjpeg-turbo 3
/// lays it out.
#[repr(C)]
struct Decompress {
    err: *mut ErrorMgr,
    mem: *mut c_void,
    progress: *mut c_void,
    client_data: *mut c_void,
    is_decompressor: Boolean,
    global_state: c_int,
    src: *mut Source,
    image_width: c_uint,
    image_height: c_uint,
    num_components: c_int,
    jpeg_color_space: c_int,
    out_color_space: c_int,
    scale_num: c_uint,
    scale_denom: c_uint,
    output_gamma: f64,
    buffered_image: Boolean,
    raw_data_out: Boolean,
    dct_method: c_int,
    do_fancy_upsampling: Boolean,
    do_block_smoothing: Boolean,
    quantize_colors: Boolean,
    dither_mode: c_int,
    two_pass_quantize: Boolean,
    desired_number_of_colors: c_int,
    enable_1pass_quant: Boolean,
    enable_external_quant: Boolean,
    enable_2pass_quant: Boolean,
    output_width: c_uint,
    output_height: c_uint,
    out_color_components: c_int,
    output_components: c_int,
    rec_outbuf_height: c_int,
    actual_number_of_colors: c_int,
    colormap: *mut c_void,
    output_scanline: c_uint,
    input_scan_number: c_int,
    input_imcu_row: c_uint,
    output_scan_number: c_int,
    output_imcu_row: c_uint,
    coef_bits: *mut c_void,
    quant_tbl_ptrs: [*mut c_void; 4],
    dc_huff_tbl_ptrs: [*mut c_void; 4],
    ac_huff_tbl_ptrs: [*mut c_void; 4],
    data_precision: c_int,
    comp_info: *mut c_void,
    progressive_mode: Boolean,
    arith_code: Boolean,
    arith_dc_l: [u8; 16],
    arith_dc_u: [u8; 16],
    arith_ac_k: [u8; 16],
    restart_interval: c_uint,
    saw_jfif_marker: Boolean,
    jfif_major_version: u8,
    jfif_minor_version: u8,
    density_unit: u8,
    x_density: u16,
    y_density: u16,
    saw_adobe_marker: Boolean,
    adobe_transform: u8,
    ccir601_sampling: Boolean,
    marker_list: *mut c_void,
    max_h_samp_factor: c_int,
    max_v_samp_factor: c_int,
    min_dct_scaled_size: c_int,
    total_imcu_rows: c_uint,
    sample_range_limit: *mut u8,
    comps_in_scan: c_int,
    cur_comp_info: [*mut c_void; 4],
    mcus_per_row: c_uint,
    mcu_rows_in_scan: c_uint,
    blocks_in_mcu: c_int,
    mcu_membership: [c_int; 10],
    ss: c_int,
    se: c_int,
    ah: c_int,
    al: c_int,
    unread_marker: c_int,
    master: *mut c_void,
    main: *mut c_void,
    coef: *mut c_void,
    post: *mut c_void,
    inputctl: *mut c_void,
    marker: *mut c_void,
    entropy: *mut c_void,
    idct: *mut c_void,
    upsample: *mut c_void,
    cconvert: *mut c_void,
    cquantize: *mut c_void,
}

/// The library's functions this needs.
struct Lib {
    _lib: libloading::Library,
    std_error: unsafe extern "C-unwind" fn(*mut ErrorMgr) -> *mut ErrorMgr,
    create: unsafe extern "C-unwind" fn(*mut Decompress, c_int, usize),
    destroy: unsafe extern "C-unwind" fn(*mut Decompress),
    read_header: unsafe extern "C-unwind" fn(*mut Decompress, Boolean) -> c_int,
    start: unsafe extern "C-unwind" fn(*mut Decompress) -> Boolean,
    read_scanlines: unsafe extern "C-unwind" fn(*mut Decompress, *mut *mut u8, c_uint) -> c_uint,
    finish: unsafe extern "C-unwind" fn(*mut Decompress) -> Boolean,
    resync: unsafe extern "C-unwind" fn(*mut Decompress, c_int) -> Boolean,
}

static LIB: OnceLock<std::result::Result<Lib, String>> = OnceLock::new();

fn load(path: &str) -> std::result::Result<Lib, String> {
    // SAFETY: libjpeg's initialisers run no code beyond its own
    let lib = unsafe { libloading::Library::new(path) }.map_err(|e| e.to_string())?;
    macro_rules! get {
        ($name:literal) => {
            // SAFETY: the symbol is the 6.2 interface's function of that name and type
            *unsafe { lib.get(concat!($name, "\0").as_bytes()) }.map_err(|e| e.to_string())?
        };
    }
    Ok(Lib {
        std_error: get!("jpeg_std_error"),
        create: get!("jpeg_CreateDecompress"),
        destroy: get!("jpeg_destroy_decompress"),
        read_header: get!("jpeg_read_header"),
        start: get!("jpeg_start_decompress"),
        read_scanlines: get!("jpeg_read_scanlines"),
        finish: get!("jpeg_finish_decompress"),
        resync: get!("jpeg_resync_to_restart"),
        _lib: lib,
    })
}

/// libjpeg: the file LP_LIBJPEG names, else the system's of the 6.2 interface.
fn lib() -> Result<&'static Lib> {
    let found = LIB.get_or_init(|| match std::env::var("LP_LIBJPEG") {
        Ok(path) => load(&path),
        Err(_) => ["libjpeg.so.62", "jpeg62.dll", "libjpeg.62.dylib"]
            .iter()
            .map(|name| load(name))
            .find(|lib| lib.is_ok())
            .unwrap_or_else(|| Err("no libjpeg of the 6.2 interface".into())),
    });
    found.as_ref().map_err(|e| Error(format!("libjpeg: {e}")))
}

/// What error_exit unwinds with.
struct Broken;

unsafe extern "C-unwind" fn error_exit(_: *mut Decompress) {
    panic::resume_unwind(Box::new(Broken));
}

unsafe extern "C-unwind" fn output_message(_: *mut Decompress) {}

unsafe extern "C-unwind" fn stub(_: *mut Decompress) {}

unsafe extern "C-unwind" fn fill_input_buffer(_: *mut Decompress) -> Boolean {
    0 // (suspension: the data has no more)
}

unsafe extern "C-unwind" fn skip_input_data(cinfo: *mut Decompress, n: c_long) {
    // SAFETY: src is the Source this decoder made
    let source = unsafe { &mut *(*cinfo).src };
    if n > source.bytes_in_buffer as c_long {
        source.skip = n - source.bytes_in_buffer as c_long;
        // SAFETY: within the data (to its end)
        source.next_input_byte = unsafe { source.next_input_byte.add(source.bytes_in_buffer) };
        source.bytes_in_buffer = 0;
    } else {
        source.bytes_in_buffer -= n as usize;
        // SAFETY: within the data
        source.next_input_byte = unsafe { source.next_input_byte.add(n as usize) };
        source.skip = 0;
    }
}

/// The decoder's output: libjpeg's colour space for Pillow's raw mode.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Raw {
    L,
    /// RGB (JCS_EXT_RGBX: Pillow's own four bytes a pixel)
    Rgbx,
    /// CMYK;I: Adobe's inverted CMYK
    CmykI,
}

impl Raw {
    fn bytes(self) -> usize {
        match self {
            Raw::L => 1,
            _ => 4,
        }
    }
}

/// ImagingJpegDecode on the whole of `data` (from the tile's offset): rows of `size`, each in
/// the raw mode's bytes. A suspension (the data ended first) is "image file is truncated",
/// libjpeg's errors are "broken data stream".
fn decode(data: &[u8], raw: Raw, scale: u32, size: (usize, usize)) -> Result<Vec<u8>> {
    let lib = lib()?;
    // SAFETY: zeroed is how JPEGSTATE starts (calloc'd); every pointer is set before use
    let mut err: Box<ErrorMgr> = Box::new(unsafe { std::mem::zeroed() });
    let mut cinfo: Box<Decompress> = Box::new(unsafe { std::mem::zeroed() });
    let mut source = Box::new(Source {
        next_input_byte: data.as_ptr(),
        bytes_in_buffer: data.len(),
        init_source: stub,
        fill_input_buffer,
        skip_input_data,
        resync_to_restart: lib.resync,
        term_source: stub,
        skip: 0,
    });
    let created = std::cell::Cell::new(false);
    let mut rows = vec![0u8; size.0 * size.1 * raw.bytes()];
    let run = panic::catch_unwind(AssertUnwindSafe(|| -> Result<()> {
        // SAFETY: the structures live (boxed) for the whole of this; libjpeg is given them as
        // jpeglib.h has them
        unsafe {
            cinfo.err = (lib.std_error)(&mut *err);
            err.error_exit = Some(error_exit);
            err.output_message = Some(output_message);
            (lib.create)(&mut *cinfo, 62, std::mem::size_of::<Decompress>());
            created.set(true);
            cinfo.src = &mut *source;
            let truncated = || error("image file is truncated");
            let ok = loop {
                let ok = (lib.read_header)(&mut *cinfo, 0);
                if ok != JPEG_HEADER_TABLES_ONLY {
                    break ok;
                }
            };
            if ok == JPEG_SUSPENDED {
                return truncated();
            }
            cinfo.out_color_space = match raw {
                Raw::L => JCS_GRAYSCALE,
                Raw::Rgbx => JCS_EXT_RGBX,
                Raw::CmykI => JCS_CMYK,
            };
            if scale > 1 {
                cinfo.scale_num = 1;
                cinfo.scale_denom = scale;
            }
            if (lib.start)(&mut *cinfo) == 0 {
                return truncated();
            }
            if cinfo.output_width as usize > size.0
                || cinfo.output_components as usize * cinfo.output_width as usize
                    > size.0 * raw.bytes()
            {
                return error("broken data stream when reading image file");
            }
            let stride = size.0 * raw.bytes();
            for y in 0..size.1 {
                let mut line = vec![
                    0u8;
                    (cinfo.output_components as usize * cinfo.output_width as usize)
                        .max(stride)
                ];
                let mut row = line.as_mut_ptr();
                if (lib.read_scanlines)(&mut *cinfo, &mut row, 1) != 1 {
                    return truncated();
                }
                rows[y * stride..(y + 1) * stride].copy_from_slice(&line[..stride]);
            }
            let _ = (lib.finish)(&mut *cinfo);
            Ok(())
        }
    }));
    if created.get() {
        // SAFETY: made by jpeg_CreateDecompress; destroying twice is harmless in libjpeg
        unsafe { (lib.destroy)(&mut *cinfo) };
    }
    match run {
        Ok(result) => result.map(|()| rows),
        Err(payload) if payload.is::<Broken>() => {
            error("broken data stream when reading image file")
        }
        Err(payload) => panic::resume_unwind(payload),
    }
}

fn i16(b: &[u8], at: usize) -> Result<usize> {
    match b.get(at..at + 2) {
        Some(s) => Ok(usize::from(u16::from_be_bytes([s[0], s[1]]))),
        None => error("unpack requires a buffer of 2 bytes"),
    }
}

/// The file read as Python's file object reads it: short at its end, never past it.
struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    fn read(&mut self, n: usize) -> &'a [u8] {
        let from = self.pos.min(self.data.len());
        let to = from.saturating_add(n).min(self.data.len());
        self.pos = to;
        &self.data[from..to]
    }
}

/// What _open reads of a JPEG (from its tile's offset), and what Pillow keeps of it.
struct Header {
    mode: Kind,
    size: (usize, usize),
    exif: Option<Vec<u8>>,
    xmp: Option<Vec<u8>>,
    /// info["dpi"] from JFIF (so _read_dpi_from_exif reads no EXIF while opening)
    jfif_dpi: bool,
    /// info["mp"] and info["mpoffset"]
    mp: Option<(Vec<u8>, usize)>,
    /// an APP1 naming hdrgm:Version (Ultra HDR, which Pillow opens as a JPEG)
    hdr: bool,
}

/// JpegImageFile._open from `at`: the markers up to the first scan.
fn open_at(data: &[u8], at: usize) -> Result<Header> {
    let mut r = Reader { data, pos: at };
    if !r.read(3).starts_with(b"\xff\xd8\xff") {
        return error("not a JPEG file");
    }
    let mut h = Header {
        mode: Kind::L,
        size: (0, 0),
        exif: None,
        xmp: None,
        jfif_dpi: false,
        mp: None,
        hdr: false,
    };
    let mut mode = None;
    let mut icc: Vec<Vec<u8>> = Vec::new();
    let mut s: Vec<u8> = vec![0xff];
    let mut scan = false;
    while !s.is_empty() {
        if s[0] != 0xff {
            s = r.read(1).to_vec();
            continue;
        }
        s.extend_from_slice(r.read(1));
        if s.len() < 2 {
            break;
        }
        let i = (usize::from(s[0]) << 8) | usize::from(s[1]);
        if (0xFFC0..=0xFFFE).contains(&i) {
            // (MARKER: a segment of its own length, read by its handler; none for JPG, RSTn,
            // SOI, EOI and JPGn)
            let handled = !matches!(i, 0xFFC8 | 0xFFD0..=0xFFD9 | 0xFFF0..=0xFFFD);
            if handled {
                let n = i16(r.read(2), 0)? as isize - 2;
                let segment = if n > 0 {
                    let seg = r.read(n as usize);
                    if seg.len() < n as usize {
                        return error("Truncated File Read");
                    }
                    seg.to_vec()
                } else {
                    Vec::new()
                };
                let seg = &segment[..];
                if i == 0xFFE1 && find(seg, b" hdrgm:Version=\"") {
                    h.hdr = true;
                }
                match i {
                    0xFFE0 if seg.starts_with(b"JFIF") => {
                        let _version = i16(seg, 5)?;
                        if let (Some(&unit), Ok(_), Ok(_)) = (seg.get(7), i16(seg, 8), i16(seg, 10))
                        {
                            if unit == 1 || unit == 2 {
                                h.jfif_dpi = true;
                            }
                        }
                    }
                    0xFFE1 if seg.starts_with(b"Exif\0\0") => match &mut h.exif {
                        Some(e) => e.extend_from_slice(&seg[6..]),
                        None => h.exif = Some(seg.to_vec()),
                    },
                    0xFFE1 if seg.starts_with(b"http://ns.adobe.com/xap/1.0/\0") => {
                        h.xmp = Some(seg[29..].to_vec());
                    }
                    0xFFE2 if seg.starts_with(b"ICC_PROFILE\0") => icc.push(seg.to_vec()),
                    0xFFE2 if seg.starts_with(b"MPF\0") => {
                        h.mp = Some((seg[4..].to_vec(), r.pos - seg.len() + 4));
                    }
                    0xFFEE if seg.starts_with(b"Adobe") => {
                        i16(seg, 5)?;
                    }
                    0xFFED if seg.starts_with(b"Photoshop 3.0\0") => photoshop(seg)?,
                    0xFFC0..=0xFFC3
                    | 0xFFC5..=0xFFC7
                    | 0xFFC9..=0xFFCB
                    | 0xFFCD..=0xFFCF
                    | 0xFFDE => {
                        // SOF: the size and mode
                        let (w, ht) = (i16(seg, 3)?, i16(seg, 1)?);
                        h.size = (w, ht);
                        if seg[0] != 8 {
                            return error(format!("cannot handle {}-bit layers", seg[0]));
                        }
                        let Some(&layers) = seg.get(5) else {
                            return error("index out of range");
                        };
                        mode = Some(match layers {
                            1 => Kind::L,
                            3 => Kind::RGB,
                            4 => Kind::Cmyk,
                            n => return error(format!("cannot handle {n}-layer images")),
                        });
                        if !icc.is_empty() {
                            icc.sort();
                            if icc[0].len() < 14 {
                                return error("index out of range");
                            }
                            icc.clear();
                        }
                        if (seg.len() - 6) % 3 != 0 {
                            return error("index out of range");
                        }
                    }
                    0xFFDB => {
                        // DQT: each table whole
                        let mut t = seg;
                        while !t.is_empty() {
                            let length = 1 + if t[0] / 16 == 0 { 64 } else { 128 };
                            if t.len() < length {
                                return error("bad quantization table marker");
                            }
                            t = &t[length..];
                        }
                    }
                    _ => {}
                }
            }
            if i == 0xFFDA {
                scan = true;
                break;
            }
            s = r.read(1).to_vec();
        } else if i == 0xFFFF {
            s = vec![0xff];
        } else if i == 0xFF00 {
            s = r.read(1).to_vec();
        } else {
            return error("no marker found");
        }
    }
    let Some(mode) = mode else {
        return error("cannot load this image");
    };
    if !scan {
        return error("cannot load this image");
    }
    h.mode = mode;
    Ok(h)
}

/// APP13's image resources as APP reads them: a struct.error (a short field) ends the reading
/// quietly, a name's length read beyond the data is an IndexError.
fn photoshop(s: &[u8]) -> Result<()> {
    let i32_at = |at: usize| {
        s.get(at..at + 4)
            .map(|b| u32::from_be_bytes([b[0], b[1], b[2], b[3]]) as usize)
    };
    let mut offset = 14;
    while s.get(offset..offset + 4) == Some(b"8BIM") {
        offset += 4;
        let Ok(code) = i16(s, offset) else {
            return Ok(());
        };
        offset += 2;
        let Some(&name) = s.get(offset) else {
            return error("index out of range");
        };
        offset += 1 + usize::from(name);
        offset += offset & 1;
        let Some(size) = i32_at(offset) else {
            return Ok(());
        };
        offset += 4;
        if code == 0x03ED {
            let data = s.get(offset..).unwrap_or(&[]);
            let data = &data[..size.min(data.len())];
            if data.len() < 14 {
                return Ok(());
            }
        }
        offset += size;
        offset += offset & 1;
    }
    Ok(())
}

fn find(hay: &[u8], needle: &[u8]) -> bool {
    hay.windows(needle.len()).any(|w| w == needle)
}

/// A JPEG's first picture as the app opens it, a draft asked for or not: draft(mode, size)
/// as JpegImageFile.draft takes it (the scale of 1/2, 1/4 or 1/8 that still covers `size`;
/// "L" read as grey by libjpeg itself where the file is RGB).
pub(crate) fn decode_picture(
    data: &[u8],
    draft: Option<(Option<Kind>, (usize, usize))>,
) -> Result<Picture> {
    let h = open_at(data, 0)?;
    let (w, ht) = h.size;
    if (w.max(1) as u64) * (ht.max(1) as u64) > BOMB {
        return error("decompression bomb");
    }
    let mut kind = h.mode;
    let mut raw = match kind {
        Kind::L => Raw::L,
        Kind::RGB => Raw::Rgbx,
        _ => Raw::CmykI,
    };
    let (mut size, mut scale) = (h.size, 1u32);
    if let Some((mode, want)) = draft {
        if kind == Kind::RGB && mode == Some(Kind::L) {
            kind = Kind::L;
            raw = Raw::L;
        }
        if want.0 > 0 && want.1 > 0 {
            let s = (w / want.0).min(ht / want.1);
            let s = [8, 4, 2, 1].into_iter().find(|&f| s >= f).unwrap_or(1);
            size = (w.div_ceil(s), ht.div_ceil(s));
            scale = s as u32;
        } else if want.0 == 0 || want.1 == 0 {
            return error("integer division or modulo by zero");
        }
    }
    if size.0 == 0 || size.1 == 0 {
        return error("tile cannot extend outside image");
    }
    let rows = decode(data, raw, scale, size)?;
    let pixels = match raw {
        Raw::L => rows,
        Raw::Rgbx => rows
            .chunks_exact(4)
            .flat_map(|p| [p[0], p[1], p[2]])
            .collect(),
        Raw::CmykI => rows.iter().map(|&v| 255 - v).collect(),
    };
    // (_read_dpi_from_exif reads the EXIF while opening where JFIF gives no density: an
    // EXIF Pillow fails on then is read as none later, and XMP's with it)
    let exif = match h.exif {
        Some(e) if !h.jfif_dpi && crate::exif::orientation(&e).is_err() => Some(Exif::Spoilt),
        Some(e) => Some(Exif::Bytes(e)),
        None => None,
    };
    Ok(Picture {
        kind,
        width: size.0,
        height: size.1,
        data: pixels,
        palette: None,
        transparency: None,
        exif,
        raw_exif: None,
        xmp: None,
        xmp_bytes: h.xmp,
    })
}

/// The MP index's image offsets (_getmp; None where Pillow takes the file as a plain JPEG:
/// no index, one image, an index it cannot read, Ultra HDR).
fn mp_offsets(h: &Header) -> Option<Vec<usize>> {
    let (mp, mpoffset) = h.mp.as_ref()?;
    if h.hdr {
        return None;
    }
    let head = mp.get(..8)?;
    let big = head.starts_with(b"MM\x00\x2a");
    if !(head.starts_with(b"MM\x00\x2a") || head.starts_with(b"II\x2a\x00")) {
        return None;
    }
    let u16_at = |b: &[u8], at: usize| -> Option<u32> {
        let s = b.get(at..at + 2)?;
        Some(u32::from(if big {
            u16::from_be_bytes([s[0], s[1]])
        } else {
            u16::from_le_bytes([s[0], s[1]])
        }))
    };
    let u32_at = |b: &[u8], at: usize| -> Option<u32> {
        let s = b.get(at..at + 4)?;
        let a = [s[0], s[1], s[2], s[3]];
        Some(if big {
            u32::from_be_bytes(a)
        } else {
            u32::from_le_bytes(a)
        })
    };
    let ifd = u32_at(head, 4)? as usize;
    let count = u16_at(mp, ifd)?;
    let (mut quant, mut entries) = (None, None);
    for n in 0..count as usize {
        let e = mp.get(ifd + 2 + n * 12..ifd + 14 + n * 12)?;
        let (tag, typ, cnt) = (u16_at(e, 0)?, u16_at(e, 2)?, u32_at(e, 4)?);
        match (tag, typ) {
            (0xB001, 4) if cnt == 1 && quant.is_none() => quant = Some(u32_at(e, 8)?),
            (0xB002, 7) if entries.is_none() => {
                let off = if cnt > 4 {
                    u32_at(e, 8)? as usize
                } else {
                    ifd + 2 + n * 12 + 8
                };
                entries = Some(mp.get(off..off + cnt as usize)?.to_vec());
            }
            _ => {}
        }
    }
    let quant = quant? as usize;
    if quant <= 1 {
        return None;
    }
    let entries = entries?;
    let mut offsets = Vec::with_capacity(quant);
    for n in 0..quant {
        let e = entries.get(n * 16..n * 16 + 16)?;
        let attribute = u32_at(e, 0)?;
        if (attribute >> 24) & 7 != 0 {
            return None; // ("unsupported picture format in MPO")
        }
        offsets.push(u32_at(e, 8)? as usize + mpoffset);
    }
    offsets[0] = 0;
    Some(offsets)
}

/// Every frame as the app reads an animation: a JPEG's one, an MPO's each (MpoImageFile.seek:
/// the JPEG at each offset of the MP index, opened and decoded anew).
pub(crate) fn frames(data: &[u8]) -> Result<Vec<Frame>> {
    let rgba = |p: Picture| p.convert(lp_pixels::Mode::RGBA);
    let first = decode_picture(data, None)?;
    let h = open_at(data, 0)?;
    let mut out = vec![Frame {
        duration: None,
        image: rgba(first)?,
    }];
    let Some(offsets) = mp_offsets(&h) else {
        return Ok(out);
    };
    for &offset in &offsets[1..] {
        if data.get(offset + 2..offset + 4).is_none() {
            return error("No data found for frame");
        }
        let h = open_at(data, offset)?;
        let (w, ht) = h.size;
        if (w.max(1) as u64) * (ht.max(1) as u64) > BOMB {
            return error("decompression bomb");
        }
        let raw = match h.mode {
            Kind::L => Raw::L,
            Kind::RGB => Raw::Rgbx,
            _ => Raw::CmykI,
        };
        let rows = decode(&data[offset..], raw, 1, h.size)?;
        let pixels = match raw {
            Raw::L => rows,
            Raw::Rgbx => rows
                .chunks_exact(4)
                .flat_map(|p| [p[0], p[1], p[2]])
                .collect(),
            Raw::CmykI => rows.iter().map(|&v| 255 - v).collect(),
        };
        let picture = Picture {
            kind: h.mode,
            width: w,
            height: ht,
            data: pixels,
            palette: None,
            transparency: None,
            exif: None,
            raw_exif: None,
            xmp: None,
            xmp_bytes: None,
        };
        out.push(Frame {
            duration: None,
            image: rgba(picture)?,
        });
    }
    Ok(out)
}
