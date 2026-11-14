//! What Python's re does where it matches without regard to case (`re.IGNORECASE`, as pathlib's
//! glob matches a folder's names on Windows): a character of the pattern that has a case meets
//! a character whose simple lowercase (Unicode 15.0) is the pattern's, or is one of the other
//! lowercase characters of the same uppercase ("ſ" for "s", "ι" for "ͅ"); a character without
//! case meets only itself. One character for one: "ß" never meets "SS".

#[path = "re_case.rs"]
mod table;

fn lookup(cp: u32) -> Option<u32> {
    table::LOWER
        .binary_search_by_key(&cp, |&(from, _)| from)
        .ok()
        .map(|i| table::LOWER[i].1)
}

/// _sre.unicode_tolower: the character's simple lowercase (itself where it has none).
pub fn lower(cp: u32) -> u32 {
    lookup(cp).unwrap_or(cp)
}

/// _sre.unicode_iscased: whether the character has a lowercase or an uppercase other than
/// itself.
pub fn cased(cp: u32) -> bool {
    let i = table::CASED.partition_point(|&(_, last)| last < cp);
    table::CASED.get(i).is_some_and(|&(first, _)| first <= cp)
}

/// The other lowercase characters of the uppercase of `lowercase` (re._casefix).
fn others(lowercase: u32) -> &'static [u32] {
    table::EXTRA
        .binary_search_by_key(&lowercase, |&(from, _)| from)
        .map_or(&[], |i| table::EXTRA[i].1)
}

/// Whether `name`'s character meets the pattern's `pattern` under re.IGNORECASE.
pub fn same_ignoring_case(pattern: char, name: char) -> bool {
    let p = pattern as u32;
    if !cased(p) {
        return pattern == name;
    }
    let (want, got) = (lower(p), lower(name as u32));
    got == want || others(want).contains(&got)
}

/// How many characters of `name` `prefix` takes up where `name` starts with it under
/// re.IGNORECASE (`re.compile(re.escape(prefix) + ".*", re.I | re.S).fullmatch(name)`).
pub fn prefix_ignoring_case(prefix: &str, name: &str) -> Option<usize> {
    let mut chars = name.chars();
    let mut count = 0;
    for p in prefix.chars() {
        if !same_ignoring_case(p, chars.next()?) {
            return None;
        }
        count += 1;
    }
    Some(count)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cases_as_re_meets_them() {
        assert!(same_ignoring_case('a', 'A'));
        assert!(same_ignoring_case('S', 'ſ'));
        assert!(same_ignoring_case('k', '\u{212a}')); // the Kelvin sign
        assert!(same_ignoring_case('\u{345}', 'Ι'));
        assert!(same_ignoring_case('i', 'İ')); // its simple lowercase is i
        assert!(!same_ignoring_case('ß', 's'));
        assert!(!same_ignoring_case('-', '_'));
        assert!(same_ignoring_case('1', '1'));
        assert_eq!(prefix_ignoring_case("Lumen", "lumen-vertical"), Some(5));
        assert_eq!(prefix_ignoring_case("Kx", "\u{212a}x-1920x720"), Some(2));
        assert_eq!(prefix_ignoring_case("lumen", "lume"), None);
        assert!(cased('A' as u32) && cased('ǅ' as u32) && !cased('1' as u32));
        assert!(!cased(0x10ffff));
    }
}
