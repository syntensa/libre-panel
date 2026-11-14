//! render/motion.py: what moves on the panel, from frame to frame and from theme to theme.
//! Values glide from reading to reading (a critically damped spring per sensor), threshold
//! colours fade instead of jumping, and graphs may draw from a history of the gliding values,
//! RATE points a second. Everything runs on the planned time of the frame being drawn, in
//! doubles as Python's floats, each sum and product in Python's order: the same values to the
//! bit.

use std::collections::{HashMap, VecDeque};

use lp_py::Value;

/// History points per second, whatever the frame rate.
pub const RATE: f64 = 25.0;
/// A value off the panel this long starts again where it is...
const PAUSE_S: f64 = 0.25;
/// ... but not over the pauses of the PNG path (up to 0.5 s).
const IDLE_S: f64 = 1.0;
/// A threshold colour takes this long per step.
const FADE_S: f64 = 0.18;
/// A history no graph asked for this long is dropped (and a spring not drawn so long).
const KEEP_S: f64 = 600.0;
/// The longest history: ten minutes.
pub(crate) const MAX_POINTS: usize = 600 * 25;

/// grid: the history point at or just before `t`.
pub fn grid(t: f64) -> i64 {
    (t * RATE + 1e-6).floor() as i64
}

/// number: a reading's value as a number for gliding and graphs (None for none, a bool, a text,
/// an int past a float's range, NaN or an infinity).
pub fn number(value: &Value) -> Option<f64> {
    let x = match value {
        Value::Int(n) => lp_py::num::int_to_float(n).ok()?,
        Value::Float(f) => *f,
        _ => return None,
    };
    x.is_finite().then_some(x)
}

/// A piece of a spring's glide: from `start` on, its value is target + (error + (speed + omega *
/// error) * dt) * exp(-omega * dt); omega 0: it stands at `target`; no target: no reading.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Part {
    made: u64,
    start: f64,
    target: Option<f64>,
    error: f64,
    speed: f64,
    omega: f64,
}

/// Spring: a value gliding from reading to reading, kept piece by piece (one per new reading),
/// so its value is known at any moment and, until the next reading, also in the future.
#[derive(Clone, Debug, Default)]
pub struct Spring {
    pieces: Vec<Part>,
    target: Option<f64>,
    /// the frame it was last drawn in
    seen: Option<f64>,
    /// pieces so far
    made: u64,
}

/// (value, speed) at `t` of a glide kept as `pieces`; None without a reading then.
fn state_in(pieces: &[Part], t: f64) -> Option<(f64, f64)> {
    let p = pieces.iter().rev().find(|p| p.start <= t)?;
    let target = p.target?;
    if p.omega <= 0.0 {
        return Some((target, 0.0));
    }
    let dt = t - p.start;
    let decay = (-p.omega * dt).exp();
    let curve = p.speed + p.omega * p.error;
    Some((
        target + (p.error + curve * dt) * decay,
        (p.speed - p.omega * curve * dt) * decay,
    ))
}

/// A spring's pieces as they stood when taken (a graph's strip drew ahead with them): the
/// value they glide through at any time, and which pieces they were.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Course {
    pieces: Vec<Part>,
}

impl Course {
    /// The value at `t` (None without a reading then).
    pub fn at(&self, t: f64) -> Option<f64> {
        state_in(&self.pieces, t).map(|s| s.0)
    }
}

impl Spring {
    fn piece(&self, t: f64) -> Option<&Part> {
        self.pieces.iter().rev().find(|p| p.start <= t)
    }

    /// (value, speed) at `t`; None without a reading then.
    pub fn state(&self, t: f64) -> Option<(f64, f64)> {
        state_in(&self.pieces, t)
    }

    pub fn at(&self, t: f64) -> Option<f64> {
        self.state(t).map(|s| s.0)
    }

    fn add(&mut self, start: f64, target: Option<f64>, error: f64, speed: f64, omega: f64) {
        self.pieces.push(Part {
            made: self.made,
            start,
            target,
            error,
            speed,
            omega,
        });
        self.made += 1;
    }

    /// Stand at `target` from `now` on, without gliding there.
    fn restart(&mut self, now: f64, target: Option<f64>) {
        self.pieces.clear();
        self.add(now, target, 0.0, 0.0, 0.0);
    }

    /// Drop the pieces that end before `t` (the one still in force at `t` stays).
    fn forget_before(&mut self, t: f64) {
        // (the last that starts by `t`, as Python looks through them all)
        let keep = self.pieces.iter().rposition(|p| p.start <= t).unwrap_or(0);
        if keep > 0 {
            self.pieces.drain(..keep);
        }
    }
}

/// History: a sensor's gliding value RATE times a second (None: no reading then), newest last;
/// `last` the grid number of the newest point.
#[derive(Clone, Debug)]
pub struct History {
    pub points: VecDeque<Option<f64>>,
    pub maxlen: usize,
    pub last: Option<i64>,
    /// when a graph last asked for it
    used: f64,
}

impl History {
    fn new(size: usize) -> History {
        History {
            points: VecDeque::new(),
            maxlen: size,
            last: None,
            used: 0.0,
        }
    }

    /// deque.append with its maxlen: the oldest goes.
    fn push(&mut self, point: Option<f64>) {
        if self.maxlen == 0 {
            return;
        }
        if self.points.len() == self.maxlen {
            self.points.pop_front();
        }
        self.points.push_back(point);
    }

    /// The points from grid number `first` on (None before the oldest).
    pub fn since(&self, first: i64) -> Vec<Option<f64>> {
        let Some(last) = self.last else {
            return Vec::new();
        };
        let oldest = last - self.points.len() as i64 + 1;
        let missing = (oldest.min(last + 1) - first).max(0) as usize;
        let skip = (first - oldest).max(0) as usize;
        let mut out = vec![None; missing];
        out.extend(self.points.iter().skip(skip).copied());
        out
    }
}

/// What a threshold colour's fade is kept by: the sensor and the widget's rules (their
/// thresholds and colours), equal as Python's tuples are (1 == 1.0).
pub type FadeKey = (String, Vec<(Value, String)>);

/// Motion: springs, threshold fades and histories, by sensor key. A renderer begins each frame
/// (begin); its widgets then ask for gliding values (glide), colour steps (fade) and, for graphs
/// drawn continuously, histories (history).
#[derive(Clone, Debug, Default)]
pub struct Motion {
    springs: HashMap<String, Spring>,
    /// (key, (step shown, when)): few, looked up by Python's equality
    fades: Vec<(FadeKey, (f64, f64))>,
    histories: HashMap<String, History>,
    /// a fine course: the last taken, its last point
    fed: HashMap<String, (String, i64)>,
    /// the frame before this one
    previous: Option<f64>,
    /// the frame being drawn
    now: Option<f64>,
}

impl Motion {
    /// A frame at `now`: the histories take their points up to it (each sensor's value gliding
    /// towards its reading in `readings`); springs not drawn for KEEP_S go, the others forget
    /// their pieces of more than a second before they were last drawn.
    pub fn begin(
        &mut self,
        readings: &dyn Fn(&str) -> Option<f64>,
        now: f64,
        glide: f64,
        lead: f64,
    ) {
        if Some(now) != self.now {
            (self.previous, self.now) = (self.now, Some(now));
        }
        let last = grid(now);
        let keys: Vec<String> = self.histories.keys().cloned().collect();
        for key in keys {
            let used = self.histories[&key].used;
            if now - used > KEEP_S {
                self.histories.remove(&key);
                self.fed.remove(&key);
                continue;
            }
            if !self.fed.contains_key(&key) {
                // (a fine course stands in its spring: no glide)
                self.glide(&key, readings(&key), now, glide, lead);
            }
            self.record(&key, last);
        }
        self.springs.retain(|_, spring| match spring.seen {
            Some(seen) if now - seen > KEEP_S => false,
            Some(seen) => {
                spring.forget_before(seen - 1.0);
                true
            }
            None => true,
        });
    }

    /// _record: the history of `key` takes its spring's points up to grid number `last`.
    fn record(&mut self, key: &str, last: i64) {
        let (Some(spring), Some(history)) = (self.springs.get(key), self.histories.get_mut(key))
        else {
            return;
        };
        let Some(before) = history.last else {
            return;
        };
        if last <= before {
            return;
        }
        let first = (before + 1).max(last - history.maxlen as i64 + 1);
        for k in first..=last {
            history.push(spring.at(k as f64 / RATE));
        }
        history.last = Some(last);
    }

    /// Whether something last drawn at `seen` starts again at `now`: it was off the panel a
    /// while, and must not catch up a glide nobody saw (a pause between frames does not count).
    fn fresh(&self, seen: Option<f64>, now: f64) -> bool {
        let Some(seen) = seen else {
            return true;
        };
        if now < seen {
            return true;
        }
        if now - seen <= PAUSE_S {
            return false;
        }
        !(Some(seen) == self.previous && now - seen <= IDLE_S)
    }

    /// (value, speed) of `key` at `now`, gliding towards `target`; a new reading glides from
    /// the frame before (`lead` earlier, or when the value was last drawn). None stays None.
    pub fn glide(
        &mut self,
        key: &str,
        target: Option<f64>,
        now: f64,
        glide: f64,
        lead: f64,
    ) -> Option<(f64, f64)> {
        let seen = self.springs.get(key).and_then(|s| s.seen);
        let fresh = self.fresh(seen, now);
        let spring = self.springs.entry(key.to_string()).or_default();
        if fresh || spring.pieces.is_empty() {
            spring.restart(now, target);
        } else if target != spring.target {
            let seen = spring.seen.expect("seen where not fresh");
            let start = if lead <= 0.0 {
                seen
            } else {
                py_max(seen, now - lead)
            };
            let state = if target.is_some() {
                spring.state(start)
            } else {
                None
            };
            match (target, state) {
                (None, _) => spring.add(start, None, 0.0, 0.0, 0.0),
                // (back after a gap: no glide from nothing)
                (Some(_), None) => spring.add(now, target, 0.0, 0.0, 0.0),
                (Some(to), Some((value, speed))) => {
                    // (95 % of a step after `glide` s)
                    let omega = if glide > 0.0 { 4.74 / glide } else { 0.0 };
                    spring.add(start, target, value - to, speed, omega);
                }
            }
        }
        (spring.target, spring.seen) = (target, Some(now));
        spring.state(now)
    }

    /// A fine course of `key` (`text`, read as `course`: RATE points a second, the last at
    /// `end`; Nones at its end not known yet): the points after the last taken stand in the
    /// spring each until the next. A course taken already is not looked at again; one that comes
    /// after `key` was off the panel a while begins it anew.
    pub fn trace(&mut self, key: &str, text: &str, course: &[Option<f64>], end: f64, now: f64) {
        let seen = self.springs.get(key).and_then(|s| s.seen);
        let fresh = self.fresh(seen, now);
        let mut fed = self.fed.get(key).cloned();
        let spring = self.springs.entry(key.to_string()).or_default();
        spring.seen = Some(now);
        if fed.as_ref().is_some_and(|f| f.0 == text) && !fresh {
            return;
        }
        if fresh {
            spring.pieces.clear();
            self.fed.remove(key);
            fed = None;
        }
        let mut known = course.len();
        while known > 0 && course[known - 1].is_none() {
            known -= 1;
        }
        let mut newest = fed.map(|f| f.1);
        // (the grid number of the first point)
        let base = lp_py::round(end * RATE) as i64 - course.len() as i64 + 1;
        for (i, point) in course.iter().enumerate().take(known) {
            let k = base + i as i64;
            if newest.is_some_and(|n| k <= n) {
                continue;
            }
            spring.add(k as f64 / RATE, *point, 0.0, 0.0, 0.0);
            newest = Some(k);
        }
        let Some(newest) = newest else {
            return; // (nothing known yet)
        };
        if known > 0 {
            spring.target = course[known - 1];
        }
        self.fed.insert(key.to_string(), (text.to_string(), newest));
    }

    /// Whether `key`'s spring stands in a fine course.
    pub fn is_fed(&self, key: &str) -> bool {
        self.fed.contains_key(key)
    }

    /// Whether the glide of `key` passes its target after `t`, to come back to it from the
    /// other side; a glide that does not only comes nearer its target until the next reading.
    pub fn passes(&self, key: &str, t: f64) -> bool {
        let Some(p) = self.springs.get(key).and_then(|s| s.piece(t)) else {
            return false;
        };
        if p.target.is_none() || p.omega <= 0.0 {
            return false;
        }
        let curve = p.speed + p.omega * p.error;
        curve != 0.0 && -p.error / curve > t - p.start
    }

    /// The gliding value of `key` at `t` (None if it was not drawn then).
    pub fn value(&self, key: &str, t: f64) -> Option<f64> {
        self.springs.get(key)?.at(t)
    }

    /// `key`'s glide as it stands now: how many pieces it was made of so far, and its pieces
    /// (None where `key` does not glide).
    pub fn course(&self, key: &str) -> Option<(u64, Course)> {
        let spring = self.springs.get(key)?;
        let pieces = spring.pieces.clone();
        Some((spring.made, Course { pieces }))
    }

    /// _valid_until: until when a strip drawn with `key`'s glide of `made` pieces (taken at
    /// `taken`, drawn ahead until `until`) is the sensor's: the end of what it drew ahead, or
    /// when the value took a course the strip did not know.
    pub fn valid_until(&self, key: &str, made: u64, taken: f64, until: f64) -> f64 {
        let Some(spring) = self.springs.get(key) else {
            return taken;
        };
        if spring.made == made {
            return until;
        }
        match spring.pieces.iter().find(|p| p.made >= made) {
            Some(first) if first.made == made => first.start,
            _ => taken, // (gone already: right until it was taken)
        }
    }

    /// When the oldest piece of `key`'s glide that is kept begins.
    pub fn start(&self, key: &str) -> f64 {
        self.springs
            .get(key)
            .and_then(|s| s.pieces.first())
            .map_or(f64::NEG_INFINITY, |p| p.start)
    }

    /// The colour step a value shows (0: its own colour, 1: the first threshold's, ...), on its
    /// way to `step` one step per FADE_S: a bar and a number of the same value (the same `key`)
    /// change together.
    pub fn fade(&mut self, key: &FadeKey, step: usize, now: f64) -> f64 {
        let step = step as f64;
        let at = self.fades.iter().position(|(k, _)| k == key);
        let state = at.map(|i| self.fades[i].1);
        let shown = match state {
            Some((shown, then)) if !self.fresh(Some(then), now) => {
                let movement = (now - then) / FADE_S;
                if (step - shown).abs() <= movement {
                    step
                } else {
                    shown + movement.copysign(step - shown)
                }
            }
            _ => step,
        };
        match at {
            Some(i) => self.fades[i].1 = (shown, now),
            None => self.fades.push((key.clone(), (shown, now))),
        }
        shown
    }

    /// At least `size` points of `key` up to `now`: a history that is new (or too short) filled
    /// from `readings` (the hub's, one each `interval`, the last at `last_at`) as the spring
    /// would have glided through them.
    #[allow(clippy::too_many_arguments)]
    pub fn history(
        &mut self,
        key: &str,
        size: usize,
        now: f64,
        readings: &[Value],
        last_at: f64,
        interval: f64,
        glide: f64,
    ) -> &History {
        let size = size.clamp(2, MAX_POINTS);
        let reuse = self.histories.get(key).is_some_and(|h| h.maxlen >= size);
        if reuse {
            let history = self.histories.get_mut(key).expect("a history");
            history.used = now;
            return history;
        }
        let last = grid(now);
        let older = self.histories.remove(key);
        let mut history = History::new(size);
        history.used = now;
        let replayed = replay(readings, last_at, interval, glide);
        let spring = self.springs.get(key);
        let first = last - size as i64 + 1;
        let known = match &older {
            Some(o) if o.last.is_some() => o.last.expect("a last") - o.points.len() as i64 + 1,
            _ => last + 1,
        };
        for k in first..known.min(last + 1) {
            let t = k as f64 / RATE;
            let live = spring
                .filter(|s| s.piece(t).is_some())
                .and_then(|s| s.at(t));
            history.push(live.or_else(|| replayed.at(t)));
        }
        if let Some(older) = older.as_ref().filter(|o| o.last.is_some()) {
            for point in older.since(first.max(known)) {
                history.push(point);
            }
            for k in older.last.expect("a last") + 1..=last {
                history.push(spring.and_then(|s| s.at(k as f64 / RATE)));
            }
        }
        history.last = Some(last);
        self.histories.insert(key.to_string(), history);
        &self.histories[key]
    }
}

/// Python's max(a, b) of two floats: the first where neither is greater.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// _replay: a spring that went through `readings`, one each `interval`, the last at `last_at`
/// (NaN and the infinities a gap, as no reading is).
fn replay(readings: &[Value], last_at: f64, interval: f64, glide: f64) -> Spring {
    let mut spring = Spring::default();
    let omega = if glide > 0.0 { 4.74 / glide } else { 0.0 };
    let start = last_at - (readings.len() as f64 - 1.0) * interval;
    for (i, reading) in readings.iter().enumerate() {
        let value = number(reading);
        let at = start + i as f64 * interval;
        match (value, spring.state(at)) {
            (Some(v), Some((x, speed))) => spring.add(at, Some(v), x - v, speed, omega),
            (value, _) => spring.add(at, value, 0.0, 0.0, 0.0),
        }
    }
    spring
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_new_reading_glides_and_stands_after_a_pause() {
        let mut m = Motion::default();
        let none = |_: &str| None;
        m.begin(&none, 10.0, 0.4, 0.0);
        assert_eq!(
            m.glide("cpu", Some(10.0), 10.0, 0.4, 0.0),
            Some((10.0, 0.0))
        );
        m.begin(&none, 10.04, 0.4, 0.0);
        // (from the frame before, standing: on its way at once)
        let (v, s) = m
            .glide("cpu", Some(20.0), 10.04, 0.4, 0.0)
            .expect("a value");
        assert!(10.0 < v && v < 11.0 && s > 0.0, "{v} {s}");
        m.begin(&none, 10.2, 0.4, 0.0);
        let w = m
            .glide("cpu", Some(20.0), 10.2, 0.4, 0.0)
            .expect("a value")
            .0;
        assert!(v < w && w < 20.0, "{w}");
        // (off the panel more than a second: it starts again where its reading is)
        m.begin(&none, 12.0, 0.4, 0.0);
        assert_eq!(
            m.glide("cpu", Some(30.0), 12.0, 0.4, 0.0),
            Some((30.0, 0.0))
        );
        assert_eq!(grid(0.04), 1);
        assert_eq!(number(&Value::Bool(true)), None);
    }
}
