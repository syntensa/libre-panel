//! Python's fnmatch (3.12): a shell pattern as translate() makes it a regular expression, and
//! that expression's match of a whole name, as re reads it. `*` takes any run of letters, `?`
//! one, `[...]` one of a set (`[!...]` one not in it), with translate's ranges: a range from a
//! greater letter to a smaller one is left out, a hyphen at an end is one; anything else is
//! itself. translate's atomic groups for a `*` within the pattern take the first place the
//! rest fits at: as a plain glob, which finds a match wherever one is.

use std::fmt;

/// What the pattern is made of.
#[derive(Clone, Debug, PartialEq, Eq)]
enum Token {
    /// `*`: any run of letters (none too)
    Star,
    /// `?`: one letter
    Any,
    /// a letter as it is
    Letter(char),
    /// one letter of a set (or not of it), its letters and ranges
    Set(bool, Vec<(char, char)>),
    /// an empty set: no letter
    Never,
}

/// A pattern re would not take (Python raises re.error there).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Error(pub String);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A pattern made ready to match names (fnmatch._compile_pattern).
#[derive(Clone, Debug)]
pub struct Pattern {
    tokens: Vec<Token>,
}

impl Pattern {
    /// translate(pat) as re compiles it.
    pub fn new(pattern: &str) -> Result<Pattern, Error> {
        Ok(Pattern {
            tokens: translate(pattern)?,
        })
    }

    /// Whether the whole of `name` matches (fnmatchcase: as it is, case and all).
    pub fn matches(&self, name: &str) -> bool {
        let text: Vec<char> = name.chars().collect();
        glob(&self.tokens, &text)
    }
}

/// fnmatch.fnmatchcase(name, pat).
pub fn fnmatchcase(name: &str, pattern: &str) -> Result<bool, Error> {
    Ok(Pattern::new(pattern)?.matches(name))
}

/// os.path.normcase: on Windows its slashes backslashes and its letters small as the system
/// makes them (ntpath's LCMapStringEx, the invariant locale's LCMAP_LOWERCASE); as it is
/// elsewhere.
pub fn normcase(name: &str) -> String {
    #[cfg(windows)]
    {
        if name.is_empty() {
            return String::new();
        }
        let wide: Vec<u16> = name.replace('/', "\\").encode_utf16().collect();
        lowered(&wide).unwrap_or_else(|| crate::lower(&name.replace('/', "\\")))
    }
    #[cfg(not(windows))]
    {
        name.to_string()
    }
}

/// LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_LOWERCASE, text), as _winapi calls it.
#[cfg(windows)]
fn lowered(wide: &[u16]) -> Option<String> {
    use windows_sys::Win32::Globalization::{LCMapStringEx, LCMAP_LOWERCASE};
    let invariant: [u16; 1] = [0];
    let len = i32::try_from(wide.len()).ok()?;
    let call = |out: *mut u16, room: i32| unsafe {
        LCMapStringEx(
            invariant.as_ptr(),
            LCMAP_LOWERCASE,
            wide.as_ptr(),
            len,
            out,
            room,
            std::ptr::null(),
            std::ptr::null(),
            0,
        )
    };
    let size = call(std::ptr::null_mut(), 0);
    if size <= 0 {
        return None;
    }
    let mut out = vec![0u16; size as usize];
    let written = call(out.as_mut_ptr(), size);
    if written <= 0 {
        return None;
    }
    out.truncate(written as usize);
    String::from_utf16(&out).ok()
}

/// fnmatch.filter(names, pat): the names that match, each and the pattern as normcase has
/// them (on Windows, whatever their case).
pub fn filter<'a>(
    names: impl IntoIterator<Item = &'a str>,
    pattern: &str,
) -> Result<Vec<&'a str>, Error> {
    let compiled = Pattern::new(&normcase(pattern))?;
    Ok(names
        .into_iter()
        .filter(|name| compiled.matches(&normcase(name)))
        .collect())
}

/// fnmatch.translate's pieces, before they are joined: a `*` once where several follow each
/// other, a set as translate writes it out.
fn translate(pattern: &str) -> Result<Vec<Token>, Error> {
    let pat: Vec<char> = pattern.chars().collect();
    let n = pat.len();
    let mut out = Vec::new();
    let mut i = 0;
    while i < n {
        let c = pat[i];
        i += 1;
        match c {
            '*' => {
                if out.last() != Some(&Token::Star) {
                    out.push(Token::Star);
                }
            }
            '?' => out.push(Token::Any),
            '[' => {
                let mut j = i;
                if j < n && pat[j] == '!' {
                    j += 1;
                }
                if j < n && pat[j] == ']' {
                    j += 1;
                }
                while j < n && pat[j] != ']' {
                    j += 1;
                }
                if j >= n {
                    out.push(Token::Letter('['));
                    continue;
                }
                let stuff = set_text(&pat, i, j);
                i = j + 1;
                out.push(set_of(&stuff)?);
            }
            other => out.push(Token::Letter(other)),
        }
    }
    Ok(out)
}

/// What translate writes between a set's brackets, `pat[i..j]`: backslashes doubled; where it
/// has hyphens, cut into chunks at the ones that make ranges, a range that runs backwards
/// taken out, the other hyphens escaped; `&`, `~` and `|` escaped.
fn set_text(pat: &[char], start: usize, j: usize) -> Vec<char> {
    let escaped = |chunk: &[char], hyphens: bool| -> Vec<char> {
        let mut out = Vec::new();
        for &c in chunk {
            if c == '\\' || (hyphens && c == '-') {
                out.push('\\');
            }
            out.push(c);
        }
        out
    };
    let mut stuff = if !pat[start..j].contains(&'-') {
        escaped(&pat[start..j], false)
    } else {
        let mut i = start;
        let mut chunks: Vec<Vec<char>> = Vec::new();
        let mut k = if pat[i] == '!' { i + 2 } else { i + 1 };
        // (pat.find('-', k, j): none where k reaches j)
        while let Some(found) = (k..j).find(|&q| pat[q] == '-') {
            chunks.push(pat[i..found].to_vec());
            i = found + 1;
            k = found + 3;
        }
        let chunk = pat[i..j].to_vec();
        if !chunk.is_empty() {
            chunks.push(chunk);
        } else if let Some(last) = chunks.last_mut() {
            last.push('-');
        }
        // (a range from a greater letter to a smaller: none; re would refuse it)
        let mut k = chunks.len();
        while k > 1 {
            k -= 1;
            let (before, after) = (chunks[k - 1].last().copied(), chunks[k].first().copied());
            if let (Some(a), Some(b)) = (before, after) {
                if a > b {
                    let tail: Vec<char> = chunks[k][1..].to_vec();
                    let head = &mut chunks[k - 1];
                    head.pop();
                    head.extend(tail);
                    chunks.remove(k);
                }
            }
        }
        let mut joined = Vec::new();
        for (n, chunk) in chunks.iter().enumerate() {
            if n > 0 {
                joined.push('-');
            }
            joined.extend(escaped(chunk, true));
        }
        joined
    };
    // (re.sub(r'([&~|])', r'\\\1', stuff))
    let mut out = Vec::with_capacity(stuff.len());
    for c in stuff.drain(..) {
        if matches!(c, '&' | '~' | '|') {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

/// The set translate writes for `stuff`, as re reads `[stuff]`: none for an empty one, any
/// letter for `[!]`, else its letters and ranges (`!` first: those not in it).
fn set_of(stuff: &[char]) -> Result<Token, Error> {
    if stuff.is_empty() {
        return Ok(Token::Never);
    }
    if stuff == ['!'] {
        return Ok(Token::Any);
    }
    let mut body: Vec<char> = stuff.to_vec();
    if body[0] == '!' {
        body[0] = '^';
    } else if body[0] == '^' || body[0] == '[' {
        body.insert(0, '\\');
    }
    // re's character set: `^` first turns it round; a `]` that comes first is itself; `\x`
    // is x; a hyphen between two letters makes a range, one at the end is itself
    let negated = body[0] == '^';
    let body = if negated { &body[1..] } else { &body[..] };
    let mut items = Vec::new();
    let mut at = 0;
    let letter = |at: &mut usize| -> char {
        let c = body[*at];
        if c == '\\' && *at + 1 < body.len() {
            *at += 2;
            body[*at - 1]
        } else {
            *at += 1;
            c
        }
    };
    while at < body.len() {
        let lo = letter(&mut at);
        if at < body.len() && body[at] == '-' {
            if at + 1 == body.len() {
                // (a hyphen before the closing bracket: itself)
                items.push((lo, lo));
                items.push(('-', '-'));
                break;
            }
            at += 1;
            let hi = letter(&mut at);
            if hi < lo {
                return Err(Error(format!("bad character range {lo}-{hi}")));
            }
            items.push((lo, hi));
        } else {
            items.push((lo, lo));
        }
    }
    Ok(Token::Set(negated, items))
}

/// One letter against a piece that takes one.
fn takes(token: &Token, c: char) -> bool {
    match token {
        Token::Any => true,
        Token::Letter(l) => *l == c,
        Token::Set(negated, items) => items.iter().any(|&(lo, hi)| lo <= c && c <= hi) != *negated,
        Token::Never | Token::Star => false,
    }
}

/// The whole of `text` against the pieces: a `*` takes as little as lets the rest match,
/// more where it must (the last `*` seen is tried with one letter more).
fn glob(tokens: &[Token], text: &[char]) -> bool {
    let (mut t, mut s) = (0, 0);
    let mut star: Option<(usize, usize)> = None;
    while s < text.len() {
        if t < tokens.len() {
            if tokens[t] == Token::Star {
                star = Some((t, s));
                t += 1;
                continue;
            }
            if takes(&tokens[t], text[s]) {
                t += 1;
                s += 1;
                continue;
            }
        }
        match star {
            Some((at, from)) => {
                star = Some((at, from + 1));
                t = at + 1;
                s = from + 1;
            }
            None => return false,
        }
    }
    tokens[t..].iter().all(|token| *token == Token::Star)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn patterns_match_as_python_s_fnmatch_does() {
        let cases = [
            ("temp.*", "temp.cpu", true),
            ("temp.*", "temp.", true),
            ("temp.*", "tem", false),
            ("fan.?", "fan.1", true),
            ("fan.?", "fan.12", false),
            ("[a-c]x", "bx", true),
            ("[c-a]x", "bx", false),
            ("[!a-c]x", "dx", true),
            ("[!a-c]x", "ax", false),
            ("[]]", "]", true),
            ("[!]]", "a", true),
            ("[a-]", "-", true),
            ("[", "[", true),
            ("[!]", "x", false),
            ("[!]", "[!]", true),
            ("*a*b", "xaxb", true),
            ("*a*b", "xaxbx", false),
            ("a\\b", "a\\b", true),
        ];
        for (pattern, name, want) in cases {
            assert_eq!(fnmatchcase(name, pattern), Ok(want), "{pattern} {name}");
        }
    }
}
