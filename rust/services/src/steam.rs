//! Where Steam is, which games it has installed, and their logos (services/steam.py). Only
//! reads.
//!
//! Steam keeps its libraries in `steamapps/libraryfolders.vdf` and one
//! `appmanifest_<id>.acf` per installed game, and the pictures of every game it knows in
//! `appcache/librarycache`. Libre Panel needs the games to tell that a program that runs is
//! a game (whatever its name), and the logo to show at the start of game mode. Nothing here
//! talks to Steam or the network.

use std::collections::HashSet;

use indexmap::IndexMap;
use lp_pixels::{Image, Mode};
use lp_py::{Dict, Value};

use crate::paths::{self, Path, HERE};
use crate::registry::{Entry, Hive, Registry, View};

/// how often the installed games are read again: a new one is known by itself
pub const REFRESH_S: f64 = 120.0;
/// Tools that lie in a library like games (compared with the start of the folder's or the
/// game's name).
const NOT_GAMES: [&str; 7] = [
    "steamworks shared",
    "steamworks common redistributables",
    "steamvr",
    "proton",
    "steam linux runtime",
    "steamlinuxruntime",
    "wallpaper_engine",
];
const WINDOWS_STEAM_KEYS: [(Hive, &str, &str); 3] = [
    (Hive::CurrentUser, r"Software\Valve\Steam", "SteamPath"),
    (
        Hive::LocalMachine,
        r"SOFTWARE\WOW6432Node\Valve\Steam",
        "InstallPath",
    ),
    (Hive::LocalMachine, r"SOFTWARE\Valve\Steam", "InstallPath"),
];
const WINDOWS_STEAM_FOLDERS: [&str; 2] = ["ProgramFiles(x86)", "ProgramFiles"];
/// Under the home folder: the usual place, the link Steam makes, the Flatpak and the Snap
const LINUX_STEAM_FOLDERS: [&str; 5] = [
    ".local/share/Steam",
    ".steam/steam",
    ".steam/root",
    ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    "snap/steam/common/.local/share/Steam",
];

/// The environment: what it has for a name.
pub type Env = Box<dyn Fn(&str) -> Option<String> + Send + Sync>;

/// What the services ask of the computer they run on to find a launcher's files: the system,
/// the home folder, the environment and the registry.
pub struct Places {
    /// whether the system is Windows (sys.platform is "win32")
    pub windows: bool,
    pub home: String,
    pub env: Env,
    pub registry: Option<Box<dyn Registry>>,
}

impl Places {
    /// This computer's.
    pub fn here() -> Places {
        let home = if cfg!(windows) {
            std::env::var("USERPROFILE")
        } else {
            std::env::var("HOME")
        };
        Places {
            windows: cfg!(windows),
            home: home.unwrap_or_default(),
            env: Box::new(|name| std::env::var(name).ok()),
            registry: crate::registry::system(),
        }
    }

    /// `os.environ.get(name)` where it is set and not empty.
    pub fn set(&self, name: &str) -> Option<String> {
        (self.env)(name).filter(|value| !value.is_empty())
    }
}

pub(crate) fn is_dir(path: &Path) -> bool {
    std::path::Path::new(&path.text()).is_dir()
}

pub(crate) fn is_file(path: &Path) -> bool {
    std::path::Path::new(&path.text()).is_file()
}

/// The names in a folder, in the order the system gives them; none where it cannot be read.
pub(crate) fn names_in(folder: &Path) -> Option<Vec<String>> {
    let entries = std::fs::read_dir(folder.text()).ok()?;
    let names = entries.filter_map(|entry| entry.ok()?.file_name().into_string().ok());
    Some(names.collect())
}

/// Names as pathlib sorts the paths of one folder: without regard to case on Windows.
pub(crate) fn sorted(mut names: Vec<String>) -> Vec<String> {
    if HERE {
        names.sort_by_cached_key(|name| lp_py::lower(name));
    } else {
        names.sort();
    }
    names
}

/// A text file as Python reads one (`errors="replace"`, any line end a "\n").
pub(crate) fn read_text(path: &Path) -> Option<String> {
    let bytes = std::fs::read(path.text()).ok()?;
    let text = String::from_utf8_lossy(&bytes);
    Some(text.replace("\r\n", "\n").replace('\r', "\n"))
}

enum Item {
    Text(String),
    Table(usize),
}

/// parse_vdf: Steam's text format as nested dicts, keys in lower case (Steam does not care
/// about their case). Text that is not quite right gives what could be read.
pub fn parse_vdf(text: &str) -> Dict {
    let chars: Vec<char> = text.chars().collect();
    // (a table that was written over stays one that is written to, as Python's stays)
    let mut tables: Vec<IndexMap<String, Item>> = vec![IndexMap::new()];
    let mut stack = vec![0usize];
    let mut key: Option<String> = None;
    let mut at = 0;
    while at < chars.len() {
        match chars[at] {
            '"' => {
                // a text in quotes: any letter, or a backslash and the letter after it
                let (mut end, mut value, mut closed) = (at + 1, String::new(), false);
                while end < chars.len() {
                    match chars[end] {
                        '"' => {
                            closed = true;
                            break;
                        }
                        '\\' => match chars.get(end + 1) {
                            Some(next) if *next != '\n' => {
                                value.push(match next {
                                    'n' => '\n',
                                    't' => '\t',
                                    other => *other,
                                });
                                end += 2;
                            }
                            _ => break,
                        },
                        other => {
                            value.push(other);
                            end += 1;
                        }
                    }
                }
                if !closed {
                    at += 1;
                    continue;
                }
                at = end + 1;
                let top = stack[stack.len() - 1];
                match key.take() {
                    None => key = Some(lp_py::lower(&value)),
                    Some(key) => {
                        tables[top].insert(key, Item::Text(value));
                    }
                }
            }
            '{' => {
                at += 1;
                if let Some(key) = key.take() {
                    let top = stack[stack.len() - 1];
                    tables.push(IndexMap::new());
                    let child = tables.len() - 1;
                    tables[top].insert(key, Item::Table(child));
                    stack.push(child);
                }
            }
            '}' => {
                at += 1;
                if stack.len() > 1 {
                    stack.pop();
                    key = None;
                }
            }
            '/' if chars.get(at + 1) == Some(&'/') => {
                // a comment, to the end of its line
                while at < chars.len() && chars[at] != '\n' {
                    at += 1;
                }
            }
            _ => at += 1,
        }
    }
    fn made(tables: &[IndexMap<String, Item>], table: usize) -> Dict {
        let pairs = tables[table].iter().map(|(key, item)| {
            let value = match item {
                Item::Text(text) => Value::Str(text.clone()),
                Item::Table(child) => Value::Dict(made(tables, *child)),
            };
            (key.clone(), value)
        });
        pairs.collect()
    }
    made(&tables, 0)
}

pub fn read_vdf(path: &Path) -> Dict {
    read_text(path).map_or_else(Dict::default, |text| parse_vdf(&text))
}

/// find_root: Steam's folder: the one in `given`, else where the system or the usual places
/// say it is.
pub fn find_root(given: &str, places: &Places) -> Option<Path> {
    let steam = |path: &Path| is_dir(&path.join("steamapps"));
    if !given.is_empty() {
        return Some(Path::new(HERE, given)).filter(steam);
    }
    let mut candidates = Vec::new();
    if places.windows {
        for (hive, key, name) in WINDOWS_STEAM_KEYS {
            let found = (places.registry.as_ref())
                .and_then(|registry| registry.value(hive, View::Own, key, name));
            if let Some(Entry::Text(path)) = found {
                candidates.push(Path::new(HERE, &path));
            }
        }
        for name in WINDOWS_STEAM_FOLDERS {
            if let Some(folder) = places.set(name) {
                candidates.push(Path::new(HERE, &folder).join("Steam"));
            }
        }
    } else {
        let home = Path::new(HERE, &places.home);
        candidates.extend(LINUX_STEAM_FOLDERS.map(|folder| home.join(folder)));
    }
    candidates.into_iter().find(steam)
}

fn same(a: &Path, b: &Path) -> bool {
    paths::folded(HERE, &a.text()) == paths::folded(HERE, &b.text())
}

/// libraries: every library folder: Steam's own and those `libraryfolders.vdf` names.
pub fn libraries(root: &Path) -> Vec<Path> {
    let mut found = vec![root.clone()];
    let listed = read_vdf(&root.join("steamapps").join("libraryfolders.vdf"));
    let Some(Value::Dict(listed)) = listed.get("libraryfolders") else {
        return found;
    };
    for (number, entry) in listed {
        // newer Steam: a table with a "path"; older: the path itself, numbered
        let numbered = !number.is_empty() && number.chars().all(lp_py::isdigit);
        let path = match entry {
            Value::Dict(entry) => entry.get("path").and_then(Value::as_str),
            Value::Str(path) if numbered => Some(path.as_str()),
            _ => None,
        };
        let Some(library) = path.filter(|path| !path.is_empty()) else {
            continue;
        };
        let library = Path::new(HERE, library);
        if library.is_absolute()
            && is_dir(&library)
            && found.iter().all(|other| !same(&library, other))
        {
            found.push(library);
        }
    }
    found
}

/// tidy: a game's name without trademark signs and double spaces: on a panel it stands
/// large.
pub fn tidy(name: &str) -> String {
    let plain: String = name.chars().filter(|c| !"™®©".contains(*c)).collect();
    let words: Vec<&str> = plain
        .split(lp_py::isspace)
        .filter(|word| !word.is_empty())
        .collect();
    words.join(" ")
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SteamGame {
    pub appid: String,
    pub name: String,
    /// `<library>/steamapps/common/<installdir>`
    pub folder: Path,
}

/// installed_games: the games installed in one library, from their manifests.
pub fn installed_games(library: &Path) -> Vec<SteamGame> {
    let steamapps = library.join("steamapps");
    let fits = |name: &String| {
        let name = paths::normcase(HERE, name);
        name.len() >= 16 && name.starts_with("appmanifest_") && name.ends_with(".acf")
    };
    let manifests: Vec<String> = (names_in(&steamapps).unwrap_or_default().into_iter())
        .filter(fits)
        .collect();
    let mut games = Vec::new();
    for manifest in sorted(manifests) {
        let state = read_vdf(&steamapps.join(&manifest));
        let Some(Value::Dict(state)) = state.get("appstate") else {
            continue;
        };
        let text = |key: &str| (state.get(key).and_then(Value::as_str)).filter(|t| !t.is_empty());
        let (Some(appid), Some(installdir)) = (text("appid"), text("installdir")) else {
            continue;
        };
        games.push(SteamGame {
            appid: appid.to_string(),
            name: tidy(text("name").unwrap_or(installdir)),
            folder: steamapps.join("common").join(installdir),
        });
    }
    games
}

/// The names (case does not matter) of games and tools that are never "the game".
pub(crate) fn ignored(ignore: &[Value]) -> HashSet<String> {
    (ignore.iter())
        .map(|name| lp_py::lower(lp_py::strip(&name.str())))
        .filter(|name| !name.is_empty())
        .collect()
}

type Clock = Box<dyn Fn() -> f64 + Send + Sync>;

/// The games installed through Steam, read again every `REFRESH_S`.
///
/// `given` is Steam's folder when it is not where Libre Panel looks; `ignore` names games
/// and tools that are never "the game".
pub struct Library {
    given: String,
    ignore: HashSet<String>,
    clock: Clock,
    places: Places,
    root: Option<Path>,
    read_at: f64,
    warned: bool,
    /// by folder
    games: IndexMap<String, SteamGame>,
    commons: Vec<String>,
    /// lines for the log
    pub notes: Vec<String>,
}

impl Library {
    pub fn new(given: &str, ignore: &[Value], clock: Clock, places: Places) -> Library {
        Library {
            given: given.to_string(),
            ignore: ignored(ignore),
            clock,
            places,
            root: None,
            read_at: -REFRESH_S,
            warned: false,
            games: IndexMap::new(),
            commons: Vec::new(),
            notes: Vec::new(),
        }
    }

    /// A library that has nothing and looks nowhere (a place holder).
    pub(crate) fn none() -> Library {
        let places = Places {
            windows: false,
            home: String::new(),
            env: Box::new(|_| None),
            registry: None,
        };
        let mut none = Library::new("", &[], Box::new(|| 0.0), places);
        none.read_at = f64::INFINITY;
        none
    }

    pub fn games(&mut self) -> Vec<SteamGame> {
        self.refresh();
        self.games.values().cloned().collect()
    }

    /// Steam's folder, as last found.
    pub fn root(&mut self) -> Option<&Path> {
        self.refresh();
        self.root.as_ref()
    }

    /// match: the game a program at `path` (or working in that folder) belongs to.
    pub fn match_path(&mut self, path: &str) -> Option<SteamGame> {
        self.refresh();
        if path.is_empty() {
            return None;
        }
        let own = paths::sep(HERE);
        let place = paths::folded(HERE, path);
        for common in &self.commons {
            let Some(inside) = place.strip_prefix(&format!("{common}{own}")) else {
                continue;
            };
            let installdir = inside.split(own).next().unwrap_or("");
            return self
                .games
                .get(&format!("{common}{own}{installdir}"))
                .cloned();
        }
        None
    }

    fn refresh(&mut self) {
        let now = (self.clock)();
        if now - self.read_at < REFRESH_S {
            return;
        }
        self.read_at = now;
        self.root = find_root(&self.given, &self.places);
        if self.root.is_none() && !self.given.is_empty() && !self.warned {
            self.warned = true;
            self.notes
                .push(format!("Steam: {} has no steamapps folder", self.given));
        }
        let (mut games, mut commons) = (IndexMap::new(), Vec::new());
        for library in self.root.as_ref().map(libraries).unwrap_or_default() {
            let common = library.join("steamapps").join("common");
            commons.push(paths::folded(HERE, &common.text()));
            for game in installed_games(&library) {
                if self.is_game(&game) {
                    games.insert(paths::folded(HERE, &game.folder.text()), game);
                }
            }
        }
        (self.games, self.commons) = (games, commons);
    }

    fn is_game(&self, game: &SteamGame) -> bool {
        let names = [lp_py::lower(game.folder.name()), lp_py::lower(&game.name)];
        !names.iter().any(|name| {
            self.ignore.contains(name) || NOT_GAMES.iter().any(|tool| name.starts_with(tool))
        })
    }

    /// logo: the logo of a game from Steam's cache, cut out and not scaled (the header
    /// picture when there is no logo), or None.
    pub fn logo(&mut self, appid: &str) -> Option<Image> {
        self.refresh();
        load_logo(self.root.as_ref()?, appid)
    }
}

/// cache_files: cached pictures of a game, newest first. Steam keeps them in a folder per
/// game and version (a new season's logo is a new file) or, older, as `<appid>_<name>`.
pub fn cache_files(root: &Path, appid: &str, names: &[&str]) -> Vec<Path> {
    if appid.is_empty() || !appid.chars().all(lp_py::isdigit) {
        return Vec::new();
    }
    let cache = root.join("appcache").join("librarycache");
    let own = cache.join(appid);
    let mut found = Vec::new();
    let there = |path: &Path| std::path::Path::new(&path.text()).exists();
    for name in names {
        for version in names_in(&own).unwrap_or_default() {
            let folder = own.join(&version);
            if is_dir(&folder) {
                found.push(folder.join(name));
            }
        }
        found.push(own.join(name));
        found.push(cache.join(&format!("{appid}_{name}")));
    }
    let mut dated: Vec<(std::time::SystemTime, Path)> = (found.into_iter())
        .filter(|path| there(path) && is_file(path))
        .filter_map(|path| {
            let changed = std::fs::metadata(path.text()).ok()?.modified().ok()?;
            Some((changed, path))
        })
        .collect();
    dated.sort_by_key(|(changed, _)| std::cmp::Reverse(*changed));
    dated.into_iter().map(|(_, path)| path).collect()
}

/// load_logo: the newest logo (a PNG with a transparent surrounding) cut to what shows, or
/// the newest header picture as it is. Not scaled: a screen scales it where it needs it.
pub fn load_logo(root: &Path, appid: &str) -> Option<Image> {
    let kinds: [(&[&str], bool); 2] = [
        (&["logo.png"], true),
        (&["header.jpg", "header.png"], false),
    ];
    for (names, cut) in kinds {
        let Some(path) = cache_files(root, appid, names).into_iter().next() else {
            continue;
        };
        let read = std::fs::read(path.text()).ok();
        let Some(logo) = read.and_then(|data| lp_decode::open(&data, Mode::RGBA).ok()) else {
            continue;
        };
        return Some(match logo.getbbox(true).filter(|_| cut) {
            Some((x0, y0, x1, y1)) => logo.crop(x0 as i64, y0 as i64, x1 as i64, y1 as i64),
            None => logo,
        });
    }
    None
}
