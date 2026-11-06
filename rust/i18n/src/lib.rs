//! lp-i18n: the app's languages as i18n.py has them. English is the source text; German,
//! French, Spanish, Italian, Polish, Portuguese (Brazil), Russian, Japanese and Simplified
//! Chinese come from `locale/<lang>.json`, which stay the files they are. A text is looked up by
//! its English wording; one without a translation stays English.
//!
//! The language is the process's, as Python's is (`set_language`, `t`): the tray, the editor
//! and the panel speak the same one. A `Language` can also be held and asked by itself.

use std::path::Path;
use std::sync::{Arc, RwLock};

use lp_py::{Dict, Value};

mod windows;

pub use windows::WINDOWS_LOCALE;

/// The languages Libre Panel speaks, with their own names.
pub static LANGUAGES: &[(&str, &str)] = &[
    ("en", "English"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("es", "Español"),
    ("it", "Italiano"),
    ("pl", "Polski"),
    ("pt", "Português (Brasil)"),
    ("ru", "Русский"),
    ("ja", "日本語"),
    ("zh", "简体中文"),
];
/// what follows a day's number before a month's name: "27. September", "27 de septiembre"
pub static DAY_BEFORE_MONTH: &[(&str, &str)] = &[("de", "."), ("es", " de"), ("pt", " de")];
/// a day with its month's name written as the language writes it instead: "9月27日"
pub static MONTH_DAY: &[(&str, &str)] = &[("zh", "{month}月{day}日"), ("ja", "{month}月{day}日")];
/// 1,5 rather than 1.5
pub static DECIMAL_COMMA: &[&str] = &["de", "fr", "es", "it", "pl", "pt", "ru"];

fn known(code: &str) -> bool {
    LANGUAGES.iter().any(|&(c, _)| c == code)
}

/// A language and its catalog (empty for English).
#[derive(Clone, Debug, Default)]
pub struct Language {
    code: String,
    catalog: Dict,
}

impl Language {
    /// English: the source text, no catalog.
    pub fn english() -> Language {
        Language {
            code: "en".into(),
            catalog: Dict::new(),
        }
    }

    /// catalog(language): the translation tables of `code` from `locale_dir` (none for
    /// English and for a language Libre Panel does not speak).
    pub fn load(locale_dir: &Path, code: &str) -> Result<Language, String> {
        if code == "en" || !known(code) {
            return Ok(Language {
                code: code.into(),
                catalog: Dict::new(),
            });
        }
        let file = locale_dir.join(format!("{code}.json"));
        let text =
            std::fs::read_to_string(&file).map_err(|e| format!("{}: {e}", file.display()))?;
        match lp_py::json::loads(&text) {
            Ok(Value::Dict(catalog)) => Ok(Language {
                code: code.into(),
                catalog,
            }),
            Ok(_) => Err(format!("{}: not a catalog", file.display())),
            Err(e) => Err(format!("{}: {e}", file.display())),
        }
    }

    pub fn code(&self) -> &str {
        &self.code
    }

    /// Its tables (`messages`, `fields`, `days`, `months` ...), as the catalog's file has them.
    pub fn catalog(&self) -> &Dict {
        &self.catalog
    }

    fn table(&self, name: &str) -> Option<&Dict> {
        self.catalog.get(name).and_then(Value::as_dict)
    }

    /// t(text): the translation of an English text, the text itself where there is none.
    pub fn t(&self, text: &str) -> String {
        match self.table("messages").and_then(|m| m.get(text)) {
            Some(Value::Str(translated)) => translated.clone(),
            _ => text.to_string(),
        }
    }

    /// field_label(key): a field's name as the editor shows it.
    pub fn field_label(&self, key: &str) -> String {
        match self.table("fields").and_then(|m| m.get(key)) {
            Some(Value::Str(label)) => label.clone(),
            _ => key.replace('_', " "),
        }
    }
}

fn current() -> &'static RwLock<Arc<Language>> {
    static CURRENT: std::sync::OnceLock<RwLock<Arc<Language>>> = std::sync::OnceLock::new();
    CURRENT.get_or_init(|| RwLock::new(Arc::new(Language::english())))
}

/// set_language: the language of this process from now on.
pub fn set_language(language: Language) {
    *current().write().unwrap_or_else(|e| e.into_inner()) = Arc::new(language);
}

/// The language of this process.
pub fn language() -> Arc<Language> {
    current().read().unwrap_or_else(|e| e.into_inner()).clone()
}

/// t(text) in the language of this process.
pub fn t(text: &str) -> String {
    language().t(text)
}

/// _windows_language: the name of the user's Windows language (`GetUserDefaultUILanguage`) as
/// Python's locale.windows_locale names it ("" for one it does not know).
pub fn windows_name(id: u32) -> &'static str {
    WINDOWS_LOCALE
        .binary_search_by_key(&id, |&(k, _)| k)
        .map_or("", |i| WINDOWS_LOCALE[i].1)
}

/// system_language: the user's language if Libre Panel speaks it, otherwise English, from
/// LC_ALL, LC_MESSAGES, LANG and LANGUAGE (`env`), on Windows its own language first
/// (`windows`: the name Python's table gives its id). The first real setting counts, even one
/// Libre Panel does not speak.
pub fn system_language(env: &dyn Fn(&str) -> String, windows: Option<&str>) -> String {
    let mut candidates: Vec<String> = ["LC_ALL", "LC_MESSAGES", "LANG"]
        .iter()
        .map(|k| env(k))
        .collect();
    candidates.extend(env("LANGUAGE").split(':').map(str::to_string));
    if let Some(name) = windows {
        candidates.insert(0, name.to_string());
    }
    for value in candidates {
        let code = lp_py::lower(&value.chars().take(2).collect::<String>());
        if known(&code) {
            return code;
        }
        if !value.is_empty() && value != "C" && value != "POSIX" && !value.starts_with("C.") {
            return "en".into();
        }
    }
    "en".into()
}

/// resolve: a language setting ("auto" or one of LANGUAGES) as the code it comes to.
pub fn resolve(setting: Option<&str>, system: impl FnOnce() -> String) -> String {
    match setting {
        Some(code) if known(code) => code.into(),
        _ => system(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_first_real_setting_counts() {
        let env = |vars: &'static [(&'static str, &'static str)]| {
            move |k: &str| {
                vars.iter()
                    .find(|(name, _)| *name == k)
                    .map_or(String::new(), |(_, v)| v.to_string())
            }
        };
        assert_eq!(
            system_language(&env(&[("LANG", "de_DE.UTF-8")]), None),
            "de"
        );
        assert_eq!(
            system_language(&env(&[("LC_ALL", "C"), ("LANG", "fr_FR")]), None),
            "fr"
        );
        assert_eq!(system_language(&env(&[("LANG", "sv_SE")]), None), "en");
        assert_eq!(
            system_language(&env(&[("LANGUAGE", "C.UTF-8:ja")]), None),
            "ja"
        );
        assert_eq!(system_language(&env(&[]), Some("pt_BR")), "pt");
        assert_eq!(windows_name(0x0407), "de_DE");
        assert_eq!(windows_name(0xffff), "");
        assert_eq!(resolve(Some("auto"), || "it".into()), "it");
        assert_eq!(resolve(Some("pl"), || "it".into()), "pl");
    }
}
