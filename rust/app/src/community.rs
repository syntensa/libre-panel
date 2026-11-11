//! The themes the community shares, for Start → Themes (editor/community.py). The project's
//! GitHub keeps a list of them, `community/themes/index.json`: each entry names a theme's .zip
//! and its preview beside the list, and the zip's SHA-256. The list is asked for only when the
//! person opens *Community themes*, kept an hour, and each preview as long. *Install*
//! downloads the zip from beside the list (a redirect to another host is not followed), checks
//! its SHA-256 and that the theme in it is made for the sizes the list says, and puts it in
//! place as an import of the file does (share.rs); one made for another panel is adapted to
//! the person's as it is put in place, where they ask for it. *How it looks on* draws it so,
//! with the demo readings, before anything is installed.

use std::sync::Mutex;
use std::time::Instant;

use lp_devices::models::find_model;
use lp_py::regex::Regex;
use lp_py::{Dict, Value};
use lp_theme::versions::Places;
use lp_theme::Env;

use crate::share::{self, Refused};

const INDEX_URL: &str =
    "https://raw.githubusercontent.com/syntensa/libre-panel/main/community/themes/index.json";
const INDEX_FORMAT: &str = "libre-panel-community/1";
const TIMEOUT_S: f64 = 10.0;
/// the list and the previews, kept an hour
const KEEP_S: f64 = 3600.0;
/// after no answer: asked again a minute later, not at each look
const RETRY_S: f64 = 60.0;
const MAX_INDEX: usize = 1024 * 1024;
const MAX_PREVIEW: usize = 2 * 1024 * 1024;
const MAX_ENTRIES: usize = 500;
/// at most so many previews kept at once (the oldest goes)
const KEPT_PREVIEWS: usize = 100;
/// downloads kept for a look and an install after it (the oldest goes)
const KEPT_ZIPS: usize = 4;
const TEXTS: [(&str, usize); 3] = [("name", 80), ("author", 80), ("description", 400)];

/// What a request answers where it gives no theme: its status and the error.
pub type Failed = (u16, String);

/// An entry of the list, checked.
#[derive(Clone, Debug)]
struct Entry {
    id: String,
    name: String,
    author: String,
    description: String,
    sizes: Vec<(i64, i64)>,
    zip: String,
    preview: String,
    sha256: String,
}

fn chars(text: &str) -> Vec<char> {
    text.chars().collect()
}

fn pattern(text: &str) -> Regex {
    Regex::new(text, false).expect("a pattern that reads")
}

/// The list's address: the project's (LP_COMMUNITY_INDEX: the tests' own server).
fn index_url() -> String {
    std::env::var("LP_COMMUNITY_INDEX")
        .ok()
        .filter(|url| !url.is_empty())
        .unwrap_or_else(|| INDEX_URL.to_string())
}

/// fetch: the bytes at `url`, at most `limit` of them (more is an error), a redirect followed
/// only on the same host.
fn fetch(url: &str, limit: usize) -> Result<Vec<u8>, String> {
    let agent = format!("libre-panel/{}", lp_py::VERSION);
    lp_net::get_same_host(url, &[("User-Agent", &agent)], TIMEOUT_S, limit).map_err(|e| e.text)
}

/// beside: the address of a file the list names: in the list's folder.
fn beside(index_url: &str, path: &str) -> String {
    let folder = index_url
        .rsplit_once('/')
        .map_or(index_url, |(folder, _)| folder);
    format!("{folder}/{path}")
}

/// _entry: an entry of the list as the app shows it, or None for one that is not sound (left
/// out: the others still show).
fn entry_of(raw: &Value) -> Option<Entry> {
    let raw = raw.as_dict()?;
    let mut texts = Vec::new();
    for (key, most) in TEXTS {
        let value = match raw.get(key) {
            None => String::new(),
            Some(Value::Str(value)) if value.chars().count() <= most => value.clone(),
            Some(_) => return None,
        };
        texts.push(lp_py::strip(&value).to_string());
    }
    let sizes = raw.get("sizes")?.as_list()?;
    if sizes.is_empty() || sizes.len() > 16 {
        return None;
    }
    let size_pattern = pattern(r"^(\d{2,5})x(\d{2,5})$");
    let mut found_sizes = Vec::new();
    for size in sizes {
        let text = chars(size.as_str()?);
        let found = size_pattern.fullmatch(&text)?;
        let number = |n| -> Option<i64> {
            let digits: String = found
                .group(&text, n)?
                .chars()
                .map(|c| lp_py::decimal(c).map_or(c, |d| char::from(b'0' + d as u8)))
                .collect();
            digits.parse().ok()
        };
        found_sizes.push((number(1)?, number(2)?));
    }
    let path = pattern(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$");
    let sha = pattern(r"^[0-9a-f]{64}$");
    let text_of = |key: &str| match raw.get(key) {
        Some(Value::Str(text)) => Some(text.clone()),
        _ => None,
    };
    let (zip, preview, sha256) = (text_of("zip")?, text_of("preview")?, text_of("sha256")?);
    let sha256 = lp_py::lower(&sha256);
    let sound = !texts[0].is_empty()
        && path.fullmatch(&chars(&zip)).is_some()
        && zip.ends_with(".zip")
        && path.fullmatch(&chars(&preview)).is_some()
        && preview.ends_with(".png")
        && sha.fullmatch(&chars(&sha256)).is_some();
    if !sound {
        return None;
    }
    // "aurora/aurora.zip": "aurora"
    let file = zip.rsplit('/').next().unwrap_or(&zip);
    let id = file[..file.len() - ".zip".len()].to_string();
    if !lp_theme::model::valid_theme_name(&id) {
        return None;
    }
    let [name, author, description]: [String; 3] = texts.try_into().ok()?;
    Some(Entry {
        id,
        name,
        author,
        description,
        sizes: found_sizes,
        zip,
        preview,
        sha256,
    })
}

/// parse_index: the entries of the community's list (its bytes), each once by its id.
fn parse_index(data: &[u8]) -> Result<Vec<Entry>, String> {
    let text = std::str::from_utf8(data).map_err(|e| e.to_string())?;
    let index = lp_py::json::loads(text).map_err(|e| e.to_string())?;
    let Some(index) = index
        .as_dict()
        .filter(|index| index.get("format") == Some(&Value::from(INDEX_FORMAT)))
    else {
        return Err(format!("not a list of community themes ({INDEX_FORMAT})"));
    };
    let Some(themes) = index.get("themes").and_then(Value::as_list) else {
        return Err("its themes are no list".into());
    };
    let mut entries: Vec<Entry> = Vec::new();
    for raw in themes.iter().take(MAX_ENTRIES) {
        match entry_of(raw) {
            None => crate::logs::log(
                crate::logs::Level::Info,
                "libre_panel.editor.community",
                "community themes: an entry that is not sound is left out",
            ),
            Some(entry) if !entries.iter().any(|kept| kept.id == entry.id) => entries.push(entry),
            Some(_) => {}
        }
    }
    Ok(entries)
}

/// adapted_for: a theme's data adapted to `model_id`'s panel (scaled, in the orientation of
/// its own shape); None where it is made for that panel's size already.
fn adapted_for(raw: &Value, model_id: &str, env: &Env) -> Result<Option<Value>, String> {
    let model = find_model(model_id).ok_or_else(|| format!("unknown panel model {model_id}"))?;
    let theme = lp_theme::model::parse_theme(raw, None, env).map_err(|e| e.msg)?;
    let size = (theme.width, theme.height);
    if size == model.size("landscape") || size == model.size("portrait") {
        return Ok(None);
    }
    let turned = model.size("portrait");
    let upright = theme.height > theme.width && turned.1 > turned.0;
    let way = if upright { "portrait" } else { "landscape" };
    let zero = num_bigint::BigInt::from(0);
    lp_theme::adapt::adapt_theme(raw, model_id, way, "scale", &zero, &zero, env)
        .map(Some)
        .map_err(|e| e.msg)
}

fn public(entry: &Entry) -> Value {
    let sizes = entry
        .sizes
        .iter()
        .map(|(w, h)| Value::List(vec![Value::int(*w), Value::int(*h)]))
        .collect();
    let mut told = Dict::new();
    told.insert("id".into(), Value::Str(entry.id.clone()));
    told.insert("name".into(), Value::Str(entry.name.clone()));
    told.insert("author".into(), Value::Str(entry.author.clone()));
    told.insert("description".into(), Value::Str(entry.description.clone()));
    told.insert("sizes".into(), Value::List(sizes));
    Value::Dict(told)
}

fn say(message: &str) {
    crate::logs::log(
        crate::logs::Level::Info,
        "libre_panel.editor.community",
        message,
    );
}

/// A theme's preview or its look on a panel: by the theme and what it is of.
type Kept<T> = Vec<((String, String), (Instant, T))>;

#[derive(Default)]
struct Inner {
    /// the last list that came, where it came from, when it was asked for, and why not
    entries: Option<Vec<Entry>>,
    url: String,
    asked: Option<Instant>,
    error: String,
    previews: Kept<Vec<u8>>,
    /// an entry's id and SHA-256: when, and the zip
    zips: Kept<Vec<u8>>,
}

/// The list, kept an hour, and its previews; one ask for the list at a time.
#[derive(Default)]
pub struct Community {
    inner: Mutex<Inner>,
}

fn older(seconds: f64, since: Instant) -> bool {
    since.elapsed().as_secs_f64() >= seconds
}

fn keep<T>(kept: &mut Kept<T>, most: usize, key: (String, String), value: T) {
    if kept.len() >= most {
        if let Some(oldest) = (0..kept.len()).min_by_key(|&i| kept[i].1 .0) {
            kept.remove(oldest);
        }
    }
    kept.retain(|(k, _)| *k != key);
    kept.push((key, (Instant::now(), value)));
}

fn share_failed(why: Refused) -> Failed {
    match why {
        Refused::Share(why) | Refused::Theme(why) => (400, why),
        Refused::Os(why) => (409, why),
        Refused::Failed(why) => (500, why),
    }
}

impl Community {
    /// _index: the list: the one kept, or (after an hour, or a minute after no answer)
    /// GitHub's.
    fn index(&self, inner: &mut Inner) -> Option<Vec<Entry>> {
        let url = index_url();
        let wait = if inner.error.is_empty() {
            KEEP_S
        } else {
            RETRY_S
        };
        if url != inner.url || inner.asked.is_none_or(|asked| older(wait, asked)) {
            match fetch(&url, MAX_INDEX).and_then(|data| parse_index(&data)) {
                Ok(entries) => {
                    inner.entries = Some(entries);
                    inner.error = String::new();
                }
                Err(why) => {
                    say(&format!("no list of community themes: {why}"));
                    inner.error = why;
                    if url != inner.url {
                        inner.entries = None;
                    }
                }
            }
            inner.url = url;
            inner.asked = Some(Instant::now());
        }
        inner.entries.clone()
    }

    /// themes: `{"themes": [{id, name, author, description, sizes}], "offline"}`.
    pub fn themes(&self) -> Value {
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let entries = self.index(&mut inner).unwrap_or_default();
        let mut told = Dict::new();
        told.insert(
            "themes".into(),
            Value::List(entries.iter().map(public).collect()),
        );
        told.insert("offline".into(), Value::Bool(!inner.error.is_empty()));
        Value::Dict(told)
    }

    fn find(&self, inner: &mut Inner, theme_id: &str) -> Option<Entry> {
        self.index(inner)?
            .into_iter()
            .find(|entry| entry.id == theme_id)
    }

    /// preview: the PNG an entry names as its preview (kept an hour); None where there is
    /// none.
    pub fn preview(&self, theme_id: &str) -> Option<Vec<u8>> {
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let entry = self.find(&mut inner, theme_id)?;
        let key = (theme_id.to_string(), entry.preview.clone());
        if let Some((_, (when, data))) = inner.previews.iter().find(|(k, _)| *k == key) {
            if !older(KEEP_S, *when) {
                return Some(data.clone());
            }
        }
        let data = match fetch(&beside(&inner.url, &entry.preview), MAX_PREVIEW) {
            Ok(data) => data,
            Err(why) => {
                say(&format!(
                    "no preview of the community theme {theme_id}: {why}"
                ));
                return None;
            }
        };
        if !data.starts_with(b"\x89PNG") {
            return None;
        }
        keep(&mut inner.previews, KEPT_PREVIEWS, key, data.clone());
        Some(data)
    }

    /// _download: an entry and its zip, checked against the list's SHA-256 and the sizes it
    /// names (a few kept a while: a look and an install after it download it once).
    fn download(
        &self,
        inner: &mut Inner,
        theme_id: &str,
        places: &Places,
        env: &Env,
    ) -> Result<(Entry, Vec<u8>), Failed> {
        let Some(entry) = self.find(inner, theme_id) else {
            let said = if inner.error.is_empty() {
                "This theme is not in the community's list (any more)."
            } else {
                "The community themes cannot be reached right now."
            };
            return Err((400, lp_i18n::t(said)));
        };
        let key = (entry.id.clone(), entry.sha256.clone());
        if let Some((_, (when, data))) = inner.zips.iter().find(|(k, _)| *k == key) {
            if !older(KEEP_S, *when) {
                return Ok((entry, data.clone()));
            }
        }
        let data =
            fetch(&beside(&inner.url, &entry.zip), share::MAX_ZIP as usize).map_err(|why| {
                let said = lp_i18n::t("The theme could not be downloaded: {reason}");
                (400, said.replace("{reason}", &why))
            })?;
        let digest: String = {
            use sha2::{Digest, Sha256};
            Sha256::digest(&data)
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect()
        };
        if digest != entry.sha256 {
            return Err((
                400,
                lp_i18n::t(
                    "The download is not the file the community's list names (its checksum \
                     differs): nothing was installed.",
                ),
            ));
        }
        let (width, height) = share::inspect_shared(&data, places, env).map_err(share_failed)?;
        let named = entry
            .sizes
            .iter()
            .any(|size| *size == (width, height) || *size == (height, width));
        if !named {
            let said = lp_i18n::t(
                "The theme in the download is made for {size}, not for the panels the \
                 community's list names: nothing was installed.",
            );
            return Err((400, said.replace("{size}", &format!("{width} × {height}"))));
        }
        keep(&mut inner.zips, KEPT_ZIPS, key, data.clone());
        Ok((entry, data))
    }

    /// install: an entry's zip downloaded, checked against the list's SHA-256 and the theme
    /// put in the person's themes folder: `{"id", "name", "files"}`; with `model_id`, one made
    /// for another size adapted to that panel (`"adapted"`: the size it has now).
    pub fn install(
        &self,
        theme_id: &str,
        model_id: &str,
        places: &Places,
        env: &Env,
    ) -> Result<Dict, Failed> {
        if !model_id.is_empty() && find_model(model_id).is_none() {
            return Err((
                400,
                format!("unknown panel model {}", lp_py::repr(model_id)),
            ));
        }
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let (entry, mut data) = self.download(&mut inner, theme_id, places, env)?;
        drop(inner);
        let mut size = None;
        if !model_id.is_empty() {
            // adapted before it is put in place: it is never there as it came
            data = share::rewritten(&data, |raw| match adapted_for(&raw, model_id, env)? {
                None => Ok(raw),
                Some(adapted) => {
                    let display = adapted.as_dict().and_then(|d| d.get("display")).cloned();
                    let side = |key: &str| {
                        display
                            .as_ref()
                            .and_then(Value::as_dict)
                            .and_then(|d| d.get(key))
                            .cloned()
                            .unwrap_or(Value::None)
                    };
                    size = Some(Value::List(vec![side("width"), side("height")]));
                    Ok(adapted)
                }
            })
            .map_err(share_failed)?;
        }
        let done = share::import_theme(&data, &places.user, places, env, "", &entry.id)
            .map_err(share_failed)?;
        let mut told = Dict::new();
        told.insert("id".into(), Value::Str(done.id));
        told.insert("name".into(), Value::Str(done.name));
        told.insert("files".into(), Value::int(done.files as i64));
        if let Some(size) = size {
            told.insert("adapted".into(), size);
        }
        Ok(told)
    }

    /// look_on: a PNG of the theme as it would show on `model_id`'s panel: adapted where it is
    /// made for another size, drawn with the demo readings; kept an hour. Nothing installed.
    pub fn look_on(
        &self,
        theme_id: &str,
        model_id: &str,
        places: &Places,
        env: &Env,
    ) -> Result<Vec<u8>, Failed> {
        if find_model(model_id).is_none() {
            return Err((
                400,
                format!("unknown panel model {}", lp_py::repr(model_id)),
            ));
        }
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        let (entry, data) = self.download(&mut inner, theme_id, places, env)?;
        let key = (
            theme_id.to_string(),
            format!("on:{model_id}:{}", entry.sha256),
        );
        if let Some((_, (when, png))) = inner.previews.iter().find(|(k, _)| *k == key) {
            if !older(KEEP_S, *when) {
                return Ok(png.clone());
            }
        }
        drop(inner);
        let png = look(&data, model_id, places, env)?;
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        keep(&mut inner.previews, KEPT_PREVIEWS, key, png.clone());
        Ok(png)
    }
}

/// The theme of a zip drawn on `model_id`'s panel with the demo readings, as a PNG.
fn look(data: &[u8], model_id: &str, places: &Places, env: &Env) -> Result<Vec<u8>, Failed> {
    let bad = |why: String| (400, why);
    let place = share::Place::new().map_err(|e| (409, e.to_string()))?;
    let done = share::import_theme(data, &place.0, places, env, "", "").map_err(share_failed)?;
    let folder = place.0.join(&done.id);
    let text = std::fs::read_to_string(folder.join(lp_theme::tables::THEME_FILENAME))
        .map_err(|e| (409, e.to_string()))?;
    let raw = lp_py::json::loads(&text).map_err(|e| bad(e.to_string()))?;
    let raw = adapted_for(&raw, model_id, env)
        .map_err(bad)?
        .unwrap_or(raw);
    let theme = lp_theme::model::parse_theme(&raw, Some(&folder), env).map_err(|e| bad(e.msg))?;
    let options = lp_render::Options {
        preview: true,
        animate: false,
        dither: false,
    };
    let mut renderer = lp_render::Renderer::new(theme, env, options).map_err(|e| bad(e.0))?;
    let utc = lp_py::zones::Zone::utc();
    let zone = lp_py::zones::local().unwrap_or(&utc);
    let mut snapshot = lp_services::demo::demo_snapshot(
        crate::share::now(),
        lp_services::demo::HISTORY_SAMPLES,
        zone,
    )
    .map_err(bad)?;
    snapshot.images = lp_services::demo::pictures(&env.fonts_dir).map_err(bad)?;
    let drawn = renderer.render(snapshot, lp_render::video::monotonic());
    renderer.close();
    let (frame, _boxes) = drawn.map_err(|why| (500, why.0))?;
    let frame = frame
        .convert(lp_pixels::Mode::RGB)
        .map_err(|e| (500, e.to_string()))?;
    lp_devices::png::encode_with(&frame, lp_devices::png::Compression::new(6)).map_err(|e| (500, e))
}
