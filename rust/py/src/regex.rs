//! The part of Python's re the app's own patterns are written in (str patterns, with or without
//! re.IGNORECASE): characters, `.`, sets with ranges and `\d \s \w`, `^ $ \A \Z \b \B`, groups
//! (capturing, `(?:…)`, `(?=…)`, `(?!…)`, `(?>…)`), `|`, and `* + ? {m,n}`, greedy, not greedy
//! (`*?`) or keeping what they took (`*+`). It matches as
//! re does: by going back, the first way that matches from the leftmost start; `\d`, `\s`, `\w`
//! and the cases by Unicode as str's isdecimal, isspace and isalnum have them. What it does not
//! have (references back, looking behind, flags inside a pattern, names) is an error when the
//! pattern is made, never another match.

use unicode_general_category::{get_general_category, GeneralCategory as Cat};

use crate::re::same_ignoring_case;

#[derive(Debug, Clone, PartialEq)]
pub struct Error(pub String);

/// str.isalnum of one character: a letter, or a number of any kind.
pub fn isalnum(ch: char) -> bool {
    isalpha(ch)
        || matches!(
            get_general_category(ch),
            Cat::DecimalNumber | Cat::LetterNumber | Cat::OtherNumber
        )
}

/// str.isalpha of one character.
pub fn isalpha(ch: char) -> bool {
    matches!(
        get_general_category(ch),
        Cat::UppercaseLetter
            | Cat::LowercaseLetter
            | Cat::TitlecaseLetter
            | Cat::ModifierLetter
            | Cat::OtherLetter
    )
}

fn word(ch: char) -> bool {
    ch == '_' || isalnum(ch)
}

#[derive(Debug, Clone)]
enum Item {
    Range(char, char),
    /// `\d`, `\s`, `\w` (true) or `\D`, `\S`, `\W`
    Digit(bool),
    Space(bool),
    Word(bool),
}

#[derive(Debug, Clone)]
enum Node {
    Char(char),
    Any,
    /// a set, negated or not
    Set(bool, Vec<Item>),
    Start,
    /// `$`: the end, or before a line feed that ends the text
    End,
    /// `\Z`
    VeryEnd,
    /// `\b` (true) or `\B`
    Boundary(bool),
    Group(Option<usize>, Box<Node>),
    Cat(Vec<Node>),
    Alt(Vec<Node>),
    Repeat(Box<Node>, usize, usize, bool),
    /// `(?=…)`, or `(?!…)` (true)
    Look(Box<Node>, bool),
    /// `(?>…)` and `x*+`: the first way it matches, never gone back into
    Atomic(Box<Node>),
}

struct Parser {
    chars: Vec<char>,
    at: usize,
    groups: usize,
}

impl Parser {
    fn peek(&self) -> Option<char> {
        self.chars.get(self.at).copied()
    }

    fn eat(&mut self, ch: char) -> bool {
        if self.peek() == Some(ch) {
            self.at += 1;
            true
        } else {
            false
        }
    }

    fn alt(&mut self) -> Result<Node, Error> {
        let mut branches = vec![self.cat()?];
        while self.eat('|') {
            branches.push(self.cat()?);
        }
        Ok(if branches.len() == 1 {
            branches.pop().unwrap_or(Node::Cat(Vec::new()))
        } else {
            Node::Alt(branches)
        })
    }

    fn cat(&mut self) -> Result<Node, Error> {
        let mut nodes = Vec::new();
        while let Some(ch) = self.peek() {
            if ch == '|' || ch == ')' {
                break;
            }
            let atom = self.atom()?;
            nodes.push(self.repeated(atom)?);
        }
        Ok(Node::Cat(nodes))
    }

    /// `{m}`, `{m,}`, `{,n}`, `{m,n}` at the place, where it is one (else `{` is a character).
    fn braces(&mut self) -> Option<(usize, usize)> {
        let start = self.at;
        let number = |p: &mut Parser| {
            let from = p.at;
            while p.peek().is_some_and(|c| c.is_ascii_digit()) {
                p.at += 1;
            }
            let digits: String = p.chars[from..p.at].iter().collect();
            digits.parse::<usize>().ok()
        };
        self.at += 1; // {
        let low = number(self);
        let found = if self.eat(',') {
            let high = number(self);
            (low.is_some() || high.is_some() || self.peek() == Some('}'))
                .then(|| (low.unwrap_or(0), high.unwrap_or(usize::MAX)))
                .filter(|_| low.is_some() || high.is_some())
        } else {
            low.map(|n| (n, n))
        };
        match found {
            Some(range) if self.eat('}') => Some(range),
            _ => {
                self.at = start;
                None
            }
        }
    }

    fn repeated(&mut self, atom: Node) -> Result<Node, Error> {
        let (min, max) = match self.peek() {
            Some('*') => (0, usize::MAX),
            Some('+') => (1, usize::MAX),
            Some('?') => (0, 1),
            Some('{') => match self.braces() {
                Some(range) => {
                    self.at -= 1; // (the `}` is eaten below as the others' sign is)
                    range
                }
                None => return Ok(atom),
            },
            _ => return Ok(atom),
        };
        self.at += 1;
        if min > max {
            return Err(Error("min repeat greater than max repeat".into()));
        }
        if matches!(
            atom,
            Node::Start | Node::End | Node::VeryEnd | Node::Boundary(_)
        ) {
            return Err(Error("nothing to repeat".into()));
        }
        let lazy = self.eat('?');
        let possessive = !lazy && self.eat('+');
        if matches!(self.peek(), Some('*' | '+' | '?')) {
            return Err(Error("multiple repeat".into()));
        }
        if self.peek() == Some('{') {
            let here = self.at;
            let more = self.braces().is_some();
            self.at = here;
            if more {
                return Err(Error("multiple repeat".into()));
            }
        }
        let node = Node::Repeat(Box::new(atom), min, max, !lazy);
        Ok(if possessive {
            Node::Atomic(Box::new(node))
        } else {
            node
        })
    }

    /// What follows a backslash, in a set or out of one: a class, or a character.
    fn escape(&mut self, in_set: bool) -> Result<Node, Error> {
        let Some(ch) = self.peek() else {
            return Err(Error("bad escape (end of pattern)".into()));
        };
        self.at += 1;
        let class = |item: Item| Node::Set(false, vec![item]);
        Ok(match ch {
            'd' => class(Item::Digit(true)),
            'D' => class(Item::Digit(false)),
            's' => class(Item::Space(true)),
            'S' => class(Item::Space(false)),
            'w' => class(Item::Word(true)),
            'W' => class(Item::Word(false)),
            'n' => Node::Char('\n'),
            't' => Node::Char('\t'),
            'r' => Node::Char('\r'),
            'f' => Node::Char('\x0c'),
            'v' => Node::Char('\x0b'),
            'a' => Node::Char('\x07'),
            'b' if in_set => Node::Char('\x08'),
            'b' => Node::Boundary(true),
            'B' if !in_set => Node::Boundary(false),
            'A' if !in_set => Node::Start,
            'Z' if !in_set => Node::VeryEnd,
            c if c.is_ascii_alphanumeric() => return Err(Error(format!("bad escape \\{c}"))),
            c => Node::Char(c),
        })
    }

    fn set(&mut self) -> Result<Node, Error> {
        let negated = self.eat('^');
        let mut items = Vec::new();
        let mut first = true;
        loop {
            let Some(ch) = self.peek() else {
                return Err(Error("unterminated character set".into()));
            };
            self.at += 1;
            if ch == ']' && !first {
                break;
            }
            first = false;
            let one = |p: &mut Parser, ch: char| -> Result<Result<char, Item>, Error> {
                if ch != '\\' {
                    return Ok(Ok(ch));
                }
                match p.escape(true)? {
                    Node::Char(c) => Ok(Ok(c)),
                    Node::Set(_, mut items) => Ok(Err(items.remove(0))),
                    _ => Err(Error("bad escape".into())),
                }
            };
            let low = one(self, ch)?;
            let ranged =
                self.peek() == Some('-') && self.chars.get(self.at + 1).is_some_and(|&c| c != ']');
            match (low, ranged) {
                (Ok(low), true) => {
                    self.at += 1;
                    let ch = self.chars[self.at];
                    self.at += 1;
                    let Ok(high) = one(self, ch)? else {
                        return Err(Error("bad character range".into()));
                    };
                    if high < low {
                        return Err(Error("bad character range".into()));
                    }
                    items.push(Item::Range(low, high));
                }
                (Ok(low), false) => items.push(Item::Range(low, low)),
                (Err(_), true) => return Err(Error("bad character range".into())),
                (Err(item), false) => items.push(item),
            }
        }
        Ok(Node::Set(negated, items))
    }

    fn atom(&mut self) -> Result<Node, Error> {
        let ch = self.chars[self.at];
        self.at += 1;
        Ok(match ch {
            '.' => Node::Any,
            '^' => Node::Start,
            '$' => Node::End,
            '*' | '+' | '?' => return Err(Error("nothing to repeat".into())),
            '\\' => self.escape(false)?,
            '[' => self.set()?,
            '(' => {
                let node = if self.eat('?') {
                    let kind = self.peek();
                    self.at += 1;
                    let inner = self.alt()?;
                    match kind {
                        Some(':') => Node::Group(None, Box::new(inner)),
                        Some('=') => Node::Look(Box::new(inner), false),
                        Some('!') => Node::Look(Box::new(inner), true),
                        Some('>') => Node::Atomic(Box::new(inner)),
                        _ => return Err(Error("this kind of group is not here".into())),
                    }
                } else {
                    self.groups += 1;
                    let number = self.groups;
                    Node::Group(Some(number), Box::new(self.alt()?))
                };
                if !self.eat(')') {
                    return Err(Error("missing ), unterminated subpattern".into()));
                }
                node
            }
            c => Node::Char(c),
        })
    }
}

/// Where a pattern met a text: the whole and each group, as places among the text's characters.
#[derive(Debug, Clone, PartialEq)]
pub struct Found {
    pub spans: Vec<Option<(usize, usize)>>,
}

impl Found {
    pub fn start(&self) -> usize {
        self.spans[0].map_or(0, |s| s.0)
    }

    pub fn end(&self) -> usize {
        self.spans[0].map_or(0, |s| s.1)
    }

    /// m.group(n) of the match in `text` (the characters it was looked for in).
    pub fn group(&self, text: &[char], n: usize) -> Option<String> {
        self.spans
            .get(n)
            .copied()
            .flatten()
            .map(|(a, b)| text[a..b].iter().collect())
    }
}

type Caps = Vec<Option<(usize, usize)>>;

/// A compiled pattern (re.compile).
#[derive(Debug, Clone)]
pub struct Regex {
    node: Node,
    groups: usize,
    ignore: bool,
}

struct Run<'a> {
    text: &'a [char],
    ignore: bool,
}

impl Run<'_> {
    fn in_set(&self, negated: bool, items: &[Item], ch: char) -> bool {
        let hit = items.iter().any(|item| match *item {
            Item::Range(low, high) => {
                (low <= ch && ch <= high)
                    || (self.ignore
                        && if low == high {
                            same_ignoring_case(low, ch)
                        } else if (high as u32) - (low as u32) <= 4096 {
                            (low..=high).any(|c| same_ignoring_case(c, ch))
                        } else {
                            let lower = char::from_u32(crate::re::lower(ch as u32)).unwrap_or(ch);
                            low <= lower && lower <= high
                        })
            }
            Item::Digit(yes) => crate::decimal(ch).is_some() == yes,
            Item::Space(yes) => crate::isspace(ch) == yes,
            Item::Word(yes) => word(ch) == yes,
        });
        hit != negated
    }

    /// Whether the node, which is one character wide, takes the character.
    fn one(&self, node: &Node, ch: char) -> Option<bool> {
        Some(match node {
            Node::Char(c) => {
                if self.ignore {
                    same_ignoring_case(*c, ch)
                } else {
                    *c == ch
                }
            }
            Node::Any => ch != '\n',
            Node::Set(negated, items) => self.in_set(*negated, items, ch),
            _ => return None,
        })
    }

    fn step(
        &self,
        node: &Node,
        i: usize,
        caps: &mut Caps,
        k: &mut dyn FnMut(usize, &mut Caps) -> bool,
    ) -> bool {
        let text = self.text;
        match node {
            Node::Char(_) | Node::Any | Node::Set(..) => {
                i < text.len() && self.one(node, text[i]) == Some(true) && k(i + 1, caps)
            }
            Node::Start => i == 0 && k(i, caps),
            Node::End => {
                (i == text.len() || (i + 1 == text.len() && text[i] == '\n')) && k(i, caps)
            }
            Node::VeryEnd => i == text.len() && k(i, caps),
            Node::Boundary(wanted) => {
                let before = i > 0 && word(text[i - 1]);
                let after = i < text.len() && word(text[i]);
                // (in an empty text neither is met: Python 3.12's \B)
                !text.is_empty() && ((before != after) == *wanted) && k(i, caps)
            }
            Node::Group(None, inner) => self.step(inner, i, caps, k),
            Node::Group(Some(n), inner) => {
                let n = *n;
                self.step(inner, i, caps, &mut |j, caps| {
                    let old = caps[n];
                    caps[n] = Some((i, j));
                    if k(j, caps) {
                        true
                    } else {
                        caps[n] = old;
                        false
                    }
                })
            }
            Node::Cat(nodes) => self.cat(nodes, i, caps, k),
            Node::Alt(branches) => branches.iter().any(|branch| self.step(branch, i, caps, k)),
            Node::Look(inner, negative) => {
                let mut tried = caps.clone();
                let met = self.step(inner, i, &mut tried, &mut |_, _| true);
                if met == *negative {
                    return false;
                }
                if met {
                    *caps = tried; // (what a look ahead that met caught stays caught)
                }
                k(i, caps)
            }
            Node::Atomic(inner) => {
                let mut end = None;
                let mut tried = caps.clone();
                if !self.step(inner, i, &mut tried, &mut |j, _| {
                    end = Some(j);
                    true
                }) {
                    return false;
                }
                let before = std::mem::replace(caps, tried);
                if k(end.unwrap_or(i), caps) {
                    true
                } else {
                    *caps = before;
                    false
                }
            }
            Node::Repeat(inner, min, max, greedy) => {
                if matches!(**inner, Node::Char(_) | Node::Any | Node::Set(..)) {
                    return self.run_of(inner, *min, *max, *greedy, i, caps, k);
                }
                self.repeat(inner, *min, *max, *greedy, 0, i, caps, k)
            }
        }
    }

    fn cat(
        &self,
        nodes: &[Node],
        i: usize,
        caps: &mut Caps,
        k: &mut dyn FnMut(usize, &mut Caps) -> bool,
    ) -> bool {
        match nodes.split_first() {
            None => k(i, caps),
            Some((first, rest)) => {
                self.step(first, i, caps, &mut |j, caps| self.cat(rest, j, caps, k))
            }
        }
    }

    /// A character-wide node again and again: counted, then the rest tried from the longest
    /// run down (or from the shortest up).
    #[allow(clippy::too_many_arguments)]
    fn run_of(
        &self,
        node: &Node,
        min: usize,
        max: usize,
        greedy: bool,
        i: usize,
        caps: &mut Caps,
        k: &mut dyn FnMut(usize, &mut Caps) -> bool,
    ) -> bool {
        let text = self.text;
        let fits = |j: usize| j < text.len() && self.one(node, text[j]) == Some(true);
        if greedy {
            let mut count = 0;
            while count < max && fits(i + count) {
                count += 1;
            }
            loop {
                if count < min {
                    return false;
                }
                if k(i + count, caps) {
                    return true;
                }
                if count == 0 {
                    return false;
                }
                count -= 1;
            }
        }
        let mut count = 0;
        while count < min {
            if !fits(i + count) {
                return false;
            }
            count += 1;
        }
        loop {
            if k(i + count, caps) {
                return true;
            }
            if count >= max || !fits(i + count) {
                return false;
            }
            count += 1;
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn repeat(
        &self,
        node: &Node,
        min: usize,
        max: usize,
        greedy: bool,
        count: usize,
        i: usize,
        caps: &mut Caps,
        k: &mut dyn FnMut(usize, &mut Caps) -> bool,
    ) -> bool {
        let again = |caps: &mut Caps, k: &mut dyn FnMut(usize, &mut Caps) -> bool| {
            count < max
                && self.step(node, i, caps, &mut |j, caps| {
                    // (a round that took nothing, once enough were made, is the last: what
                    // it caught stays caught, and the rest follows)
                    if j == i && count >= min {
                        k(j, caps)
                    } else {
                        self.repeat(node, min, max, greedy, count + 1, j, caps, k)
                    }
                })
        };
        // (which is tried first is the whole difference)
        if greedy {
            if again(caps, k) {
                return true;
            }
            count >= min && k(i, caps)
        } else {
            if count >= min && k(i, caps) {
                return true;
            }
            again(caps, k)
        }
    }
}

impl Regex {
    /// re.compile(pattern), with re.IGNORECASE where `ignore_case`.
    pub fn new(pattern: &str, ignore_case: bool) -> Result<Regex, Error> {
        let mut parser = Parser {
            chars: pattern.chars().collect(),
            at: 0,
            groups: 0,
        };
        let node = parser.alt()?;
        if parser.at < parser.chars.len() {
            return Err(Error("unbalanced parenthesis".into()));
        }
        Ok(Regex {
            node,
            groups: parser.groups,
            ignore: ignore_case,
        })
    }

    /// The match that starts at `start`, where there is one; `whole`: it ends with the text;
    /// `advance`: it is not empty.
    fn at(&self, text: &[char], start: usize, whole: bool, advance: bool) -> Option<Found> {
        let run = Run {
            text,
            ignore: self.ignore,
        };
        let mut caps: Caps = vec![None; self.groups + 1];
        let mut end = None;
        let met = run.step(&self.node, start, &mut caps, &mut |j, _| {
            if (whole && j != text.len()) || (advance && j == start) {
                return false;
            }
            end = Some(j);
            true
        });
        met.then(|| {
            caps[0] = Some((start, end.unwrap_or(start)));
            Found { spans: caps }
        })
    }

    fn search_from(&self, text: &[char], from: usize, advance: bool) -> Option<Found> {
        (from..=text.len()).find_map(|start| self.at(text, start, false, advance && start == from))
    }

    /// pattern.search(text)
    pub fn search(&self, text: &[char]) -> Option<Found> {
        self.search_from(text, 0, false)
    }

    /// pattern.match(text)
    pub fn matches(&self, text: &[char]) -> Option<Found> {
        self.at(text, 0, false, false)
    }

    /// pattern.fullmatch(text)
    pub fn fullmatch(&self, text: &[char]) -> Option<Found> {
        self.at(text, 0, true, false)
    }

    /// Every match from left to right, none over another (pattern.finditer); at most `limit`.
    pub fn find_all(&self, text: &[char], limit: usize) -> Vec<Found> {
        let mut found = Vec::new();
        let (mut from, mut advance) = (0, false);
        while found.len() < limit {
            let Some(m) = self.search_from(text, from, advance) else {
                break;
            };
            from = m.end();
            advance = m.start() == m.end();
            found.push(m);
        }
        found
    }

    /// pattern.sub(with, text), `with` taken as it is (no references to groups); `count`: how
    /// many at most (0: all).
    pub fn sub(&self, with: &str, text: &str, count: usize) -> String {
        let chars: Vec<char> = text.chars().collect();
        let limit = if count == 0 { usize::MAX } else { count };
        let mut out = String::with_capacity(text.len());
        let mut done = 0;
        for m in self.find_all(&chars, limit) {
            out.extend(&chars[done..m.start()]);
            out.push_str(with);
            done = m.end();
        }
        out.extend(&chars[done..]);
        out
    }

    /// pattern.split(text, maxsplit): the parts between the matches, and the groups' texts
    /// between them (None for a group that took no part).
    pub fn split(&self, text: &str, maxsplit: usize) -> Vec<Option<String>> {
        let chars: Vec<char> = text.chars().collect();
        let limit = if maxsplit == 0 { usize::MAX } else { maxsplit };
        let mut out = Vec::new();
        let mut done = 0;
        for m in self.find_all(&chars, limit) {
            out.push(Some(chars[done..m.start()].iter().collect()));
            for n in 1..m.spans.len() {
                out.push(m.group(&chars, n));
            }
            done = m.end();
        }
        out.push(Some(chars[done..].iter().collect()));
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chars(text: &str) -> Vec<char> {
        text.chars().collect()
    }

    #[test]
    fn matches_as_re_does() {
        let number = Regex::new(r"^\s*(-?[0-9]+(?:[.,][0-9]+)?)\s*(.*)$", false).unwrap();
        let text = chars(" 45,0 °C");
        let m = number.matches(&text).unwrap();
        assert_eq!(m.group(&text, 1).as_deref(), Some("45,0"));
        assert_eq!(m.group(&text, 2).as_deref(), Some("°C"));
        let slug = Regex::new("[^a-z0-9]+", false).unwrap();
        assert_eq!(slug.sub("_", "core (tctl/tdie)", 0), "core_tctl_tdie_");
        let stars = Regex::new("x*", false).unwrap();
        assert_eq!(stars.sub("-", "abxd", 0), "-a-b--d-");
        let marks = Regex::new(r"\((?:R|TM|C)\)|[®™©]", true).unwrap();
        assert_eq!(
            marks.sub("", "Intel(r) Core(TM)2 Duo®", 0),
            "Intel Core2 Duo"
        );
        assert!(Regex::new(r"(a)\1", false).is_err());
        assert!(Regex::new("a**", false).is_err());
    }
}
