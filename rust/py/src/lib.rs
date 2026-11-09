//! lp-py: what Python does that Libre Panel's files and messages depend on, for the Rust app
//! to do the same (docs/RUST.md): str's repr (the app's messages quote values with it), its
//! white space and its digits (Python's regular expressions and int() go by them), lower()
//! and upper(), its rounding and its division of whole numbers. Python's Unicode database is
//! the release's (Python 3.12: Unicode 15.0), and so is this crate's: Rust's own (17.0 in Rust
//! 1.97) is taken only for characters 15.0 has.

use unicode_general_category::{get_general_category, GeneralCategory as G};

pub mod codecs;
pub mod datetime;
pub mod fnmatch;
pub mod format;
pub mod json;
pub mod math;
pub mod num;
pub mod random;
pub mod re;
pub mod strftime;
pub mod toml;
pub mod value;
pub mod zoneinfo;
pub mod zones;

pub use value::{Dict, Value};

/// str.isspace (and the regular expressions' \s) for one character.
pub fn isspace(ch: char) -> bool {
    matches!(ch, '\t'..='\r' | '\x1c'..='\x1f' | ' ' | '\u{85}' | '\u{a0}' | '\u{1680}'
        | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}

/// str.strip(): the text without Python's white space at either end.
pub fn strip(text: &str) -> &str {
    text.trim_matches(isspace)
}

/// str.isprintable for one character: all but the "Other" and "Separator" categories (the
/// ASCII space is printable).
pub fn isprintable(ch: char) -> bool {
    if ch == ' ' {
        return true;
    }
    !matches!(
        get_general_category(ch),
        G::Control
            | G::Format
            | G::Surrogate
            | G::PrivateUse
            | G::Unassigned
            | G::LineSeparator
            | G::ParagraphSeparator
            | G::SpaceSeparator
    )
}

/// The value of a decimal digit (category Nd, the regular expressions' \d, what int() reads):
/// Unicode keeps them in runs of ten from 0.
pub fn decimal(ch: char) -> Option<u32> {
    if get_general_category(ch) != G::DecimalNumber {
        return None;
    }
    let mut start = u32::from(ch);
    while let Some(before) = start.checked_sub(1).and_then(char::from_u32) {
        if get_general_category(before) != G::DecimalNumber {
            break;
        }
        start -= 1;
    }
    Some((u32::from(ch) - start) % 10)
}

/// The digits that are no decimals (superscripts, circled digits and the like), which
/// str.isdigit takes too, as Python 3.12's Unicode 15.0 has them.
const DIGITS: &[(u32, u32)] = &[
    (0xb2, 0xb3),
    (0xb9, 0xb9),
    (0x1369, 0x1371),
    (0x19da, 0x19da),
    (0x2070, 0x2070),
    (0x2074, 0x2079),
    (0x2080, 0x2089),
    (0x2460, 0x2468),
    (0x2474, 0x247c),
    (0x2488, 0x2490),
    (0x24ea, 0x24ea),
    (0x24f5, 0x24fd),
    (0x24ff, 0x24ff),
    (0x2776, 0x277e),
    (0x2780, 0x2788),
    (0x278a, 0x2792),
    (0x10a40, 0x10a43),
    (0x10e60, 0x10e68),
    (0x11052, 0x1105a),
    (0x1f100, 0x1f10a),
];

/// str.isdigit of a character: a decimal or another digit.
pub fn isdigit(ch: char) -> bool {
    let c = u32::from(ch);
    decimal(ch).is_some() || DIGITS.iter().any(|&(first, last)| first <= c && c <= last)
}

/// repr() of a str: in single quotes unless it has some and no double ones, with Python's
/// escapes for the quote, the backslash and what is not printable.
pub fn repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for ch in text.chars() {
        match ch {
            _ if ch == quote || ch == '\\' => {
                out.push('\\');
                out.push(ch);
            }
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            _ if (ch as u32) < 0x20 || ch as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", ch as u32));
            }
            _ if (ch as u32) < 0x7f => out.push(ch),
            _ if isprintable(ch) => out.push(ch),
            _ if (ch as u32) <= 0xff => out.push_str(&format!("\\x{:02x}", ch as u32)),
            _ if (ch as u32) <= 0xffff => out.push_str(&format!("\\u{:04x}", ch as u32)),
            _ => out.push_str(&format!("\\U{:08x}", ch as u32)),
        }
    }
    out.push(quote);
    out
}

/// round() of a float: to the nearest whole number, halves to the even one.
pub fn round(x: f64) -> f64 {
    x.round_ties_even()
}

/// round(x, digits) of a float: the decimal of `digits` places nearest to it (half to even, of
/// its exact value), as the float nearest to that.
pub fn round_digits(x: f64, digits: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{x:.digits$}").parse().unwrap_or(x)
}

/// int() of a str as Python reads one: white space around it, a sign, digits of any script
/// with single underscores between them; None where Python raises ValueError.
pub fn int_of_str(text: &str) -> Option<num_bigint::BigInt> {
    let body = strip(text);
    let (negative, digits) = match body.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, body.strip_prefix('+').unwrap_or(body)),
    };
    if digits.is_empty()
        || digits.starts_with('_')
        || digits.ends_with('_')
        || digits.contains("__")
    {
        return None;
    }
    if digits.chars().filter(|&c| c != '_').count() > json::INT_MAX_STR_DIGITS {
        return None; // (Python's limit of digits)
    }
    let mut n = num_bigint::BigInt::from(0);
    for ch in digits.chars().filter(|&c| c != '_') {
        n = n * 10 + decimal(ch)?;
    }
    Some(if negative { -n } else { n })
}

/// The order Python's set goes through a set of pairs of small whole numbers (0 up to 2^61 - 2)
/// made by adding them in this order: by CPython's hash of a tuple and the places its table
/// gives (setobject.c), as sorted() and iteration meet them.
pub fn set_order(pairs: &[(u64, u64)]) -> Vec<(u64, u64)> {
    const PRIME_1: u64 = 11400714785074694791;
    const PRIME_2: u64 = 14029467366897019727;
    const PRIME_5: u64 = 2870177450012600261;
    let hash = |&(a, b): &(u64, u64)| -> u64 {
        let mut acc = PRIME_5;
        for lane in [a, b] {
            acc = acc.wrapping_add(lane.wrapping_mul(PRIME_2));
            acc = acc.rotate_left(31);
            acc = acc.wrapping_mul(PRIME_1);
        }
        acc = acc.wrapping_add(2 ^ (PRIME_5 ^ 3527539));
        if acc == u64::MAX {
            1546275796
        } else {
            acc
        }
    };
    // a table of (hash, pair) slots, probed as set_add_entry and set_insert_clean probe it
    fn place(table: &mut [Option<(u64, (u64, u64))>], h: u64, pair: (u64, u64)) -> bool {
        let mask = table.len() - 1;
        let mut i = (h as usize) & mask;
        let mut perturb = h;
        loop {
            let probes = if i + 9 <= mask { 9 } else { 0 };
            for step in 0..=probes {
                match table[i + step] {
                    None => {
                        table[i + step] = Some((h, pair));
                        return true;
                    }
                    Some((other, p)) if other == h && p == pair => return false,
                    Some(_) => {}
                }
            }
            perturb >>= 5;
            i = (i
                .wrapping_mul(5)
                .wrapping_add(1)
                .wrapping_add(perturb as usize))
                & mask;
        }
    }
    let mut table: Vec<Option<(u64, (u64, u64))>> = vec![None; 8];
    let mut used = 0usize;
    for pair in pairs {
        let h = hash(pair);
        if place(&mut table, h, *pair) {
            used += 1;
            let mask = table.len() - 1;
            if used * 5 >= mask * 3 {
                let wanted = if used > 50000 { used * 2 } else { used * 4 };
                let mut size = 8;
                while size <= wanted {
                    size <<= 1;
                }
                let old = std::mem::replace(&mut table, vec![None; size]);
                for (h, p) in old.into_iter().flatten() {
                    place(&mut table, h, p);
                }
            }
        }
    }
    table.into_iter().flatten().map(|(_, p)| p).collect()
}

/// format(x, ".{precision}g") (and `{x:g}` with 6): `precision` significant digits, rounded as
/// Python rounds them (to the nearest, a tie to the even one), in full from 1e-4 up to below
/// 10^precision and with an exponent beyond, trailing zeros dropped (5.0 is `5`, 1234567.0
/// `1.23457e+06`).
pub fn format_g(x: f64, precision: usize) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.into();
    }
    let p = precision.max(1);
    let rounded = format!("{:.*e}", p - 1, x);
    let (mantissa, exponent) = rounded.split_once('e').expect("an exponent");
    let exponent: i32 = exponent.parse().expect("a whole exponent");
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let mut out = String::from(if mantissa.starts_with('-') { "-" } else { "" });
    let trim = |text: String| -> String {
        if text.contains('.') {
            text.trim_end_matches('0').trim_end_matches('.').to_string()
        } else {
            text
        }
    };
    if (-4..p as i32).contains(&exponent) {
        let point = exponent + 1; // the digits before the decimal point
        let body = if point <= 0 {
            format!("0.{}{digits}", "0".repeat(point.unsigned_abs() as usize))
        } else if point as usize >= digits.len() {
            format!("{digits}{}", "0".repeat(point as usize - digits.len()))
        } else {
            format!(
                "{}.{}",
                &digits[..point as usize],
                &digits[point as usize..]
            )
        };
        out.push_str(&trim(body));
    } else {
        out.push_str(&trim(format!("{}.{}", &digits[..1], &digits[1..])));
        let sign = if exponent < 0 { '-' } else { '+' };
        out.push_str(&format!("e{sign}{:02}", exponent.abs()));
    }
    out
}

/// Of the numbers of as many digits as `digits` that read back as `x` (x > 0, its value
/// 0.<digits> times ten to the power of `point`), the one Python's repr takes: the nearest to
/// x, and of two as near the one that ends in an even digit (Rust's own can be the other:
/// 1853594818928005.25 is 1853594818928005.2 to Python and .3 to Rust).
fn nearest(x: f64, digits: &str, point: i32) -> String {
    use num_bigint::BigInt;
    use num_traits::{One, Signed, Zero};
    if x == 0.0 {
        return digits.to_string();
    }
    let n = digits.len() as u32;
    let here: BigInt = digits.parse().expect("digits");
    let ten_power = |k: i32| BigInt::from(10u32).pow(k.max(0) as u32);
    // x exactly: m times 2 to the power of e
    let bits = x.to_bits();
    let (biased, fraction) = ((bits >> 52) as i32, bits & ((1 << 52) - 1));
    let (m, e) = if biased == 0 {
        (fraction, -1074)
    } else {
        (fraction | (1 << 52), biased - 1075)
    };
    let q = point - n as i32; // a candidate is c times 10 to the power of q
                              // both as whole numbers over one denominator, 10^-q 2^-e where those are below 0
    let exact = (BigInt::from(m) * ten_power(-q)) << e.max(0) as usize;
    let scaled = |c: &BigInt| (c * ten_power(q)) << (-e).max(0) as usize;
    let (low, high) = (ten_power(n as i32 - 1), ten_power(n as i32));
    let mut best: Option<(BigInt, BigInt)> = None;
    for c in [&here - BigInt::one(), here.clone(), &here + BigInt::one()] {
        if c < low || c >= high || format!("{c}e{q}").parse::<f64>() != Ok(x) {
            continue;
        }
        let distance = (scaled(&c) - &exact).abs();
        let better = match &best {
            None => true,
            Some((_, d)) => distance < *d || (distance == *d && (&c % 2u32).is_zero()),
        };
        if better {
            best = Some((c, distance));
        }
    }
    best.map_or_else(|| digits.to_string(), |(c, _)| c.to_string())
}

/// repr() of a float: the fewest digits that read back as it, the nearest of them (nearest),
/// laid out as Python lays them out: in full from 1e-4 up to below 1e16 (`0.0001`, `100.0`), with an
/// exponent beyond (`1e-05`, `1e+16`, `1.5e+300`); `inf`, `-inf`, `nan`.
pub fn float_repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.into();
    }
    let shortest = format!("{x:e}"); // d.ddde±x, the fewest digits
    let (mantissa, exponent) = shortest.split_once('e').expect("an exponent");
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    // the value is 0.<digits> times ten to the power of point
    let point = exponent.parse::<i32>().expect("a whole exponent") + 1;
    let digits = nearest(x.abs(), &digits, point);
    let mut out = String::from(if mantissa.starts_with('-') { "-" } else { "" });
    if point <= -4 || point > 16 {
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let e = point - 1;
        out.push_str(&format!("e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs()));
    } else if point <= 0 {
        out.push_str("0.");
        out.push_str(&"0".repeat(point.unsigned_abs() as usize));
        out.push_str(&digits);
    } else {
        let whole = point as usize;
        if digits.len() <= whole {
            out.push_str(&digits);
            out.push_str(&"0".repeat(whole - digits.len()));
            out.push_str(".0");
        } else {
            out.push_str(&digits[..whole]);
            out.push('.');
            out.push_str(&digits[whole..]);
        }
    }
    out
}

/// `a / b` of two ints: the float nearest the exact quotient (halfway: the even one), as
/// Python's true division of ints gives it (not `a as f64 / b as f64`, which rounds twice
/// once a number has more than 53 bits). `b` must not be 0.
pub fn true_div(a: i128, b: i64) -> f64 {
    assert!(b != 0, "division by zero");
    let negative = (a < 0) != (b < 0);
    let (a, b) = (a.unsigned_abs(), u128::from(b.unsigned_abs()));
    let q = if a == 0 {
        0.0
    } else if a <= 1 << 53 && b <= 1 << 53 {
        a as f64 / b as f64 // (both exact: one rounding)
    } else {
        // the quotient to 55 bits or more, the rest only as whether there is any
        let bits = |n: u128| 128 - n.leading_zeros() as i32;
        let shift = (55 + bits(b) - bits(a)).max(0);
        let scaled = a << shift; // (at most 55 + 64 bits)
        let (mut n, inexact) = (scaled / b, scaled % b != 0);
        let drop = bits(n) - 53;
        let rest = n & ((1u128 << drop) - 1);
        let half = 1u128 << (drop - 1);
        n >>= drop;
        if rest > half || (rest == half && (inexact || n & 1 == 1)) {
            n += 1;
        }
        n as f64 * 2f64.powi(drop - shift)
    };
    if negative {
        -q
    } else {
        q
    }
}

/// Whether Unicode 15.0 has the character (Python 3.12's database knows nothing of later ones:
/// no case, no category).
fn known(ch: char) -> bool {
    get_general_category(ch) != G::Unassigned
}

/// Word_Break MidLetter, MidNumLet and Single_Quote (Unicode 15.0): case-ignorable as the
/// marks, format characters and modifiers are.
const MIDS: [char; 17] = [
    '\'', '.', ':', '\u{b7}', '\u{387}', '\u{55f}', '\u{5f4}', '\u{2018}', '\u{2019}', '\u{2024}',
    '\u{2027}', '\u{fe13}', '\u{fe52}', '\u{fe55}', '\u{ff07}', '\u{ff0e}', '\u{ff1a}',
];

/// Unicode's Case_Ignorable, as Python 3.12 has it.
fn case_ignorable(ch: char) -> bool {
    matches!(
        get_general_category(ch),
        G::NonspacingMark | G::EnclosingMark | G::Format | G::ModifierLetter | G::ModifierSymbol
    ) || MIDS.contains(&ch)
}

/// Unicode's Cased, as Python 3.12 has it: the letters of a case in Unicode 15.0 (ʕ was one
/// until 16.0) and what else Unicode counts as lower- or uppercase.
fn cased(ch: char) -> bool {
    known(ch)
        && (matches!(
            get_general_category(ch),
            G::UppercaseLetter | G::LowercaseLetter | G::TitlecaseLetter
        ) || ch.is_lowercase()
            || ch.is_uppercase())
}

/// A character's case mapping as Unicode 15.0 has it: Rust's, unless the character or what it
/// maps to came later (ƛ's capital was new in 16.0: 15.0 has ƛ without one).
fn push_mapped(out: &mut String, ch: char, to: impl Iterator<Item = char> + Clone) {
    if known(ch) && to.clone().all(known) {
        out.extend(to);
    } else {
        out.push(ch);
    }
}

/// Whether the capital sigma at `at` ends a word (Final_Sigma): a cased letter before it and
/// none after it, case-ignorable characters between not counted.
fn final_sigma(chars: &[char], at: usize) -> bool {
    let before = chars[..at].iter().rev().find(|&&c| !case_ignorable(c));
    let after = chars[at + 1..].iter().find(|&&c| !case_ignorable(c));
    before.is_some_and(|&c| cased(c)) && !after.is_some_and(|&c| cased(c))
}

/// str.lower(): every character's full lowercase (some become two), the capital sigma at the
/// end of a word the final one (ς).
pub fn lower(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    for (i, &ch) in chars.iter().enumerate() {
        if ch == '\u{3a3}' {
            out.push(if final_sigma(&chars, i) {
                '\u{3c2}'
            } else {
                '\u{3c3}'
            });
        } else {
            push_mapped(&mut out, ch, ch.to_lowercase());
        }
    }
    out
}

/// str.upper(): every character's full uppercase (ß becomes SS).
pub fn upper(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        push_mapped(&mut out, ch, ch.to_uppercase());
    }
    out
}

#[path = "fold.rs"]
mod fold;

/// str.casefold(): every character folded as Unicode's CaseFolding has it (C and F: ß becomes
/// ss, ς σ, Cherokee's small letters its capitals), else its full lowercase (without the
/// final sigma of lower()).
pub fn casefold(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match fold::FOLD.binary_search_by_key(&u32::from(ch), |&(from, _)| from) {
            Ok(i) => out.push_str(fold::FOLD[i].1),
            Err(_) => push_mapped(&mut out, ch, ch.to_lowercase()),
        }
    }
    out
}

/// int() of a text of decimal digits only (what a regular expression's \d+ matched): its
/// value, in Python's digits of any script (beyond i128's range: its largest, where Python's
/// ints go on); None for anything else.
pub fn int_of_digits(text: &str) -> Option<i128> {
    if text.is_empty() {
        return None;
    }
    text.chars().try_fold(0i128, |n, ch| {
        let d = decimal(ch)?;
        Some(n.saturating_mul(10).saturating_add(i128::from(d)))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repr_quotes_and_escapes_as_python_does() {
        assert_eq!(repr("abc"), "'abc'");
        assert_eq!(repr("it's"), "\"it's\"");
        assert_eq!(repr("'\""), "'\\'\"'");
        assert_eq!(repr("a\\b\n\t\r\x01\x7f"), "'a\\\\b\\n\\t\\r\\x01\\x7f'");
        assert_eq!(repr("\u{a0}é\u{200b}\u{e000}"), "'\\xa0é\\u200b\\ue000'");
        assert_eq!(repr("\u{10ffff}😀"), "'\\U0010ffff😀'");
    }

    #[test]
    fn digits_of_every_script_and_python_s_rounding() {
        assert_eq!(decimal('7'), Some(7));
        assert_eq!(decimal('\u{663}'), Some(3));
        assert_eq!(decimal('\u{1D7D8}'), Some(0)); // (the third run of ten in a row)
        assert_eq!(decimal('x'), None);
        assert_eq!(int_of_digits("\u{663}\u{661}"), Some(31));
        assert_eq!(
            [0.5, 1.5, 2.5, -2.5, 2.6].map(round),
            [0.0, 2.0, 2.0, -2.0, 3.0]
        );
        assert!(isspace('\u{1c}') && !isspace('\u{200b}'));
        assert_eq!(strip("\u{3000} a \n"), "a");
    }

    #[test]
    fn case_and_division_as_python_s() {
        // (the apostrophe and the accent are skipped: what comes after them decides)
        assert_eq!(
            lower("ΣΑΣ ΑΣ'Α Α\u{301}Σ\u{301}"),
            "σας ασ'α α\u{301}ς\u{301}"
        );
        assert_eq!(lower("İ"), "i\u{307}");
        assert_eq!(upper("straße ŉ"), "STRASSE ʼN");
        // (16.0's capitals of ƛ and ɤ, and ʕ a letter of a case until then)
        assert_eq!(upper("ƛɤ\u{a7cb}"), "ƛɤ\u{a7cb}");
        assert_eq!(lower("ΑΣʕ"), "ασʕ");
        assert_eq!(true_div(0, -5).to_bits(), (-0.0f64).to_bits());
        assert_eq!(true_div(-7, 2), -3.5);
        // 2⁵⁵ + 3 over 1 and 2⁵⁴ + 1 over 1: halfway and past it, to the even neighbour
        assert_eq!(true_div((1 << 55) + 3, 1), ((1u64 << 55) + 4) as f64);
        assert_eq!(true_div((1 << 54) + 2, 1), (1u64 << 54) as f64);
        assert_eq!(true_div(11997452401628418 * 100, 100), 11997452401628418.0);
    }
}

#[path = "wide.rs"]
mod wide_table;

/// unicodedata.east_asian_width(ch) in "WF": a character Unicode 15.0 calls wide or
/// fullwidth.
pub fn east_asian_wide(ch: char) -> bool {
    let cp = ch as u32;
    let i = wide_table::WIDE.partition_point(|&(_, last)| last < cp);
    wide_table::WIDE
        .get(i)
        .is_some_and(|&(first, _)| first <= cp)
}

/// sum() of floats as Python 3.12 adds them: from the int 0, the first added to it, the
/// others with Neumaier's compensation (sum([0.55] * 7) is 3.8500000000000005, not the
/// 3.8499999999999996 a plain loop gives).
pub fn sum_floats(items: impl IntoIterator<Item = f64>) -> f64 {
    let mut items = items.into_iter();
    let Some(first) = items.next() else {
        return 0.0;
    };
    let (mut total, mut c) = (0.0 + first, 0.0f64);
    for x in items {
        let t = total + x;
        if total.abs() >= x.abs() {
            c += (total - t) + x;
        } else {
            c += (x - t) + total;
        }
        total = t;
    }
    // (the compensation not added where it is 0 or no number, keeping a sum's sign and
    // infinity)
    if c != 0.0 && c.is_finite() {
        total += c;
    }
    total
}

/// str.splitlines(): the lines of a text, split at every line boundary Python knows (\n, \r,
/// \r\n, \v, \f, \x1c, \x1d, \x1e, \x85, U+2028, U+2029), the ends left out.
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, ch)) = chars.next() {
        let end = match ch {
            '\r' => {
                if let Some(&(_, '\n')) = chars.peek() {
                    chars.next();
                    i + 2
                } else {
                    i + 1
                }
            }
            '\n' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => i + ch.len_utf8(),
            _ => continue,
        };
        lines.push(&text[start..i]);
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// float(text) of a str, as Python 3.12 reads it (PyFloat_FromString): its digits of any
/// script and its white space made ASCII (what is neither, nor ASCII, is no number),
/// underscores only between two digits, ASCII white space at either end, then a sign and
/// inf, infinity or nan, or digits with a point and an exponent, rounded to the nearest;
/// None where Python raises ValueError.
pub fn float_of_str(text: &str) -> Option<f64> {
    // _PyUnicode_TransformDecimalAndSpaceToASCII
    let mut ascii = String::with_capacity(text.len());
    for ch in text.chars() {
        if (ch as u32) < 127 {
            ascii.push(ch);
        } else if isspace(ch) {
            ascii.push(' ');
        } else {
            ascii.push(char::from(b'0' + decimal(ch)? as u8));
        }
    }
    if ascii.contains('\0') {
        return None; // (no embedded NULs)
    }
    // _Py_string_to_number_with_underscores: underscores only after a digit and before one
    let mut clean = String::with_capacity(ascii.len());
    let mut prev = '\0';
    for ch in ascii.chars() {
        if ch == '_' {
            if !prev.is_ascii_digit() {
                return None;
            }
        } else {
            if prev == '_' && !ch.is_ascii_digit() {
                return None;
            }
            clean.push(ch);
        }
        prev = ch;
    }
    if prev == '_' {
        return None;
    }
    // float_from_string_inner: Py_ISSPACE stripped, then the whole of it a number
    let s = clean.trim_matches(|c: char| matches!(c, ' ' | '\t' | '\n' | '\x0b' | '\x0c' | '\r'));
    if s.is_empty() {
        return None;
    }
    // (Rust's grammar is _Py_dg_strtod's and _Py_parse_inf_or_nan's: a sign, inf, infinity or
    // nan in any case, digits with a point and digits on one side at least, an exponent
    // with a digit at least; and it rounds as dtoa does, to the nearest)
    s.parse::<f64>().ok()
}
