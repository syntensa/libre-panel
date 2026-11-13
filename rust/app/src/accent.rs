//! The accent colour every theme may take instead of its own (accent.py; config.toml's
//! `accent`): the one Windows shows (`"system"`) or one of the person's (`"#e0457b"`).
//!
//! Windows keeps its accent colour in the registry (DWM's AccentColor, as 0xAABBGGRR). One
//! that is too dark to stand out on a panel's dark ground is lightened as far as it needs
//! (lp_pixels::colour::readable).

pub const SYSTEM: &str = "system";

/// system_accent: Windows' accent colour, made readable on a dark panel; None elsewhere, or
/// where Windows does not say.
#[cfg(windows)]
pub fn system_accent() -> Option<String> {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{
        RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_DWORD, RRF_RT_REG_QWORD,
    };

    let wide = |text: &str| -> Vec<u16> { text.encode_utf16().chain(Some(0)).collect() };
    let (key, name) = (wide(r"Software\Microsoft\Windows\DWM"), wide("AccentColor"));
    // (a number of 4 or 8 bytes, as winreg gives an int for; anything else is no colour)
    let mut value = 0u64;
    let mut size = std::mem::size_of::<u64>() as u32;
    // SAFETY: the strings end in NUL; the value is read into `size` bytes of `value`.
    let read = unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            name.as_ptr(),
            RRF_RT_REG_DWORD | RRF_RT_REG_QWORD,
            std::ptr::null_mut(),
            std::ptr::from_mut(&mut value).cast(),
            &mut size,
        )
    };
    if read != ERROR_SUCCESS {
        return None;
    }
    Some(of_registry(value))
}

#[cfg(not(windows))]
pub fn system_accent() -> Option<String> {
    None
}

/// The colour of the registry's number (0xAABBGGRR), readable on a dark panel.
#[cfg_attr(not(windows), allow(dead_code))]
fn of_registry(value: u64) -> String {
    let (red, green, blue) = (value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF);
    let colour = format!("#{red:02X}{green:02X}{blue:02X}");
    lp_pixels::colour::readable(&colour).unwrap_or(colour)
}

/// accent_of: the colour config.toml's `accent` stands for now: None for the themes' own.
pub fn accent_of(choice: &str) -> Option<String> {
    if choice == SYSTEM {
        return system_accent();
    }
    let hex = choice.len() == 7
        && choice.starts_with('#')
        && choice[1..].chars().all(|c| c.is_ascii_hexdigit());
    hex.then(|| choice.to_ascii_uppercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_registrys_number_is_read_as_blue_green_red() {
        // (as accent.py's readable makes them: Windows' default blue #0078D7 and a dark red
        // lightened to the lightness a dark ground needs, their hue kept; a light one as it is)
        assert_eq!(of_registry(0xFFD7_7800), "#008EFF");
        assert_eq!(of_registry(0xFF00_0080), "#FF0000");
        assert_eq!(of_registry(0), "#808080");
        assert_eq!(of_registry(0xFF80_FFFF), "#FFFF80");
    }

    #[test]
    fn a_choice_stands_for_its_colour() {
        assert_eq!(accent_of("#e0457b").as_deref(), Some("#E0457B"));
        assert_eq!(accent_of(""), None);
        assert_eq!(accent_of("#12345"), None);
        assert_eq!(accent_of("#12345g"), None);
        #[cfg(not(windows))]
        assert_eq!(accent_of("system"), None);
    }
}
