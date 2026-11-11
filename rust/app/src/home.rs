//! Where the app's own files are: the themes, fonts and languages that come with it.

use std::path::{Path, PathBuf};

use lp_theme::versions::Places;

/// The app's own files and the person's.
#[derive(Clone, Debug)]
pub struct Home {
    /// the folder with `themes`, `fonts` and `locale` in it
    pub data: PathBuf,
    /// config.toml's folder
    pub settings: PathBuf,
}

fn holds_data(folder: &Path) -> bool {
    folder.join("themes").is_dir() && folder.join("fonts").is_dir()
}

impl Home {
    /// The app's files: the folder LIBRE_PANEL_DATA names; else `share/libre-panel` beside
    /// the program or beside its `bin`; else, for a program built in its source tree,
    /// `src/libre_panel` of that tree.
    pub fn find() -> Result<Home, String> {
        let settings = lp_config::dirs::config_dir();
        if let Some(named) = std::env::var_os("LIBRE_PANEL_DATA").filter(|v| !v.is_empty()) {
            let data = PathBuf::from(named);
            return if holds_data(&data) {
                Ok(Home { data, settings })
            } else {
                Err(format!(
                    "LIBRE_PANEL_DATA: no themes and fonts in {}",
                    data.display()
                ))
            };
        }
        let program = std::env::current_exe().map_err(|e| e.to_string())?;
        let program = std::fs::canonicalize(&program).unwrap_or(program);
        let beside = program.parent().unwrap_or(Path::new("."));
        let mut tried = vec![beside.join("share").join("libre-panel")];
        if let Some(above) = beside.parent() {
            tried.push(above.join("share").join("libre-panel"));
        }
        tried.extend(
            beside
                .ancestors()
                .map(|folder| folder.join("src").join("libre_panel")),
        );
        match tried.into_iter().find(|folder| holds_data(folder)) {
            Some(data) => Ok(Home { data, settings }),
            None => Err(format!(
                "the themes and fonts of Libre Panel were not found beside {} \
                 (LIBRE_PANEL_DATA names their folder)",
                program.display()
            )),
        }
    }

    pub fn fonts(&self) -> PathBuf {
        self.data.join("fonts")
    }

    pub fn locale(&self) -> PathBuf {
        self.data.join("locale")
    }

    pub fn config_file(&self) -> PathBuf {
        self.settings.join(lp_config::dirs::CONFIG_FILENAME)
    }

    /// Where themes are looked for: the person's own, the plugins', then those that come
    /// with the app.
    pub fn places(&self) -> Places {
        Places {
            user: self.settings.join("themes"),
            plugins: crate::plugins::theme_folders(&self.settings),
            builtin: self.data.join("themes"),
        }
    }
}
