//! libraqm 0.11 as Pillow 13.0 has it (src/thirdparty/raqm, and text_layout_raqm in
//! _imagingft.c), in Rust: the text cut into runs of one direction (FriBiDi's embedding levels,
//! the runs reordered as raqm reorders them), one script (raqm's rules for common and inherited
//! characters and for paired brackets) and one font, each run shaped by HarfBuzz.
//!
//! HarfBuzz and FriBiDi stay libraries of C (docs/RUST.md), loaded where the app runs:
//! LP_LIBHARFBUZZ and LP_LIBFRIBIDI name their files, else the system's. As in Pillow, the
//! layout is there only where FriBiDi is (Pillow's own on Windows finds none).
//!
//! What Pillow never asks of raqm is left out: letter and word spacing, an invisible glyph,
//! UTF-8 and UTF-16 text, a transform of the face (FreeType's stays the identity).

use std::ffi::{c_char, c_int, c_uint, c_void, CString};
use std::sync::OnceLock;

use crate::{ft, Error, GlyphInfo, Kind, Result};

type Buffer = *mut c_void;
type HbFont = *mut c_void;
type Language = *const c_void;
type UnicodeFuncs = *mut c_void;

const HB_DIRECTION_LTR: c_uint = 4;
const HB_DIRECTION_RTL: c_uint = 5;
const HB_DIRECTION_TTB: c_uint = 6;
const HB_BUFFER_FLAG_BOT: c_uint = 1;
const HB_BUFFER_FLAG_EOT: c_uint = 2;
const HB_SCRIPT_COMMON: u32 = u32::from_be_bytes(*b"Zyyy");
const HB_SCRIPT_INHERITED: u32 = u32::from_be_bytes(*b"Zinh");
const HB_SCRIPT_INVALID: u32 = 0;
const HB_GENERAL_CATEGORY_NON_SPACING_MARK: c_uint = 12;

const FRIBIDI_PAR_LTR: u32 = 0x0000_0110;
const FRIBIDI_PAR_RTL: u32 = 0x0000_0111;
const FRIBIDI_PAR_ON: u32 = 0x0000_0040;
/// FRIBIDI_IS_EXPLICIT_OR_BN_OR_WS
const FRIBIDI_EXPLICIT_OR_BN_OR_WS: u32 = 0x0090_1000;

#[repr(C)]
#[derive(Clone, Copy)]
struct GlyphInfoHb {
    codepoint: u32,
    mask: u32,
    cluster: u32,
    var1: u32,
    var2: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct GlyphPosition {
    x_advance: i32,
    y_advance: i32,
    x_offset: i32,
    y_offset: i32,
    var: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct Feature {
    tag: u32,
    value: u32,
    start: c_uint,
    end: c_uint,
}

struct Libs {
    _harfbuzz: libloading::Library,
    _fribidi: libloading::Library,
    version: String,
    buffer_create: unsafe extern "C" fn() -> Buffer,
    buffer_destroy: unsafe extern "C" fn(Buffer),
    buffer_add_utf32: unsafe extern "C" fn(Buffer, *const u32, c_int, c_uint, c_int),
    buffer_set_script: unsafe extern "C" fn(Buffer, u32),
    buffer_set_language: unsafe extern "C" fn(Buffer, Language),
    buffer_set_direction: unsafe extern "C" fn(Buffer, c_uint),
    buffer_set_flags: unsafe extern "C" fn(Buffer, c_uint),
    buffer_get_glyph_infos: unsafe extern "C" fn(Buffer, *mut c_uint) -> *const GlyphInfoHb,
    buffer_get_glyph_positions: unsafe extern "C" fn(Buffer, *mut c_uint) -> *const GlyphPosition,
    shape_full:
        unsafe extern "C" fn(HbFont, Buffer, *const Feature, c_uint, *const c_void) -> c_int,
    ft_font_create_referenced: unsafe extern "C" fn(*mut c_void) -> HbFont,
    font_destroy: unsafe extern "C" fn(HbFont),
    language_get_default: unsafe extern "C" fn() -> Language,
    language_from_string: unsafe extern "C" fn(*const c_char, c_int) -> Language,
    feature_from_string: unsafe extern "C" fn(*const c_char, c_int, *mut Feature) -> c_int,
    unicode_funcs_get_default: unsafe extern "C" fn() -> UnicodeFuncs,
    unicode_general_category: unsafe extern "C" fn(UnicodeFuncs, u32) -> c_uint,
    unicode_script: unsafe extern "C" fn(UnicodeFuncs, u32) -> u32,
    get_bidi_types: unsafe extern "C" fn(*const u32, c_int, *mut u32),
    get_bracket_types: unsafe extern "C" fn(*const u32, c_int, *const u32, *mut u32),
    get_par_embedding_levels_ex:
        unsafe extern "C" fn(*const u32, *const u32, c_int, *mut u32, *mut i8) -> i8,
}

// SAFETY: HarfBuzz's and FriBiDi's functions are for any thread (the buffers and fonts made
// here are each one call's own)
unsafe impl Send for Libs {}
unsafe impl Sync for Libs {}

static LIBS: OnceLock<std::result::Result<Libs, String>> = OnceLock::new();

fn open(names: &[&str], key: &str) -> std::result::Result<libloading::Library, String> {
    let path = std::env::var(key).ok();
    let names: Vec<&str> = match &path {
        Some(path) => vec![path.as_str()],
        None => names.to_vec(),
    };
    let mut last = String::from("no name");
    for name in names {
        // SAFETY: HarfBuzz's and FriBiDi's initialisers run no code beyond their own
        match unsafe { libloading::Library::new(name) } {
            Ok(lib) => return Ok(lib),
            Err(e) => last = e.to_string(),
        }
    }
    Err(last)
}

fn load() -> std::result::Result<Libs, String> {
    // (FreeType first: HarfBuzz's faces are FreeType's, the same library as the app's)
    ft::ft()?;
    let harfbuzz = open(
        &[
            "libharfbuzz.so.0",
            "libharfbuzz-0.dll",
            "harfbuzz.dll",
            "libharfbuzz.0.dylib",
        ],
        "LP_LIBHARFBUZZ",
    )?;
    let fribidi = open(
        &[
            "libfribidi.so",
            "libfribidi.so.0",
            "fribidi.dll",
            "fribidi-0.dll",
            "libfribidi-0.dll",
            "libfribidi.dylib",
            "/usr/local/lib/libfribidi.dylib",
        ],
        "LP_LIBFRIBIDI",
    )?;
    macro_rules! get {
        ($lib:expr, $name:literal) => {
            // SAFETY: the symbol is the library's function of that name and type
            *unsafe { $lib.get(concat!($name, "\0").as_bytes()) }.map_err(|e| e.to_string())?
        };
    }
    let version_string: unsafe extern "C" fn() -> *const c_char =
        get!(harfbuzz, "hb_version_string");
    // SAFETY: HarfBuzz's version, a static C string
    let version = unsafe { std::ffi::CStr::from_ptr(version_string()) }
        .to_string_lossy()
        .into_owned();
    Ok(Libs {
        version,
        buffer_create: get!(harfbuzz, "hb_buffer_create"),
        buffer_destroy: get!(harfbuzz, "hb_buffer_destroy"),
        buffer_add_utf32: get!(harfbuzz, "hb_buffer_add_utf32"),
        buffer_set_script: get!(harfbuzz, "hb_buffer_set_script"),
        buffer_set_language: get!(harfbuzz, "hb_buffer_set_language"),
        buffer_set_direction: get!(harfbuzz, "hb_buffer_set_direction"),
        buffer_set_flags: get!(harfbuzz, "hb_buffer_set_flags"),
        buffer_get_glyph_infos: get!(harfbuzz, "hb_buffer_get_glyph_infos"),
        buffer_get_glyph_positions: get!(harfbuzz, "hb_buffer_get_glyph_positions"),
        shape_full: get!(harfbuzz, "hb_shape_full"),
        ft_font_create_referenced: get!(harfbuzz, "hb_ft_font_create_referenced"),
        font_destroy: get!(harfbuzz, "hb_font_destroy"),
        language_get_default: get!(harfbuzz, "hb_language_get_default"),
        language_from_string: get!(harfbuzz, "hb_language_from_string"),
        feature_from_string: get!(harfbuzz, "hb_feature_from_string"),
        unicode_funcs_get_default: get!(harfbuzz, "hb_unicode_funcs_get_default"),
        unicode_general_category: get!(harfbuzz, "hb_unicode_general_category"),
        unicode_script: get!(harfbuzz, "hb_unicode_script"),
        get_bidi_types: get!(fribidi, "fribidi_get_bidi_types"),
        get_bracket_types: get!(fribidi, "fribidi_get_bracket_types"),
        get_par_embedding_levels_ex: get!(fribidi, "fribidi_get_par_embedding_levels_ex"),
        _harfbuzz: harfbuzz,
        _fribidi: fribidi,
    })
}

fn libs() -> std::result::Result<&'static Libs, &'static str> {
    LIBS.get_or_init(load).as_ref().map_err(|e| e.as_str())
}

/// Whether text is laid out as libraqm does (HarfBuzz and FriBiDi found).
pub(crate) fn available() -> bool {
    libs().is_ok()
}

/// HarfBuzz's version, where it is loaded.
pub(crate) fn harfbuzz_version() -> Option<String> {
    libs().ok().map(|l| l.version.clone())
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Direction {
    Default,
    Rtl,
    Ltr,
    Ttb,
}

#[derive(Clone, Copy)]
struct BidiRun {
    pos: usize,
    len: usize,
    level: i8,
}

struct Run {
    pos: usize,
    len: usize,
    direction: c_uint,
    script: u32,
}

fn runtime(message: &str) -> Error {
    Error::new(Kind::Runtime, message)
}

/// Unicode's paragraph separators (bidi class B): raqm lays out one paragraph only.
fn paragraph_separator(ch: u32) -> bool {
    matches!(ch, 0x0A | 0x0D | 0x1C | 0x1D | 0x1E | 0x85 | 0x2029)
}

/// The brackets raqm pairs up for scripts, in order (an opening one at each even index).
const PAIRED: [u32; 34] = [
    0x0028, 0x0029, 0x003c, 0x003e, 0x005b, 0x005d, 0x007b, 0x007d, 0x00ab, 0x00bb, 0x2018, 0x2019,
    0x201c, 0x201d, 0x2039, 0x203a, 0x3008, 0x3009, 0x300a, 0x300b, 0x300c, 0x300d, 0x300e, 0x300f,
    0x3010, 0x3011, 0x3014, 0x3015, 0x3016, 0x3017, 0x3018, 0x3019, 0x301a, 0x301b,
];

fn pair_index(ch: u32) -> Option<usize> {
    PAIRED.binary_search(&ch).ok()
}

/// _raqm_resolve_scripts: each character's script, a common or inherited one taking the
/// script before it (a closing bracket its opening one's), what is left at the start the
/// script after it.
fn resolve_scripts(libs: &Libs, text: &[u32]) -> Vec<u32> {
    // SAFETY: HarfBuzz's default Unicode functions, static
    let funcs = unsafe { (libs.unicode_funcs_get_default)() };
    let mut scripts: Vec<u32> = text
        .iter()
        .map(|&u| {
            // SAFETY: as above
            unsafe {
                if (libs.unicode_general_category)(funcs, u) == HB_GENERAL_CATEGORY_NON_SPACING_MARK
                {
                    HB_SCRIPT_INHERITED
                } else {
                    (libs.unicode_script)(funcs, u)
                }
            }
        })
        .collect();
    // raqm's stack: its entries from 1 (one more than the text's length is room for)
    let capacity = text.len();
    let mut stack_script = vec![HB_SCRIPT_INVALID; capacity + 1];
    let mut stack_pair = vec![0usize; capacity + 1];
    let mut size = 0usize;
    let (mut last_script_index, mut last_set_index) = (-1i64, -1i64);
    let mut last_script = HB_SCRIPT_INVALID;
    for i in 0..text.len() {
        if scripts[i] == HB_SCRIPT_COMMON && last_script_index != -1 {
            match pair_index(text[i]) {
                Some(pair) if pair & 1 == 0 => {
                    scripts[i] = last_script;
                    last_set_index = i as i64;
                    if size < capacity {
                        size += 1;
                        stack_script[size] = scripts[i];
                        stack_pair[size] = pair;
                    }
                }
                Some(pair) => {
                    while size > 0 && stack_pair[size] != (pair & !1) {
                        size -= 1;
                    }
                    if size > 0 {
                        scripts[i] = stack_script[size];
                        last_script = scripts[i];
                    } else {
                        scripts[i] = last_script;
                    }
                    last_set_index = i as i64;
                }
                None => {
                    scripts[i] = last_script;
                    last_set_index = i as i64;
                }
            }
        } else if scripts[i] == HB_SCRIPT_INHERITED && last_script_index != -1 {
            scripts[i] = last_script;
            last_set_index = i as i64;
        } else {
            for j in (last_set_index + 1) as usize..i {
                scripts[j] = scripts[i];
            }
            last_script = scripts[i];
            last_script_index = i as i64;
            last_set_index = i as i64;
        }
    }
    for i in (0..text.len().saturating_sub(1)).rev() {
        if scripts[i] == HB_SCRIPT_INHERITED || scripts[i] == HB_SCRIPT_COMMON {
            scripts[i] = scripts[i + 1];
        }
    }
    scripts
}

/// _raqm_reorder_runs: the runs of one level, in the order they are seen (UAX #9's L1 for
/// the white space at the line's end, L2's reversals).
fn reorder_runs(types: &[u32], base: u32, levels: &mut [i8]) -> Vec<BidiRun> {
    let len = levels.len();
    if len == 0 {
        return Vec::new();
    }
    let base_level = i8::from(base & 1 != 0);
    for i in (0..len).rev() {
        if types[i] & FRIBIDI_EXPLICIT_OR_BN_OR_WS == 0 {
            break;
        }
        levels[i] = base_level;
    }
    let max_level = levels.iter().copied().fold(0i8, i8::max);
    let mut runs = Vec::new();
    let mut start = 0;
    while start < len {
        let mut end = start;
        while end < len && levels[start] == levels[end] {
            end += 1;
        }
        runs.push(BidiRun {
            pos: start,
            len: end - start,
            level: levels[start],
        });
        start = end;
    }
    let count = runs.len() as i64;
    let mut level = max_level;
    while level > 0 {
        let mut i = count - 1;
        while i >= 0 {
            if runs[i as usize].level >= level {
                let end = i;
                i -= 1;
                while i >= 0 && runs[i as usize].level >= level {
                    i -= 1;
                }
                runs[(i + 1) as usize..=end as usize].reverse();
            }
            i -= 1;
        }
        level -= 1;
    }
    runs
}

/// _raqm_bidi_itemize: FriBiDi's levels in runs; None where FriBiDi fails.
fn bidi_itemize(libs: &Libs, text: &[u32], base: Direction) -> Option<Vec<BidiRun>> {
    let n = text.len();
    let len = c_int::try_from(n).ok()?;
    let mut types = vec![0u32; n];
    let mut brackets = vec![0u32; n];
    let mut levels = vec![0i8; n];
    let mut par = match base {
        Direction::Rtl => FRIBIDI_PAR_RTL,
        Direction::Ltr => FRIBIDI_PAR_LTR,
        _ => FRIBIDI_PAR_ON,
    };
    // SAFETY: FriBiDi's functions on arrays of the text's length
    let max_level = unsafe {
        (libs.get_bidi_types)(text.as_ptr(), len, types.as_mut_ptr());
        (libs.get_bracket_types)(text.as_ptr(), len, types.as_ptr(), brackets.as_mut_ptr());
        (libs.get_par_embedding_levels_ex)(
            types.as_ptr(),
            brackets.as_ptr(),
            len,
            &mut par,
            levels.as_mut_ptr(),
        )
    };
    if max_level == 0 {
        return None;
    }
    Some(reorder_runs(&types, par, &mut levels))
}

/// What HarfBuzz made and has to be given back.
struct Made<'a> {
    libs: &'a Libs,
    font: HbFont,
    buffers: Vec<Buffer>,
}

impl Drop for Made<'_> {
    fn drop(&mut self) {
        // SAFETY: each made by HarfBuzz for this layout, given back once
        unsafe {
            for &buffer in &self.buffers {
                (self.libs.buffer_destroy)(buffer);
            }
            if !self.font.is_null() {
                (self.libs.font_destroy)(self.font);
            }
        }
    }
}

/// text_layout_raqm: the text's glyphs as libraqm lays them out, in the order they are seen.
pub(crate) fn layout(
    face: ft::Face,
    text: &str,
    direction: Option<&str>,
    features: Option<&[String]>,
    language: Option<&str>,
) -> Result<Vec<GlyphInfo>> {
    let libs = libs().map_err(|e| Error::new(Kind::Os, format!("libraqm: {e}")))?;
    let text: Vec<u32> = text.chars().map(u32::from).collect();
    if text.is_empty() {
        return Ok(Vec::new());
    }
    // SAFETY: HarfBuzz's languages live as long as the library
    let mut lang = unsafe { (libs.language_get_default)() };
    if let Some(language) = language {
        let name = CString::new(language)
            .map_err(|_| Error::new(Kind::Value, "embedded null character"))?;
        // SAFETY: a C string, read to its end
        lang = unsafe { (libs.language_from_string)(name.as_ptr(), -1) };
    }
    let base = match direction {
        None => Direction::Default,
        Some("rtl") => Direction::Rtl,
        Some("ltr") => Direction::Ltr,
        Some("ttb") => Direction::Ttb,
        Some(_) => {
            return Err(Error::new(
                Kind::Value,
                "direction must be either 'rtl', 'ltr' or 'ttb'",
            ))
        }
    };
    let mut wanted = Vec::new();
    for feature in features.unwrap_or_default() {
        let mut f = Feature::default();
        let len = c_int::try_from(feature.len()).unwrap_or(c_int::MAX);
        // SAFETY: the feature's bytes, `len` of them
        let ok = unsafe { (libs.feature_from_string)(feature.as_ptr().cast(), len, &mut f) };
        if ok == 0 {
            return Err(Error::new(Kind::Value, "raqm_add_font_feature() failed"));
        }
        wanted.push(f);
    }
    // raqm_layout
    if text.iter().any(|&ch| paragraph_separator(ch)) {
        return Err(runtime("raqm_layout() failed."));
    }
    let scripts = resolve_scripts(libs, &text);
    let bidi = if base == Direction::Ttb {
        vec![BidiRun {
            pos: 0,
            len: text.len(),
            level: 0,
        }]
    } else {
        bidi_itemize(libs, &text, base).ok_or_else(|| runtime("raqm_layout() failed."))?
    };
    let direction_of = |level: i8| {
        if base == Direction::Ttb {
            HB_DIRECTION_TTB
        } else if level & 1 != 0 {
            HB_DIRECTION_RTL
        } else {
            HB_DIRECTION_LTR
        }
    };
    // runs of one script (the font, its load flags and the language are the text's own)
    let mut runs: Vec<Run> = Vec::new();
    for b in &bidi {
        let direction = direction_of(b.level);
        if direction == HB_DIRECTION_RTL {
            let mut run = Run {
                pos: b.pos + b.len - 1,
                len: 0,
                direction,
                script: scripts[b.pos + b.len - 1],
            };
            for j in (0..b.len).rev() {
                if scripts[b.pos + j] != scripts[run.pos] {
                    runs.push(run);
                    run = Run {
                        pos: b.pos + j,
                        len: 1,
                        direction,
                        script: scripts[b.pos + j],
                    };
                } else {
                    run.len += 1;
                    run.pos = b.pos + j;
                }
            }
            runs.push(run);
        } else {
            let mut run = Run {
                pos: b.pos,
                len: 0,
                direction,
                script: scripts[b.pos],
            };
            for j in 0..b.len {
                if scripts[b.pos + j] != scripts[run.pos] {
                    runs.push(run);
                    run = Run {
                        pos: b.pos + j,
                        len: 1,
                        direction,
                        script: scripts[b.pos + j],
                    };
                } else {
                    run.len += 1;
                }
            }
            runs.push(run);
        }
    }
    // _raqm_shape and raqm_get_glyphs
    let len = c_int::try_from(text.len()).map_err(|_| runtime("raqm_layout() failed."))?;
    let mut made = Made {
        libs,
        // SAFETY: a live FreeType face; HarfBuzz takes a reference of its own
        font: unsafe { (libs.ft_font_create_referenced)(face.cast()) },
        buffers: Vec::with_capacity(runs.len()),
    };
    let mut glyphs = Vec::new();
    for run in &runs {
        // SAFETY: HarfBuzz's buffer, filled from the text, shaped with the font, read back
        unsafe {
            let buffer = (libs.buffer_create)();
            made.buffers.push(buffer);
            (libs.buffer_add_utf32)(
                buffer,
                text.as_ptr(),
                len,
                run.pos as c_uint,
                run.len as c_int,
            );
            (libs.buffer_set_script)(buffer, run.script);
            (libs.buffer_set_language)(buffer, lang);
            (libs.buffer_set_direction)(buffer, run.direction);
            (libs.buffer_set_flags)(buffer, HB_BUFFER_FLAG_BOT | HB_BUFFER_FLAG_EOT);
            (libs.shape_full)(
                made.font,
                buffer,
                wanted.as_ptr(),
                wanted.len() as c_uint,
                std::ptr::null(),
            );
            let mut count: c_uint = 0;
            let infos = (libs.buffer_get_glyph_infos)(buffer, &mut count);
            let positions = (libs.buffer_get_glyph_positions)(buffer, &mut count);
            for i in 0..count as usize {
                let (info, position) = (*infos.add(i), *positions.add(i));
                glyphs.push(GlyphInfo {
                    index: info.codepoint,
                    x_offset: position.x_offset,
                    x_advance: position.x_advance,
                    y_offset: position.y_offset,
                    y_advance: position.y_advance,
                    cluster: info.cluster,
                });
            }
        }
    }
    drop(made);
    Ok(glyphs)
}

/// Python sets the locale's character type from the environment when it starts, and HarfBuzz
/// takes the language of a text without one from it: the Rust app does the same, once, at
/// its start.
pub fn set_locale_from_environment() {
    #[cfg(target_os = "linux")]
    const LC_CTYPE: c_int = 0;
    #[cfg(not(target_os = "linux"))]
    const LC_CTYPE: c_int = 2;
    extern "C" {
        fn setlocale(category: c_int, locale: *const c_char) -> *mut c_char;
    }
    // SAFETY: setlocale with "", at the start before other threads use the locale
    unsafe { setlocale(LC_CTYPE, c"".as_ptr()) };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runs_are_reordered_as_raqm_reorders_them() {
        // levels 0 0 1 1 2 2 1 0: L2 reverses from the highest level down
        let mut levels = [0, 0, 1, 1, 2, 2, 1, 0];
        let types = [0x110; 8];
        let runs = reorder_runs(&types, FRIBIDI_PAR_LTR, &mut levels);
        let order: Vec<(usize, i8)> = runs.iter().map(|r| (r.pos, r.level)).collect();
        assert_eq!(order, vec![(0, 0), (6, 1), (4, 2), (2, 1), (7, 0)]);
        // white space at the end of the line goes to the paragraph's level (L1)
        let mut levels = [1, 1, 1];
        let types = [0x111, 0x111, 0x0080_0000 | 0x1000];
        let runs = reorder_runs(&types, FRIBIDI_PAR_LTR, &mut levels);
        assert_eq!(levels, [1, 1, 0]);
        assert_eq!(runs.len(), 2);
    }

    #[test]
    fn the_brackets_are_paired_by_their_index() {
        assert_eq!(pair_index(0x28), Some(0));
        assert_eq!(pair_index(0x29), Some(1));
        assert_eq!(pair_index(0x301b), Some(33));
        assert_eq!(pair_index(0x41), None);
    }
}
