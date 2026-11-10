//! Names of the processor, graphics card and memory, tidied up for a panel
//! (sensors/hwnames.py): a system names its parts for a spec sheet ("12th Gen Intel(R)
//! Core(TM) i7-12700K"); a heading on a panel has room for the part that tells them apart
//! ("Core i7-12700K"). One set of rules serves every source of names.

use std::sync::LazyLock;

use lp_py::regex::Regex;

fn pattern(text: &str, ignore_case: bool) -> Regex {
    Regex::new(text, ignore_case).unwrap_or_else(|e| panic!("{text}: {}", e.0))
}

/// "(R)", "(TM)" and the like: "Intel(R) Core(TM)2 Duo" -> "Intel Core2 Duo"
static MARKS: LazyLock<Regex> = LazyLock::new(|| pattern(r"\((?:R|TM|C)\)|[®™©]", true));
/// What a processor's name loses, in this order: the clock ("@ 3.60GHz"), "with Radeon
/// Graphics", "6-Core Processor", "12th Gen", and "CPU" under a heading that says it.
static CPU_CUTS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
        r"\s*@?\s*\b\d+(?:[.,]\d+)?\s*[GM]Hz\b",
        r"\s+(?:with|w/)\s+(?:AMD\s+)?(?:Radeon|Vega)\b.*$",
        r"\s*\b(?:\d+|[A-Za-z]+)-Cores?\b(?:\s+Processor)?",
        r"^\s*\d+(?:st|nd|rd|th)\s+Gen\b",
        r"\b(?:CPU|Processor)\b",
    ]
    .iter()
    .map(|text| pattern(text, true))
    .collect()
});
/// The maker in front: "AMD Ryzen 5 7600" is a "Ryzen 5 7600" under a CPU heading.
static VENDOR: LazyLock<Regex> = LazyLock::new(|| {
    pattern(
        r"^(?:AMD|Intel|NVIDIA|ATI|Advanced\s+Micro\s+Devices)\b[,.]?\s*(?:Inc\.?|Corporation|Corp\.?)?",
        true,
    )
});
static GENUINE: LazyLock<Regex> = LazyLock::new(|| pattern(r"^Genuine\s+", true));
/// A name that is left with only these says nothing: the maker stays then.
const GENERIC: [&str; 7] = [
    "",
    "cpu",
    "gpu",
    "graphics",
    "processor",
    "adapter",
    "device",
];

/// One line, single spaces, no loose dashes or commas at the ends.
fn plain(text: &str) -> String {
    let joined = text
        .split(lp_py::isspace)
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    joined.trim_matches(|c| " -,;:/".contains(c)).to_string()
}

fn tidy(raw: &str, cuts: &[Regex]) -> String {
    let named = plain(&GENUINE.sub("", &MARKS.sub("", raw, 0), 0));
    let mut text = named.clone();
    for cut in cuts {
        text = cut.sub(" ", &text, 0);
    }
    let text = plain(&VENDOR.sub("", &plain(&text), 0));
    if GENERIC.contains(&lp_py::lower(&text).as_str()) || !text.chars().any(lp_py::regex::isalpha) {
        return named;
    }
    text
}

/// A processor's name for a panel: "AMD Ryzen 5 7600 6-Core Processor" becomes "Ryzen 5 7600".
pub fn tidy_cpu(raw: &str) -> String {
    tidy(raw, &CPU_CUTS)
}

/// A graphics card's name for a panel: "NVIDIA GeForce RTX 4070" becomes "GeForce RTX 4070".
pub fn tidy_gpu(raw: &str) -> String {
    tidy(raw, &[])
}

// -- memory ---------------------------------------------------------------------------------

/// Makers whose name is not what their modules say, compared on letters and digits only.
const MAKERS: [(&str, &str); 6] = [
    ("adata", "ADATA"),
    ("geil", "GeIL"),
    ("gskill", "G.Skill"),
    ("hynix", "SK hynix"),
    ("skhynix", "SK hynix"),
    ("team", "TeamGroup"),
];
/// What firmware writes where it does not know the maker.
const UNKNOWN_MAKERS: [&str; 18] = [
    "",
    "unknown",
    "undefined",
    "not specified",
    "none",
    "n/a",
    "na",
    "default string",
    "to be filled by o.e.m.",
    "manufacturer",
    "manufacturer0",
    "oem",
    "bad index",
    "[empty]",
    "empty",
    "invalid",
    "0",
    "",
];
/// "Kingston Technology Company, Inc." is "Kingston": these words close a company's name.
static COMPANY: LazyLock<Regex> = LazyLock::new(|| {
    pattern(
        concat!(
            r"(?:[\s,.]+(?:technology|technologies|corporation|corp|company|co|inc|ltd|limited|",
            r"gmbh|semiconductor|semiconductors|electronics|international|intl|group|memory|",
            r"modules?|information|usa))+[\s,.]*$"
        ),
        true,
    )
});
static CODE: LazyLock<Regex> = LazyLock::new(|| pattern(r"(?:0x)?[0-9a-f]{4}", true));
static BRACKETS: LazyLock<Regex> = LazyLock::new(|| pattern(r"\s*\([^)]*\)$", false));

/// str.title() of a text that is all capitals: each word's first letter stays, the rest are
/// made small.
fn title(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut inside = false;
    for ch in text.chars() {
        let cased = ch.is_uppercase() || ch.is_lowercase();
        if inside {
            out.push_str(&lp_py::lower(&ch.to_string()));
        } else {
            out.push(ch);
        }
        inside = cased;
    }
    out
}

/// Who made a memory module, shortened: "Kingston Technology" -> "Kingston". Empty where the
/// firmware does not know (or only has a code, as "859B").
pub fn tidy_maker(raw: &str) -> String {
    let text = plain(raw);
    let chars: Vec<char> = text.chars().collect();
    if UNKNOWN_MAKERS.contains(&lp_py::lower(&text).as_str()) || CODE.fullmatch(&chars).is_some() {
        return String::new();
    }
    let letters: String = lp_py::lower(&text)
        .chars()
        .filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        .collect();
    for (prefix, name) in MAKERS {
        if letters.starts_with(prefix) {
            return name.to_string();
        }
    }
    let text = plain(&COMPANY.sub("", &BRACKETS.sub("", &text, 0), 0));
    let cased = |c: char| c.is_uppercase() || c.is_lowercase();
    let upper = text.chars().any(cased) && !text.chars().any(char::is_lowercase);
    if upper && text.chars().count() > 4 {
        return title(&text);
    }
    text
}

/// What the memory is worth saying, in sizes people sell it in.
const SIZES_GB: [i64; 28] = [
    1, 2, 3, 4, 6, 8, 12, 16, 20, 24, 32, 36, 40, 48, 64, 80, 96, 128, 192, 256, 384, 512, 768,
    1024, 1536, 2048, 3072, 4096,
];

/// The memory a computer has, from the part of it that the system leaves to programs: 15.4 GiB
/// visible is a 16 GB machine, 25 GiB is 25.
pub fn installed_gb(visible_bytes: u64) -> i64 {
    let gib = visible_bytes as f64 / (1u64 << 30) as f64;
    for size in SIZES_GB {
        if gib <= size as f64 {
            return if gib >= size as f64 * 0.9 {
                size
            } else {
                lp_py::round(gib) as i64
            };
        }
    }
    lp_py::round(gib) as i64
}

/// A size as people say it: "32 GB", "1.5 GB".
pub fn size_label(gigabytes: f64) -> String {
    format!(
        "{} GB",
        lp_py::format_g(lp_py::round_digits(gigabytes, 1), 6)
    )
}

/// The memory's name, "Kingston DDR5-5600 32 GB"; what is not known is left out.
pub fn memory_name(maker: &str, kind: &str, speed: i64, gigabytes: f64) -> String {
    let memory = if !kind.is_empty() && speed != 0 {
        format!("{kind}-{speed}")
    } else {
        kind.to_string()
    };
    [maker.to_string(), memory, size_label(gigabytes)]
        .into_iter()
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}
