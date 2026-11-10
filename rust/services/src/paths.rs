//! Paths as Python 3.12 takes them (os.path and pathlib), for the services that compare the
//! folders of games and programs: a path is a text, and what is "the same folder" follows
//! ntpath on Windows and posixpath elsewhere.
//!
//! Every function takes the flavour (`windows`), so both are the same on every system; `HERE`
//! is this system's.

/// Whether this system's paths are Windows'.
pub const HERE: bool = cfg!(windows);

fn text(chars: &[char]) -> String {
    chars.iter().collect()
}

fn is_sep(windows: bool, ch: char) -> bool {
    ch == '/' || (windows && ch == '\\')
}

/// os.sep.
pub fn sep(windows: bool) -> char {
    if windows {
        '\\'
    } else {
        '/'
    }
}

/// os.path.splitroot: the drive, the root and the rest.
pub fn splitroot(windows: bool, path: &str) -> (String, String, String) {
    let p: Vec<char> = path.chars().collect();
    if !windows {
        if p.first() != Some(&'/') {
            return (String::new(), String::new(), path.to_string());
        }
        if p.get(1) != Some(&'/') || p.get(2) == Some(&'/') {
            return (String::new(), "/".into(), text(&p[1..]));
        }
        return (String::new(), text(&p[..2]), text(&p[2..]));
    }
    let normal: Vec<char> = (p.iter())
        .map(|ch| if *ch == '/' { '\\' } else { *ch })
        .collect();
    let find = |from: usize| (from..normal.len()).find(|at| normal[*at] == '\\');
    let none = || (path.to_string(), String::new(), String::new());
    if normal.first() == Some(&'\\') {
        if normal.get(1) != Some(&'\\') {
            // a relative path with a root, e.g. \Windows
            return (String::new(), text(&p[..1]), text(&p[1..]));
        }
        // UNC drives (\\server\share, \\?\UNC\server\share) and device drives (\\.\device)
        let head: String = normal.iter().take(8).collect();
        let start = if lp_py::upper(&head) == "\\\\?\\UNC\\" {
            8
        } else {
            2
        };
        let Some(index) = find(start) else {
            return none();
        };
        let Some(index2) = find(index + 1) else {
            return none();
        };
        return (
            text(&p[..index2]),
            text(&p[index2..index2 + 1]),
            text(&p[index2 + 1..]),
        );
    }
    if normal.get(1) == Some(&':') {
        if normal.get(2) == Some(&'\\') {
            return (text(&p[..2]), text(&p[2..3]), text(&p[3..]));
        }
        return (text(&p[..2]), String::new(), text(&p[2..]));
    }
    (String::new(), String::new(), path.to_string())
}

/// os.path.normpath.
pub fn normpath(windows: bool, path: &str) -> String {
    if !windows {
        if path.is_empty() {
            return ".".into();
        }
        let slashes = match path {
            path if path.starts_with("//") && !path.starts_with("///") => 2,
            path if path.starts_with('/') => 1,
            _ => 0,
        };
        let mut parts: Vec<&str> = Vec::new();
        for part in path.split('/') {
            if part.is_empty() || part == "." {
                continue;
            }
            let back = part == "..";
            if !back || (slashes == 0 && parts.is_empty()) || parts.last() == Some(&"..") {
                parts.push(part);
            } else {
                parts.pop();
            }
        }
        let joined = format!("{}{}", "/".repeat(slashes), parts.join("/"));
        return if joined.is_empty() {
            ".".into()
        } else {
            joined
        };
    }
    let path = path.replace('/', "\\");
    let (drive, root, rest) = splitroot(true, &path);
    let mut parts: Vec<&str> = rest.split('\\').collect();
    let mut at = 0;
    while at < parts.len() {
        if parts[at].is_empty() || parts[at] == "." {
            parts.remove(at);
        } else if parts[at] == ".." {
            if at > 0 && parts[at - 1] != ".." {
                parts.drain(at - 1..=at);
                at -= 1;
            } else if at == 0 && !root.is_empty() {
                parts.remove(at);
            } else {
                at += 1;
            }
        } else {
            at += 1;
        }
    }
    if drive.is_empty() && root.is_empty() && parts.is_empty() {
        parts.push(".");
    }
    format!("{drive}{root}{}", parts.join("\\"))
}

/// os.path.normcase: on Windows its slashes backslashes and its letters small.
pub fn normcase(windows: bool, path: &str) -> String {
    match windows {
        // (this system's own lower case where it is Windows: ntpath asks the system there)
        true if HERE => lp_py::fnmatch::normcase(path),
        true => lp_py::lower(&path.replace('/', "\\")),
        false => path.to_string(),
    }
}

/// `normcase(normpath(path))`: one text for one folder.
pub fn folded(windows: bool, path: &str) -> String {
    normcase(windows, &normpath(windows, path))
}

/// os.path.join of two paths.
pub fn join(windows: bool, path: &str, more: &str) -> String {
    if !windows {
        if more.starts_with('/') {
            return more.to_string();
        }
        if path.is_empty() || path.ends_with('/') {
            return format!("{path}{more}");
        }
        return format!("{path}/{more}");
    }
    let (mut drive, mut root, mut rest) = splitroot(true, path);
    let (more_drive, more_root, more_rest) = splitroot(true, more);
    if !more_root.is_empty() {
        // the second path is absolute
        if !more_drive.is_empty() || drive.is_empty() {
            drive = more_drive;
        }
        (root, rest) = (more_root, more_rest);
    } else if !more_drive.is_empty()
        && more_drive != drive
        && lp_py::lower(&more_drive) != lp_py::lower(&drive)
    {
        // another drive: the first path counts for nothing
        (drive, root, rest) = (more_drive, more_root, more_rest);
    } else {
        if !more_drive.is_empty() && more_drive != drive {
            drive = more_drive; // (the same drive in another case)
        }
        if rest.chars().last().is_some_and(|last| !is_sep(true, last)) {
            rest.push('\\');
        }
        rest.push_str(&more_rest);
    }
    // (a separator between a UNC drive and a path that is not absolute)
    let open = (drive.chars().last()).is_some_and(|last| last != ':' && !is_sep(true, last));
    if !rest.is_empty() && root.is_empty() && open {
        return format!("{drive}\\{rest}");
    }
    format!("{drive}{root}{rest}")
}

/// A path as pathlib holds it: its drive, its root and its parts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Path {
    pub windows: bool,
    pub drive: String,
    pub root: String,
    pub tail: Vec<String>,
}

impl Path {
    /// `Path(text)`.
    pub fn new(windows: bool, path: &str) -> Path {
        let mut made = Path {
            windows,
            drive: String::new(),
            root: String::new(),
            tail: Vec::new(),
        };
        if path.is_empty() {
            return made;
        }
        let path = match windows {
            true => path.replace('/', "\\"),
            false => path.to_string(),
        };
        let own = sep(windows);
        let (drive, mut root, rest) = splitroot(windows, &path);
        if root.is_empty() && drive.starts_with(own) && !drive.ends_with(own) {
            let parts: Vec<&str> = drive.split(own).collect();
            // (//server/share, and //?/unc/server/share)
            let server = parts.len() == 4 && !"?.".contains(parts[2]);
            if server || parts.len() == 6 {
                root = own.to_string();
            }
        }
        made.tail = (rest.split(own))
            .filter(|part| !part.is_empty() && *part != ".")
            .map(str::to_string)
            .collect();
        (made.drive, made.root) = (drive, root);
        made
    }

    /// `str(path)`.
    pub fn text(&self) -> String {
        let own = sep(self.windows).to_string();
        let mut joined = format!("{}{}{}", self.drive, self.root, self.tail.join(&own));
        // (a first part that reads as a drive, of a path without one)
        let first = self
            .tail
            .first()
            .map(|first| splitroot(self.windows, first).0);
        if self.anchor().is_empty() && first.is_some_and(|drive| !drive.is_empty()) {
            joined = format!(".{own}{joined}");
        }
        if joined.is_empty() {
            ".".into()
        } else {
            joined
        }
    }

    /// `path / more`.
    pub fn join(&self, more: &str) -> Path {
        Path::new(self.windows, &join(self.windows, &self.text(), more))
    }

    /// `path.name`.
    pub fn name(&self) -> &str {
        self.tail.last().map_or("", String::as_str)
    }

    /// `path.suffix`.
    pub fn suffix(&self) -> &str {
        let name = self.name();
        match name.rfind('.') {
            Some(at) if at > 0 && at + 1 < name.len() => &name[at..],
            _ => "",
        }
    }

    /// `path.anchor`.
    pub fn anchor(&self) -> String {
        format!("{}{}", self.drive, self.root)
    }

    /// `path.expanduser()`: a first part `~` or `~name` as that user's home folder; none where
    /// Python raises (the folder cannot be told).
    pub fn expanduser(&self, homes: &Homes) -> Option<Path> {
        let first = self.tail.first().filter(|first| first.starts_with('~'));
        let Some(first) = first.filter(|_| self.anchor().is_empty()) else {
            return Some(self.clone());
        };
        let home = expanduser(self.windows, first, homes);
        if home.starts_with('~') {
            return None;
        }
        let mut found = Path::new(self.windows, &home);
        found.tail.extend(self.tail[1..].iter().cloned());
        Some(found)
    }

    /// `path.is_absolute()`.
    pub fn is_absolute(&self) -> bool {
        match self.windows {
            true => !self.drive.is_empty() && !self.root.is_empty(),
            false => !self.root.is_empty(),
        }
    }
}

/// `text.strip().strip('"')`.
pub fn bare(text: &str) -> &str {
    lp_py::strip(text).trim_matches('"')
}

/// What a path's `~` and `$NAME` stand for: the environment, and another user's home folder
/// (None: the user of this process) where the system can tell it.
pub struct Homes<'a> {
    pub env: &'a dyn Fn(&str) -> Option<String>,
    pub user: &'a dyn Fn(Option<&str>) -> Option<String>,
}

/// ntpath.split: the folder and the name in it.
fn split_windows(path: &str) -> (String, String) {
    let (drive, root, rest) = splitroot(true, path);
    let chars: Vec<char> = rest.chars().collect();
    let mut at = chars.len();
    while at > 0 && !is_sep(true, chars[at - 1]) {
        at -= 1;
    }
    let head = text(&chars[..at]);
    let head = head.trim_end_matches(['/', '\\']);
    (format!("{drive}{root}{head}"), text(&chars[at..]))
}

/// os.path.expanduser: a `~` at the start as the user's folder, `~name` as that user's.
pub fn expanduser(windows: bool, path: &str, homes: &Homes) -> String {
    if !path.starts_with('~') {
        return path.to_string();
    }
    let chars: Vec<char> = path.chars().collect();
    let end = (1..chars.len())
        .find(|at| is_sep(windows, chars[*at]))
        .unwrap_or(chars.len());
    let (name, rest) = (text(&chars[1..end]), text(&chars[end..]));
    if !windows {
        let home = match (name.is_empty(), (homes.env)("HOME")) {
            (true, Some(home)) => Some(home),
            (true, None) => (homes.user)(None),
            (false, _) => (homes.user)(Some(&name)),
        };
        let Some(home) = home else {
            return path.to_string();
        };
        let joined = format!("{}{rest}", home.trim_end_matches('/'));
        return if joined.is_empty() {
            "/".into()
        } else {
            joined
        };
    }
    let mut home = match ((homes.env)("USERPROFILE"), (homes.env)("HOMEPATH")) {
        (Some(home), _) => home,
        (None, Some(folder)) => join(true, &(homes.env)("HOMEDRIVE").unwrap_or_default(), &folder),
        (None, None) => return path.to_string(),
    };
    let own = (homes.env)("USERNAME");
    if !name.is_empty() && own.as_deref() != Some(&name) {
        // (another user's folder lies beside this one's, where this one's has the user's name)
        let (folder, last) = split_windows(&home);
        if own.as_deref() != Some(&last) {
            return path.to_string();
        }
        home = join(true, &folder, &name);
    }
    format!("{home}{rest}")
}

/// os.path.expandvars: `$NAME` and `${NAME}` (on Windows `%NAME%` too, and nothing within
/// single quotes) as the environment has them; those it has not stay as they are.
pub fn expandvars(windows: bool, path: &str, env: &dyn Fn(&str) -> Option<String>) -> String {
    let chars: Vec<char> = path.chars().collect();
    let find = |from: usize, what: char| (from..chars.len()).find(|at| chars[*at] == what);
    let mut out = String::new();
    let mut at = 0;
    if !windows {
        let word = |ch: char| ch.is_ascii_alphanumeric() || ch == '_';
        while at < chars.len() {
            let mut name = None;
            if chars[at] == '$' {
                if chars.get(at + 1) == Some(&'{') {
                    let Some(end) = find(at + 2, '}') else {
                        // (a name that is not closed: the rest stays as it is)
                        out.push_str(&text(&chars[at..]));
                        break;
                    };
                    name = Some((at + 2, end, end + 1));
                } else {
                    let end = (at + 1..chars.len()).find(|n| !word(chars[*n]));
                    let end = end.unwrap_or(chars.len());
                    name = (end > at + 1).then_some((at + 1, end, end));
                }
            }
            match name {
                Some((from, to, after)) => {
                    match env(&text(&chars[from..to])) {
                        Some(value) => out.push_str(&value),
                        None => out.push_str(&text(&chars[at..after])),
                    }
                    at = after;
                }
                None => {
                    out.push(chars[at]);
                    at += 1;
                }
            }
        }
        return out;
    }
    let word = |ch: char| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-';
    while at < chars.len() {
        let ch = chars[at];
        let next = chars.get(at + 1).copied();
        match ch {
            '\'' => match find(at + 1, '\'') {
                // (nothing is taken for a name within single quotes)
                Some(end) => {
                    out.push_str(&text(&chars[at..=end]));
                    at = end;
                }
                None => {
                    out.push_str(&text(&chars[at..]));
                    at = chars.len();
                }
            },
            '%' | '$' if next == Some(ch) => {
                out.push(ch);
                at += 1;
            }
            '%' => match find(at + 1, '%') {
                Some(end) => {
                    let name = text(&chars[at + 1..end]);
                    out.push_str(&env(&name).unwrap_or(format!("%{name}%")));
                    at = end;
                }
                None => {
                    out.push_str(&text(&chars[at..]));
                    at = chars.len();
                }
            },
            '$' if next == Some('{') => match find(at + 2, '}') {
                Some(end) => {
                    let name = text(&chars[at + 2..end]);
                    out.push_str(&env(&name).unwrap_or(format!("${{{name}}}")));
                    at = end;
                }
                None => {
                    out.push_str(&text(&chars[at..]));
                    at = chars.len();
                }
            },
            '$' => {
                let end = (at + 1..chars.len()).find(|n| !word(chars[*n]));
                let end = end.unwrap_or(chars.len());
                let name = text(&chars[at + 1..end]);
                out.push_str(&env(&name).unwrap_or(format!("${name}")));
                at = end - 1;
            }
            other => out.push(other),
        }
        at += 1;
    }
    out
}

/// Another user's home folder on this computer (None: this process's user), where the
/// system keeps a list of them.
pub fn user_home(name: Option<&str>) -> Option<String> {
    #[cfg(unix)]
    {
        let mut entry: libc::passwd = unsafe { std::mem::zeroed() };
        let mut found: *mut libc::passwd = std::ptr::null_mut();
        let mut room = vec![0 as libc::c_char; 16384];
        // SAFETY: the entry's texts are written into `room`, which outlives their use here;
        // a name is passed as a text ending with a zero.
        unsafe {
            let code = match name {
                Some(name) => {
                    let name = std::ffi::CString::new(name).ok()?;
                    libc::getpwnam_r(
                        name.as_ptr(),
                        &mut entry,
                        room.as_mut_ptr(),
                        room.len(),
                        &mut found,
                    )
                }
                None => libc::getpwuid_r(
                    libc::getuid(),
                    &mut entry,
                    room.as_mut_ptr(),
                    room.len(),
                    &mut found,
                ),
            };
            if code != 0 || found.is_null() || entry.pw_dir.is_null() {
                return None;
            }
            Some(
                std::ffi::CStr::from_ptr(entry.pw_dir)
                    .to_string_lossy()
                    .into_owned(),
            )
        }
    }
    #[cfg(not(unix))]
    {
        let _ = name;
        None
    }
}

/// os.path.basename.
pub fn basename(windows: bool, path: &str) -> String {
    match windows {
        true => split_windows(path).1,
        false => path.rsplit('/').next().unwrap_or("").to_string(),
    }
}
