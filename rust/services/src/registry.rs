//! Windows' registry as the services read it (Python's winreg): the values of a key and the
//! names of the keys in it. Only reads. The services take a `Registry`, so tests bring one
//! made up; elsewhere than on Windows there is none.

use std::collections::HashMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Hive {
    CurrentUser,
    LocalMachine,
}

/// Which view of the machine's keys: the process's own, the 64-bit or the 32-bit one.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum View {
    Own,
    Wide,
    Narrow,
}

/// A value as winreg gives it.
#[derive(Clone, Debug, PartialEq)]
pub enum Entry {
    Text(String),
    Number(u64),
    /// bytes, lists of texts: nothing the services read
    Other,
}

impl Entry {
    /// `str(value or "")`.
    pub fn text(&self) -> String {
        match self {
            Entry::Text(text) => text.clone(),
            Entry::Number(0) | Entry::Other => String::new(),
            Entry::Number(number) => number.to_string(),
        }
    }
}

pub trait Registry: Send + Sync {
    /// QueryValueEx of a key's value: None where the key or the value is not there.
    fn value(&self, hive: Hive, view: View, key: &str, name: &str) -> Option<Entry>;

    /// EnumKey: the names of the keys in a key, None where it cannot be opened.
    fn subkeys(&self, hive: Hive, view: View, key: &str) -> Option<Vec<String>>;
}

/// A key made up: its values and the names of the keys in it.
pub type Key = (HashMap<String, Entry>, Vec<String>);

/// A registry made up: by hive, view and key (in lower case) its values and its keys' names.
#[derive(Clone, Debug, Default)]
pub struct MadeUp {
    pub keys: HashMap<(Hive, View, String), Key>,
}

impl MadeUp {
    fn key(&self, hive: Hive, view: View, key: &str) -> Option<&Key> {
        self.keys.get(&(hive, view, key.to_lowercase()))
    }
}

impl Registry for MadeUp {
    fn value(&self, hive: Hive, view: View, key: &str, name: &str) -> Option<Entry> {
        self.key(hive, view, key)?.0.get(name).cloned()
    }

    fn subkeys(&self, hive: Hive, view: View, key: &str) -> Option<Vec<String>> {
        Some(self.key(hive, view, key)?.1.clone())
    }
}

/// This computer's registry, where it has one.
pub fn system() -> Option<Box<dyn Registry>> {
    #[cfg(windows)]
    {
        Some(Box::new(windows::System))
    }
    #[cfg(not(windows))]
    {
        None
    }
}

#[cfg(windows)]
mod windows {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegEnumKeyExW, RegOpenKeyExW, RegQueryValueExW, HKEY, HKEY_CURRENT_USER,
        HKEY_LOCAL_MACHINE, KEY_READ, KEY_WOW64_32KEY, KEY_WOW64_64KEY, REG_DWORD, REG_EXPAND_SZ,
        REG_QWORD, REG_SZ,
    };

    use super::{Entry, Hive, Registry, View};

    pub struct System;

    struct Key(HKEY);

    impl Drop for Key {
        fn drop(&mut self) {
            // SAFETY: the key was opened by RegOpenKeyExW and is closed once.
            unsafe { RegCloseKey(self.0) };
        }
    }

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(Some(0)).collect()
    }

    fn open(hive: Hive, view: View, key: &str) -> Option<Key> {
        let root = match hive {
            Hive::CurrentUser => HKEY_CURRENT_USER,
            Hive::LocalMachine => HKEY_LOCAL_MACHINE,
        };
        let access = KEY_READ
            | match view {
                View::Own => 0,
                View::Wide => KEY_WOW64_64KEY,
                View::Narrow => KEY_WOW64_32KEY,
            };
        let mut opened: HKEY = std::ptr::null_mut();
        // SAFETY: the name ends with a zero and the key is written to `opened` on success.
        let code = unsafe { RegOpenKeyExW(root, wide(key).as_ptr(), 0, access, &mut opened) };
        (code == ERROR_SUCCESS).then_some(Key(opened))
    }

    impl Registry for System {
        fn value(&self, hive: Hive, view: View, key: &str, name: &str) -> Option<Entry> {
            let key = open(hive, view, key)?;
            let name = wide(name);
            let (mut kind, mut size) = (0u32, 0u32);

            // SAFETY: first the size is asked for, then the value is read into a buffer of
            // at least that size (in u16s, so that a text is aligned).
            unsafe {
                let code = RegQueryValueExW(
                    key.0,
                    name.as_ptr(),
                    std::ptr::null(),
                    &mut kind,
                    std::ptr::null_mut(),
                    &mut size,
                );
                if code != ERROR_SUCCESS {
                    return None;
                }
                let mut data = vec![0u16; (size as usize).div_ceil(2) + 1];
                let mut room = (data.len() * 2) as u32;
                let code = RegQueryValueExW(
                    key.0,
                    name.as_ptr(),
                    std::ptr::null(),
                    &mut kind,
                    data.as_mut_ptr().cast(),
                    &mut room,
                );
                if code != ERROR_SUCCESS {
                    return None;
                }
                let bytes = std::slice::from_raw_parts(data.as_ptr().cast::<u8>(), room as usize);
                Some(match kind {
                    REG_SZ | REG_EXPAND_SZ => {
                        let text = &data[..room as usize / 2];
                        let end = text
                            .iter()
                            .position(|unit| *unit == 0)
                            .unwrap_or(text.len());
                        Entry::Text(String::from_utf16_lossy(&text[..end]))
                    }
                    REG_DWORD if bytes.len() >= 4 => {
                        Entry::Number(u64::from(u32::from_le_bytes([
                            bytes[0], bytes[1], bytes[2], bytes[3],
                        ])))
                    }
                    REG_QWORD if bytes.len() >= 8 => {
                        let mut number = [0u8; 8];
                        number.copy_from_slice(&bytes[..8]);
                        Entry::Number(u64::from_le_bytes(number))
                    }
                    _ => Entry::Other,
                })
            }
        }

        fn subkeys(&self, hive: Hive, view: View, key: &str) -> Option<Vec<String>> {
            let key = open(hive, view, key)?;
            let mut names = Vec::new();
            loop {
                let mut name = [0u16; 256];
                let mut length = name.len() as u32;

                // SAFETY: the name is written into a buffer of the length given.
                let code = unsafe {
                    RegEnumKeyExW(
                        key.0,
                        names.len() as u32,
                        name.as_mut_ptr(),
                        &mut length,
                        std::ptr::null(),
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                    )
                };
                if code != ERROR_SUCCESS {
                    return Some(names); // no more
                }
                names.push(String::from_utf16_lossy(&name[..length as usize]));
            }
        }
    }
}
