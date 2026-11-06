//! FreeType as Pillow 13.0 uses it (_imagingft.c), loaded where the app runs: LP_LIBFREETYPE
//! names its file, else the system's. FreeType stays a library of C (docs/RUST.md): only it
//! draws the letters to the bit as Pillow does.
//!
//! The structures are FreeType's public ones as its headers have them (freetype.h, ftimage.h,
//! ftglyph.h), as far as Pillow reads them; FT_Long and FT_Pos are C's `long` (32 bits on
//! Windows).

use std::ffi::{c_char, c_int, c_long, c_short, c_uchar, c_uint, c_ulong, c_ushort, c_void};
use std::sync::{Mutex, OnceLock};

pub(crate) type FtError = c_int;
pub(crate) type Pos = c_long;
pub(crate) type Fixed = c_long;

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub(crate) struct Vector {
    pub x: Pos,
    pub y: Pos,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
pub(crate) struct BBox {
    pub x_min: Pos,
    pub y_min: Pos,
    pub x_max: Pos,
    pub y_max: Pos,
}

#[repr(C)]
pub(crate) struct Generic {
    data: *mut c_void,
    finalizer: *mut c_void,
}

/// FT_FaceRec, its public part.
#[repr(C)]
pub(crate) struct FaceRec {
    num_faces: c_long,
    face_index: c_long,
    pub face_flags: c_long,
    style_flags: c_long,
    pub num_glyphs: c_long,
    pub family_name: *const c_char,
    pub style_name: *const c_char,
    num_fixed_sizes: c_int,
    available_sizes: *mut c_void,
    num_charmaps: c_int,
    charmaps: *mut c_void,
    generic: Generic,
    bbox: BBox,
    units_per_em: c_ushort,
    ascender: c_short,
    descender: c_short,
    height: c_short,
    max_advance_width: c_short,
    max_advance_height: c_short,
    underline_position: c_short,
    underline_thickness: c_short,
    pub glyph: *mut GlyphSlotRec,
    pub size: *mut SizeRec,
    charmap: *mut c_void,
}

#[repr(C)]
pub(crate) struct SizeMetrics {
    pub x_ppem: c_ushort,
    pub y_ppem: c_ushort,
    x_scale: Fixed,
    y_scale: Fixed,
    pub ascender: Pos,
    pub descender: Pos,
    pub height: Pos,
    max_advance: Pos,
}

#[repr(C)]
pub(crate) struct SizeRec {
    face: *mut FaceRec,
    generic: Generic,
    pub metrics: SizeMetrics,
    internal: *mut c_void,
}

#[repr(C)]
pub(crate) struct GlyphMetrics {
    width: Pos,
    height: Pos,
    hori_bearing_x: Pos,
    hori_bearing_y: Pos,
    pub hori_advance: Pos,
    vert_bearing_x: Pos,
    vert_bearing_y: Pos,
    vert_advance: Pos,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct Bitmap {
    pub rows: c_uint,
    pub width: c_uint,
    pub pitch: c_int,
    pub buffer: *mut c_uchar,
    num_grays: c_ushort,
    pub pixel_mode: c_uchar,
    palette_mode: c_uchar,
    palette: *mut c_void,
}

impl Bitmap {
    pub fn empty() -> Bitmap {
        Bitmap {
            rows: 0,
            width: 0,
            pitch: 0,
            buffer: std::ptr::null_mut(),
            num_grays: 0,
            pixel_mode: 0,
            palette_mode: 0,
            palette: std::ptr::null_mut(),
        }
    }
}

#[repr(C)]
struct Outline {
    n_contours: c_short,
    n_points: c_short,
    points: *mut Vector,
    tags: *mut c_char,
    contours: *mut c_short,
    flags: c_int,
}

/// FT_GlyphSlotRec, as far as Pillow reads it (its bitmap and where it lies).
#[repr(C)]
pub(crate) struct GlyphSlotRec {
    library: *mut c_void,
    face: *mut FaceRec,
    next: *mut c_void,
    glyph_index: c_uint,
    generic: Generic,
    pub metrics: GlyphMetrics,
    linear_hori_advance: Fixed,
    linear_vert_advance: Fixed,
    advance: Vector,
    format: c_int,
    pub bitmap: Bitmap,
    pub bitmap_left: c_int,
    pub bitmap_top: c_int,
    outline: Outline,
}

#[repr(C)]
struct GlyphRec {
    library: *mut c_void,
    clazz: *const c_void,
    format: c_int,
    advance: Vector,
}

#[repr(C)]
pub(crate) struct BitmapGlyphRec {
    root: GlyphRec,
    pub left: c_int,
    pub top: c_int,
    pub bitmap: Bitmap,
}

#[repr(C)]
struct SizeRequest {
    kind: c_int,
    width: c_long,
    height: c_long,
    hori_resolution: c_uint,
    vert_resolution: c_uint,
}

/// FT_Color: blue, green, red, alpha.
#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct Color {
    pub blue: u8,
    pub green: u8,
    pub red: u8,
    pub alpha: u8,
}

pub(crate) type Face = *mut FaceRec;
pub(crate) type Glyph = *mut c_void;
pub(crate) type Stroker = *mut c_void;
type Library = *mut c_void;

pub(crate) const LOAD_DEFAULT: c_int = 0;
pub(crate) const LOAD_RENDER: c_int = 1 << 2;
pub(crate) const LOAD_NO_BITMAP: c_int = 1 << 3;
pub(crate) const LOAD_TARGET_MONO: c_int = 2 << 16;
pub(crate) const LOAD_COLOR: c_int = 1 << 20;
pub(crate) const FACE_FLAG_KERNING: c_long = 1 << 6;
pub(crate) const KERNING_DEFAULT: c_uint = 0;
pub(crate) const GLYPH_BBOX_PIXELS: c_uint = 3;
pub(crate) const RENDER_MODE_NORMAL: c_int = 0;
pub(crate) const STROKER_LINECAP_ROUND: c_int = 1;
pub(crate) const STROKER_LINEJOIN_ROUND: c_int = 0;
pub(crate) const PIXEL_MODE_MONO: u8 = 1;
pub(crate) const PIXEL_MODE_GRAY: u8 = 2;
pub(crate) const PIXEL_MODE_GRAY2: u8 = 3;
pub(crate) const PIXEL_MODE_GRAY4: u8 = 4;
pub(crate) const PIXEL_MODE_BGRA: u8 = 7;
const SIZE_REQUEST_TYPE_NOMINAL: c_int = 0;
const ERR_OUT_OF_MEMORY: FtError = 0x40;

/// FreeType's messages for its errors (fterrdef.h), as Pillow raises them.
const MESSAGES: &[(FtError, &str)] = &[
    (0, "no error"),
    (1, "cannot open resource"),
    (2, "unknown file format"),
    (3, "broken file"),
    (4, "invalid FreeType version"),
    (5, "module version is too low"),
    (6, "invalid argument"),
    (7, "unimplemented feature"),
    (8, "broken table"),
    (9, "broken offset within table"),
    (10, "array allocation size too large"),
    (11, "missing module"),
    (12, "missing property"),
    (16, "invalid glyph index"),
    (17, "invalid character code"),
    (18, "unsupported glyph image format"),
    (19, "cannot render this glyph format"),
    (20, "invalid outline"),
    (21, "invalid composite glyph"),
    (22, "too many hints"),
    (23, "invalid pixel size"),
    (24, "invalid SVG document"),
    (32, "invalid object handle"),
    (33, "invalid library handle"),
    (34, "invalid module handle"),
    (35, "invalid face handle"),
    (36, "invalid size handle"),
    (37, "invalid glyph slot handle"),
    (38, "invalid charmap handle"),
    (39, "invalid cache manager handle"),
    (40, "invalid stream handle"),
    (48, "too many modules"),
    (49, "too many extensions"),
    (64, "out of memory"),
    (65, "unlisted object"),
    (81, "cannot open stream"),
    (82, "invalid stream seek"),
    (83, "invalid stream skip"),
    (84, "invalid stream read"),
    (85, "invalid stream operation"),
    (86, "invalid frame operation"),
    (87, "nested frame access"),
    (88, "invalid frame read"),
    (96, "raster uninitialized"),
    (97, "raster corrupted"),
    (98, "raster overflow"),
    (99, "negative height while rastering"),
    (112, "too many registered caches"),
    (128, "invalid opcode"),
    (129, "too few arguments"),
    (130, "stack overflow"),
    (131, "code overflow"),
    (132, "bad argument"),
    (133, "division by zero"),
    (134, "invalid reference"),
    (135, "found debug opcode"),
    (136, "found ENDF opcode in execution stream"),
    (137, "nested DEFS"),
    (138, "invalid code range"),
    (139, "execution context too long"),
    (140, "too many function definitions"),
    (141, "too many instruction definitions"),
    (142, "SFNT font table missing"),
    (143, "horizontal header (hhea) table missing"),
    (144, "locations (loca) table missing"),
    (145, "name table missing"),
    (146, "character map (cmap) table missing"),
    (147, "horizontal metrics (hmtx) table missing"),
    (148, "PostScript (post) table missing"),
    (149, "invalid horizontal metrics"),
    (150, "invalid character map (cmap) format"),
    (151, "invalid ppem value"),
    (152, "invalid vertical metrics"),
    (153, "could not find context"),
    (154, "invalid PostScript (post) table format"),
    (155, "invalid PostScript (post) table"),
    (156, "found FDEF or IDEF opcode in glyf bytecode"),
    (157, "missing bitmap in strike"),
    (158, "SVG hooks have not been set"),
    (160, "opcode syntax error"),
    (161, "argument stack underflow"),
    (162, "ignore"),
    (163, "no Unicode glyph name found"),
    (164, "glyph too big for hinting"),
    (176, "`STARTFONT' field missing"),
    (177, "`FONT' field missing"),
    (178, "`SIZE' field missing"),
    (179, "`FONTBOUNDINGBOX' field missing"),
    (180, "`CHARS' field missing"),
    (181, "`STARTCHAR' field missing"),
    (182, "`ENCODING' field missing"),
    (183, "`BBX' field missing"),
    (184, "`BBX' too big"),
    (185, "Font header corrupted or missing fields"),
    (186, "Font glyphs corrupted or missing fields"),
];

/// The message Pillow raises (OSError) for a FreeType error.
pub(crate) fn message(code: FtError) -> &'static str {
    MESSAGES
        .iter()
        .find(|(c, _)| *c == code)
        .map_or("unknown freetype error", |(_, m)| m)
}

pub(crate) struct Ft {
    _lib: libloading::Library,
    library: Library,
    /// (FreeType's library is not for two threads at once where faces are made and done)
    faces: Mutex<()>,
    pub version: (c_int, c_int, c_int),
    new_face: unsafe extern "C" fn(Library, *const c_char, c_long, *mut Face) -> FtError,
    new_memory_face: unsafe extern "C" fn(Library, *const u8, c_long, c_long, *mut Face) -> FtError,
    done_face: unsafe extern "C" fn(Face) -> FtError,
    request_size: unsafe extern "C" fn(Face, *const SizeRequest) -> FtError,
    select_charmap: unsafe extern "C" fn(Face, c_uint) -> FtError,
    pub get_char_index: unsafe extern "C" fn(Face, c_ulong) -> c_uint,
    pub load_glyph: unsafe extern "C" fn(Face, c_uint, c_int) -> FtError,
    pub get_kerning: unsafe extern "C" fn(Face, c_uint, c_uint, c_uint, *mut Vector) -> FtError,
    pub get_glyph: unsafe extern "C" fn(*mut GlyphSlotRec, *mut Glyph) -> FtError,
    pub glyph_get_cbox: unsafe extern "C" fn(Glyph, c_uint, *mut BBox),
    pub done_glyph: unsafe extern "C" fn(Glyph),
    pub glyph_to_bitmap: unsafe extern "C" fn(*mut Glyph, c_int, *const Vector, u8) -> FtError,
    pub glyph_stroke: unsafe extern "C" fn(*mut Glyph, Stroker, u8) -> FtError,
    pub glyph_stroke_border: unsafe extern "C" fn(*mut Glyph, Stroker, u8, u8) -> FtError,
    stroker_new: unsafe extern "C" fn(Library, *mut Stroker) -> FtError,
    pub stroker_set: unsafe extern "C" fn(Stroker, Fixed, c_int, c_int, Fixed),
    pub stroker_done: unsafe extern "C" fn(Stroker),
    pub bitmap_init: unsafe extern "C" fn(*mut Bitmap),
    bitmap_convert: unsafe extern "C" fn(Library, *const Bitmap, *mut Bitmap, c_int) -> FtError,
    bitmap_done: unsafe extern "C" fn(Library, *mut Bitmap) -> FtError,
    pub palette_set_foreground_color: Option<unsafe extern "C" fn(Face, Color) -> FtError>,
}

// SAFETY: the library handle is FreeType's, used from any thread: faces are made and done
// under `faces`, everything else on a face only by the one thread that holds that face
unsafe impl Send for Ft {}
unsafe impl Sync for Ft {}

static FT: OnceLock<Result<Ft, String>> = OnceLock::new();

fn open(name: &str) -> Result<libloading::Library, String> {
    // SAFETY: FreeType's initialisers run no code beyond its own
    unsafe { libloading::Library::new(name) }.map_err(|e| e.to_string())
}

fn load() -> Result<Ft, String> {
    let lib = match std::env::var("LP_LIBFREETYPE") {
        Ok(path) => open(&path)?,
        Err(_) => {
            let mut last = String::from("no name");
            let mut found = None;
            for name in [
                "libfreetype.so.6",
                "freetype.dll",
                "libfreetype-6.dll",
                "libfreetype.6.dylib",
            ] {
                match open(name) {
                    Ok(lib) => {
                        found = Some(lib);
                        break;
                    }
                    Err(e) => last = e,
                }
            }
            found.ok_or(last)?
        }
    };
    macro_rules! get {
        ($name:literal) => {
            // SAFETY: the symbol is FreeType's function of that name and type
            *unsafe { lib.get(concat!($name, "\0").as_bytes()) }.map_err(|e| e.to_string())?
        };
    }
    let init: unsafe extern "C" fn(*mut Library) -> FtError = get!("FT_Init_FreeType");
    let library_version: unsafe extern "C" fn(Library, *mut c_int, *mut c_int, *mut c_int) =
        get!("FT_Library_Version");
    let mut library: Library = std::ptr::null_mut();
    // SAFETY: FT_Init_FreeType fills the handle it is given
    if unsafe { init(&mut library) } != 0 || library.is_null() {
        return Err("failed to initialize FreeType library".into());
    }
    let mut version = (0, 0, 0);
    // SAFETY: a live library; three ints to fill
    unsafe { library_version(library, &mut version.0, &mut version.1, &mut version.2) };
    // SAFETY: the symbol, where FreeType has it (2.10 and later), is of this type
    let palette = unsafe { lib.get(b"FT_Palette_Set_Foreground_Color\0") }
        .ok()
        .map(|f: libloading::Symbol<unsafe extern "C" fn(Face, Color) -> FtError>| *f);
    Ok(Ft {
        library,
        faces: Mutex::new(()),
        version,
        new_face: get!("FT_New_Face"),
        new_memory_face: get!("FT_New_Memory_Face"),
        done_face: get!("FT_Done_Face"),
        request_size: get!("FT_Request_Size"),
        select_charmap: get!("FT_Select_Charmap"),
        get_char_index: get!("FT_Get_Char_Index"),
        load_glyph: get!("FT_Load_Glyph"),
        get_kerning: get!("FT_Get_Kerning"),
        get_glyph: get!("FT_Get_Glyph"),
        glyph_get_cbox: get!("FT_Glyph_Get_CBox"),
        done_glyph: get!("FT_Done_Glyph"),
        glyph_to_bitmap: get!("FT_Glyph_To_Bitmap"),
        glyph_stroke: get!("FT_Glyph_Stroke"),
        glyph_stroke_border: get!("FT_Glyph_StrokeBorder"),
        stroker_new: get!("FT_Stroker_New"),
        stroker_set: get!("FT_Stroker_Set"),
        stroker_done: get!("FT_Stroker_Done"),
        bitmap_init: get!("FT_Bitmap_Init"),
        bitmap_convert: get!("FT_Bitmap_Convert"),
        bitmap_done: get!("FT_Bitmap_Done"),
        palette_set_foreground_color: palette,
        _lib: lib,
    })
}

/// FreeType, loaded once.
pub(crate) fn ft() -> Result<&'static Ft, String> {
    FT.get_or_init(load)
        .as_ref()
        .map_err(|e| format!("FreeType: {e}"))
}

impl Ft {
    /// getfont: the face of font `index` in the file or the bytes, at `size` (Pillow's float:
    /// its 26.6 width is `size * 64` in single precision, cut to a whole number), with the
    /// charmap of `encoding` where it has four letters.
    pub fn face(
        &self,
        path: Option<&std::ffi::CStr>,
        bytes: Option<&[u8]>,
        index: i64,
        size: f32,
        encoding: &str,
    ) -> Result<Face, FtError> {
        let mut face: Face = std::ptr::null_mut();
        let mut error = {
            let _held = self.faces.lock().unwrap_or_else(|e| e.into_inner());
            match (path, bytes) {
                // SAFETY: a live library; the path is a C string; face is filled
                (Some(path), None) => unsafe {
                    (self.new_face)(self.library, path.as_ptr(), index as c_long, &mut face)
                },
                // SAFETY: the bytes outlive the face (the Font keeps them)
                (_, Some(bytes)) => match c_long::try_from(bytes.len()) {
                    Ok(len) => unsafe {
                        (self.new_memory_face)(
                            self.library,
                            bytes.as_ptr(),
                            len,
                            index as c_long,
                            &mut face,
                        )
                    },
                    Err(_) => ERR_OUT_OF_MEMORY,
                },
                (None, None) => 1,
            }
        };
        if error == 0 {
            let width = (size * 64.0) as c_long;
            let request = SizeRequest {
                kind: SIZE_REQUEST_TYPE_NOMINAL,
                width,
                height: width,
                hori_resolution: 0,
                vert_resolution: 0,
            };
            // SAFETY: a live face
            error = unsafe { (self.request_size)(face, &request) };
        }
        let tag = encoding.as_bytes();
        if error == 0 && tag.len() == 4 {
            let encoding = u32::from_be_bytes([tag[0], tag[1], tag[2], tag[3]]);
            // SAFETY: a live face
            error = unsafe { (self.select_charmap)(face, encoding) };
        }
        if error != 0 {
            if !face.is_null() {
                self.done(face);
            }
            return Err(error);
        }
        Ok(face)
    }

    pub fn done(&self, face: Face) {
        let _held = self.faces.lock().unwrap_or_else(|e| e.into_inner());
        // SAFETY: a face made by FT_New_Face or FT_New_Memory_Face, done once
        unsafe { (self.done_face)(face) };
    }

    pub fn stroker(&self) -> Result<Stroker, FtError> {
        let mut stroker: Stroker = std::ptr::null_mut();
        // SAFETY: a live library; stroker is filled
        match unsafe { (self.stroker_new)(self.library, &mut stroker) } {
            0 => Ok(stroker),
            error => Err(error),
        }
    }

    pub fn convert(&self, source: &Bitmap, target: &mut Bitmap) -> FtError {
        // SAFETY: a live library; both bitmaps FreeType's
        unsafe { (self.bitmap_convert)(self.library, source, target, 1) }
    }

    pub fn bitmap_done(&self, bitmap: &mut Bitmap) {
        // SAFETY: a bitmap FT_Bitmap_Convert filled
        unsafe { (self.bitmap_done)(self.library, bitmap) };
    }
}
