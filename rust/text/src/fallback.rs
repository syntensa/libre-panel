//! fonts/fallback.py in Rust: a face that draws what its file has no glyph for with the first
//! system font that has it, at the same size and weight and on the same baseline.
//!
//! What a font covers is read from its `cmap` table as the Python app reads it (its rules for
//! broken tables too). A text the face covers goes to the face as it is; any other is cut into
//! runs by the face that has their glyphs (marks, spaces and the invisible go with the character
//! before them), placed on this face's baseline, measured and drawn run by run. Python's
//! Unicode database is the release's (Python 3.12: Unicode 15.0), and so are these tables.

use std::cell::RefCell;
use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use lp_pixels::Image;
use unicode_general_category::{get_general_category, GeneralCategory as G};
use unicode_normalization::UnicodeNormalization;

use crate::text::{self, Text, TextFace};
use crate::{Error, Font, Kind, Layout, Result};

// -- what a font file covers -------------------------------------------------------------------

/// (platform, encoding) of the cmap subtables that map Unicode, the widest first.
const UNICODE_SUBTABLES: [(u16, u16); 8] = [
    (3, 10),
    (0, 6),
    (0, 4),
    (3, 1),
    (0, 3),
    (0, 2),
    (0, 1),
    (0, 0),
];

/// The code points a font file has glyphs for, and whether it draws outlines at any size.
#[derive(Debug)]
pub struct Coverage {
    starts: Vec<i64>,
    ends: Vec<i64>,
    pub outlines: bool,
}

impl Coverage {
    fn new(mut ranges: Vec<(i64, i64)>, outlines: bool) -> Coverage {
        ranges.sort_unstable();
        Coverage {
            starts: ranges.iter().map(|r| r.0).collect(),
            ends: ranges.iter().map(|r| r.1).collect(),
            outlines,
        }
    }

    /// Whether the last range that starts at or before `code` goes as far as it.
    pub fn contains(&self, code: u32) -> bool {
        let code = i64::from(code);
        let at = self.starts.partition_point(|&s| s <= code);
        at > 0 && code <= self.ends[at - 1]
    }

    /// How many code points the ranges hold (Python's len: a broken table's backward ranges
    /// count less than none).
    pub fn len(&self) -> i64 {
        self.starts
            .iter()
            .zip(&self.ends)
            .map(|(&a, &b)| b - a + 1)
            .sum()
    }

    pub fn is_empty(&self) -> bool {
        self.starts.is_empty()
    }
}

/// A table that ends too soon (Python's struct.error).
struct Short;

/// The cmap table and whether the font has outlines; Ok(None) for a file that is no font
/// of these kinds.
type Cmap = std::result::Result<Option<(Vec<u8>, bool)>, Short>;

fn be16(t: &[u8], at: usize) -> std::result::Result<u32, Short> {
    let b = t.get(at..at.checked_add(2).ok_or(Short)?).ok_or(Short)?;
    Ok(u32::from(u16::from_be_bytes([b[0], b[1]])))
}

fn be32(t: &[u8], at: usize) -> std::result::Result<u32, Short> {
    let b = t.get(at..at.checked_add(4).ok_or(Short)?).ok_or(Short)?;
    Ok(u32::from_be_bytes([b[0], b[1], b[2], b[3]]))
}

/// struct.unpack_from(f">{count}H", t, at): all of them, or Short.
fn be16s(t: &[u8], at: usize, count: usize) -> std::result::Result<Vec<u32>, Short> {
    let end = at
        .checked_add(count.checked_mul(2).ok_or(Short)?)
        .ok_or(Short)?;
    let b = t.get(at..end).ok_or(Short)?;
    Ok(b.chunks_exact(2)
        .map(|p| u32::from(u16::from_be_bytes([p[0], p[1]])))
        .collect())
}

fn read_exact_or_less(file: &mut File, n: usize) -> std::io::Result<Vec<u8>> {
    let mut out = Vec::new();
    file.take(n as u64).read_to_end(&mut out)?;
    Ok(out)
}

/// _read_cmap: the `cmap` table of font `index` in a TrueType or OpenType file (or
/// collection) and whether the font has outlines; None for any other file.
fn read_cmap(path: &Path, index: i64) -> std::io::Result<Cmap> {
    let mut file = File::open(path)?;
    let mut head = read_exact_or_less(&mut file, 12)?;
    let mut base = 0u64;
    if head.get(..4) == Some(b"ttcf") {
        let Ok(count) = be32(&head, 8) else {
            return Ok(Err(Short));
        };
        if !(0..i64::from(count)).contains(&index) {
            return Ok(Ok(None));
        }
        file.seek(SeekFrom::Start(12 + 4 * index as u64))?;
        let at = read_exact_or_less(&mut file, 4)?;
        let Ok(at) = be32(&at, 0) else {
            return Ok(Err(Short));
        };
        base = u64::from(at);
        file.seek(SeekFrom::Start(base))?;
        head = read_exact_or_less(&mut file, 12)?;
    }
    if !matches!(head.get(..4), Some(b"\x00\x01\x00\x00" | b"OTTO" | b"true")) {
        return Ok(Ok(None));
    }
    let Ok(tables) = be16(&head, 4) else {
        return Ok(Err(Short));
    };
    file.seek(SeekFrom::Start(base + 12))?;
    let records = read_exact_or_less(&mut file, 16 * tables as usize)?;
    if records.len() % 16 != 0 {
        return Ok(Err(Short));
    }
    let mut found: HashMap<[u8; 4], (u32, u32)> = HashMap::new();
    for r in records.chunks_exact(16) {
        let tag = [r[0], r[1], r[2], r[3]];
        let offset = u32::from_be_bytes([r[8], r[9], r[10], r[11]]);
        let length = u32::from_be_bytes([r[12], r[13], r[14], r[15]]);
        found.insert(tag, (offset, length));
    }
    let Some(&(offset, length)) = found.get(b"cmap") else {
        return Ok(Ok(None));
    };
    file.seek(SeekFrom::Start(u64::from(offset)))?;
    let table = read_exact_or_less(&mut file, length as usize)?;
    let outlines = [b"glyf", b"CFF ", b"CFF2"]
        .iter()
        .any(|t| found.contains_key(*t));
    Ok(Ok(Some((table, outlines))))
}

/// _subtable_ranges: the code points one cmap subtable maps to a glyph (not to glyph 0).
fn subtable_ranges(t: &[u8], at: usize) -> std::result::Result<Option<Vec<(i64, i64)>>, Short> {
    let form = be16(t, at)?;
    let mut ranges = Vec::new();
    match form {
        4 => {
            let count = (be16(t, at + 6)? / 2) as usize;
            let ends = be16s(t, at + 14, count)?;
            let starts = be16s(t, at + 16 + 2 * count, count)?;
            let offsets_at = at + 16 + 6 * count;
            let offsets = be16s(t, offsets_at, count)?;
            for n in 0..count {
                let (first, last) = (starts[n], ends[n]);
                if first > last || first == 0xFFFF {
                    continue;
                }
                if offsets[n] == 0 {
                    ranges.push((i64::from(first), i64::from(last)));
                    continue;
                }
                let glyphs_at = offsets_at + 2 * n + offsets[n] as usize;
                let room = (t.len() as i64 - glyphs_at as i64).div_euclid(2);
                let room = room.min(i64::from(last - first + 1)).max(0) as usize;
                if glyphs_at > t.len() {
                    return Err(Short);
                }
                let glyphs = be16s(t, glyphs_at, room)?;
                let mut begin = None;
                for (k, &glyph) in glyphs.iter().enumerate() {
                    let code = i64::from(first) + k as i64;
                    if glyph != 0 && begin.is_none() {
                        begin = Some(code);
                    } else if glyph == 0 {
                        if let Some(b) = begin.take() {
                            ranges.push((b, code - 1));
                        }
                    }
                }
                if let Some(b) = begin {
                    ranges.push((b, i64::from(first) + glyphs.len() as i64 - 1));
                }
            }
        }
        12 | 13 => {
            let count = be32(t, at + 12)? as usize;
            let start = (at + 16).min(t.len());
            let end = at
                .saturating_add(16)
                .saturating_add(count.saturating_mul(12))
                .min(t.len());
            let data = &t[start..end.max(start)];
            if !data.len().is_multiple_of(12) {
                return Err(Short);
            }
            for g in data.chunks_exact(12) {
                ranges.push((i64::from(be32(g, 0)?), i64::from(be32(g, 4)?)));
            }
        }
        6 => {
            let (first, count) = (be16(t, at + 6)?, be16(t, at + 8)?);
            let glyphs = be16s(t, at + 10, count as usize)?;
            for (k, &glyph) in glyphs.iter().enumerate() {
                if glyph != 0 {
                    let code = i64::from(first) + k as i64;
                    ranges.push((code, code));
                }
            }
        }
        10 => {
            let (first, count) = (be32(t, at + 12)?, be32(t, at + 16)?);
            let glyphs = be16s(t, at + 20, count as usize)?;
            for (k, &glyph) in glyphs.iter().enumerate() {
                if glyph != 0 {
                    let code = i64::from(first) + k as i64;
                    ranges.push((code, code));
                }
            }
        }
        0 => {
            let start = (at + 6).min(t.len());
            let glyphs = &t[start..(at + 262).min(t.len()).max(start)];
            for (code, &glyph) in glyphs.iter().enumerate() {
                if glyph != 0 {
                    ranges.push((code as i64, code as i64));
                }
            }
        }
        _ => return Ok(None),
    }
    Ok(Some(ranges))
}

/// _unicode_ranges: the ranges of the widest Unicode subtable that can be read.
fn unicode_ranges(t: &[u8]) -> std::result::Result<Option<Vec<(i64, i64)>>, Short> {
    let count = be16(t, 2)? as usize;
    let mut subtables: HashMap<(u16, u16), usize> = HashMap::new();
    for n in 0..count {
        let platform = be16(t, 4 + 8 * n)? as u16;
        let encoding = be16(t, 6 + 8 * n)? as u16;
        let at = be32(t, 8 + 8 * n)? as usize;
        subtables.insert((platform, encoding), at);
    }
    for key in UNICODE_SUBTABLES {
        if let Some(&at) = subtables.get(&key) {
            if let Some(ranges) = subtable_ranges(t, at)? {
                return Ok(Some(ranges));
            }
        }
    }
    Ok(None)
}

type CoverageCache = Mutex<HashMap<(PathBuf, i64), Option<Arc<Coverage>>>>;

fn coverages() -> &'static CoverageCache {
    static CACHE: OnceLock<CoverageCache> = OnceLock::new();
    CACHE.get_or_init(Default::default)
}

/// coverage: what font `index` of the file at `path` covers; None where that cannot be told
/// (not a TrueType or OpenType font, no Unicode table, unreadable). Read once per process.
pub fn coverage(path: &Path, index: i64) -> Option<Arc<Coverage>> {
    let key = (path.to_path_buf(), index);
    if let Some(found) = coverages()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(&key)
    {
        return found.clone();
    }
    let found = match read_cmap(path, index) {
        Ok(Ok(Some((table, outlines)))) => match unicode_ranges(&table) {
            Ok(Some(ranges)) => Some(Arc::new(Coverage::new(ranges, outlines))),
            _ => None,
        },
        _ => None,
    };
    coverages()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .insert(key, found.clone());
    found
}

/// _opens: whether FreeType makes a face of the font at a size asked for (a colour emoji
/// font of bitmaps has one size only).
fn opens(path: &Path, index: i64) -> bool {
    static CACHE: OnceLock<Mutex<HashMap<(PathBuf, i64), bool>>> = OnceLock::new();
    let cache = CACHE.get_or_init(Default::default);
    let key = (path.to_path_buf(), index);
    if let Some(&found) = cache.lock().unwrap_or_else(|e| e.into_inner()).get(&key) {
        return found;
    }
    let found = crate::truetype(path, 16.0, index, "", None).is_ok();
    cache
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .insert(key, found);
    found
}

// -- system fonts ------------------------------------------------------------------------------

/// A font of a chain: its regular file, its bold one, its index in the file.
pub type Entry = (String, Option<String>, i64);

/// A table's font: its regular file's name, its bold one's, its index.
type Listed = (&'static str, Option<&'static str>, i64);

const WINDOWS: &[Listed] = &[
    ("segoeui.ttf", Some("segoeuib.ttf"), 0),
    ("seguisym.ttf", None, 0),
    ("YuGothR.ttc", Some("YuGothB.ttc"), 0),
    ("meiryo.ttc", Some("meiryob.ttc"), 0),
    ("msyh.ttc", Some("msyhbd.ttc"), 0),
    ("msjh.ttc", Some("msjhbd.ttc"), 0),
    ("malgun.ttf", Some("malgunbd.ttf"), 0),
    ("Nirmala.ttc", Some("NirmalaB.ttc"), 0),
    ("Nirmala.ttf", Some("NirmalaB.ttf"), 0),
    ("ebrima.ttf", Some("ebrimabd.ttf"), 0),
    ("LeelawUI.ttf", Some("LeelaUIb.ttf"), 0),
    ("seguiemj.ttf", None, 0),
    ("seguihis.ttf", None, 0),
    ("mmrtext.ttf", Some("mmrtextb.ttf"), 0),
    ("gadugi.ttf", Some("gadugib.ttf"), 0),
    ("simsun.ttc", None, 0),
    ("msgothic.ttc", None, 0),
    ("arial.ttf", Some("arialbd.ttf"), 0),
];

const WINDOWS_JA: &[Listed] = &[
    ("YuGothR.ttc", Some("YuGothB.ttc"), 0),
    ("meiryo.ttc", Some("meiryob.ttc"), 0),
    ("msgothic.ttc", None, 0),
];

const WINDOWS_ZH: &[Listed] = &[
    ("msyh.ttc", Some("msyhbd.ttc"), 0),
    ("msjh.ttc", Some("msjhbd.ttc"), 0),
    ("simsun.ttc", None, 0),
];

const FOLDERS: &[&str] = &[
    "/usr/share/fonts/truetype/noto",
    "/usr/share/fonts/noto",
    "/usr/share/fonts/google-noto",
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/TTF",
    "/usr/share/fonts/opentype/noto",
    "/usr/share/fonts/noto-cjk",
    "/usr/share/fonts/google-noto-cjk",
    "/usr/share/fonts/truetype/wqy",
    "/usr/share/fonts/opentype/ipafont-gothic",
    "/usr/share/fonts/ipa-gothic",
    "/usr/share/fonts/OTF",
    "/usr/share/fonts/truetype/takao-gothic",
    "/usr/share/fonts/truetype/vlgothic",
    "/usr/share/fonts/vlgothic",
];

const POSIX: &[Listed] = &[
    ("NotoSans-Regular.ttf", Some("NotoSans-Bold.ttf"), 0),
    ("DejaVuSans.ttf", Some("DejaVuSans-Bold.ttf"), 0),
    (
        "NotoSansArabic-Regular.ttf",
        Some("NotoSansArabic-Bold.ttf"),
        0,
    ),
    (
        "NotoSansHebrew-Regular.ttf",
        Some("NotoSansHebrew-Bold.ttf"),
        0,
    ),
    ("NotoSansCJK-Regular.ttc", Some("NotoSansCJK-Bold.ttc"), 0),
    ("wqy-zenhei.ttc", None, 0),
    ("wqy-microhei.ttc", None, 0),
    ("NotoSansSymbols-Regular.ttf", None, 0),
    ("NotoSansSymbols2-Regular.ttf", None, 0),
    ("NotoEmoji-Regular.ttf", None, 0),
    ("ipag.ttf", None, 0),
    ("TakaoGothic.ttf", None, 0),
    ("VL-Gothic-Regular.ttf", None, 0),
];

const POSIX_JA: &[Listed] = &[
    ("NotoSansCJK-Regular.ttc", Some("NotoSansCJK-Bold.ttc"), 0),
    (
        "NotoSansCJKjp-Regular.otf",
        Some("NotoSansCJKjp-Bold.otf"),
        0,
    ),
    ("ipag.ttf", None, 0),
    ("ipagp.ttf", None, 0),
    ("TakaoGothic.ttf", None, 0),
    ("VL-Gothic-Regular.ttf", None, 0),
];

const POSIX_ZH: &[Listed] = &[
    ("NotoSansCJK-Regular.ttc", Some("NotoSansCJK-Bold.ttc"), 2),
    (
        "NotoSansCJKsc-Regular.otf",
        Some("NotoSansCJKsc-Bold.otf"),
        0,
    ),
    ("wqy-zenhei.ttc", None, 0),
    ("wqy-microhei.ttc", None, 0),
];

/// east_asian: whether the forms of `ch` depend on the language (ideographs, kana, the
/// symbols and punctuation that go with them, full-width forms).
pub fn east_asian(ch: char) -> bool {
    let code = u32::from(ch);
    (0x2E80..=0x9FFF).contains(&code)
        || (0xF900..=0xFAFF).contains(&code)
        || (0xFE30..=0xFE4F).contains(&code)
        || (0xFF00..=0xFFEF).contains(&code)
        || (0x20000..=0x3FFFF).contains(&code)
}

/// Questions to the system's font query in a process; most texts need none.
pub const QUERIES: usize = 48;

static SERIALS: AtomicU64 = AtomicU64::new(1);

type Query =
    Box<dyn Fn(char, bool) -> std::result::Result<Vec<(String, i64)>, String> + Send + Sync>;
type Language = Box<dyn Fn() -> String + Send + Sync>;

#[derive(Default)]
struct Learned {
    learned: HashMap<(bool, String), Vec<(String, i64)>>,
    found: HashMap<(char, bool, String), Option<(String, i64)>>,
    asked: usize,
}

/// Fallbacks: where to find a glyph a font lacks: font files tried in order, then `query`
/// (fontconfig's), asked for a character no listed font has, a few times at most. Answers are
/// kept: a character is looked for once. `scripts`: by language, the fonts that come first
/// for the characters whose forms depend on it while `language()` is that language.
pub struct Fallbacks {
    pub fonts: Vec<Entry>,
    pub scripts: HashMap<String, Vec<Entry>>,
    query: Option<Query>,
    language: Option<Language>,
    pub serial: u64,
    state: Mutex<Learned>,
}

impl Fallbacks {
    pub fn new(
        fonts: Vec<Entry>,
        query: Option<Query>,
        language: Option<Language>,
        scripts: HashMap<String, Vec<Entry>>,
    ) -> Fallbacks {
        Fallbacks {
            fonts,
            scripts,
            query,
            language,
            serial: SERIALS.fetch_add(1, Ordering::Relaxed),
            state: Mutex::new(Learned::default()),
        }
    }

    /// The language whose East Asian fonts come first now ("": the usual order).
    pub fn variant(&self) -> String {
        match &self.language {
            Some(language) => {
                let language = language();
                if self.scripts.contains_key(&language) {
                    language
                } else {
                    String::new()
                }
            }
            None => String::new(),
        }
    }

    /// The file and the index in it of the first font that has a glyph for `ch`.
    pub fn find(&self, ch: char, bold: bool) -> Option<(String, i64)> {
        let variant = if east_asian(ch) {
            self.variant()
        } else {
            String::new()
        };
        let key = (ch, bold, variant);
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(found) = state.found.get(&key) {
            return found.clone();
        }
        let found = self.search(&mut state, u32::from(ch), bold, &key.2);
        state.found.insert(key, found.clone());
        found
    }

    fn search(
        &self,
        state: &mut Learned,
        code: u32,
        bold: bool,
        variant: &str,
    ) -> Option<(String, i64)> {
        let first = self.scripts.get(variant).cloned().unwrap_or_default();
        let mut fonts = first.clone();
        fonts.extend(self.fonts.iter().filter(|f| !first.contains(f)).cloned());
        let learned = state
            .learned
            .entry((bold, variant.to_string()))
            .or_default()
            .clone();
        let listed = fonts.iter().map(|(a, b, i)| match b {
            Some(b) if bold => (b.clone(), *i),
            _ => (a.clone(), *i),
        });
        for (path, index) in listed.chain(learned) {
            if has(code, &path, index) {
                return Some((path, index));
            }
        }
        let query = self.query.as_ref()?;
        if state.asked >= QUERIES {
            return None;
        }
        state.asked += 1;
        let ch = char::from_u32(code)?;
        let answers = query(ch, bold).ok()?;
        for (path, index) in answers {
            if has(code, &path, index) {
                state
                    .learned
                    .entry((bold, variant.to_string()))
                    .or_default()
                    .push((path.clone(), index));
                return Some((path, index));
            }
        }
        None
    }
}

fn has(code: u32, path: &str, index: i64) -> bool {
    let path = Path::new(path);
    match coverage(path, index) {
        Some(found) => found.outlines && found.contains(code) && opens(path, index),
        None => false,
    }
}

/// fontconfig's fonts for `ch`, best first (`fc-match -s`), for the app's `language` where the
/// forms of `ch` depend on it.
pub fn fontconfig(
    ch: char,
    bold: bool,
    language: &str,
) -> std::result::Result<Vec<(String, i64)>, String> {
    let Some(program) = which("fc-match") else {
        return Ok(Vec::new());
    };
    let lang = match language {
        "ja" if east_asian(ch) => Some("ja"),
        "zh" if east_asian(ch) => Some("zh-cn"),
        _ => None,
    };
    let mut pattern = format!(":charset={:x}", u32::from(ch));
    if let Some(lang) = lang {
        pattern += &format!(":lang={lang}");
    }
    if bold {
        pattern += ":weight=bold";
    }
    let mut child = std::process::Command::new(program)
        .args(["-s", "-f", "%{file}\t%{index}\n", &pattern])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    let started = std::time::Instant::now();
    loop {
        if child.try_wait().map_err(|e| e.to_string())?.is_some() {
            break;
        }
        if started.elapsed() > std::time::Duration::from_secs(3) {
            let _ = child.kill();
            let _ = child.wait();
            return Err("fc-match took too long".into());
        }
        std::thread::sleep(std::time::Duration::from_millis(5));
    }
    let mut out = String::new();
    if let Some(mut stdout) = child.stdout.take() {
        let mut bytes = Vec::new();
        stdout.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
        out = String::from_utf8_lossy(&bytes).into_owned();
    }
    let mut found = Vec::new();
    for line in python_lines(&out).into_iter().take(12) {
        let (path, index) = line.split_once('\t').unwrap_or((line, ""));
        if !path.is_empty() && !index.is_empty() && index.chars().all(|c| c.is_ascii_digit()) {
            if let Ok(index) = index.parse() {
                found.push((path.to_string(), index));
            }
        }
    }
    Ok(found)
}

/// str.splitlines (the line ends Python splits at).
fn python_lines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut rest = text;
    while !rest.is_empty() {
        let end = rest
            .char_indices()
            .find(|&(_, c)| {
                matches!(
                    c,
                    '\n' | '\r'
                        | '\x0b'
                        | '\x0c'
                        | '\x1c'
                        | '\x1d'
                        | '\x1e'
                        | '\u{85}'
                        | '\u{2028}'
                        | '\u{2029}'
                )
            })
            .map(|(i, _)| i);
        match end {
            Some(i) => {
                lines.push(&rest[..i]);
                let skip = if rest[i..].starts_with("\r\n") {
                    2
                } else {
                    rest[i..].chars().next().map_or(1, char::len_utf8)
                };
                rest = &rest[i + skip..];
            }
            None => {
                lines.push(rest);
                break;
            }
        }
    }
    lines
}

fn which(program: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(program))
        .find(|candidate| candidate.is_file())
}

/// discover: the system fonts to fall back to on `windows` or not: those of the table that
/// exist (the bold file where there is one), and elsewhere fontconfig; the East Asian fonts of
/// `language()` first for the characters whose forms depend on it.
pub fn discover(windows: bool, windir: Option<&str>, language: Language) -> Fallbacks {
    let language: Arc<Language> = Arc::new(language);
    let folders: Vec<PathBuf> = if windows {
        vec![Path::new(windir.unwrap_or("C:\\Windows")).join("Fonts")]
    } else {
        FOLDERS.iter().map(PathBuf::from).collect()
    };
    let (table, ja, zh) = if windows {
        (WINDOWS, WINDOWS_JA, WINDOWS_ZH)
    } else {
        (POSIX, POSIX_JA, POSIX_ZH)
    };
    let scripts = [("ja", ja), ("zh", zh)];
    let installed = |entries: &[Listed]| -> Vec<Entry> {
        let mut found = Vec::new();
        for &(regular, bold, index) in entries {
            for folder in &folders {
                if folder.join(regular).is_file() {
                    let heavy = bold
                        .filter(|b| folder.join(b).is_file())
                        .map(|b| folder.join(b).to_string_lossy().into_owned());
                    found.push((
                        folder.join(regular).to_string_lossy().into_owned(),
                        heavy,
                        index,
                    ));
                    break;
                }
            }
        }
        found
    };
    let scripts = scripts
        .iter()
        .map(|(name, entries)| (name.to_string(), installed(entries)))
        .collect();
    let query: Option<Query> = if windows {
        None
    } else {
        let language = Arc::clone(&language);
        Some(Box::new(move |ch, bold| fontconfig(ch, bold, &language())))
    };
    Fallbacks::new(
        installed(table),
        query,
        Some(Box::new(move || language())),
        scripts,
    )
}

type System = Mutex<(Option<Arc<Fallbacks>>, u64)>;

fn system() -> &'static System {
    static SYSTEM: OnceLock<System> = OnceLock::new();
    SYSTEM.get_or_init(Default::default)
}

/// How the system's fallbacks are found the first time a face needs them (the app's language).
static LANGUAGE: OnceLock<Box<dyn Fn() -> String + Send + Sync>> = OnceLock::new();

/// The app's language, for the East Asian fonts that come first (i18n.language).
pub fn set_language(language: Box<dyn Fn() -> String + Send + Sync>) {
    let _ = LANGUAGE.set(language);
}

/// system_fallbacks: the system's fonts to fall back to, found once.
pub fn system_fallbacks() -> Arc<Fallbacks> {
    let mut held = system().lock().unwrap_or_else(|e| e.into_inner());
    if let Some(found) = &held.0 {
        return Arc::clone(found);
    }
    let var = |key: &str| std::env::var(key).ok().filter(|v| !v.is_empty());
    let windir = var("WINDIR").or_else(|| var("SystemRoot"));
    let language: Language = Box::new(|| LANGUAGE.get().map(|f| f()).unwrap_or_default());
    let found = Arc::new(discover(cfg!(windows), windir.as_deref(), language));
    held.0 = Some(Arc::clone(&found));
    found
}

/// set_fallbacks: fall back to `fallbacks` from now on (None: look for the system's again);
/// the ones used before.
pub fn set_fallbacks(fallbacks: Option<Arc<Fallbacks>>) -> Option<Arc<Fallbacks>> {
    let mut held = system().lock().unwrap_or_else(|e| e.into_inner());
    held.1 += 1;
    std::mem::replace(&mut held.0, fallbacks)
}

// -- the face ------------------------------------------------------------------------------------

/// Texts whose runs a face keeps.
pub const PLANS: usize = 256;

/// Marks, format characters, spaces and controls go with the character before them.
fn follows(ch: char) -> Option<G> {
    let kind = get_general_category(ch);
    matches!(
        kind,
        G::NonspacingMark
            | G::SpacingMark
            | G::EnclosingMark
            | G::Format
            | G::SpaceSeparator
            | G::LineSeparator
            | G::ParagraphSeparator
            | G::Control
    )
    .then_some(kind)
}

/// The Unicode tables the plans are made with, as one text: for each code point (surrogates
/// left out) that goes with the character before it or that decomposes, "XXXX category NFD"
/// (the category one of the eight that follow, else "-"), for the tests to compare with
/// Python's unicodedata.
pub fn unicode_table() -> String {
    let mut out = String::new();
    for ch in (0..=0x10FFFFu32).filter_map(char::from_u32) {
        let kind = follows(ch).map(|k| match k {
            G::NonspacingMark => "Mn",
            G::SpacingMark => "Mc",
            G::EnclosingMark => "Me",
            G::Format => "Cf",
            G::SpaceSeparator => "Zs",
            G::LineSeparator => "Zl",
            G::ParagraphSeparator => "Zp",
            _ => "Cc",
        });
        let parts: Vec<char> = std::iter::once(ch).nfd().collect();
        if kind.is_none() && parts == [ch] {
            continue;
        }
        let parts: Vec<String> = parts
            .iter()
            .map(|&p| format!("{:04X}", u32::from(p)))
            .collect();
        out += &format!(
            "{:04X} {} {}\n",
            u32::from(ch),
            kind.unwrap_or("-"),
            parts.join(",")
        );
    }
    out
}

/// Blocks of scripts whose letters join (Arabic, Syriac, N'Ko, Mandaic, Mongolian, Adlam).
const CURSIVE: [(u32, u32); 8] = [
    (0x0600, 0x077F),
    (0x07C0, 0x07FF),
    (0x0840, 0x085F),
    (0x08A0, 0x08FF),
    (0x1800, 0x18AF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
    (0x1E900, 0x1E95F),
];

/// cursive: whether `text` has letters of a script that joins them.
pub fn cursive(text: &str) -> bool {
    !text.is_ascii()
        && text.chars().any(|ch| {
            CURSIVE
                .iter()
                .any(|&(a, b)| (a..=b).contains(&u32::from(ch)))
        })
}

/// _has: whether a face covers `ch`; one whose coverage is unknown covers everything.
/// `shaped`: libraqm builds a letter from its parts (an accent) where the font has them.
fn covers(found: Option<&Coverage>, ch: char, shaped: bool) -> bool {
    let Some(found) = found else {
        return true;
    };
    if found.contains(u32::from(ch)) {
        return true;
    }
    if shaped {
        let parts: Vec<char> = std::iter::once(ch).nfd().collect();
        return parts.len() > 1 && parts.iter().all(|&p| found.contains(u32::from(p)));
    }
    false
}

/// Which face draws a run: None for the font's own file.
pub type Spec = Option<(String, i64)>;
type Plan = Vec<(Spec, String)>;

/// _edged: the runs, the spaces at the edges of a run of another font drawn with the font
/// itself.
fn edged(runs: Vec<(Spec, String)>) -> Plan {
    let mut out: Plan = Vec::new();
    let mut put = |spec: Spec, text: &str| {
        if text.is_empty() {
            return;
        }
        match out.last_mut() {
            Some((last, run)) if *last == spec => run.push_str(text),
            _ => out.push((spec, text.to_string())),
        }
    };
    for (spec, run) in runs {
        let body = run.trim_matches(lp_py::isspace);
        if spec.is_none() || body.is_empty() {
            put(None, &run);
            continue;
        }
        let lead = run.len() - run.trim_start_matches(lp_py::isspace).len();
        put(None, &run[..lead]);
        put(spec, body);
        put(None, &run[lead + body.len()..]);
    }
    out
}

#[derive(Default)]
struct Kept {
    seen: Option<Arc<Fallbacks>>,
    variant: String,
    plans: HashMap<String, Option<Plan>>,
    faces: HashMap<Spec, Option<Rc<Font>>>,
    drops: HashMap<char, i64>,
    own: Option<Option<Arc<Coverage>>>,
    heavy: Option<bool>,
}

/// FallbackFont: a FreeType face that draws a character it has no glyph for with a system
/// font. getlength, getbbox and getmask2 know the runs, so every way ImageDraw draws and
/// measures text comes out right. Its faces are its own (FreeType's are for one thread).
pub struct FallbackFont {
    pub font: Font,
    chain: Option<Arc<Fallbacks>>,
    kept: RefCell<Kept>,
}

/// (where each run starts from the anchor, how far below it the baseline lies, the
/// horizontal anchor)
type Placed = (Vec<(Rc<Font>, String, f64)>, i64, char);

impl FallbackFont {
    /// truetype: Pillow's font, falling back to `chain` (None: the system's).
    pub fn new(font: Font, chain: Option<Arc<Fallbacks>>) -> FallbackFont {
        FallbackFont {
            font,
            chain,
            kept: RefCell::new(Kept::default()),
        }
    }

    /// truetype(path, size, index, layout): the face of the file, falling back to the system's.
    pub fn open(
        path: impl AsRef<Path>,
        size: f64,
        index: i64,
        layout: Option<Layout>,
    ) -> Result<FallbackFont> {
        Ok(FallbackFont::new(
            crate::truetype(path, size, index, "", layout)?,
            None,
        ))
    }

    fn own_coverage(&self) -> Option<Arc<Coverage>> {
        if let Some(own) = &self.kept.borrow().own {
            return own.clone();
        }
        let own = self
            .font
            .path()
            .and_then(|p| coverage(p, self.font.index()));
        self.kept.borrow_mut().own = Some(own.clone());
        own
    }

    fn bold(&self) -> bool {
        if let Some(heavy) = self.kept.borrow().heavy {
            return heavy;
        }
        let style = self.font.getname().1.unwrap_or_default();
        let stem = self
            .font
            .path()
            .and_then(|p| p.file_stem())
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let name = format!("{style} {stem}").to_lowercase();
        let heavy = ["bold", "black", "heavy"].iter().any(|w| name.contains(w));
        self.kept.borrow_mut().heavy = Some(heavy);
        heavy
    }

    /// _face: the plain face for a font of the chain (None: this font's own file), made once.
    fn face(&self, spec: &Spec) -> Option<Rc<Font>> {
        if let Some(face) = self.kept.borrow().faces.get(spec) {
            return face.clone();
        }
        let (size, encoding, layout) = (self.font.size(), self.font.encoding(), self.font.layout());
        let made = match spec {
            None => match self.font.path() {
                Some(path) => {
                    crate::truetype(path, size, self.font.index(), encoding, Some(layout))
                }
                None => self.font.variant(None, None, None, None),
            },
            Some((path, index)) => crate::truetype(path, size, *index, encoding, Some(layout)),
        }
        .ok()
        .map(Rc::new);
        self.kept
            .borrow_mut()
            .faces
            .insert(spec.clone(), made.clone());
        made
    }

    fn chain(&self) -> Arc<Fallbacks> {
        self.chain.clone().unwrap_or_else(system_fallbacks)
    }

    /// _plan: the text in runs (spec None: the font itself); None where the font has all of it.
    fn plan(&self, text: &str) -> Option<Plan> {
        let chain = self.chain();
        let variant = chain.variant();
        {
            let mut kept = self.kept.borrow_mut();
            let same = kept.seen.as_ref().is_some_and(|s| Arc::ptr_eq(s, &chain));
            if !same {
                kept.plans.clear();
                kept.faces.clear();
                kept.seen = Some(Arc::clone(&chain));
            } else if variant != kept.variant {
                kept.plans.clear();
            }
            kept.variant = variant;
            if let Some(plan) = kept.plans.get(text) {
                return plan.clone();
            }
        }
        let plan = self.split(text, &chain);
        let mut kept = self.kept.borrow_mut();
        if kept.plans.len() >= PLANS {
            kept.plans.clear();
        }
        kept.plans.insert(text.to_string(), plan.clone());
        plan
    }

    fn split(&self, text: &str, chain: &Fallbacks) -> Option<Plan> {
        let own = self.own_coverage()?;
        self.face(&None)?;
        let shaped = self.font.layout() == Layout::Raqm;
        let bold = self.bold();
        let mut runs: Vec<(Spec, Option<Arc<Coverage>>, String)> = Vec::new();
        for ch in text.chars() {
            if let Some(kind) = follows(ch) {
                if runs.is_empty() {
                    runs.push((None, Some(Arc::clone(&own)), String::new()));
                }
                let (spec, found, chars) = runs.last_mut().expect("a run");
                if covers(found.as_deref(), ch, shaped && spec.is_none()) {
                    chars.push(ch);
                } else if kind == G::SpaceSeparator {
                    chars.push(' ');
                } else if matches!(kind, G::Control | G::LineSeparator | G::ParagraphSeparator) {
                    chars.push(ch);
                }
                continue;
            }
            let (mut spec, mut found): (Spec, Option<Arc<Coverage>>) =
                (None, Some(Arc::clone(&own)));
            if !covers(Some(&own), ch, shaped) {
                if let Some(where_) = chain.find(ch, bold) {
                    if self.face(&Some(where_.clone())).is_some() {
                        found = coverage(Path::new(&where_.0), where_.1);
                        spec = Some(where_);
                    }
                }
            }
            match runs.last_mut() {
                Some((last, _, chars)) if *last == spec => chars.push(ch),
                _ => runs.push((spec, found, ch.to_string())),
            }
        }
        let mut plan = edged(runs.into_iter().map(|(s, _, t)| (s, t)).collect());
        if plan.is_empty() {
            plan = vec![(None, String::new())];
        }
        if plan.len() == 1 && plan[0].0.is_none() && plan[0].1 == text {
            None
        } else {
            Some(plan)
        }
    }

    fn runs(&self, text: &str, direction: Option<&str>) -> Option<Plan> {
        if direction == Some("ttb") {
            return None;
        }
        self.plan(text)
    }

    /// _drop: how far below an anchor of that vertical kind the baseline lies, as Pillow
    /// places this face.
    fn drop_of(&self, vertical: char) -> Result<i64> {
        if vertical == 's' {
            return Ok(0);
        }
        if let Some(&d) = self.kept.borrow().drops.get(&vertical) {
            return Ok(d);
        }
        let top = |anchor: &str| -> Result<f64> {
            Ok(self
                .font
                .getbbox("H", "", None, None, None, 0.0, Some(anchor))?
                .1)
        };
        let d = lp_py::round(top(&format!("l{vertical}"))? - top("ls")?) as i64;
        self.kept.borrow_mut().drops.insert(vertical, d);
        Ok(d)
    }

    /// _place: where each run starts from the anchor, how far below it the baseline is and
    /// the horizontal anchor; None for an anchor a horizontal text has not.
    fn place(
        &self,
        plan: &Plan,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        anchor: Option<&str>,
    ) -> Result<Option<Placed>> {
        let anchor = anchor.filter(|a| !a.is_empty()).unwrap_or("la");
        let letters: Vec<char> = anchor.chars().collect();
        if letters.len() != 2 || !"lmr".contains(letters[0]) || !"amsd".contains(letters[1]) {
            return Ok(None);
        }
        let mut faces = Vec::with_capacity(plan.len());
        let mut widths = Vec::with_capacity(plan.len());
        for (spec, run) in plan {
            let face = self
                .face(spec)
                .ok_or_else(|| Error::new(Kind::Os, "a face of the plan"))?;
            widths.push(face.getlength(run, mode, direction, features, language)?);
            faces.push(face);
        }
        let units = lp_py::round(widths.iter().fold(0.0, |s, w| s + w) * 64.0) as i64;
        let mut x = match letters[0] {
            'l' => 0.0,
            'm' => -((units.div_euclid(2) + 32) >> 6) as f64,
            _ => -((units + 32) >> 6) as f64,
        };
        let mut placed = Vec::with_capacity(plan.len());
        for ((face, (_, run)), width) in faces.into_iter().zip(plan).zip(widths) {
            placed.push((face, run.clone(), x));
            x += width;
        }
        Ok(Some((placed, self.drop_of(letters[1])?, letters[0])))
    }

    /// _extent: the box all the runs' ink lies in, from the anchor; None for no ink.
    #[allow(clippy::too_many_arguments)]
    fn extent(
        runs: &[(Rc<Font>, String, f64)],
        drop: i64,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        stroke_width: f64,
    ) -> Result<Option<(f64, f64, f64, f64)>> {
        let mut found: Option<(f64, f64, f64, f64)> = None;
        let drop = drop as f64;
        for (face, run, x) in runs {
            let (left, top, right, bottom) = face.getbbox(
                run,
                mode,
                direction,
                features,
                language,
                stroke_width,
                Some("ls"),
            )?;
            if right <= left || bottom <= top {
                continue;
            }
            let b = (x + left, drop + top, x + right, drop + bottom);
            found = Some(match found {
                None => b,
                Some(f) => (f.0.min(b.0), f.1.min(b.1), f.2.max(b.2), f.3.max(b.3)),
            });
        }
        Ok(found)
    }

    /// _compose: several faces' runs in one mask, each drawn by ImageDraw at its place on the
    /// baseline.
    #[allow(clippy::too_many_arguments)]
    fn compose(
        runs: &[(Rc<Font>, String, f64)],
        drop: i64,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        stroke_width: f64,
        start: (f64, f64),
    ) -> Result<(Image, (i32, i64))> {
        let (fx, fy) = start;
        let Some(found) = Self::extent(
            runs,
            drop,
            mode,
            direction,
            features,
            language,
            stroke_width,
        )?
        else {
            return Ok((Image::blank(lp_pixels::Mode::L, 1, 1), (0, 0)));
        };
        let x0 = (found.0 + fx.min(0.0)).floor();
        let y0 = (found.1 + fy.min(0.0)).floor();
        let width = ((found.2 + fx.max(0.0)).ceil() - x0 + 2.0) as usize;
        let height = ((found.3 + fy.max(0.0)).ceil() - y0 + 2.0) as usize;
        let mut canvas = Image::blank(lp_pixels::Mode::L, width, height);
        let fontmode = if mode == "1" { "1" } else { "L" };
        for (face, run, x) in runs {
            let mut t = Text::new(run, face.as_ref(), "L");
            t.direction = direction;
            t.features = features;
            t.language = language;
            t.stroke_width = stroke_width;
            let at = (x + fx - x0, drop as f64 + fy - y0);
            text::draw_in(
                &mut canvas,
                at,
                &t,
                [255, 0, 0, 0],
                None,
                Some("ls"),
                "left",
                fontmode,
            )?;
        }
        Ok((canvas, (x0 as i32, y0 as i64)))
    }
}

impl TextFace for FallbackFont {
    fn getlength(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
    ) -> Result<f64> {
        let plan = if text.is_ascii() {
            None
        } else {
            self.runs(text, direction)
        };
        let Some(plan) = plan else {
            return self
                .font
                .getlength(text, mode, direction, features, language);
        };
        let mut total = 0.0;
        for (spec, run) in &plan {
            let face = self
                .face(spec)
                .ok_or_else(|| Error::new(Kind::Os, "a face of the plan"))?;
            total += face.getlength(run, mode, direction, features, language)?;
        }
        Ok(total)
    }

    fn getbbox(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        stroke_width: f64,
        anchor: Option<&str>,
    ) -> Result<(f64, f64, f64, f64)> {
        let plan = if text.is_ascii() {
            None
        } else {
            self.runs(text, direction)
        };
        let placed = match &plan {
            Some(plan) => self.place(plan, mode, direction, features, language, anchor)?,
            None => None,
        };
        let Some((runs, drop, _)) = placed else {
            return self.font.getbbox(
                text,
                mode,
                direction,
                features,
                language,
                stroke_width,
                anchor,
            );
        };
        match Self::extent(
            &runs,
            drop,
            mode,
            direction,
            features,
            language,
            stroke_width,
        )? {
            None => Ok((0.0, drop as f64, 0.0, drop as f64)),
            Some((l, t, r, b)) => Ok((l.floor(), t.floor(), r.ceil(), b.ceil())),
        }
    }

    fn getmask2(
        &self,
        text: &str,
        mode: &str,
        direction: Option<&str>,
        features: Option<&[String]>,
        language: Option<&str>,
        stroke_width: f64,
        anchor: Option<&str>,
        ink: i64,
        start: (f64, f64),
        stroke_filled: bool,
    ) -> Result<(Image, (i32, i64))> {
        let plan = if text.is_ascii() || !matches!(mode, "" | "L" | "1") {
            None
        } else {
            self.runs(text, direction)
        };
        let placed = match &plan {
            Some(plan) => self.place(plan, mode, direction, features, language, anchor)?,
            None => None,
        };
        let Some((runs, drop, horizontal)) = placed else {
            return self.font.getmask2(
                text,
                mode,
                direction,
                features,
                language,
                stroke_width,
                anchor,
                ink,
                start,
                stroke_filled,
            );
        };
        if runs.len() == 1 {
            let (face, run, _) = &runs[0];
            let anchor = format!("{horizontal}s");
            let (mask, (x, y)) = face.getmask2(
                run,
                mode,
                direction,
                features,
                language,
                stroke_width,
                Some(&anchor),
                ink,
                start,
                stroke_filled,
            )?;
            return Ok((mask, (x, y + drop)));
        }
        Self::compose(
            &runs,
            drop,
            mode,
            direction,
            features,
            language,
            stroke_width,
            start,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coverage_finds_the_last_range_that_starts_at_or_before() {
        let c = Coverage::new(vec![(10, 20), (5, 6), (30, 30)], true);
        assert!(c.contains(5) && c.contains(15) && c.contains(30));
        assert!(!c.contains(7) && !c.contains(21) && !c.contains(31) && !c.contains(0));
        assert_eq!(c.len(), 2 + 11 + 1);
    }

    #[test]
    fn spaces_at_the_edges_of_another_font_s_run_go_to_the_font() {
        let spec: Spec = Some(("cjk".into(), 0));
        let plan = edged(vec![
            (None, "ab ".into()),
            (spec.clone(), " 中文 ".into()),
            (None, "c".into()),
        ]);
        assert_eq!(
            plan,
            vec![
                (None, "ab  ".into()),
                (spec, "中文".into()),
                (None, " c".into())
            ]
        );
    }

    #[test]
    fn python_s_line_ends() {
        assert_eq!(python_lines("a\r\nb\nc\u{2028}d"), vec!["a", "b", "c", "d"]);
    }
}
