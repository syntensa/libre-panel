//! The proxy an address goes through, as urllib's getproxies() and proxy_bypass() have it: the
//! one the environment names (ureq reads it, with NO_PROXY) and, on Windows where the
//! environment names none, the one set in Windows' own settings ("Use a proxy server": the
//! registry's Internet Settings), which the Python app went through too. Without it a
//! computer that reaches the web only through that proxy had no weather, no word of a new
//! version and no community themes.

use ureq::Proxy;

/// What Windows' Internet Settings say of a proxy (ProxyEnable, ProxyServer, ProxyOverride).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Settings {
    pub enable: bool,
    pub server: String,
    pub bypass: String,
}

/// Whether an address names its kind before its host ("socks5://host", urllib's
/// `(?:[^/:]+)://`).
fn has_scheme(address: &str) -> bool {
    address
        .split_once("://")
        .is_some_and(|(scheme, _)| !scheme.is_empty() && !scheme.contains(['/', ':']))
}

/// getproxies_registry: the proxy's address for `scheme` ("http" or "https"); None where the
/// settings name none for it.
pub fn of_settings(settings: &Settings, scheme: &str) -> Option<String> {
    if !settings.enable {
        return None;
    }
    let server = settings.server.as_str();
    let one_for_all;
    let server = if !server.contains('=') && !server.contains(';') {
        // (one setting for all protocols)
        one_for_all = format!("http={server};https={server};ftp={server}");
        one_for_all.as_str()
    } else {
        server
    };
    let mut proxies: Vec<(&str, String)> = Vec::new();
    let mut whole = true;
    for part in server.split(';') {
        let Some((protocol, address)) = part.split_once('=') else {
            whole = false; // (an entry urllib cannot read: what came before it stays)
            break;
        };
        let address = match has_scheme(address) {
            true => address.to_string(),
            false if ["http", "https", "ftp"].contains(&protocol) => format!("http://{address}"),
            false if protocol == "socks" => format!("socks://{address}"),
            false => address.to_string(),
        };
        proxies.retain(|(known, _)| *known != protocol);
        proxies.push((protocol, address));
    }
    let named = |proxies: &[(&str, String)], protocol: &str| {
        (proxies.iter())
            .find(|(known, _)| *known == protocol)
            .map(|(_, address)| address.clone())
            .filter(|address| !address.is_empty())
    };
    if whole {
        // a SOCKS proxy is for HTTP(S) too where none of their own is named (Windows' is SOCKS4)
        if let Some(socks) = named(&proxies, "socks") {
            let socks = match socks.strip_prefix("socks://") {
                Some(rest) => format!("socks4://{rest}"),
                None => socks,
            };
            for protocol in ["http", "https"] {
                if named(&proxies, protocol).is_none() {
                    proxies.retain(|(known, _)| *known != protocol);
                    proxies.push((protocol, socks.clone()));
                }
            }
        }
    }
    // (a proxy switched on with no address is none: urllib would ask "http://" and fail)
    named(&proxies, scheme).filter(|address| {
        address
            .split_once("://")
            .map_or(address.as_str(), |(_, host)| host)
            != ""
    })
}

/// fnmatch as Windows has it: `*`, `?` and `[...]`, upper and lower case alike.
fn matches(name: &[char], pattern: &[char]) -> bool {
    let Some((first, rest)) = pattern.split_first() else {
        return name.is_empty();
    };
    match first {
        '*' => (0..=name.len()).any(|skipped| matches(&name[skipped..], rest)),
        '?' => !name.is_empty() && matches(&name[1..], rest),
        '[' => {
            // (a set: "[a-c]", "[!x]"; one that is not closed is the letter itself)
            let negated = rest.first() == Some(&'!');
            let from = usize::from(negated);
            let Some(end) = (rest.iter().skip(from + 1))
                .position(|c| *c == ']')
                .map(|at| at + from + 1)
            else {
                return name.first() == Some(&'[') && matches(&name[1..], rest);
            };
            let Some(letter) = name.first() else {
                return false;
            };
            let set = &rest[from..end];
            let mut found = false;
            let mut at = 0;
            while at < set.len() {
                if at + 2 < set.len() && set[at + 1] == '-' {
                    found |= set[at] <= *letter && *letter <= set[at + 2];
                    at += 3;
                } else {
                    found |= set[at] == *letter;
                    at += 1;
                }
            }
            found != negated && matches(&name[1..], &rest[end + 1..])
        }
        letter => name.first() == Some(letter) && matches(&name[1..], rest),
    }
}

/// _proxy_bypass_winreg_override: whether `host` (with or without its port) goes round the
/// proxy, by Windows' list of exceptions ("www.example.com;*.example.net; 192.168.0.1";
/// "<local>": every name without a dot).
pub fn bypassed(host: &str, bypass: &str) -> bool {
    // (_splitport: a port is what follows the last colon, where that is digits)
    let host = match host.rsplit_once(':') {
        Some((name, port)) if port.chars().all(|c| c.is_ascii_digit()) => name,
        _ => host,
    };
    let folded = |text: &str| -> Vec<char> { text.to_lowercase().chars().collect() };
    let name = folded(host);
    bypass.split(';').map(str::trim).any(|test| match test {
        "<local>" => !host.contains('.'),
        test => matches(&name, &folded(test)),
    })
}

/// (scheme, host with its port) of an address.
fn origin(url: &str) -> (String, &str) {
    let (scheme, rest) = url.split_once("://").unwrap_or(("", url));
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    // (what stands before an "@" is a name and a password, not the host)
    let host = host.rsplit_once('@').map_or(host, |(_, host)| host);
    (scheme.to_lowercase(), host)
}

/// The proxy of `settings` for `url`; None where it names none or the address goes round it.
/// A SOCKS proxy is gone through too (urllib could not, and failed where one was set).
pub fn of_system(settings: &Settings, url: &str) -> Option<Proxy> {
    let (scheme, host) = origin(url);
    let address = of_settings(settings, &scheme)?;
    if !settings.bypass.is_empty() && bypassed(host, &settings.bypass) {
        return None;
    }
    Proxy::new(&address).ok()
}

/// Windows' Internet Settings as they stand; None where they cannot be read.
#[cfg(windows)]
fn settings() -> Option<Settings> {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{
        RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_DWORD, RRF_RT_REG_QWORD, RRF_RT_REG_SZ,
    };

    let wide = |text: &str| -> Vec<u16> { text.encode_utf16().chain(Some(0)).collect() };
    let key = wide(r"Software\Microsoft\Windows\CurrentVersion\Internet Settings");
    let number = |name: &str| -> Option<u64> {
        let name = wide(name);
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
        (read == ERROR_SUCCESS).then_some(value)
    };
    let text = |name: &str| -> Option<String> {
        let name = wide(name);
        let mut size = 0u32;
        // SAFETY: the strings end in NUL; with no buffer the size of the value is asked for.
        let asked = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut size,
            )
        };
        if asked != ERROR_SUCCESS {
            return None;
        }
        let mut letters = vec![0u16; (size as usize).div_ceil(2) + 1];
        let mut size = (letters.len() * 2) as u32;
        // SAFETY: the value is read into `size` bytes of `letters`, which holds as many.
        let read = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                letters.as_mut_ptr().cast(),
                &mut size,
            )
        };
        if read != ERROR_SUCCESS {
            return None;
        }
        let end = letters
            .iter()
            .position(|unit| *unit == 0)
            .unwrap_or(letters.len());
        Some(String::from_utf16_lossy(&letters[..end]))
    };
    let enable = number("ProxyEnable")? != 0;
    if !enable {
        return Some(Settings::default());
    }
    Some(Settings {
        enable,
        server: text("ProxyServer")?,
        bypass: text("ProxyOverride").unwrap_or_default(),
    })
}

/// The proxy for `url`: the environment's (with its NO_PROXY), else on Windows the system's.
pub fn proxy_for(url: &str) -> Option<Proxy> {
    if let Some(proxy) = Proxy::try_from_env() {
        return Some(proxy);
    }
    #[cfg(windows)]
    {
        of_system(&settings()?, url)
    }
    #[cfg(not(windows))]
    {
        let _ = url;
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn set(server: &str) -> Settings {
        Settings {
            enable: true,
            server: server.into(),
            bypass: String::new(),
        }
    }

    #[test]
    fn the_proxy_of_windows_settings_is_read_as_urllib_reads_it() {
        let both = |server: &str| {
            let settings = set(server);
            (
                of_settings(&settings, "http"),
                of_settings(&settings, "https"),
            )
        };
        let some = |address: &str| Some(address.to_string());
        // one for all protocols; one that names its kind keeps it
        assert_eq!(
            both("proxy:8080"),
            (some("http://proxy:8080"), some("http://proxy:8080"))
        );
        assert_eq!(
            both("http://p:3128"),
            (some("http://p:3128"), some("http://p:3128"))
        );
        // one per protocol: the default kind of Windows is HTTP, for https too
        assert_eq!(
            both("http=a:80;https=b:443"),
            (some("http://a:80"), some("http://b:443"))
        );
        assert_eq!(both("https=b:443"), (None, some("http://b:443")));
        // SOCKS stands in for both where they have none (SOCKS4, Windows' kind)
        assert_eq!(
            both("socks=s:1080"),
            (some("socks4://s:1080"), some("socks4://s:1080"))
        );
        assert_eq!(
            both("http=a:80;socks=s:1080"),
            (some("http://a:80"), some("socks4://s:1080"))
        );
        // an entry that cannot be read ends the list: what came before it stays
        assert_eq!(both("http=a:80;"), (some("http://a:80"), None));
        // switched off, or switched on with no address: none
        assert_eq!(both(""), (None, None));
        let off = Settings {
            enable: false,
            ..set("proxy:8080")
        };
        assert_eq!(of_settings(&off, "https"), None);
    }

    #[test]
    fn an_address_goes_round_the_proxy_as_urllib_decides_it() {
        // (Python 3.12's _proxy_bypass_winreg_override said so for each; the one whose case
        // differs is what Windows' fnmatch answers, which folds case)
        let cases = [
            ("localhost", "<local>", true),
            ("nas", "<local>", true),
            ("nas.lan", "<local>", false),
            ("api.open-meteo.com", "*.open-meteo.com", true),
            ("API.Open-Meteo.com:443", "*.open-meteo.com", true),
            ("192.168.1.20", "192.168.*", true),
            ("192.169.1.20", "192.168.*", false),
            (
                "example.com",
                "www.example.com;*.example.net; 192.168.0.1",
                false,
            ),
            (
                "192.168.0.1",
                "www.example.com;*.example.net; 192.168.0.1",
                true,
            ),
            (
                "a.example.net",
                "www.example.com;*.example.net; 192.168.0.1",
                true,
            ),
            ("host1", "host?", true),
            ("host12", "host?", false),
            ("x.internal", "[a-c].internal;[!x].internal", false),
            ("b.internal", "[a-c].internal", true),
            ("github.com", "", false),
            ("github.com", ";", false),
            ("github.com", "*", true),
            ("[::1]:8080", "<local>", true),
            ("nas:8123", "<local>", true),
        ];
        for (host, bypass, round) in cases {
            assert_eq!(bypassed(host, bypass), round, "{host} with {bypass}");
        }
    }

    #[test]
    fn the_systems_proxy_is_for_what_does_not_go_round_it() {
        let settings = Settings {
            bypass: "<local>;192.168.*".into(),
            ..set("proxy.example:8080")
        };
        let through = |url: &str| of_system(&settings, url).is_some();
        assert!(through(
            "https://api.open-meteo.com/v1/forecast?latitude=52"
        ));
        assert!(through("http://example.com/feed"));
        assert!(!through("http://homeassistant:8123/api/states")); // (a name without a dot)
        assert!(!through("http://192.168.1.20:8123/api/states"));
        assert!(!through("http://user:secret@192.168.1.20/x"));
        // a SOCKS proxy is gone through as well: never past the proxy that is set
        assert!(of_system(&set("socks=s:1080"), "https://github.com/").is_some());
        // nothing switched on: nothing
        assert!(of_system(&Settings::default(), "https://github.com/").is_none());
    }
}
