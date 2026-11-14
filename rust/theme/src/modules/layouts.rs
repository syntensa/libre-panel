// The starting layouts per grid and the wishes for other grids (theme/modules.py's _TEMPLATES
// and _WISHES): made from Python's own once, kept here since (tests compare).

/// (kind, col, row, cols, rows, source: "" for none)
pub type Placed = (&'static str, i64, i64, i64, i64, &'static str);

/// A starting layout: its name and its modules.
pub type Layout = (&'static str, &'static [Placed]);

#[rustfmt::skip]
pub static TEMPLATES: &[((i64, i64), &[Layout])] = &[
    ((8, 2), &[
        ("overview", &[("clock", 0, 0, 3, 1, ""), ("network", 0, 1, 3, 1, ""), ("weather", 3, 0, 2, 2, ""), ("ring", 5, 0, 3, 1, "cpu"), ("ring", 5, 1, 3, 1, "gpu")]),
        ("performance", &[("stat", 0, 0, 2, 2, "cpu"), ("ring", 2, 0, 2, 1, "gpu"), ("ring", 2, 1, 2, 1, "mem"), ("bars", 4, 0, 1, 2, ""), ("clock", 5, 0, 3, 1, ""), ("graph", 5, 1, 3, 1, "net")]),
        ("calm", &[("clock", 0, 0, 4, 2, ""), ("weather", 4, 0, 4, 1, ""), ("date", 4, 1, 2, 1, ""), ("system", 6, 1, 2, 1, "")]),
    ]),
    ((3, 2), &[
        ("overview", &[("clock", 0, 0, 2, 1, ""), ("weather", 2, 0, 1, 1, ""), ("ring", 0, 1, 1, 1, "cpu"), ("ring", 1, 1, 1, 1, "mem"), ("network", 2, 1, 1, 1, "")]),
        ("performance", &[("ring", 0, 0, 1, 2, "cpu"), ("stat", 1, 0, 2, 1, "gpu"), ("bars", 1, 1, 1, 1, ""), ("date", 2, 1, 1, 1, "")]),
        ("calm", &[("clock", 0, 0, 3, 1, ""), ("weather", 0, 1, 2, 1, ""), ("date", 2, 1, 1, 1, "")]),
    ]),
    ((2, 2), &[
        ("overview", &[("clock", 0, 0, 2, 1, ""), ("ring", 0, 1, 1, 1, "cpu"), ("ring", 1, 1, 1, 1, "mem")]),
        ("performance", &[("ring", 0, 0, 1, 1, "cpu"), ("ring", 1, 0, 1, 1, "gpu"), ("ring", 0, 1, 1, 1, "mem"), ("ring", 1, 1, 1, 1, "disk")]),
        ("calm", &[("clock", 0, 0, 2, 1, ""), ("weather", 0, 1, 2, 1, "")]),
    ]),
    ((2, 1), &[
        ("overview", &[("stat", 0, 0, 1, 1, "cpu"), ("stat", 1, 0, 1, 1, "mem")]),
        ("performance", &[("ring", 0, 0, 1, 1, "cpu"), ("ring", 1, 0, 1, 1, "gpu")]),
        ("calm", &[("clock", 0, 0, 2, 1, "")]),
    ]),
    ((2, 3), &[
        ("overview", &[("clock", 0, 0, 2, 1, ""), ("stat", 0, 1, 1, 1, "cpu"), ("stat", 1, 1, 1, 1, "mem"), ("graph", 0, 2, 2, 1, "cpu")]),
        ("performance", &[("ring", 0, 0, 1, 1, "cpu"), ("ring", 1, 0, 1, 1, "gpu"), ("ring", 0, 1, 1, 1, "mem"), ("ring", 1, 1, 1, 1, "disk"), ("network", 0, 2, 2, 1, "")]),
        ("calm", &[("clock", 0, 0, 2, 1, ""), ("weather", 0, 1, 2, 1, ""), ("date", 0, 2, 2, 1, "")]),
    ]),
];

/// A wish: (kind, source: "" for none, cols, rows).
pub type Wish = (&'static str, &'static str, i64, i64);

#[rustfmt::skip]
pub static WISHES: &[(&str, &[Wish])] = &[
    ("overview", &[("clock", "", 3, 1), ("weather", "", 2, 2), ("ring", "cpu", 3, 1), ("ring", "gpu", 3, 1), ("network", "", 3, 1), ("ring", "mem", 1, 1), ("bars", "", 1, 1)]),
    ("performance", &[("stat", "cpu", 2, 2), ("ring", "gpu", 2, 1), ("ring", "mem", 2, 1), ("bars", "", 1, 2), ("clock", "", 3, 1), ("graph", "net", 3, 1), ("ring", "disk", 1, 1)]),
    ("calm", &[("clock", "", 4, 2), ("weather", "", 4, 1), ("date", "", 2, 1), ("system", "", 2, 1)]),
];
