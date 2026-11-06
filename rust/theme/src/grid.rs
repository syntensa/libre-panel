//! The grid modules sit on (theme/modules.py's make_grid): cells of about half the short side,
//! clear of what the panel's bezel hides, none finer than MIN_CELL.

use lp_devices::models::find_model;
use lp_py::{num, Dict, Value};
use num_bigint::BigInt;

/// px: a grid never has cells smaller than this (it has fewer of them instead)
pub const MIN_CELL: i64 = 4;

#[derive(Clone, Debug, PartialEq)]
pub struct Grid {
    pub x: i64,
    pub y: i64,
    pub cell_w: f64,
    pub cell_h: f64,
    pub gap: i64,
    pub columns: i64,
    pub rows: i64,
}

/// A theme's grid settings (columns, rows, gap, margin; 0: automatic).
#[derive(Clone, Copy, Debug, Default)]
pub struct Settings {
    pub columns: i64,
    pub rows: i64,
    pub gap: i64,
    pub margin: i64,
}

fn round(x: f64) -> i64 {
    lp_py::round(x) as i64
}

fn fitting(mut cells: i64, fit: i64) -> i64 {
    while cells > fit.max(48) && cells % 2 == 0 {
        cells /= 2;
    }
    cells.min(fit)
}

/// make_grid: the cells of a `width` x `height` frame.
pub fn make_grid(width: i64, height: i64, given: Settings, model: &str) -> Grid {
    let short = width.min(height);
    let margin = if given.margin != 0 {
        given.margin
    } else {
        4.max(28.min(round(short as f64 * 0.05)))
    };
    let gap = if given.gap != 0 {
        given.gap
    } else {
        3.max(20.min(round(short as f64 * 0.035)))
    };
    let (mut left, mut top, mut right, mut bottom) = (margin, margin, margin, margin);
    if let Some(panel) = find_model(model) {
        let orientation = if width >= height {
            "landscape"
        } else {
            "portrait"
        };
        let [t, r, b, l] = panel.hidden_edges(orientation);
        top += t;
        right += r;
        bottom += b;
        left += l;
    }
    let usable_w = 1.max(width - left - right);
    let usable_h = 1.max(height - top - bottom);
    // max(64, min(240, short / 2))
    let half = short as f64 / 2.0;
    let target = {
        let low = if half < 240.0 { half } else { 240.0 };
        if low > 64.0 {
            low
        } else {
            64.0
        }
    };
    let columns = if given.columns != 0 {
        given.columns
    } else {
        1.max(round(usable_w as f64 / target))
    };
    let rows = if given.rows != 0 {
        given.rows
    } else {
        1.max(round(usable_h as f64 / target))
    };
    let columns = fitting(columns, 1.max((usable_w + gap).div_euclid(MIN_CELL + gap)));
    let rows = fitting(rows, 1.max((usable_h + gap).div_euclid(MIN_CELL + gap)));
    let cell = |usable: i64, n: i64| {
        let c = (usable - (n - 1) * gap) as f64 / n as f64;
        if c > 1.0 {
            c
        } else {
            1.0
        }
    };
    Grid {
        x: left,
        y: top,
        cell_w: cell(usable_w, columns),
        cell_h: cell(usable_h, rows),
        gap,
        columns,
        rows,
    }
}

/// The grid of a theme as its file holds it, made as make_grid makes it of whatever its values
/// are: Python's arithmetic on them (a margin of 10.5 counts in floats, an int of 400 digits as
/// that int, a string fails where Python fails), and the grid's numbers as Python has them.
#[derive(Clone, Debug, PartialEq)]
pub struct Cells {
    pub x: Value,
    pub y: Value,
    pub cell_w: Value,
    pub cell_h: Value,
    pub gap: Value,
    pub columns: Value,
    pub rows: Value,
}

fn fitting_values(cells: Value, fit: &Value) -> num::Result<Value> {
    let mut cells = cells;
    while num::gt(&cells, &num::max(fit, &Value::int(48))?)?
        && num::rem(&cells, &Value::int(2))? == Value::int(0)
    {
        cells = num::floor_div(&cells, &Value::int(2))?;
    }
    num::min(&cells, fit)
}

/// make_grid of a `width` x `height` frame with the settings a file holds (`settings or {}`:
/// none, or a dict; anything else is no mapping).
pub fn make_cells(
    width: &BigInt,
    height: &BigInt,
    settings: Option<&Value>,
    model: &str,
) -> num::Result<Cells> {
    let empty = Dict::new();
    let given = match settings {
        Some(v) if v.truthy() => match v {
            Value::Dict(d) => d,
            other => {
                return Err(num::Error {
                    kind: num::ErrorKind::Type,
                    msg: format!("'{}' object is not a mapping", other.type_name()),
                })
            }
        },
        _ => &empty,
    };
    let get = |k: &str| given.get(k).cloned().unwrap_or_else(|| Value::int(0));
    let int = Value::int;
    let (w, h) = (Value::Int(width.clone()), Value::Int(height.clone()));
    let short = num::min(&w, &h)?;
    let automatic = |share: f64, low: i64, high: i64| -> num::Result<Value> {
        let made = num::round(&num::mul(&short, &Value::Float(share))?)?;
        num::max(&int(low), &num::min(&int(high), &made)?)
    };
    let margin = match get("margin") {
        m if m.truthy() => m,
        _ => automatic(0.05, 4, 28)?,
    };
    let gap = match get("gap") {
        g if g.truthy() => g,
        _ => automatic(0.035, 3, 20)?,
    };
    let (mut left, mut top, mut right, mut bottom) =
        (margin.clone(), margin.clone(), margin.clone(), margin);
    if let Some(panel) = find_model(model) {
        let orientation = if width >= height {
            "landscape"
        } else {
            "portrait"
        };
        let [t, r, b, l] = panel.hidden_edges(orientation);
        top = num::add(&top, &int(t))?;
        right = num::add(&right, &int(r))?;
        bottom = num::add(&bottom, &int(b))?;
        left = num::add(&left, &int(l))?;
    }
    let usable_w = num::max(&int(1), &num::sub(&num::sub(&w, &left)?, &right)?)?;
    let usable_h = num::max(&int(1), &num::sub(&num::sub(&h, &top)?, &bottom)?)?;
    let target = num::max(
        &int(64),
        &num::min(&int(240), &num::true_div(&short, &int(2))?)?,
    )?;
    let count = |key: &str, usable: &Value| -> num::Result<Value> {
        match get(key) {
            n if n.truthy() => Ok(n),
            _ => num::max(&int(1), &num::round(&num::true_div(usable, &target)?)?),
        }
    };
    let columns = count("columns", &usable_w)?;
    let rows = count("rows", &usable_h)?;
    let fit = |usable: &Value| -> num::Result<Value> {
        let across = num::floor_div(&num::add(usable, &gap)?, &num::add(&int(MIN_CELL), &gap)?)?;
        num::max(&int(1), &across)
    };
    let columns = fitting_values(columns, &fit(&usable_w)?)?;
    let rows = fitting_values(rows, &fit(&usable_h)?)?;
    let cell = |usable: &Value, n: &Value| -> num::Result<Value> {
        let between = num::mul(&num::sub(n, &int(1))?, &gap)?;
        let size = num::true_div(&num::sub(usable, &between)?, n)?;
        num::max(&Value::Float(1.0), &size)
    };
    let cell_w = cell(&usable_w, &columns)?;
    let cell_h = cell(&usable_h, &rows)?;
    Ok(Cells {
        x: left,
        y: top,
        cell_w,
        cell_h,
        gap,
        columns,
        rows,
    })
}
