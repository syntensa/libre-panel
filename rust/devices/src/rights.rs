//! Running with administrator rights (Windows) or as root: what Libre Panel then holds back
//! (rights.py).
//!
//! Started with the highest rights, Libre Panel would hand them to whatever it starts from a
//! place that programs without those rights can change. So, while it has them, it starts a
//! program (the engine, ffmpeg, PresentMon) only from a folder that only administrators may
//! change:
//! - Windows: Program Files and Windows (as the registry of the machine names them), and the
//!   folder of the program itself with its `tools`: whoever may write there may replace Libre
//!   Panel anyway;
//! - elsewhere, as root: a file or folder that, with every folder above it, belongs to root
//!   and that nobody else may write to (a folder anyone may write to with the sticky bit, as
//!   /tmp, keeps what is root's in it root's).
//!
//! Without those rights nothing is held back.

use std::path::{Path, PathBuf};

/// Whether this program runs with administrator rights (Windows) or as root (elsewhere).
pub fn elevated() -> bool {
    #[cfg(windows)]
    {
        // SAFETY: a call without arguments.
        unsafe { windows_sys::Win32::UI::Shell::IsUserAnAdmin() != 0 }
    }
    #[cfg(unix)]
    {
        // SAFETY: a call without arguments.
        unsafe { libc::geteuid() == 0 }
    }
    #[cfg(not(any(windows, unix)))]
    {
        false
    }
}

/// A text value of the machine's registry; none where it is not there.
#[cfg(windows)]
fn machine_text(key: &str, name: &str) -> Option<String> {
    use windows_sys::Win32::System::Registry::{RegGetValueW, HKEY_LOCAL_MACHINE, RRF_RT_REG_SZ};
    let wide = |text: &str| -> Vec<u16> { text.encode_utf16().chain(std::iter::once(0)).collect() };
    let mut buffer = [0u16; 1024];
    let mut size = size_of_val(&buffer) as u32;
    // SAFETY: the value is written into a buffer of the size given.
    let code = unsafe {
        RegGetValueW(
            HKEY_LOCAL_MACHINE,
            wide(key).as_ptr(),
            wide(name).as_ptr(),
            RRF_RT_REG_SZ,
            std::ptr::null_mut(),
            buffer.as_mut_ptr().cast(),
            &mut size,
        )
    };
    if code != 0 {
        return None;
    }
    let end = buffer.iter().position(|unit| *unit == 0).unwrap_or(0);
    Some(String::from_utf16_lossy(&buffer[..end])).filter(|text| !text.is_empty())
}

/// protected_folders: Windows: the folders only administrators may change, the program's own
/// among them.
pub fn protected_folders() -> Vec<PathBuf> {
    let mut folders: Vec<PathBuf> = Vec::new();
    #[cfg(windows)]
    {
        // (read from the registry of the machine: the environment of the process is the
        // user's, who may change it)
        const CURRENT: &str = r"SOFTWARE\Microsoft\Windows\CurrentVersion";
        const NT: &str = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion";
        for (key, name) in [
            (CURRENT, "ProgramFilesDir"),
            (CURRENT, "ProgramFilesDir (x86)"),
            (CURRENT, "ProgramW6432Dir"),
            (NT, "SystemRoot"),
        ] {
            folders.extend(machine_text(key, name).map(PathBuf::from));
        }
    }
    if let Some(own) = std::env::current_exe()
        .ok()
        .and_then(|program| std::fs::canonicalize(program).ok())
        .and_then(|program| program.parent().map(Path::to_path_buf))
    {
        folders.push(own.join("tools"));
        folders.push(own);
    }
    folders
}

/// Elsewhere: whether `path` and every folder above it belong to root (`owners`) and nobody
/// else may write to them (but where the sticky bit keeps what is root's root's).
#[cfg(unix)]
pub fn root_only(path: &Path, owners: &[u32]) -> bool {
    use std::os::unix::fs::MetadataExt;
    for part in path.ancestors() {
        if part.as_os_str().is_empty() {
            continue;
        }
        let Ok(info) = std::fs::metadata(part) else {
            return false;
        };
        if !owners.contains(&info.uid()) {
            return false;
        }
        if info.mode() & 0o022 != 0 && !(part != path && info.mode() & 0o1000 != 0) {
            return false;
        }
    }
    true
}

/// Whether `path` is inside one of `folders`.
pub fn inside(path: &Path, folders: &[PathBuf]) -> bool {
    folders
        .iter()
        .any(|folder| std::fs::canonicalize(folder).is_ok_and(|folder| path.starts_with(folder)))
}

/// _safe: safe_place with the highest rights: on Windows inside `folders` (the protected
/// ones), elsewhere belonging to root all the way up.
pub fn safe(path: &Path, folders: &[PathBuf]) -> bool {
    let Ok(resolved) = std::fs::canonicalize(path) else {
        return false;
    };
    #[cfg(unix)]
    {
        let _ = folders;
        root_only(&resolved, &[0])
    }
    #[cfg(not(unix))]
    {
        inside(&resolved, folders)
    }
}

/// safe_place: whether a program or a folder at `path` may be used with the rights Libre
/// Panel has: always without the highest rights; with them only where nobody without them
/// can change it.
pub fn safe_place(path: &Path) -> bool {
    !elevated() || safe(path, &protected_folders())
}

#[cfg(unix)]
fn runs(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn runs(path: &Path) -> bool {
    path.is_file()
}

/// The program `name` in `folder`, with the endings Windows runs.
fn in_folder(folder: &Path, name: &str) -> Option<PathBuf> {
    let plain = folder.join(name);
    if runs(&plain) && (cfg!(not(windows)) || Path::new(name).extension().is_some()) {
        return Some(plain);
    }
    if cfg!(windows) {
        for ending in ["exe", "cmd", "bat", "com"] {
            let named = folder.join(format!("{name}.{ending}"));
            if runs(&named) {
                return Some(named);
            }
        }
    }
    None
}

/// which: `shutil.which`, but with the highest rights only a program from a safe place: the
/// first one on PATH that is. What was passed over is put into `passed`.
pub fn which(name: &str, passed: &mut Vec<PathBuf>) -> Option<PathBuf> {
    let high = elevated();
    let folders = if high {
        protected_folders()
    } else {
        Vec::new()
    };
    for folder in std::env::split_paths(&std::env::var_os("PATH")?) {
        if folder.as_os_str().is_empty() {
            continue;
        }
        let Some(found) = in_folder(&folder, name) else {
            continue;
        };
        if !high || safe(&found, &folders) {
            return Some(found);
        }
        passed.push(found);
    }
    None
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn what_root_alone_may_change_is_safe() {
        // (as rights.py's _root_only judges: by the owner and the bits of every folder up)
        assert!(root_only(Path::new("/"), &[0]) || !Path::new("/").exists());
        let own = std::env::temp_dir().join(format!("lp-rights-{}", std::process::id()));
        std::fs::create_dir_all(&own).unwrap();
        let file = own.join("program");
        std::fs::write(&file, b"").unwrap();
        // SAFETY: a call without arguments.
        let me = unsafe { libc::geteuid() };
        if me != 0 {
            assert!(!root_only(&file, &[0])); // (this user's own: not root's)
        }
        assert!(!root_only(&own.join("missing"), &[0, me]));
        std::fs::remove_dir_all(&own).unwrap();
        assert!(
            inside(Path::new("/usr/bin/env"), &[PathBuf::from("/usr")])
                || !Path::new("/usr").exists()
        );
        assert!(!inside(Path::new("/usr/bin/env"), &[PathBuf::from("/var")]));
    }
}
