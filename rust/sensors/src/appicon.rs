//! The icon of the program that plays, for the badge on a "now playing" message
//! (sensors/appicon.py).
//!
//! The media controls name the player only by an id, so each system has its own way from
//! that id to a picture. Everything here only reads; no program is touched.
//!
//! - Windows: the id is the AppUserModelID. A packaged (Store) app is
//!   `PackageFamilyName!ApplicationId`; its logo is the one its manifest names. Any other
//!   program is named after its file (`Spotify.exe`, `MSEdge`): the icon comes from the file
//!   of the running program of that name.
//! - Linux: the MPRIS player name (`spotify`) finds `spotify.desktop` and the PNG its
//!   `Icon=` line names. Icons that only exist as SVG (or XPM) are left out.
//!
//! An icon is looked for once per app.

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::time::Instant;

use lp_pixels::{Filter, Image, Mode};

/// pixels: enough for the badge of the widest panel
pub const ICON_SIZE: usize = 96;
/// an app whose icon was not found is looked for again after this long
pub const RETRY_SECONDS: f64 = 60.0;
/// manifest entries that name a logo, best first (the first is what the taskbar shows)
const LOGO_ATTRIBUTES: [&str; 4] = ["Square44x44Logo", "Square150x150Logo", "Logo", "SmallLogo"];

/// Finds the icon of an app by its id.
pub type Finder = Box<dyn Fn(&str) -> Option<Image> + Send>;

/// Icons by app id, found once per app. An app without one is tried again after a while:
/// its program may not have been running yet.
pub struct AppIcons {
    find: Finder,
    /// app id -> its icon, which one it is of those found, and when it was looked for
    known: HashMap<String, (Option<Image>, u64, Instant)>,
    found: u64,
}

impl AppIcons {
    pub fn new(find: Finder) -> AppIcons {
        AppIcons {
            find,
            known: HashMap::new(),
            found: 0,
        }
    }

    /// The way to find an icon from the player's id on this system.
    pub fn of_system() -> AppIcons {
        #[cfg(windows)]
        let find: Finder = Box::new(windows::icon);
        #[cfg(not(windows))]
        let find: Finder = Box::new(|player| linux_icon(player, &system_dirs()));
        AppIcons::new(find)
    }

    /// The icon of an app, and a number that is the same as long as the icon is (0 for none).
    pub fn get(&mut self, app_id: &str) -> (Option<&Image>, u64) {
        if app_id.is_empty() {
            return (None, 0);
        }
        let due = match self.known.get(app_id) {
            Some((icon, _, at)) => icon.is_none() && at.elapsed().as_secs_f64() >= RETRY_SECONDS,
            None => true,
        };
        if due {
            let icon = (self.find)(app_id);
            self.found += 1;
            let which = if icon.is_some() { self.found } else { 0 };
            let entry = (icon, which, Instant::now());
            self.known.insert(app_id.to_string(), entry);
        }
        match self.known.get(app_id) {
            Some((icon, which, _)) => (icon.as_ref(), *which),
            None => (None, 0),
        }
    }
}

/// A square RGBA picture of `ICON_SIZE`; a wide logo is not stretched.
pub fn badge(image: &Image) -> Option<Image> {
    let mut image = image.convert(Mode::RGBA).ok()?;
    let side = image.width().max(image.height());
    if (image.width(), image.height()) != (side, side) {
        let mut square = Image::new(Mode::RGBA, side, side, &[0, 0, 0, 0]);
        let (x, y) = ((side - image.width()) / 2, (side - image.height()) / 2);
        square.paste(&image, x as i64, y as i64, None).ok()?;
        image = square;
    }
    image
        .resize(ICON_SIZE, ICON_SIZE, Filter::Lanczos, None, None)
        .ok()
}

/// An icon from a picture file that can be read, or None (an SVG).
pub fn load_file(path: &Path) -> Option<Image> {
    let data = std::fs::read(path).ok()?;
    badge(&lp_decode::open(&data, Mode::RGBA).ok()?)
}

// -- Windows -----------------------------------------------------------------------------

/// The file names (lower case) a player's program may have, from its media-controls id:
/// `Spotify.exe`, `MSEdge` (msedge.exe), `Chrome.ABCDEF` (chrome.exe), and for a packaged
/// app the program named after the application (`Pkg_hash!Spotify`).
pub fn program_names(app_id: &str) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    let mut add = |text: &str| {
        let text = lp_py::lower(lp_py::strip(text));
        if !text.is_empty() {
            if !text.ends_with(".exe") {
                names.insert(format!("{text}.exe"));
            }
            names.insert(text);
        }
    };
    add(app_id);
    match app_id.split_once('!') {
        Some((family, application)) => {
            add(application);
            add(family.split('_').next().unwrap_or(""));
        }
        None => add(app_id.split('.').next().unwrap_or("")),
    }
    names
}

/// The stem and the suffix of a file's name as pathlib has them ("Logo.scale-200", ".png").
fn stem_suffix(name: &str) -> (&str, &str) {
    match name.rfind('.') {
        Some(at) if at > 0 && at < name.len() - 1 => name.split_at(at),
        _ => (name, ""),
    }
}

/// The first whole number after `marker` in `text`, if digits follow it somewhere.
fn number_after(text: &str, marker: &str) -> Option<i128> {
    let mut rest = text;
    while let Some(at) = rest.find(marker) {
        let after = &rest[at + marker.len()..];
        let digits: String = after
            .chars()
            .take_while(|ch| lp_py::decimal(*ch).is_some())
            .collect();
        if !digits.is_empty() {
            return lp_py::int_of_digits(&digits);
        }
        rest = &rest[at + marker.len().max(1)..];
    }
    None
}

/// `(\d+)x\d+` searched in a logo's stem: the first number that an x and a digit follow.
fn base_size(stem: &str) -> Option<i128> {
    let chars: Vec<char> = stem.chars().collect();
    let digit = |at: usize| {
        chars
            .get(at)
            .is_some_and(|ch| lp_py::decimal(*ch).is_some())
    };
    for start in 0..chars.len() {
        if !digit(start) {
            continue;
        }
        // (the digits from here, given back one by one until an x and a digit follow)
        let run = (start..chars.len()).take_while(|at| digit(*at)).count();
        for length in (1..=run).rev() {
            if chars.get(start + length) == Some(&'x') && digit(start + length + 1) {
                let found: String = chars[start..start + length].iter().collect();
                return lp_py::int_of_digits(&found);
            }
        }
    }
    None
}

/// Higher is better: no plate first (`altform-unplated`, not the light variant), then the
/// size nearest above `ICON_SIZE`, then the biggest below it.
pub fn logo_rank(name: &str, base: i128) -> (bool, i128) {
    let name = lp_py::lower(name);
    let plain = name.contains("altform-unplated") && !name.contains("lightunplated");
    let size = match number_after(&name, "targetsize-") {
        Some(size) => size,
        None => match number_after(&name, "scale-") {
            Some(scale) => base.saturating_mul(scale).div_euclid(100),
            None => 0,
        },
    };
    let wanted = ICON_SIZE as i128;
    let nearness = if size >= wanted {
        -(size - wanted)
    } else {
        -10_000 + size
    };
    (plain, nearness)
}

/// The file behind a logo the manifest names. `Logo.png` ships as `Logo.scale-200.png`,
/// `Logo.targetsize-48.png` and so on; the one without a backing plate and nearest in size
/// above `ICON_SIZE` is taken (of equals the first by its name).
pub fn logo_file(declared: &Path) -> Option<PathBuf> {
    let name = declared.file_name()?.to_string_lossy().into_owned();
    let folder = declared.parent()?;
    let (stem, suffix) = stem_suffix(&name);
    // (Windows' names are the same in any case)
    let fold = |text: &str| match cfg!(windows) {
        true => lp_py::lower(text),
        false => text.to_string(),
    };
    let (start, end) = (fold(stem), fold(suffix));
    let mut candidates: Vec<String> = crate::sysfs::names(folder)
        .unwrap_or_default()
        .into_iter()
        .filter(|found| {
            let found = fold(found);
            found.len() >= start.len() + end.len()
                && found.starts_with(&start)
                && found.ends_with(&end)
        })
        .filter(|found| folder.join(found).is_file())
        .collect();
    if declared.is_file() && !candidates.contains(&name) {
        candidates.push(name.clone());
    }
    let base = base_size(stem).unwrap_or(44);
    let mut best: Option<&String> = None;
    for candidate in &candidates {
        if best.is_none_or(|b| logo_rank(candidate, base) > logo_rank(b, base)) {
            best = Some(candidate);
        }
    }
    best.map(|name| folder.join(name))
}

/// The logo `AppxManifest.xml` names for the application (the first, if none is named):
/// `Assets\Square44x44Logo.png`, a path relative to the package.
pub fn manifest_logo(folder: &Path, application: &str) -> Option<String> {
    let data = std::fs::read(folder.join("AppxManifest.xml")).ok()?;
    let text = String::from_utf8_lossy(&data);
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
    let manifest = roxmltree::Document::parse(text).ok()?;
    for app in manifest.root_element().descendants() {
        if !app.is_element() || app.tag_name().name() != "Application" {
            continue;
        }
        if !application.is_empty() && app.attribute("Id") != Some(application) {
            continue;
        }
        for element in app.descendants() {
            if element.is_element() && element.tag_name().name() == "VisualElements" {
                let named = LOGO_ATTRIBUTES
                    .iter()
                    .find_map(|name| element.attribute(*name).filter(|logo| !logo.is_empty()));
                if let Some(logo) = named {
                    return Some(logo.to_string());
                }
            }
        }
    }
    None
}

/// The logo of a packaged app (`PackageFamilyName!ApplicationId`), from the picture its
/// manifest names, in one of the folders its package is installed in. Any installed version
/// will do; the highest folder name goes first.
pub fn package_logo(app_id: &str, mut folders: Vec<PathBuf>) -> Option<Image> {
    let application = app_id
        .split_once('!')
        .map_or("", |(_, application)| application);
    folders.sort();
    for folder in folders.iter().rev() {
        let Some(declared) = manifest_logo(folder, application).filter(|logo| !logo.is_empty())
        else {
            continue;
        };
        // the manifest writes backslashes; any system reads them as a path
        let file = logo_file(&folder.join(declared.replace('\\', "/")));
        if let Some(image) = file.and_then(|file| load_file(&file)) {
            return Some(image);
        }
    }
    None
}

#[cfg(windows)]
mod windows {
    use std::path::PathBuf;

    use lp_pixels::{Image, Mode};
    use windows_sys::Win32::Foundation::{CloseHandle, ERROR_INSUFFICIENT_BUFFER};
    use windows_sys::Win32::Graphics::Gdi::{
        DeleteObject, GetDC, GetDIBits, GetObjectW, ReleaseDC, BITMAP, BITMAPINFO,
        BITMAPINFOHEADER, DIB_RGB_COLORS, HBITMAP,
    };
    use windows_sys::Win32::Storage::Packaging::Appx::{
        FindPackagesByPackageFamily, GetPackagePathByFullName, PACKAGE_FILTER_HEAD,
    };
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
        TH32CS_SNAPPROCESS,
    };
    use windows_sys::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        DestroyIcon, GetIconInfo, PrivateExtractIconsW, HICON, ICONINFO,
    };

    use super::{badge, package_logo, program_names};

    /// asked of Windows, then scaled down here (it scales less well)
    const EXTRACT_SIZE: i32 = 256;

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    fn text(units: &[u16]) -> String {
        let end = units
            .iter()
            .position(|unit| *unit == 0)
            .unwrap_or(units.len());
        String::from_utf16_lossy(&units[..end])
    }

    /// The icon of the app with this AppUserModelID: the logo of its package, else the icon
    /// of its running program.
    pub fn icon(app_id: &str) -> Option<Image> {
        if let Some((family, _)) = app_id.split_once('!') {
            if let Some(logo) = package_logo(app_id, package_folders(family)) {
                return Some(logo);
            }
        }
        exe_icon(&running_program(app_id)?)
    }

    /// The path of a running program that goes with the id, or None: the first in the order
    /// of their numbers.
    fn running_program(app_id: &str) -> Option<String> {
        let names = program_names(app_id);
        let mut found: Vec<(u32, String)> = Vec::new();
        // SAFETY: the snapshot's handle is closed here; each entry is a structure of this
        // frame with its size set; a path is written into a buffer of the length given.
        unsafe {
            let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
            if snapshot.is_null() || snapshot as isize == -1 {
                return None;
            }
            let mut entry: PROCESSENTRY32W = std::mem::zeroed();
            entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
            let mut more = Process32FirstW(snapshot, &mut entry) != 0;
            while more {
                let process =
                    OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, entry.th32ProcessID);
                if !process.is_null() {
                    let mut path = [0u16; 1024];
                    let mut length = path.len() as u32;
                    if QueryFullProcessImageNameW(process, 0, path.as_mut_ptr(), &mut length) != 0 {
                        let path = String::from_utf16_lossy(&path[..length as usize]);
                        let name = path.rsplit(['\\', '/']).next().unwrap_or("");
                        if names.contains(&lp_py::lower(name)) {
                            found.push((entry.th32ProcessID, path));
                        }
                    }
                    CloseHandle(process);
                }
                more = Process32NextW(snapshot, &mut entry) != 0;
            }
            CloseHandle(snapshot);
        }
        found.sort();
        found.into_iter().next().map(|(_, path)| path)
    }

    /// The 32-bit pixels of a bitmap, top row first (BGRA).
    fn pixels(bitmap: HBITMAP, width: i32, height: i32) -> Option<Vec<u8>> {
        let mut data = vec![0u8; width as usize * height as usize * 4];
        // SAFETY: the buffer holds the rows asked for at 32 bits a pixel; the device
        // context is released here.
        unsafe {
            let mut info: BITMAPINFO = std::mem::zeroed();
            info.bmiHeader.biSize = size_of::<BITMAPINFOHEADER>() as u32;
            info.bmiHeader.biWidth = width;
            info.bmiHeader.biHeight = -height; // a negative height: top-down
            info.bmiHeader.biPlanes = 1;
            info.bmiHeader.biBitCount = 32;
            let device = GetDC(std::ptr::null_mut());
            let rows = height as u32;
            let to = data.as_mut_ptr().cast();
            let done = GetDIBits(device, bitmap, 0, rows, to, &mut info, DIB_RGB_COLORS);
            ReleaseDC(std::ptr::null_mut(), device);
            (done != 0).then_some(data)
        }
    }

    /// BGRA as Pillow's raw mode reads it into RGBA.
    fn rgba(mut data: Vec<u8>, width: i32, height: i32) -> Option<Image> {
        for pixel in data.chunks_exact_mut(4) {
            pixel.swap(0, 2);
        }
        Image::from_bytes(Mode::RGBA, width as usize, height as usize, data).ok()
    }

    /// An HICON as a picture. An old icon has no alpha channel of its own: its mask says
    /// what is see-through then.
    fn icon_image(icon: HICON) -> Option<Image> {
        // SAFETY: the structures are this frame's; the two bitmaps GetIconInfo gives are
        // deleted here.
        unsafe {
            let mut info: ICONINFO = std::mem::zeroed();
            if GetIconInfo(icon, &mut info) == 0 {
                return None;
            }
            let image = (|| {
                if info.hbmColor.is_null() {
                    return None; // a black-and-white icon: not worth showing
                }
                let mut bitmap: BITMAP = std::mem::zeroed();
                let to: *mut BITMAP = &mut bitmap;
                GetObjectW(info.hbmColor, size_of::<BITMAP>() as i32, to.cast());
                let (width, height) = (bitmap.bmWidth, bitmap.bmHeight);
                if width <= 0 || height <= 0 {
                    return None;
                }
                let mut image = rgba(pixels(info.hbmColor, width, height)?, width, height)?;
                let alpha = image.getchannel(3).ok()?;
                if alpha.getbbox(false).is_none() && !info.hbmMask.is_null() {
                    if let Some(mask) = pixels(info.hbmMask, width, height) {
                        // (the mask as greys: what is not black is see-through)
                        let grey = rgba(mask, width, height)?.convert(Mode::L).ok()?;
                        let seen: Vec<u8> = grey
                            .as_bytes()
                            .iter()
                            .map(|value| if *value == 0 { 255 } else { 0 })
                            .collect();
                        let seen =
                            Image::from_bytes(Mode::L, width as usize, height as usize, seen);
                        image.putalpha(&seen.ok()?).ok()?;
                    }
                }
                Some(image)
            })();
            for bitmap in [info.hbmColor, info.hbmMask] {
                if !bitmap.is_null() {
                    DeleteObject(bitmap);
                }
            }
            image
        }
    }

    /// The icon of a program file. `PrivateExtractIconsW` takes the size wanted, so the
    /// large icon comes, not the 32-pixel one.
    fn exe_icon(path: &str) -> Option<Image> {
        let path = wide(path);
        // SAFETY: the path ends with a zero; one icon is asked for into this frame's
        // handle, and destroyed here.
        unsafe {
            let mut icon: HICON = std::ptr::null_mut();
            let mut ident = 0u32;
            let size = EXTRACT_SIZE;
            let found =
                PrivateExtractIconsW(path.as_ptr(), 0, size, size, &mut icon, &mut ident, 1, 0);
            if found == 0 || icon.is_null() {
                return None; // nothing, or 0xFFFFFFFF for an error
            }
            let image = icon_image(icon);
            DestroyIcon(icon);
            badge(&image?)
        }
    }

    /// The install folders of a package family's main packages (several versions can be
    /// installed at once).
    fn package_folders(family: &str) -> Vec<PathBuf> {
        let family = wide(family);
        let mut folders = Vec::new();
        // SAFETY: asked twice: first how much room it takes, then into buffers of that
        // room; each path the same way.
        unsafe {
            let (mut count, mut length) = (0u32, 0u32);
            let need = FindPackagesByPackageFamily(
                family.as_ptr(),
                PACKAGE_FILTER_HEAD,
                &mut count,
                std::ptr::null_mut(),
                &mut length,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            );
            if need != ERROR_INSUFFICIENT_BUFFER || count == 0 {
                return folders;
            }
            let mut names = vec![std::ptr::null_mut::<u16>(); count as usize];
            let mut buffer = vec![0u16; length as usize];
            let mut properties = vec![0u32; count as usize];
            let failed = FindPackagesByPackageFamily(
                family.as_ptr(),
                PACKAGE_FILTER_HEAD,
                &mut count,
                names.as_mut_ptr(),
                &mut length,
                buffer.as_mut_ptr(),
                properties.as_mut_ptr(),
            );
            if failed != 0 {
                return folders;
            }
            for full_name in names.into_iter().take(count as usize) {
                let mut size = 0u32;
                let need = GetPackagePathByFullName(full_name, &mut size, std::ptr::null_mut());
                if need != ERROR_INSUFFICIENT_BUFFER || size == 0 {
                    continue;
                }
                let mut path = vec![0u16; size as usize];
                if GetPackagePathByFullName(full_name, &mut size, path.as_mut_ptr()) == 0 {
                    folders.push(PathBuf::from(text(&path)));
                }
            }
        }
        folders
    }
}

// -- Linux -------------------------------------------------------------------------------

const THEME_SIZES: [&str; 8] = [
    "512x512", "256x256", "192x192", "128x128", "96x96", "64x64", "48x48", "32x32",
];

/// The folders desktop entries and icons live in: the user's, the system's, and where
/// Flatpak and Snap export theirs. `env` is the environment.
pub fn data_dirs(env: &dyn Fn(&str) -> Option<String>, home: &Path) -> Vec<PathBuf> {
    let set = |name: &str| env(name).filter(|value| !value.is_empty());
    let system = set("XDG_DATA_DIRS").unwrap_or_else(|| "/usr/local/share:/usr/share".into());
    let share = home.join(".local").join("share");
    let mut found = vec![set("XDG_DATA_HOME").map_or(share.clone(), PathBuf::from)];
    found.extend(
        system
            .split(':')
            .filter(|p| !p.is_empty())
            .map(PathBuf::from),
    );
    found.push(PathBuf::from("/var/lib/flatpak/exports/share"));
    found.push(share.join("flatpak").join("exports").join("share"));
    found.push(PathBuf::from("/var/lib/snapd/desktop"));
    let mut once: Vec<PathBuf> = Vec::new();
    for folder in found {
        if !once.contains(&folder) {
            once.push(folder);
        }
    }
    once
}

/// The folders of this user and this system.
#[cfg(not(windows))]
fn system_dirs() -> Vec<PathBuf> {
    let env = |name: &str| std::env::var(name).ok();
    let home = env("HOME").map_or(PathBuf::from("/"), PathBuf::from);
    data_dirs(&env, &home)
}

/// The desktop entry of an MPRIS player: `spotify.desktop`, else one named like a reverse
/// domain (`org.mozilla.firefox.desktop`) or with a suffix (`firefox-esr`): the shortest
/// name (of equals the first by its name).
pub fn desktop_entry(player: &str, dirs: &[PathBuf]) -> Option<PathBuf> {
    let name = lp_py::lower(player);
    for base in dirs {
        let exact = base.join("applications").join(format!("{name}.desktop"));
        if exact.is_file() {
            return Some(exact);
        }
    }
    for pattern in [format!("*.{name}.desktop"), format!("{name}-*.desktop")] {
        let Ok(pattern) = lp_py::fnmatch::Pattern::new(&pattern) else {
            continue;
        };
        for base in dirs {
            let folder = base.join("applications");
            let mut matches: Vec<String> = crate::sysfs::names(&folder)
                .unwrap_or_default()
                .into_iter()
                .filter(|found| pattern.matches(found))
                .collect();
            matches.sort_by_key(|found| found.chars().count());
            if let Some(found) = matches.first() {
                return Some(folder.join(found));
            }
        }
    }
    None
}

/// The `Icon=` of a desktop entry's main section.
pub fn desktop_icon_name(entry: &Path) -> Option<String> {
    let data = std::fs::read(entry).ok()?;
    let text = String::from_utf8_lossy(&data);
    let mut inside = false;
    for line in lp_py::splitlines(&text) {
        let line = lp_py::strip(line);
        if line.starts_with('[') {
            inside = line == "[Desktop Entry]";
        } else if let (true, Some(icon)) = (inside, line.strip_prefix("Icon=")) {
            let icon = lp_py::strip(icon);
            return (!icon.is_empty()).then(|| icon.to_string());
        }
    }
    None
}

/// The PNG for an icon name: the biggest in the hicolor theme, else a pixmap.
pub fn icon_file(icon: &str, dirs: &[PathBuf]) -> Option<PathBuf> {
    if icon.starts_with('/') {
        return Path::new(icon).is_file().then(|| PathBuf::from(icon));
    }
    for base in dirs {
        for size in THEME_SIZES {
            let file = format!("{icon}.png");
            let path = base
                .join("icons")
                .join("hicolor")
                .join(size)
                .join("apps")
                .join(file);
            if path.is_file() {
                return Some(path);
            }
        }
    }
    for base in dirs {
        for suffix in [".png", ".xpm"] {
            let path = base.join("pixmaps").join(format!("{icon}{suffix}"));
            if path.is_file() {
                return Some(path);
            }
        }
    }
    None
}

/// The icon of an MPRIS player, by its name.
pub fn linux_icon(player: &str, dirs: &[PathBuf]) -> Option<Image> {
    let entry = desktop_entry(player, dirs)?;
    let name = desktop_icon_name(&entry)?;
    load_file(&icon_file(&name, dirs)?)
}
