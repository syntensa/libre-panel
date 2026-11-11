//! Modules of one panel's theme sent to another panel (distribute.py: Design → a module's
//! menu → Copy to panel 2, Move to panel 2): the theme that panel shows gets them, each on
//! free cells of its grid. A theme of the person's own is written as it is; a built-in one
//! gets a copy of its own first (`lumen-own-companion`, a companion still), which the panel
//! then shows: what comes with Libre Panel is never written over. The modules keep their
//! settings and get a new id where the theme has theirs already.

use std::collections::HashSet;
use std::path::Path;

use lp_devices::models::find_model;
use lp_py::{Dict, Value};
use lp_theme::versions::Places;

/// a built-in theme's copy of the person's own: "slate-own", "lumen-own-companion"
const OWN: &str = "-own";

/// shown_theme: the theme the panel `[[panels]]` at `n` shows: as it says (`seen`, its
/// loop's), else the one config.toml gives it in the version for its size and way up.
pub fn shown_theme(config: &lp_config::Config, n: usize, seen: &str, places: &Places) -> String {
    if !seen.is_empty() {
        return seen.to_string();
    }
    let panel = &config.panels[n];
    let model = find_model(&panel.device.model);
    let name = match panel.theme.as_str() {
        "" => lp_theme::defaults::default_theme(model, places).to_string(),
        name => name.to_string(),
    };
    let way = if panel.device.orientation == "portrait" {
        "portrait"
    } else {
        "landscape"
    };
    lp_theme::versions::theme_for(
        &name,
        &panel.device.orientation,
        model.map(|model| model.size(way)),
        places,
    )
}

/// The end of a companion's name ("-companion", "-companion-vertical", "-companion-1920x480"):
/// theme.model.is_companion's.
fn beside_end(name: &str) -> &str {
    let Some(at) = name.rfind("-companion") else {
        return "";
    };
    let end = &name[at..];
    let rest = &end["-companion".len()..];
    let sized = |rest: &str| {
        let Some(rest) = rest.strip_prefix('-') else {
            return false;
        };
        let Some((w, h)) = rest.split_once('x') else {
            return false;
        };
        let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
        digits(w) && digits(h)
    };
    if rest.is_empty() || rest == "-vertical" || sized(rest) {
        end
    } else {
        ""
    }
}

/// _own_copy_name: a name for the person's copy of the built-in theme `name` that no theme
/// has yet. A companion's is a companion still ("lumen-own-companion-vertical" for
/// "lumen-companion-vertical"), so the first panel goes on leaving out what it shows.
fn own_copy_name(name: &str, places: &Places) -> String {
    let end = beside_end(name);
    let base: String = name[..name.len() - end.len()]
        .chars()
        .take(64usize.saturating_sub(OWN.len() + end.len() + 3))
        .collect();
    let mut candidate = format!("{base}{OWN}{end}");
    let mut number = 2;
    while places.user.join(&candidate).exists() {
        candidate = format!("{base}{OWN}-{number}{end}");
        number += 1;
    }
    candidate
}

/// The first place (column, row) where `cols` x `height` cells are all free.
fn free_cells(
    columns: i64,
    rows: i64,
    taken: &HashSet<(i64, i64)>,
    cols: i64,
    height: i64,
) -> Option<(i64, i64)> {
    for row in 0..=(rows - height) {
        for col in 0..=(columns - cols) {
            let free =
                (col..col + cols).all(|c| (row..row + height).all(|r| !taken.contains(&(c, r))));
            if free {
                return Some((col, row));
            }
        }
    }
    None
}

fn int_of(value: Option<&Value>, default: i64) -> i64 {
    match value {
        Some(Value::Int(n)) => i64::try_from(n).unwrap_or(default),
        Some(Value::Bool(b)) => i64::from(*b),
        _ => default,
    }
}

/// uuid.uuid4().hex[:8]
fn short_id() -> String {
    let mut bytes = [0u8; 4];
    let _ = getrandom::getrandom(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// place_modules: put `modules` into the theme `data` (its JSON, as `theme` was loaded from
/// it), each on free cells of its grid, in its own size where that fits, else in a smaller
/// one. The ids of those put in; the error a person reads when one finds no room.
pub fn place_modules(
    data: &mut Dict,
    modules: &[Value],
    theme: &lp_theme::Theme,
) -> Result<Vec<String>, String> {
    let grid = lp_theme::grid::make_grid(
        theme.width,
        theme.height,
        crate::drawings::grid_settings(theme),
        &theme.model,
    );
    let name = match data.get("name") {
        Some(Value::Str(name)) if !name.is_empty() => name.clone(),
        _ => theme.name.clone(),
    };
    if !matches!(data.get("widgets"), Some(Value::List(_))) {
        data.insert("widgets".into(), Value::List(Vec::new()));
    }
    let Some(Value::List(widgets)) = data.get_mut("widgets") else {
        return Err("widgets".into());
    };
    let mut taken = HashSet::new();
    for widget in widgets.iter().filter_map(Value::as_dict) {
        let module = widget.get("type").and_then(Value::as_str) == Some("module");
        if module && widget.contains_key("col") && widget.contains_key("row") {
            let (col, row) = (int_of(widget.get("col"), 0), int_of(widget.get("row"), 0));
            let cols = int_of(widget.get("cols"), 1);
            let height = int_of(widget.get("rows"), 1);
            for c in col..col + cols {
                for r in row..row + height {
                    taken.insert((c, r));
                }
            }
        }
    }
    let mut ids: HashSet<String> = widgets
        .iter()
        .filter_map(|widget| widget.as_dict()?.get("id").and_then(Value::as_str))
        .map(str::to_string)
        .collect();
    let mut placed = Vec::new();
    for module in modules {
        let Some(module) = module.as_dict() else {
            return Err(lp_i18n::t("only modules go to another panel"));
        };
        if module.get("type").and_then(Value::as_str) != Some("module") {
            return Err(lp_i18n::t("only modules go to another panel"));
        }
        let wanted = (
            int_of(module.get("cols"), 1).min(grid.columns),
            int_of(module.get("rows"), 1).min(grid.rows),
        );
        let half = (wanted.0.div_euclid(2)).max(1);
        let sizes = [wanted, (half, wanted.1), (half, 1), (1, 1)];
        let spot = sizes.iter().find_map(|&(cols, height)| {
            free_cells(grid.columns, grid.rows, &taken, cols, height)
                .map(|spot| (spot, cols, height))
        });
        let Some(((col, row), cols, height)) = spot else {
            return Err(lp_i18n::t(
                "there are no free cells for it on “{theme}”: make room there in Design first",
            )
            .replace("{theme}", &name));
        };
        let mut sent = module.clone();
        sent.insert("col".into(), Value::int(col));
        sent.insert("row".into(), Value::int(row));
        sent.insert("cols".into(), Value::int(cols));
        sent.insert("rows".into(), Value::int(height));
        let id = match sent.get("id") {
            Some(Value::Str(id)) if !id.is_empty() && !ids.contains(id) => id.clone(),
            _ => {
                let id = format!("m-{}", short_id());
                sent.insert("id".into(), Value::Str(id.clone()));
                id
            }
        };
        ids.insert(id.clone());
        for c in col..col + cols {
            for r in row..row + height {
                taken.insert((c, r));
            }
        }
        widgets.push(Value::Dict(sent));
        placed.push(id);
    }
    Ok(placed)
}

fn canonical(path: &Path) -> std::path::PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

/// send: `modules` to the panel `number` (2 and on, as *Which is which?* numbers them),
/// `seen` the theme its loop says it shows: `{"theme", "copied", "ids"}`. A built-in theme
/// gets a copy of its own first, and the panel shows it from then on (`[[panels]]`). The
/// error a person reads where it cannot be done.
pub fn send(
    modules: &[Value],
    number: i64,
    seen: &str,
    settings: &crate::settings::Settings,
    env: &lp_theme::Env,
) -> Result<Dict, String> {
    let config = lp_config::load_config(settings.config_file, lp_config::platform())
        .map_err(|why| why.msg)?;
    let places = settings.home.places();
    let n = usize::try_from(number - 2)
        .ok()
        .filter(|n| *n < config.panels.len())
        .ok_or_else(|| {
            lp_i18n::t("there is no panel {number}").replace("{number}", &number.to_string())
        })?;
    let name = shown_theme(&config, n, seen, &places);
    let folder = lp_theme::versions::find_theme(&name, &places).map_err(|why| why.msg)?;
    let theme = lp_theme::model::load_theme(&folder, env).map_err(|why| why.msg)?;
    let file = folder.join(lp_theme::tables::THEME_FILENAME);
    let text = std::fs::read_to_string(&file).map_err(|why| why.to_string())?;
    let Ok(Value::Dict(mut data)) = lp_py::json::loads(&text) else {
        return Err(format!("{}: not a theme", file.display()));
    };
    let built_in = !canonical(&folder).starts_with(canonical(&places.user));
    let target = if built_in {
        own_copy_name(&name, &places)
    } else {
        name.clone()
    };
    if built_in {
        let own = match data.get("name") {
            Some(Value::Str(own)) if !own.is_empty() => own.clone(),
            _ => name.clone(),
        };
        let mine = lp_i18n::t("{name} (mine)").replace("{name}", &own);
        data.insert("name".into(), Value::Str(mine));
    }
    let ids = place_modules(&mut data, modules, &theme)?;
    if !lp_theme::model::valid_theme_name(&target) {
        return Err(format!("invalid theme name {}", lp_py::repr(&target)));
    }
    let source = built_in.then_some(name.as_str());
    lp_theme::versions::save_theme(&target, &Value::Dict(data), source, &places, env)
        .map_err(|why| why.msg)?;
    if built_in {
        // the panel shows the copy from now on, its own
        let read = settings.read().map_err(|why| why.message)?;
        let panels = read
            .as_dict()
            .and_then(|read| read.get("config"))
            .and_then(Value::as_dict)
            .and_then(|config| config.get("panels"))
            .and_then(Value::as_list)
            .map(<[Value]>::to_vec)
            .unwrap_or_default();
        let panels: Vec<Value> = panels
            .into_iter()
            .enumerate()
            .map(|(i, panel)| match (i == n, panel) {
                (true, Value::Dict(mut panel)) => {
                    panel.insert("theme".into(), Value::Str(target.clone()));
                    panel.insert("follow".into(), Value::Bool(false));
                    Value::Dict(panel)
                }
                (_, panel) => panel,
            })
            .collect();
        let mut set = Dict::new();
        set.insert("panels".into(), Value::List(panels));
        let mut payload = Dict::new();
        payload.insert("set".into(), Value::Dict(set));
        settings
            .write(&Value::Dict(payload))
            .map_err(|why| why.message)?;
    }
    let mut told = Dict::new();
    told.insert("theme".into(), Value::Str(target));
    told.insert("copied".into(), Value::Bool(built_in));
    told.insert(
        "ids".into(),
        Value::List(ids.into_iter().map(Value::Str).collect()),
    );
    Ok(told)
}
