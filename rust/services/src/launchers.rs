//! The games of other launchers than Steam, and the games in folders you name
//! (services/launchers.py). Only reads.
//!
//! Every launcher keeps a list of what it has installed. On Windows: Epic its manifests (in
//! ProgramData), GOG, EA app, Battle.net and Rockstar the list of installed programs
//! (Settings → Apps), Ubisoft Connect a key of its own in the registry, Amazon Games a
//! database, and the Xbox app the folder XboxGames on its drives. On Linux (and Windows):
//! Heroic (Epic, GOG and Amazon) its lists, and on Linux Lutris its database. A program that
//! runs from such a game's folder is that game, as with Steam (steam.rs). Nothing here starts
//! a launcher or talks to the network.
//!
//! The formats are those that Playnite's library extensions (MIT), Heroic and Lutris read.

use std::collections::HashSet;
use std::sync::OnceLock;

use indexmap::IndexMap;
use lp_py::regex::Regex;
use lp_py::{Dict, Value};

use crate::paths::{self, Path, HERE};
use crate::registry::{Hive, Registry, View};
use crate::steam::{ignored, is_dir, is_file, names_in, sorted, tidy, Places};

/// how often the installed games are read again: a new one is known by itself
pub const REFRESH_S: f64 = 120.0;
const UNINSTALL: &str = r"Software\Microsoft\Windows\CurrentVersion\Uninstall";
const UBISOFT: &str = r"SOFTWARE\Ubisoft\Launcher\Installs";
/// The games of the Rockstar Games Launcher (its title ids): its own entries are no games
const ROCKSTAR_TITLES: [&str; 15] = [
    "gta5",
    "gta5_gen9",
    "rdr",
    "rdr2",
    "lanoire",
    "mp3",
    "lanoirevr",
    "gtasa",
    "gta3",
    "gtavc",
    "bully",
    "gta4",
    "gta3unreal",
    "gtavcunreal",
    "gtasaunreal",
];
/// What the EA app installs beside the games, by its name (in lower case)
const EA_TOOLS: [&str; 5] = ["ea app", "ea desktop", "origin", "anticheat", "anti-cheat"];
/// Folders too wide to be a game's: a program anywhere in them would be "the game"
const TOO_WIDE: [&str; 9] = [
    "program files",
    "program files (x86)",
    "programdata",
    "windows",
    "users",
    "home",
    "usr",
    "opt",
    "games",
];
/// Files that make a folder a game's own (else its folders are the games)
const PROGRAMS: [&str; 5] = [".exe", ".x86_64", ".x86", ".appimage", ".sh"];

/// The first group of a pattern's first match in a text.
fn found(
    pattern: &'static str,
    kept: &OnceLock<Option<Regex>>,
    text: &str,
    start: bool,
) -> Option<String> {
    let pattern = kept.get_or_init(|| Regex::new(pattern, true).ok());
    let chars: Vec<char> = text.chars().collect();
    let pattern = pattern.as_ref()?;
    let found = match start {
        true => pattern.matches(&chars),
        false => pattern.search(&chars),
    };
    found?.group(&chars, 1)
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct InstalledGame {
    /// "epic", "gog", "ubisoft", "ea", "battlenet", "rockstar", "amazon", "xbox", "lutris" or
    /// "folder" (one you named)
    pub source: String,
    /// the launcher's id for it (or its folder): one key, one game
    pub key: String,
    pub name: String,
    pub folder: Path,
}

/// A program in Windows' list of installed programs.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    /// the name of its key in the registry
    pub key: String,
    /// DisplayName
    pub name: String,
    pub publisher: String,
    /// InstallLocation
    pub location: String,
    /// UninstallString
    pub uninstall: String,
}

/// A list that Python could not read as it is written: it stops the launchers' games.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Odd;

type Games = Result<Vec<InstalledGame>, Odd>;

/// _json: a file of JSON (UTF-8, with or without its mark), none where it cannot be read.
fn json(path: &Path) -> Option<Value> {
    let text = String::from_utf8(std::fs::read(path.text()).ok()?).ok()?;
    lp_py::json::loads(text.strip_prefix('\u{feff}').unwrap_or(&text)).ok()
}

/// _rows: a query on a launcher's database, opened to read only (it may be in use).
fn rows(database: &Path, query: &str) -> Vec<Vec<Value>> {
    if !is_file(database) {
        return Vec::new();
    }
    let read = || -> rusqlite::Result<Vec<Vec<Value>>> {
        use rusqlite::types::ValueRef;
        let flags = rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY;
        let connection = rusqlite::Connection::open_with_flags(database.text(), flags)?;
        connection.busy_timeout(std::time::Duration::from_secs(1))?;
        let mut statement = connection.prepare(query)?;
        let width = statement.column_count();
        let mut found = Vec::new();
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            let mut cells = Vec::new();
            for column in 0..width {
                cells.push(match row.get_ref(column)? {
                    ValueRef::Integer(number) => Value::int(number),
                    ValueRef::Real(number) => Value::Float(number),
                    ValueRef::Text(text) => Value::Str(
                        (std::str::from_utf8(text).map_err(rusqlite::Error::Utf8Error)?)
                            .to_string(),
                    ),
                    ValueRef::Null | ValueRef::Blob(_) => Value::None,
                });
            }
            found.push(cells);
        }
        Ok(found)
    };
    read().unwrap_or_default()
}

/// `str(value)` of what is there and true, None for the rest.
fn truthy(value: Option<&Value>) -> Option<String> {
    value.filter(|value| value.truthy()).map(Value::str)
}

fn game(
    source: &str,
    key: Option<String>,
    name: Option<String>,
    folder: Option<String>,
) -> Option<InstalledGame> {
    let place = Path::new(HERE, paths::bare(&folder?));
    let title = tidy(&name.unwrap_or_default());
    Some(InstalledGame {
        source: source.to_string(),
        key: lp_py::lower(&key.unwrap_or_else(|| place.text())),
        name: if title.is_empty() {
            place.name().to_string()
        } else {
            title
        },
        folder: place,
    })
}

fn some(text: &str) -> Option<String> {
    (!text.is_empty()).then(|| text.to_string())
}

// -- Windows: the list of installed programs and the registry ----------------------------------

/// _views: the places the lists are in: the machine's in its 64- and 32-bit views, the
/// user's.
const VIEWS: [(Hive, View); 3] = [
    (Hive::LocalMachine, View::Wide),
    (Hive::LocalMachine, View::Narrow),
    (Hive::CurrentUser, View::Own),
];

/// uninstall_entries: every program in Windows' list of installed programs, once.
pub fn uninstall_entries(registry: &dyn Registry) -> Vec<Entry> {
    let mut entries: IndexMap<(String, String), Entry> = IndexMap::new();
    for (hive, view) in VIEWS {
        for name in registry.subkeys(hive, view, UNINSTALL).unwrap_or_default() {
            let item = format!("{UNINSTALL}\\{name}");
            if registry.subkeys(hive, view, &item).is_none() {
                continue;
            }
            let value = |value: &str| {
                let found = registry.value(hive, view, &item, value);
                found.map(|found| found.text()).unwrap_or_default()
            };
            let found = Entry {
                name: value("DisplayName"),
                publisher: value("Publisher"),
                location: value("InstallLocation"),
                uninstall: value("UninstallString"),
                key: name,
            };
            let once = (found.key.clone(), lp_py::lower(&found.location));
            entries.entry(once).or_insert(found);
        }
    }
    entries.into_values().collect()
}

/// gog: GOG's games (installed with GOG Galaxy or GOG's own installers), without their DLC.
pub fn gog(entries: &[Entry]) -> Vec<InstalledGame> {
    static KEY: OnceLock<Option<Regex>> = OnceLock::new();
    let mut games = Vec::new();
    for entry in entries {
        let Some(number) = found(r"^(\d+)_is1$", &KEY, &entry.key, true) else {
            continue;
        };
        if entry.publisher != "GOG.com" || entry.location.is_empty() {
            continue;
        }
        let info = Path::new(HERE, &entry.location).join(&format!("goggame-{number}.info"));
        if let Some(Value::Dict(info)) = json(&info) {
            let own = match info.get("rootGameId") {
                None | Some(Value::None) => true,
                Some(Value::Str(root)) => root.is_empty() || *root == number,
                Some(_) => false,
            };
            if !own {
                continue; // a DLC: it lies in its game's folder
            }
        }
        games.extend(game(
            "gog",
            Some(number),
            some(&entry.name),
            some(&entry.location),
        ));
    }
    games
}

/// ea: the EA app's games: what Electronic Arts installed, without the app and its tools.
pub fn ea(entries: &[Entry]) -> Vec<InstalledGame> {
    let mut games = Vec::new();
    for entry in entries {
        let name = lp_py::lower(&entry.name);
        if entry.publisher != "Electronic Arts"
            || entry.location.is_empty()
            || EA_TOOLS.iter().any(|tool| name.contains(tool))
        {
            continue;
        }
        games.extend(game(
            "ea",
            some(&entry.key),
            some(&entry.name),
            some(&entry.location),
        ));
    }
    games
}

/// battle_net: Battle.net's games: the programs it uninstalls by their id, without its own.
pub fn battle_net(entries: &[Entry]) -> Vec<InstalledGame> {
    static UID: OnceLock<Option<Regex>> = OnceLock::new();
    let mut games = Vec::new();
    for entry in entries {
        let uninstall = format!("{} ", entry.uninstall);
        let Some(uid) = found(r"Battle\.net.*--uid=(.*?)\s", &UID, &uninstall, false) else {
            continue;
        };
        if entry.location.is_empty()
            || lp_py::lower(&uid).starts_with("battle.net")
            || entry.name.ends_with("Test")
            || entry.name.ends_with("Beta")
        {
            continue;
        }
        games.extend(game(
            "battlenet",
            some(&uid),
            some(&entry.name),
            some(&entry.location),
        ));
    }
    games
}

/// rockstar: the Rockstar Games Launcher's games, by their title ids.
pub fn rockstar(entries: &[Entry]) -> Vec<InstalledGame> {
    static TITLE: OnceLock<Option<Regex>> = OnceLock::new();
    const PATTERN: &str = r"(?:Launcher|uninstall)\.exe.+uninstall=(.+)$";
    let mut games = Vec::new();
    for entry in entries {
        let Some(title) = found(PATTERN, &TITLE, &entry.uninstall, false) else {
            continue;
        };
        let title = lp_py::lower(paths::bare(&title));
        if !entry.location.is_empty() && ROCKSTAR_TITLES.contains(&title.as_str()) {
            games.extend(game(
                "rockstar",
                some(&title),
                some(&entry.name),
                some(&entry.location),
            ));
        }
    }
    games
}

/// ubisoft: Ubisoft Connect's games: its own key, the names from the list of installed
/// programs.
pub fn ubisoft(registry: &dyn Registry, entries: &[Entry]) -> Vec<InstalledGame> {
    let own = paths::sep(HERE);
    let mut games: IndexMap<String, InstalledGame> = IndexMap::new();
    for (hive, view) in &VIEWS[..2] {
        for number in registry.subkeys(*hive, *view, UBISOFT).unwrap_or_default() {
            let item = format!("{UBISOFT}\\{number}");
            if registry.subkeys(*hive, *view, &item).is_none() {
                continue;
            }
            let folder = registry.value(*hive, *view, &item, "InstallDir");
            let folder = folder.map(|folder| folder.text()).unwrap_or_default();
            let folder = folder.replace('/', &own.to_string());
            let folder = folder.trim_end_matches(own);
            let listed = format!("Uplay Install {number}");
            let name = (entries.iter().rev())
                .find(|entry| entry.key == listed)
                .map(|entry| entry.name.clone())
                .filter(|name| !name.is_empty())
                .unwrap_or_else(|| Path::new(HERE, folder).name().to_string());
            if let Some(game) = game("ubisoft", some(&number), some(&name), some(folder)) {
                games.entry(game.key.clone()).or_insert(game);
            }
        }
    }
    games.into_values().collect()
}

/// `value in kinds` as Python has it for what a manifest may hold.
fn holds(kinds: &Value, value: &str) -> Result<bool, Odd> {
    match kinds {
        Value::List(kinds) => Ok(kinds.iter().any(|kind| kind.as_str() == Some(value))),
        Value::Str(kinds) => Ok(kinds.contains(value)),
        Value::Dict(kinds) => Ok(kinds.contains_key(value)),
        _ => Err(Odd),
    }
}

/// What a `for` goes through in a value of JSON: a list's items, a table's keys, a text's
/// letters; a number has none to go through.
fn items(value: Option<&Value>) -> Result<Vec<Value>, Odd> {
    match value {
        None | Some(Value::None) => Ok(Vec::new()),
        Some(Value::List(items)) => Ok(items.clone()),
        Some(Value::Dict(table)) => Ok(table.keys().map(|key| Value::Str(key.clone())).collect()),
        Some(Value::Str(text)) => Ok(text.chars().map(|ch| Value::Str(ch.to_string())).collect()),
        Some(other) if !other.truthy() => Ok(Vec::new()),
        Some(_) => Err(Odd),
    }
}

/// epic: the Epic Games Launcher's games, from its manifests, without DLC and Unreal plugins.
pub fn epic(programdata: &Path) -> Games {
    let epic = programdata.join("Epic");
    let listed = json(
        &epic
            .join("UnrealEngineLauncher")
            .join("LauncherInstalled.dat"),
    );
    let listed = match listed.filter(Value::truthy) {
        None => Dict::new(),
        Some(Value::Dict(listed)) => listed,
        Some(_) => return Err(Odd),
    };
    let mut place: IndexMap<String, Value> = IndexMap::new();
    let installed = match listed.get("InstallationList") {
        None => Vec::new(),
        Some(Value::None) => return Err(Odd),
        Some(listed)
            if !listed.truthy()
                && !matches!(listed, Value::List(_) | Value::Dict(_) | Value::Str(_)) =>
        {
            return Err(Odd)
        }
        found => items(found)?,
    };
    for app in &installed {
        let Some(app) = app.as_dict() else {
            continue;
        };
        let name = app.get("AppName").map(Value::str).unwrap_or_default();
        let folder = (app.get("InstallLocation").filter(|folder| folder.truthy())).cloned();
        place.insert(
            lp_py::lower(&name),
            folder.unwrap_or(Value::Str(String::new())),
        );
    }
    let folder = epic
        .join("EpicGamesLauncher")
        .join("Data")
        .join("Manifests");
    let fits = |name: &String| paths::normcase(HERE, name).ends_with(".item");
    let manifests: Vec<String> = (names_in(&folder).unwrap_or_default().into_iter())
        .filter(fits)
        .collect();
    let mut games = Vec::new();
    for name in sorted(manifests) {
        let Some(Value::Dict(manifest)) = json(&folder.join(&name)) else {
            continue;
        };
        let kinds = (manifest.get("AppCategories").filter(|kinds| kinds.truthy())).cloned();
        let kinds = kinds.unwrap_or(Value::List(Vec::new()));
        if holds(&kinds, "addons")? && !holds(&kinds, "addons/launchable")? {
            continue; // a DLC
        }
        let members = items(Some(&kinds))?;
        if members
            .iter()
            .any(|kind| matches!(kind, Value::List(_) | Value::Dict(_)))
        {
            return Err(Odd);
        }
        let plugin = (members.iter())
            .any(|kind| matches!(kind.as_str(), Some("plugins" | "plugins/engine")));
        let apps = manifest.get("CompatibleApps").filter(|apps| apps.truthy());
        let technical = truthy(manifest.get("TechnicalType")).unwrap_or_default();
        if plugin
            || items(apps)?.iter().any(|app| app.str().starts_with("UE_"))
            || technical.contains("plugins/engine")
        {
            continue; // a plugin for Unreal Engine
        }
        let app = truthy(manifest.get("AppName")).unwrap_or_default();
        let mut folder = manifest
            .get("InstallLocation")
            .filter(|folder| folder.truthy());
        let there = match folder {
            None => false,
            Some(Value::Str(folder)) => is_dir(&Path::new(HERE, folder)),
            Some(_) => return Err(Odd),
        };
        if !there {
            let listed = place
                .get(&lp_py::lower(&app))
                .filter(|folder| folder.truthy());
            folder = listed.or(folder);
        }
        let folder = folder.map(Value::str);
        let key = some(&app).or(folder.clone());
        games.extend(game(
            "epic",
            key,
            truthy(manifest.get("DisplayName")),
            folder,
        ));
    }
    Ok(games)
}

/// amazon: Amazon Games' games, from its database of what it installed.
pub fn amazon(local_appdata: &Path) -> Vec<InstalledGame> {
    let mut database = local_appdata.join("Amazon Games");
    for part in ["Data", "Games", "Sql", "GameInstallInfo.sqlite"] {
        database = database.join(part);
    }
    let query = "SELECT Id, ProductTitle, InstallDirectory FROM DbSet WHERE Installed = 1";
    let rows = rows(&database, query).into_iter();
    rows.filter_map(|row| {
        game(
            "amazon",
            truthy(row.first()),
            truthy(row.get(1)),
            truthy(row.get(2)),
        )
    })
    .collect()
}

/// The folders in a folder, as pathlib sorts them.
fn folders_in(folder: &Path) -> Option<Vec<String>> {
    let inside = names_in(folder)?.into_iter();
    Some(sorted(
        inside.filter(|name| is_dir(&folder.join(name))).collect(),
    ))
}

/// xbox: the Xbox app's games: a folder each in XboxGames, where it installs them on a drive.
pub fn xbox(drives: &[Path]) -> Vec<InstalledGame> {
    let mut games = Vec::new();
    for drive in drives {
        let root = drive.join("XboxGames");
        for name in folders_in(&root).unwrap_or_default() {
            if !name.starts_with('.') {
                let folder = root.join(&name).text();
                games.extend(game("xbox", some(&name), some(&name), Some(folder)));
            }
        }
    }
    games
}

// -- Heroic and Lutris ---------------------------------------------------------------------------

/// heroic: Heroic's games: Epic's (through legendary), GOG's and Amazon's (through nile).
pub fn heroic(roots: &[Path]) -> Games {
    let mut games = Vec::new();
    for root in roots {
        let legendary = root
            .join("legendaryConfig")
            .join("legendary")
            .join("installed.json");
        if let Some(Value::Dict(legendary)) = json(&legendary) {
            for (app, info) in &legendary {
                let Some(info) = info.as_dict() else {
                    continue;
                };
                if !info.get("is_dlc").is_some_and(Value::truthy) {
                    let folder = truthy(info.get("install_path"));
                    games.extend(game("epic", some(app), truthy(info.get("title")), folder));
                }
            }
        }
        if let Some(Value::Dict(installed)) = json(&root.join("gog_store").join("installed.json")) {
            let listed = match installed.get("installed") {
                Some(Value::None) => return Err(Odd),
                Some(Value::Bool(_) | Value::Int(_) | Value::Float(_)) => return Err(Odd),
                listed => items(listed)?,
            };
            for info in &listed {
                let Some(info) = info.as_dict() else {
                    continue;
                };
                if !info.get("is_dlc").is_some_and(Value::truthy) {
                    let folder = truthy(info.get("install_path"));
                    let key = truthy(info.get("appName")).or(folder.clone());
                    games.extend(game("gog", key, None, folder));
                }
            }
        }
        if let Some(Value::List(nile)) =
            json(&root.join("nile_config").join("nile").join("installed.json"))
        {
            for info in nile.iter().filter_map(Value::as_dict) {
                let folder = truthy(info.get("path"));
                let key = truthy(info.get("id")).or(folder.clone());
                games.extend(game("amazon", key, None, folder));
            }
        }
    }
    Ok(games)
}

/// lutris: Lutris's installed games (its Steam games are Steam's).
pub fn lutris(roots: &[Path]) -> Vec<InstalledGame> {
    let query = "SELECT slug, name, directory, runner FROM games WHERE installed = 1";
    let mut games = Vec::new();
    for root in roots {
        for row in rows(&root.join("pga.db"), query) {
            let folder = truthy(row.get(2));
            let steam = matches!(row.get(3), Some(Value::Str(runner)) if runner == "steam");
            if folder.is_some() && !steam {
                let key = truthy(row.first()).or(folder.clone());
                games.extend(game("lutris", key, truthy(row.get(1)), folder));
            }
        }
    }
    games
}

// -- folders you name ----------------------------------------------------------------------------

fn holds_programs(folder: &Path) -> bool {
    names_in(folder).unwrap_or_default().iter().any(|name| {
        let file = folder.join(name);
        is_file(&file) && PROGRAMS.contains(&lp_py::lower(file.suffix()).as_str())
    })
}

/// folders: the games in the folders you name: a folder with programs in it is a game of its
/// own, any other holds a game in each of its folders. Folders that are not there are told
/// in `notes`.
pub fn folders(
    named: &[String],
    homes: &paths::Homes,
    notes: &mut Vec<String>,
) -> Vec<InstalledGame> {
    let mut games = Vec::new();
    for raw in named {
        let text = paths::bare(raw);
        if text.is_empty() {
            continue;
        }
        let expanded = paths::expanduser(HERE, text, homes);
        let folder = Path::new(HERE, &paths::expandvars(HERE, &expanded, homes.env));
        if !is_dir(&folder) {
            notes.push(format!("game folder {} is not there", folder.text()));
            continue;
        }
        if holds_programs(&folder) {
            let place = folder.text();
            games.extend(game(
                "folder",
                Some(place.clone()),
                some(folder.name()),
                Some(place),
            ));
            continue;
        }
        for name in folders_in(&folder).unwrap_or_default() {
            if !name.starts_with('.') {
                let place = folder.join(&name).text();
                games.extend(game(
                    "folder",
                    Some(place.clone()),
                    some(&name),
                    Some(place),
                ));
            }
        }
    }
    games
}

// -- where each list lies -----------------------------------------------------------------------

/// _drives: the computer's own drives (not CD drives nor network shares).
fn drives() -> Vec<Path> {
    #[cfg(windows)]
    {
        use windows_sys::Win32::Storage::FileSystem::{GetDriveTypeW, GetLogicalDrives};
        const REMOVABLE: u32 = 2;
        const FIXED: u32 = 3;
        // SAFETY: the drives' letters as bits, and a drive's kind by its name ending with a
        // zero.
        let letters = unsafe { GetLogicalDrives() };
        let mut found = Vec::new();
        for (bit, letter) in ('A'..='Z').enumerate() {
            if letters & (1 << bit) == 0 {
                continue;
            }
            let root = format!("{letter}:\\");
            let name: Vec<u16> = root.encode_utf16().chain(Some(0)).collect();
            let kind = unsafe { GetDriveTypeW(name.as_ptr()) };
            if kind == FIXED || (kind == REMOVABLE && letter != 'A') {
                found.push(Path::new(true, &root));
            }
        }
        found
    }
    #[cfg(not(windows))]
    {
        Vec::new()
    }
}

/// A folder the environment names (where it is set and not empty), else `usual` under home.
fn named_or(places: &Places, name: &str, usual: &[&str]) -> Path {
    match places.set(name) {
        Some(folder) => Path::new(HERE, &folder),
        None => usual
            .iter()
            .fold(Path::new(HERE, &places.home), |at, part| at.join(part)),
    }
}

/// launcher_games: the games of every launcher found on this computer.
pub fn launcher_games(places: &Places, drives: &[Path]) -> Games {
    let home = Path::new(HERE, &places.home);
    let mut games = Vec::new();
    if places.windows {
        // (set to nothing, they name this folder: Python takes what the environment says)
        let given = |name: &str, usual: &dyn Fn() -> Path| match (places.env)(name) {
            Some(folder) => Path::new(HERE, &folder),
            None => usual(),
        };
        let programdata = given("PROGRAMDATA", &|| Path::new(HERE, r"C:\ProgramData"));
        let local = given("LOCALAPPDATA", &|| home.join("AppData").join("Local"));
        games.extend(epic(&programdata)?);
        if let Some(registry) = places.registry.as_deref() {
            let entries = uninstall_entries(registry);
            games.extend(gog(&entries));
            games.extend(ubisoft(registry, &entries));
            games.extend(ea(&entries));
            games.extend(battle_net(&entries));
            games.extend(rockstar(&entries));
        }
        games.extend(amazon(&local));
        games.extend(xbox(drives));
        let roaming = given("APPDATA", &|| home.join("AppData").join("Roaming"));
        games.extend(heroic(&[roaming.join("heroic")])?);
    } else {
        let config = named_or(places, "XDG_CONFIG_HOME", &[".config"]);
        let flatpak = home.join(".var/app/com.heroicgameslauncher.hgl/config/heroic");
        games.extend(heroic(&[config.join("heroic"), flatpak])?);
        let data = named_or(places, "XDG_DATA_HOME", &[".local", "share"]);
        let flatpak = home.join(".var/app/net.lutris.Lutris/data/lutris");
        games.extend(lutris(&[data.join("lutris"), flatpak]));
    }
    Ok(games)
}

/// _too_wide: a folder in which "a program of the game" would be half the computer's
/// programs.
fn too_wide(folder: &Path, home: &Path) -> bool {
    let same = paths::normcase(HERE, &folder.text()) == paths::normcase(HERE, &home.text());
    match folder.tail.as_slice() {
        [] => true,
        _ if same => true,
        [only] => TOO_WIDE.contains(&lp_py::lower(only).as_str()),
        _ => false,
    }
}

type Clock = Box<dyn Fn() -> f64 + Send + Sync>;
type Read = Box<dyn Fn() -> Games + Send + Sync>;

/// The games of the other launchers and of the folders you name, read again every
/// `REFRESH_S`. `ignore` names (case does not matter) games and tools that are never "the
/// game", as for Steam.
pub struct Catalog {
    named: Vec<String>,
    ignore: HashSet<String>,
    launchers: bool,
    clock: Clock,
    read: Read,
    places: Places,
    read_at: f64,
    /// by folder, normalised
    games: IndexMap<String, InstalledGame>,
    /// lines for the log
    pub notes: Vec<String>,
}

impl Catalog {
    /// `read` reads the launchers' games (None: this computer's own lists).
    pub fn new(
        named: &[Value],
        ignore: &[Value],
        launchers: bool,
        clock: Clock,
        read: Option<Read>,
        places: Places,
    ) -> Catalog {
        let own = Places::here();
        Catalog {
            named: (named.iter().map(Value::str))
                .filter(|path| !lp_py::strip(path).is_empty())
                .collect(),
            ignore: ignored(ignore),
            launchers,
            clock,
            read: read.unwrap_or_else(|| Box::new(move || launcher_games(&own, &drives()))),
            places,
            read_at: -REFRESH_S,
            games: IndexMap::new(),
            notes: Vec::new(),
        }
    }

    pub fn wanted(&self) -> bool {
        self.launchers || !self.named.is_empty()
    }

    pub fn games(&mut self) -> Vec<InstalledGame> {
        self.refresh();
        self.games.values().cloned().collect()
    }

    /// match: the game a program at `path` (or working in that folder) belongs to: the one
    /// with the innermost folder.
    pub fn match_path(&mut self, path: &str) -> Option<InstalledGame> {
        self.refresh();
        if path.is_empty() {
            return None;
        }
        let place = paths::folded(HERE, path);
        let below = |folder: &str| {
            let rest = place.strip_prefix(folder);
            rest.is_some_and(|rest| rest.is_empty() || rest.starts_with(paths::sep(HERE)))
        };
        let mut best: Option<(usize, &InstalledGame)> = None;
        for (folder, game) in &self.games {
            let length = folder.chars().count();
            if below(folder) && best.is_none_or(|(most, _)| length > most) {
                best = Some((length, game));
            }
        }
        best.map(|(_, game)| game.clone())
    }

    fn refresh(&mut self) {
        let now = (self.clock)();
        if now - self.read_at < REFRESH_S {
            return;
        }
        self.read_at = now;
        let mut found = Vec::new();
        if self.launchers {
            match (self.read)() {
                Ok(games) => found = games,
                // (a launcher's odd list must not stop game mode)
                Err(Odd) => self
                    .notes
                    .push("could not read the launchers' games".into()),
            }
        }
        // (after the launchers': theirs have the better names)
        let homes = paths::Homes {
            env: &*self.places.env,
            user: &paths::user_home,
        };
        found.extend(folders(&self.named, &homes, &mut self.notes));
        let home = Path::new(HERE, &self.places.home);
        let mut games = IndexMap::new();
        for game in found {
            let names = [lp_py::lower(&game.name), lp_py::lower(game.folder.name())];
            if names.iter().any(|name| self.ignore.contains(name)) || too_wide(&game.folder, &home)
            {
                continue;
            }
            let folder = paths::folded(HERE, &game.folder.text());
            games.entry(folder).or_insert(game);
        }
        self.games = games;
    }
}
