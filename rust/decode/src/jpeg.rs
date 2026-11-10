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

use crate::{
    bomb_check, index_error, opened_check, opening, raise, unpack_from_error, Error, Exif, Frame,
    Kind, Picture, Raised, Result,
};

#[cfg(windows)]
type Boolean = u8;
#[cfg(not(windows))]
type Boolean = c_int;

const JCS_GRAYSCALE: c_int = 1;
const JCS_CMYK: c_int = 4;
const JCS_EXT_RGB: c_int = 6;
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

/// jpeg_destination_mgr, with where this saver keeps what libjpeg wrote (jdatadst.c's way:
/// a chunk at a time, the whole chunk taken each time libjpeg has filled it).
#[repr(C)]
struct Destination {
    next_output_byte: *mut u8,
    free_in_buffer: usize,
    init_destination: unsafe extern "C-unwind" fn(*mut Compress),
    empty_output_buffer: unsafe extern "C-unwind" fn(*mut Compress) -> Boolean,
    term_destination: unsafe extern "C-unwind" fn(*mut Compress),
    chunk: Vec<u8>,
    out: Vec<u8>,
}

/// jpeg_compress_struct of the 6.2 interface (JPEG_LIB_VERSION 62), as libjpeg-turbo 3 lays it
/// out (jpeg_CreateCompress refuses another size).
#[repr(C)]
struct Compress {
    err: *mut ErrorMgr,
    mem: *mut c_void,
    progress: *mut c_void,
    client_data: *mut c_void,
    is_decompressor: Boolean,
    global_state: c_int,
    dest: *mut Destination,
    image_width: c_uint,
    image_height: c_uint,
    input_components: c_int,
    in_color_space: c_int,
    input_gamma: f64,
    data_precision: c_int,
    num_components: c_int,
    jpeg_color_space: c_int,
    comp_info: *mut c_void,
    quant_tbl_ptrs: [*mut c_void; 4],
    dc_huff_tbl_ptrs: [*mut c_void; 4],
    ac_huff_tbl_ptrs: [*mut c_void; 4],
    arith_dc_l: [u8; 16],
    arith_dc_u: [u8; 16],
    arith_ac_k: [u8; 16],
    num_scans: c_int,
    scan_info: *const c_void,
    raw_data_in: Boolean,
    arith_code: Boolean,
    optimize_coding: Boolean,
    ccir601_sampling: Boolean,
    smoothing_factor: c_int,
    dct_method: c_int,
    restart_interval: c_uint,
    restart_in_rows: c_int,
    write_jfif_header: Boolean,
    jfif_major_version: u8,
    jfif_minor_version: u8,
    density_unit: u8,
    x_density: u16,
    y_density: u16,
    write_adobe_marker: Boolean,
    next_scanline: c_uint,
    progressive_mode: Boolean,
    max_h_samp_factor: c_int,
    max_v_samp_factor: c_int,
    total_imcu_rows: c_uint,
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
    master: *mut c_void,
    main: *mut c_void,
    prep: *mut c_void,
    coef: *mut c_void,
    marker: *mut c_void,
    cconvert: *mut c_void,
    downsample: *mut c_void,
    fdct: *mut c_void,
    entropy: *mut c_void,
    script_space: *mut c_void,
    script_space_size: c_int,
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
    // (the compressor's, for save)
    create_compress: unsafe extern "C-unwind" fn(*mut Compress, c_int, usize),
    destroy_compress: unsafe extern "C-unwind" fn(*mut Compress),
    set_defaults: unsafe extern "C-unwind" fn(*mut Compress),
    set_quality: unsafe extern "C-unwind" fn(*mut Compress, c_int, Boolean),
    start_compress: unsafe extern "C-unwind" fn(*mut Compress, Boolean),
    write_scanlines: unsafe extern "C-unwind" fn(*mut Compress, *mut *mut u8, c_uint) -> c_uint,
    finish_compress: unsafe extern "C-unwind" fn(*mut Compress),
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
        create_compress: get!("jpeg_CreateCompress"),
        destroy_compress: get!("jpeg_destroy_compress"),
        set_defaults: get!("jpeg_set_defaults"),
        set_quality: get!("jpeg_set_quality"),
        start_compress: get!("jpeg_start_compress"),
        write_scanlines: get!("jpeg_write_scanlines"),
        finish_compress: get!("jpeg_finish_compress"),
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
    found
        .as_ref()
        .map_err(|e| Error::new(Raised::Unported, format!("libjpeg: {e}")))
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
    /// RGB: JCS_EXT_RGB. (Pillow asks for JCS_EXT_RGBX, its own four bytes a pixel; libjpeg-turbo
    /// makes the same colours for both, the fourth byte Pillow's alone.)
    Rgb,
    /// CMYK;I: Adobe's inverted CMYK
    CmykI,
}

impl Raw {
    fn bytes(self) -> usize {
        match self {
            Raw::L => 1,
            Raw::Rgb => 3,
            Raw::CmykI => 4,
        }
    }
}

/// ImagingJpegDecode on the whole of `data` (from the tile's offset): the picture's pixels, rows
/// of `size` (CMYK;I's inverted to CMYK). A suspension (the data ended first) is "image file is
/// truncated", libjpeg's errors are "broken data stream".
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
            // (the data ended first: ImageFile.load's error, with the bytes the decoder was
            // given and did not take, from where libjpeg went back to)
            let truncated = |source: &Source| -> Result<()> {
                let left = source.bytes_in_buffer;
                let msg = format!("image file is truncated ({left} bytes not processed)");
                raise(Raised::OSError, msg)
            };
            let ok = loop {
                let ok = (lib.read_header)(&mut *cinfo, 0);
                if ok != JPEG_HEADER_TABLES_ONLY {
                    break ok;
                }
            };
            if ok == JPEG_SUSPENDED {
                return truncated(&source);
            }
            cinfo.out_color_space = match raw {
                Raw::L => JCS_GRAYSCALE,
                Raw::Rgb => JCS_EXT_RGB,
                Raw::CmykI => JCS_CMYK,
            };
            if scale > 1 {
                cinfo.scale_num = 1;
                cinfo.scale_denom = scale;
            }
            if (lib.start)(&mut *cinfo) == 0 {
                return truncated(&source);
            }
            if cinfo.output_width as usize > size.0
                || cinfo.output_components as usize * cinfo.output_width as usize
                    > size.0 * raw.bytes()
            {
                return broken();
            }
            let stride = size.0 * raw.bytes();
            let given = cinfo.output_components as usize * cinfo.output_width as usize;
            // (libjpeg's rows straight into the picture's, several at a call; a row narrower
            // than the picture's through a line of its own, the rest of the row 0)
            let mut line = vec![0u8; if given < stride { stride } else { 0 }];
            let mut y = 0;
            while y < size.1 {
                let mut at = [std::ptr::null_mut::<u8>(); 16];
                let want = if line.is_empty() {
                    (size.1 - y).min(at.len())
                } else {
                    1
                };
                for (i, p) in at.iter_mut().take(want).enumerate() {
                    *p = if line.is_empty() {
                        rows.as_mut_ptr().add((y + i) * stride)
                    } else {
                        line.as_mut_ptr()
                    };
                }
                let got = (lib.read_scanlines)(&mut *cinfo, at.as_mut_ptr(), want as c_uint);
                if got == 0 {
                    return truncated(&source);
                }
                if !line.is_empty() {
                    rows[y * stride..(y + 1) * stride].copy_from_slice(&line);
                }
                y += got as usize;
            }
            let _ = (lib.finish)(&mut *cinfo);
            if raw == Raw::CmykI {
                for v in rows.iter_mut() {
                    *v = !*v;
                }
            }
            Ok(())
        }
    }));
    if created.get() {
        // SAFETY: made by jpeg_CreateDecompress; destroying twice is harmless in libjpeg
        unsafe { (lib.destroy)(&mut *cinfo) };
    }
    match run {
        Ok(result) => result.map(|()| rows),
        Err(payload) if payload.is::<Broken>() => broken(),
        Err(payload) => panic::resume_unwind(payload),
    }
}

/// libjpeg's error (IMAGING_CODEC_BROKEN), raised once the decoder is done.
fn broken<T>() -> Result<T> {
    raise(
        Raised::OSError,
        "broken data stream when reading image file",
    )
}

/// i16 (PIL._binary's i16be: struct.unpack_from) of the bytes from `at`.
fn i16(b: &[u8], at: usize) -> Result<usize> {
    match b.get(at..at + 2) {
        Some(s) => Ok(usize::from(u16::from_be_bytes([s[0], s[1]]))),
        None => Err(unpack_from_error(2, at, b.len())),
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
    /// the mode its frame header gives (none: Pillow's mode stays "")
    mode: Option<Kind>,
    /// whether it found the scan (else the picture has no tile)
    scan: bool,
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

/// JpegImageFile._open from `at`: the markers up to the first scan, with the errors it raises
/// as they are (Image.open's opening takes most of them as "not this kind": [`opened`]).
fn open_at(data: &[u8], at: usize) -> Result<Header> {
    let mut r = Reader { data, pos: at };
    if !r.read(3).starts_with(b"\xff\xd8\xff") {
        return raise(Raised::SyntaxError, "not a JPEG file");
    }
    let mut h = Header {
        mode: None,
        scan: false,
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
                        return raise(Raised::OSError, "Truncated File Read");
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
                            let msg = format!("cannot handle {}-bit layers", seg[0]);
                            return raise(Raised::SyntaxError, msg);
                        }
                        let Some(&layers) = seg.get(5) else {
                            return index_error();
                        };
                        mode = Some(match layers {
                            1 => Kind::L,
                            3 => Kind::RGB,
                            4 => Kind::Cmyk,
                            n => {
                                let msg = format!("cannot handle {n}-layer images");
                                return raise(Raised::SyntaxError, msg);
                            }
                        });
                        if !icc.is_empty() {
                            icc.sort();
                            if icc[0].len() < 14 {
                                return index_error();
                            }
                            icc.clear();
                        }
                        if (seg.len() - 6) % 3 != 0 {
                            return index_error();
                        }
                    }
                    0xFFDB => {
                        // DQT: each table whole
                        let mut t = seg;
                        while !t.is_empty() {
                            let length = 1 + if t[0] / 16 == 0 { 64 } else { 128 };
                            if t.len() < length {
                                return raise(Raised::SyntaxError, "bad quantization table marker");
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
            return raise(Raised::SyntaxError, "no marker found");
        }
    }
    h.mode = mode;
    h.scan = scan;
    Ok(h)
}

/// Image.open of a JPEG (jpeg_factory): _open's errors as Image.open takes them, ImageFile's
/// check of the mode and size, the MP index read (an MPO's frames' offsets), then the
/// decompression bomb check.
fn opened(data: &[u8]) -> Result<(Header, Option<Vec<usize>>)> {
    let h = open_at(data, 0).map_err(opening)?;
    if h.mode.is_none() || h.size.0 == 0 || h.size.1 == 0 {
        opened_check(false, 0, 0)?;
    }
    let offsets = mp_offsets(&h);
    bomb_check(h.size.0 as i64, h.size.1 as i64)?;
    Ok((h, offsets))
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
            return index_error();
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
/// JpegImageFile.draft's scale for a picture of `size` drafted to `want` (at least 1 each):
/// the largest of 8, 4, 2 and 1 it can shrink by and still cover `want`.
pub(crate) fn draft_scale(size: (usize, usize), want: (usize, usize)) -> usize {
    let s = (size.0 / want.0).min(size.1 / want.1);
    [8, 4, 2, 1].into_iter().find(|&f| s >= f).unwrap_or(1)
}

/// The size a JPEG opens with (its frame header's), before any draft.
pub(crate) fn opened_size(data: &[u8]) -> Result<(usize, usize)> {
    Ok(opened(data)?.0.size)
}

pub(crate) fn decode_picture(
    data: &[u8],
    draft: Option<(Option<Kind>, (usize, usize))>,
) -> Result<Picture> {
    let (h, _) = opened(data)?;
    if !h.scan {
        // (no tile: ImageFile.load)
        return raise(Raised::OSError, "cannot load this image");
    }
    let (w, ht) = h.size;
    let mut kind = h.mode.expect("a mode");
    let mut raw = match kind {
        Kind::L => Raw::L,
        Kind::RGB => Raw::Rgb,
        _ => Raw::CmykI,
    };
    let (mut size, mut scale) = (h.size, 1u32);
    if let Some((mode, want)) = draft {
        if kind == Kind::RGB && mode == Some(Kind::L) {
            kind = Kind::L;
            raw = Raw::L;
        }
        if want.0 > 0 && want.1 > 0 {
            let s = draft_scale(h.size, want);
            size = (w.div_ceil(s), ht.div_ceil(s));
            scale = s as u32;
        } else if want.0 == 0 || want.1 == 0 {
            return raise(
                Raised::ZeroDivisionError,
                "integer division or modulo by zero",
            );
        }
    }
    if size.0 == 0 || size.1 == 0 {
        return raise(Raised::ValueError, "tile cannot extend outside image");
    }
    let pixels = decode(data, raw, scale, size)?;
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
        l_over_p: false,
    })
}

/// A TIFF directory as tiff_entries reads it: whether big-endian, and each entry's tag, type
/// and data.
type Directory = (bool, Vec<(u32, u32, Vec<u8>)>);

/// What ImageFileDirectory_v2.load keeps of a directory of TIFF entries in `data` (from the
/// offset its header names): each tag's type and data, the first of a tag kept; an entry of a
/// type Pillow has no loader for, of no data, or whose data would take the sum of the
/// entries' outside data beyond the data's length skipped (the sum counts the skipped too);
/// the reading stopped where the data ends first. None where the header is not TIFF's (a
/// SyntaxError or struct.error: _getmp's "malformed MP Index").
fn tiff_entries(data: &[u8]) -> Option<Directory> {
    let head = &data[..data.len().min(8)];
    let prefixes: [&[u8]; 6] = [
        b"MM\x00\x2a",
        b"II\x2a\x00",
        b"MM\x2a\x00",
        b"II\x00\x2a",
        b"MM\x00\x2b",
        b"II\x2b\x00",
    ];
    if head.len() < 4 || !prefixes.iter().any(|p| head[..4] == **p) || head[2] == 43 {
        return None;
    }
    if head.len() < 8 {
        return None;
    }
    let big = &head[..2] == b"MM";
    let u16_at = |b: &[u8]| -> u32 {
        u32::from(if big {
            u16::from_be_bytes([b[0], b[1]])
        } else {
            u16::from_le_bytes([b[0], b[1]])
        })
    };
    let u32_at = |b: &[u8]| -> u32 {
        let a = [b[0], b[1], b[2], b[3]];
        if big {
            u32::from_be_bytes(a)
        } else {
            u32::from_le_bytes(a)
        }
    };
    let mut at = u32_at(&head[4..8]) as usize;
    let mut entries: Vec<(u32, u32, Vec<u8>)> = Vec::new();
    let read = |at: usize, n: usize| data.get(at..at.saturating_add(n));
    let Some(count) = read(at, 2).map(u16_at) else {
        return Some((big, entries));
    };
    at += 2;
    let mut total = 0u64;
    for _ in 0..count {
        let Some(e) = read(at, 12) else {
            break; // (_ensure_read's OSError: the directory read no further)
        };
        at += 12;
        let (tag, typ, n) = (
            u16_at(&e[0..2]),
            u16_at(&e[2..4]),
            u64::from(u32_at(&e[4..8])),
        );
        if entries.iter().any(|(t, _, _)| *t == tag) {
            continue;
        }
        let unit: u64 = match typ {
            1 | 2 | 6 | 7 => 1,
            3 | 8 => 2,
            4 | 9 | 11 | 13 => 4,
            5 | 10 | 12 | 16 => 8,
            _ => continue,
        };
        let size = n * unit;
        let value = if size > 4 {
            total += size;
            if total > data.len() as u64 {
                continue; // ("Tag data larger than file")
            }
            let offset = u32_at(&e[8..12]) as usize;
            match read(offset, size as usize) {
                Some(v) => v.to_vec(),
                None => break, // (_safe_read's OSError)
            }
        } else {
            e[8..8 + size as usize].to_vec()
        };
        if value.is_empty() {
            continue;
        }
        entries.push((tag, typ, value));
    }
    Some((big, entries))
}

/// The MP index's image offsets as Image.open takes them (jpeg_factory, _getmp,
/// MpoImageFile): None where the file opens as a plain JPEG (no index, one Pillow cannot read,
/// one whose entries of the images it counts are cut short or name an image of another
/// format: _getmp's SyntaxError, which jpeg_factory takes so; of no more than one image,
/// Ultra HDR's).
fn mp_offsets(h: &Header) -> Option<Vec<usize>> {
    let (mp, mpoffset) = h.mp.as_ref()?;
    let (big, entries) = tiff_entries(mp)?;
    let tag = |t: u32| entries.iter().find(|(tag, _, _)| *tag == t);
    // NumberOfImages: its first value, where its type gives whole numbers (else range()'s
    // TypeError: a plain JPEG)
    let (_, typ, v) = tag(0xB001)?;
    let int16 = |b: &[u8]| {
        if big {
            u16::from_be_bytes([b[0], b[1]])
        } else {
            u16::from_le_bytes([b[0], b[1]])
        }
    };
    let int32 = |b: &[u8]| {
        let a = [b[0], b[1], b[2], b[3]];
        if big {
            u32::from_be_bytes(a)
        } else {
            u32::from_le_bytes(a)
        }
    };
    let quant: i64 = match typ {
        3 => i64::from(int16(v)),
        8 => i64::from(int16(v) as i16),
        4 | 13 => i64::from(int32(v)),
        9 => i64::from(int32(v) as i32),
        6 => i64::from(v[0] as i8),
        16 => {
            let w: [u8; 8] = v[..8].try_into().expect("eight bytes");
            let n = if big {
                u64::from_be_bytes(w)
            } else {
                u64::from_le_bytes(w)
            };
            i64::try_from(n).unwrap_or(i64::MAX)
        }
        _ => return None,
    };
    // MPEntry: bytes (BYTE or UNDEFINED; another type's value is no buffer: a plain JPEG)
    let raw = match tag(0xB002) {
        Some((_, 1 | 7, raw)) => raw,
        _ => return None,
    };
    // (each entry read by its own byte order: the header's "MM\0*" alone is big-endian)
    let entry_big = mp.starts_with(b"MM\x00\x2a");
    let word = |b: &[u8]| {
        let a = [b[0], b[1], b[2], b[3]];
        if entry_big {
            u32::from_be_bytes(a)
        } else {
            u32::from_le_bytes(a)
        }
    };
    let mut offsets = Vec::new();
    for n in 0..quant.max(0) as u64 {
        let Some(e) = raw.get(n as usize * 16..n as usize * 16 + 16) else {
            return None; // (struct.error: "malformed MP Index (bad MP Entry)")
        };
        if (word(&e[0..4]) >> 24) & 7 != 0 {
            return None; // ("unsupported picture format in MPO")
        }
        offsets.push(word(&e[8..12]) as usize + mpoffset);
    }
    if quant <= 1 || h.hdr {
        return None;
    }
    offsets[0] = 0;
    Some(offsets)
}

/// Image.open's count of the frames: an MPO's (n_frames), a plain JPEG's 1.
pub(crate) fn n_frames(data: &[u8]) -> Result<usize> {
    Ok(opened(data)?.1.map_or(1, |offsets| offsets.len()))
}

/// Every frame as the app reads an animation: a JPEG's one, an MPO's each (MpoImageFile.seek:
/// the JPEG at each offset of the MP index, opened and decoded anew).
pub(crate) fn each_frame(
    data: &[u8],
    each: &mut dyn FnMut(usize, Frame) -> Result<()>,
) -> Result<()> {
    let rgba = |p: Picture| p.convert(lp_pixels::Mode::RGBA);
    let first = decode_picture(data, None)?;
    let (_, offsets) = opened(data)?;
    let count = offsets.as_ref().map_or(1, Vec::len);
    let frame = Frame {
        duration: None,
        image: rgba(first)?,
    };
    each(count, frame)?;
    let Some(offsets) = offsets else {
        return Ok(());
    };
    for &offset in &offsets[1..] {
        if data.get(offset + 2..offset + 4).is_none() {
            return raise(Raised::ValueError, "No data found for frame");
        }
        // (MpoImageFile.seek: _open of the frame, its errors as they are; a frame without
        // its own frame header or scan Python reads with the last's, which Rust does not)
        let h = open_at(data, offset)?;
        let (Some(mode), true) = (h.mode, h.scan) else {
            return raise(
                Raised::Unported,
                "an MPO frame without a frame header or scan",
            );
        };
        let (w, ht) = h.size;
        if bomb_check(w as i64, ht as i64).is_err() || w == 0 || ht == 0 {
            return raise(Raised::Unported, "an MPO frame of no size or too large");
        }
        let raw = match mode {
            Kind::L => Raw::L,
            Kind::RGB => Raw::Rgb,
            _ => Raw::CmykI,
        };
        let pixels = decode(&data[offset..], raw, 1, h.size)?;
        let picture = Picture {
            kind: mode,
            width: w,
            height: ht,
            data: pixels,
            palette: None,
            transparency: None,
            exif: None,
            raw_exif: None,
            xmp: None,
            xmp_bytes: None,
            l_over_p: false,
        };
        let frame = Frame {
            duration: None,
            image: rgba(picture)?,
        };
        each(count, frame)?;
    }
    Ok(())
}

// -- saving -------------------------------------------------------------------------------

const JCS_RGB: c_int = 2;
/// What a chunk of the destination holds (Pillow's ImageFile.MAXBLOCK, the buffer it encodes
/// into; the bytes come out the same whatever its size).
const CHUNK: usize = 65536;

unsafe extern "C-unwind" fn init_destination(cinfo: *mut Compress) {
    // SAFETY: dest is the Destination save made, alive while libjpeg writes
    let dest = unsafe { &mut *(*cinfo).dest };
    dest.next_output_byte = dest.chunk.as_mut_ptr();
    dest.free_in_buffer = dest.chunk.len();
}

unsafe extern "C-unwind" fn empty_output_buffer(cinfo: *mut Compress) -> Boolean {
    // SAFETY: as above; libjpeg calls this with the whole chunk written
    let dest = unsafe { &mut *(*cinfo).dest };
    dest.out.extend_from_slice(&dest.chunk);
    dest.next_output_byte = dest.chunk.as_mut_ptr();
    dest.free_in_buffer = dest.chunk.len();
    1
}

unsafe extern "C-unwind" fn term_destination(cinfo: *mut Compress) {
    // SAFETY: as above
    let dest = unsafe { &mut *(*cinfo).dest };
    let used = dest.chunk.len() - dest.free_in_buffer;
    dest.out.extend_from_slice(&dest.chunk[..used]);
}

unsafe extern "C-unwind" fn compress_error_exit(_: *mut Compress) {
    panic::resume_unwind(Box::new(Broken));
}

unsafe extern "C-unwind" fn compress_output_message(_: *mut Compress) {}

/// `image.save(fp, "JPEG", quality=quality)` as Pillow 13.0 saves an L or RGB picture
/// (JpegImagePlugin._save, JpegEncode.c): libjpeg-turbo set up as ImagingJpegEncode sets it up,
/// its defaults, then its tables for `quality` forced to baseline (none for -1, Pillow's
/// default: the library's, of 75), the chroma halved both ways
/// (4:2:0, the library's default), no smoothing, no optimising, no restarts, the JFIF header
/// as the library writes it (no density), the whole stream in one. Another mode is refused as
/// Pillow refuses it.
pub fn save(image: &lp_pixels::Image, quality: i64) -> Result<Vec<u8>> {
    save_with(image, quality, false)
}

/// `image.save(fp, "JPEG", quality=quality, subsampling=0)` where `full_chroma`: the chroma
/// as fine as the luma (4:4:4; JpegEncode.c's subsampling 0), which keeps coloured letters
/// crisp; else as [`save`].
pub fn save_with(image: &lp_pixels::Image, quality: i64, full_chroma: bool) -> Result<Vec<u8>> {
    let (components, space) = match image.mode() {
        lp_pixels::Mode::L => (1, JCS_GRAYSCALE),
        lp_pixels::Mode::RGB => (3, JCS_RGB),
        other => {
            return raise(
                Raised::OSError,
                format!("cannot write mode {other} as JPEG"),
            )
        }
    };
    let (width, height) = image.size();
    if width == 0 || height == 0 {
        // (libjpeg's empty image)
        return raise(Raised::OSError, "encoder error -2 when writing image file");
    }
    let lib = lib()?;
    // SAFETY: zeroed is how JPEGENCODERSTATE starts (calloc'd); every pointer is set before use
    let mut err: Box<ErrorMgr> = Box::new(unsafe { std::mem::zeroed() });
    let mut cinfo: Box<Compress> = Box::new(unsafe { std::mem::zeroed() });
    let mut dest = Box::new(Destination {
        next_output_byte: std::ptr::null_mut(),
        free_in_buffer: 0,
        init_destination,
        empty_output_buffer,
        term_destination,
        chunk: vec![0; CHUNK],
        out: Vec::new(),
    });
    let created = std::cell::Cell::new(false);
    let quality = quality.clamp(c_int::MIN as i64, c_int::MAX as i64) as c_int;
    let run = panic::catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: the structures live (boxed) for the whole of this; libjpeg is given them as
        // jpeglib.h has them, and the rows it reads are the picture's, one at a time
        unsafe {
            cinfo.err = (lib.std_error)(&mut *err);
            err.error_exit = Some(std::mem::transmute::<
                unsafe extern "C-unwind" fn(*mut Compress),
                unsafe extern "C-unwind" fn(*mut Decompress),
            >(compress_error_exit));
            err.output_message = Some(std::mem::transmute::<
                unsafe extern "C-unwind" fn(*mut Compress),
                unsafe extern "C-unwind" fn(*mut Decompress),
            >(compress_output_message));
            (lib.create_compress)(&mut *cinfo, 62, std::mem::size_of::<Compress>());
            created.set(true);
            cinfo.dest = &mut *dest;
            cinfo.image_width = width as c_uint;
            cinfo.image_height = height as c_uint;
            cinfo.input_components = components;
            cinfo.in_color_space = space;
            (lib.set_defaults)(&mut *cinfo);
            if quality != -1 {
                (lib.set_quality)(&mut *cinfo, quality, 1); // (-1: Pillow's "not given")
            }
            if full_chroma && components == 3 {
                // (the first component, the luma, sampled once a block each way as the
                // chroma is by default: a jpeg_component_info begins with its id, its
                // index, then its h_samp_factor and its v_samp_factor, ints all)
                let luma = cinfo.comp_info as *mut c_int;
                *luma.add(2) = 1;
                *luma.add(3) = 1;
            }
            cinfo.smoothing_factor = 0;
            cinfo.optimize_coding = 0;
            cinfo.restart_interval = 0;
            cinfo.restart_in_rows = 0;
            (lib.start_compress)(&mut *cinfo, 1);
            let stride = width * components as usize;
            let data = image.as_bytes();
            while (cinfo.next_scanline as usize) < height {
                let y = cinfo.next_scanline as usize;
                let mut row = data[y * stride..].as_ptr() as *mut u8;
                if (lib.write_scanlines)(&mut *cinfo, &mut row, 1) != 1 {
                    break; // (never: the destination takes all)
                }
            }
            (lib.finish_compress)(&mut *cinfo);
        }
    }));
    if created.get() {
        // SAFETY: made by create_compress above
        unsafe { (lib.destroy_compress)(&mut *cinfo) };
    }
    match run {
        Ok(()) => Ok(std::mem::take(&mut dest.out)),
        Err(e) if e.is::<Broken>() => {
            raise(Raised::OSError, "encoder error -2 when writing image file")
        }
        Err(e) => panic::resume_unwind(e),
    }
}
