//! render/kept.py's Kept: values by key, the most recently used kept while all of them hold at
//! most a budget of bytes; the newest always stays, also when it alone is larger. What goes is
//! only made again: the pixels are the same either way.
//!
//! What the app's pictures of every theme filled the shared ones with is let go once its
//! window has been closed a while (light mode): `mark` notes from then on what is used, and
//! `shed` lets go of the rest, but for what a panel has drawn with (shared.rs), so that what
//! the panel draws with stays.

use std::collections::HashMap;
use std::hash::Hash;

pub(crate) struct Kept<K, V> {
    budget: usize,
    size: usize,
    tick: u64,
    /// each with its bytes, when it was last used and whether a panel has drawn with it
    items: HashMap<K, (V, usize, u64, bool)>,
    /// the tick at mark(): what was used after it stays at shed() (None: not noting)
    marked: Option<u64>,
}

impl<K: Hash + Eq + Clone, V: Clone> Kept<K, V> {
    pub(crate) fn new(budget: usize) -> Kept<K, V> {
        Kept {
            budget,
            size: 0,
            tick: 0,
            items: HashMap::new(),
            marked: None,
        }
    }

    /// The bytes kept.
    pub(crate) fn bytes(&self) -> usize {
        self.size
    }

    /// mark: from now on it notes what is used.
    pub(crate) fn mark(&mut self) {
        self.marked = Some(self.tick);
    }

    /// unmark: it no longer notes (nothing goes).
    pub(crate) fn unmark(&mut self) {
        self.marked = None;
    }

    /// shed: what was not used since mark(), and never by a panel, goes; the bytes that went.
    pub(crate) fn shed(&mut self) -> usize {
        let Some(mark) = self.marked.take() else {
            return 0;
        };
        let before = self.size;
        self.items.retain(|_, item| item.2 > mark || item.3);
        self.size = self.items.values().map(|item| item.1).sum();
        before - self.size
    }

    /// The value of `key`, now the most recently used.
    pub(crate) fn get(&mut self, key: &K) -> Option<V> {
        self.tick += 1;
        let tick = self.tick;
        let item = self.items.get_mut(key)?;
        item.2 = tick;
        item.3 |= crate::shared::a_panels();
        Some(item.0.clone())
    }

    /// get(key, valid=...): the value of `key` where it serves (`valid`), now the most recently
    /// used; one that does not serve stays where it is.
    pub(crate) fn get_if(&mut self, key: &K, valid: impl Fn(&V) -> bool) -> Option<V> {
        let serves = self.items.get(key).is_some_and(|item| valid(&item.0));
        if !serves {
            return None;
        }
        self.get(key)
    }

    /// Keep `value` as the newest, `size` bytes; the least recently used go while more than
    /// the budget is kept.
    pub(crate) fn put(&mut self, key: K, value: V, size: usize) {
        self.tick += 1;
        let mut item = (value, size, self.tick, crate::shared::a_panels());
        if let Some(old) = self.items.get(&key) {
            self.size -= old.1;
            item.3 |= old.3;
        }
        self.items.insert(key.clone(), item);
        self.size += size;
        while self.size > self.budget && self.items.len() > 1 {
            let oldest = self
                .items
                .iter()
                .filter(|(k, _)| **k != key)
                .min_by_key(|(_, item)| item.2)
                .map(|(k, _)| k.clone());
            let Some(oldest) = oldest else { break };
            if let Some(gone) = self.items.remove(&oldest) {
                self.size -= gone.1;
            }
        }
    }
}

/// Values in the order of their use, the one used last at the end, held to a number of bytes
/// (fan.py's kept layers): the least recently used go first, the newest always stays. It
/// notes what is used between `mark` and `shed` as [`Kept`] does.
pub(crate) struct InOrder<K, V> {
    /// each with its bytes and whether a panel has drawn with it
    items: Vec<(K, V, usize, bool)>,
    /// how many at the start were not used since mark() (None: not noting)
    unused: Option<usize>,
}

impl<K: PartialEq, V: Clone> InOrder<K, V> {
    pub(crate) const fn new() -> InOrder<K, V> {
        InOrder {
            items: Vec::new(),
            unused: None,
        }
    }

    /// The value kept by `key`, now the most recently used.
    pub(crate) fn get(&mut self, key: &K) -> Option<V> {
        let at = self.items.iter().position(|(k, ..)| k == key)?;
        if let Some(unused) = &mut self.unused {
            if at < *unused {
                *unused -= 1;
            }
        }
        let mut entry = self.items.remove(at);
        entry.3 |= crate::shared::a_panels();
        let value = entry.1.clone();
        self.items.push(entry);
        Some(value)
    }

    /// `value` (of `bytes`) kept as the newest; the least recently used go while more than
    /// `budget` bytes are kept.
    pub(crate) fn put(&mut self, key: K, value: V, bytes: usize, budget: usize) {
        self.items
            .push((key, value, bytes, crate::shared::a_panels()));
        let mut total: usize = self.items.iter().map(|(_, _, n, _)| n).sum();
        while total > budget && self.items.len() > 1 {
            total -= self.items.remove(0).2;
            if let Some(unused) = &mut self.unused {
                *unused = unused.saturating_sub(1);
            }
        }
    }

    /// `value` kept as the newest, in the place of any kept by `key`; the least recently
    /// used go while the values hold more than `budget` bytes, each counted as `size` says
    /// now (a value's bytes may grow while it is kept: [`put`](Self::put) counts them once).
    pub(crate) fn put_measured(
        &mut self,
        key: K,
        value: V,
        budget: usize,
        size: impl Fn(&V) -> usize,
    ) {
        let mut a_panels = crate::shared::a_panels();
        while let Some(at) = self.items.iter().position(|(k, ..)| *k == key) {
            if let Some(unused) = self.unused.as_mut().filter(|unused| at < **unused) {
                *unused -= 1;
            }
            a_panels |= self.items.remove(at).3;
        }
        self.items.push((key, value, 0, a_panels));
        let mut total = self.sizes(&size);
        while total > budget && self.items.len() > 1 {
            total -= size(&self.items.remove(0).1);
            if let Some(unused) = &mut self.unused {
                *unused = unused.saturating_sub(1);
            }
        }
    }

    /// What the values kept hold together, each as `size` says.
    pub(crate) fn sizes(&self, size: impl Fn(&V) -> usize) -> usize {
        self.items.iter().map(|(_, value, ..)| size(value)).sum()
    }

    /// The bytes kept, as [`put`](Self::put) was told them.
    pub(crate) fn bytes(&self) -> usize {
        self.items.iter().map(|(_, _, bytes, _)| bytes).sum()
    }

    pub(crate) fn mark(&mut self) {
        self.unused = Some(self.items.len());
    }

    pub(crate) fn unmark(&mut self) {
        self.unused = None;
    }

    /// What was not used since mark(), and never by a panel, goes; the bytes that went.
    pub(crate) fn shed(&mut self) -> usize {
        let Some(unused) = self.unused.take() else {
            return 0;
        };
        let unused = unused.min(self.items.len());
        let (mut gone, mut at) = (0, 0);
        for _ in 0..unused {
            if self.items[at].3 {
                at += 1; // (a panel's: it stays, in its place)
            } else {
                gone += self.items.remove(at).2;
            }
        }
        gone
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_cache_notes_what_is_used_and_lets_go_of_the_rest() {
        let mut kept = Kept::new(100);
        for (key, value) in [("a", 1), ("b", 2), ("c", 3)] {
            kept.put(key, value, 10);
        }
        assert_eq!(kept.shed(), 0); // (not noting: nothing goes)
        kept.mark();
        assert_eq!(kept.get(&"b"), Some(2));
        kept.put("d", 4, 10); // (made while it notes: used)
        assert_eq!(kept.shed(), 20);
        assert_eq!((kept.get(&"a"), kept.get(&"c")), (None, None));
        assert_eq!((kept.get(&"b"), kept.get(&"d")), (Some(2), Some(4)));
        assert_eq!(kept.shed(), 0); // (once: it no longer notes)
                                    // opened again before it let go: all stay
        kept.mark();
        kept.unmark();
        assert_eq!(kept.shed(), 0);
        assert_eq!(kept.get(&"b"), Some(2));
    }

    #[test]
    fn a_list_in_order_of_use_notes_what_is_used_and_lets_go_of_the_rest() {
        let mut kept = InOrder::new();
        for (key, value) in [("a", 1), ("b", 2), ("c", 3), ("d", 4)] {
            kept.put(key, value, 10, 100);
        }
        assert_eq!(kept.shed(), 0); // (not noting)
        kept.mark();
        assert_eq!(kept.get(&"c"), Some(3));
        assert_eq!(kept.get(&"a"), Some(1));
        assert_eq!(kept.get(&"a"), Some(1)); // (again: counted once)
        kept.put("e", 5, 10, 100);
        assert_eq!(kept.shed(), 20); // b and d
        assert_eq!((kept.get(&"b"), kept.get(&"d")), (None, None));
        assert_eq!(
            (kept.get(&"a"), kept.get(&"c"), kept.get(&"e")),
            (Some(1), Some(3), Some(5))
        );
    }

    #[test]
    fn what_went_over_the_budget_while_it_notes_is_not_counted_twice() {
        let mut kept = InOrder::new();
        for (key, value) in [("a", 1), ("b", 2), ("c", 3)] {
            kept.put(key, value, 10, 30);
        }
        kept.mark();
        assert_eq!(kept.get(&"b"), Some(2));
        kept.put("d", 4, 10, 30); // a, the oldest and not used, goes for it
        kept.put("e", 5, 10, 30); // then c
        assert_eq!(kept.get(&"a"), None);
        assert_eq!(kept.shed(), 0); // (nothing unused is left)
        assert_eq!(
            (kept.get(&"b"), kept.get(&"d"), kept.get(&"e")),
            (Some(2), Some(4), Some(5))
        );
        // the least recently used go first, the newest always stays
        kept.put("f", 6, 100, 30);
        assert_eq!(
            (kept.get(&"b"), kept.get(&"e"), kept.get(&"f")),
            (None, None, Some(6))
        );
    }

    #[test]
    fn what_a_panel_drew_with_stays_though_it_was_not_used_meanwhile() {
        let mut kept = Kept::new(100);
        kept.put("app", 1, 10);
        crate::shared::as_a_panel(|| kept.put("panel", 2, 10));
        kept.put("both", 3, 10);
        crate::shared::as_a_panel(|| assert_eq!(kept.get(&"both"), Some(3)));
        kept.mark();
        // (nothing used while it notes: a fan that stands still)
        assert_eq!(kept.shed(), 10);
        assert_eq!(kept.get(&"app"), None);
        assert_eq!((kept.get(&"panel"), kept.get(&"both")), (Some(2), Some(3)));
        // made again by the app in its place: a panel's still
        kept.put("panel", 4, 10);
        kept.mark();
        assert_eq!(kept.shed(), 0);
        assert_eq!(kept.get(&"panel"), Some(4));
    }

    #[test]
    fn a_list_in_order_keeps_what_a_panel_drew_with_in_its_place() {
        let mut kept = InOrder::new();
        kept.put("a", 1, 10, 100);
        crate::shared::as_a_panel(|| kept.put("b", 2, 10, 100));
        kept.put("c", 3, 10, 100);
        kept.put("d", 4, 10, 100);
        crate::shared::as_a_panel(|| assert_eq!(kept.get(&"a"), Some(1))); // b c d a
        kept.mark();
        assert_eq!(kept.get(&"d"), Some(4)); // b c a d, the first three not used since
        assert_eq!(kept.shed(), 10); // c alone: b and a are a panel's, d was used
        assert_eq!(kept.get(&"c"), None);
        // the order of use is as it was: b the least recently used, going first
        kept.put("e", 5, 10, 30);
        assert_eq!(kept.get(&"b"), None);
        assert_eq!(
            (kept.get(&"a"), kept.get(&"d"), kept.get(&"e")),
            (Some(1), Some(4), Some(5))
        );
    }

    #[test]
    fn values_that_grow_are_counted_as_they_are_and_a_key_is_kept_once() {
        let mut kept: InOrder<&str, Vec<u8>> = InOrder::new();
        let size = |value: &Vec<u8>| value.len();
        kept.put_measured("a", vec![0; 10], 30, size);
        kept.put_measured("b", vec![0; 10], 30, size);
        kept.put_measured("a", vec![1; 10], 30, size); // in a's place: b a
        assert_eq!(kept.sizes(size), 20);
        assert_eq!(kept.get(&"a"), Some(vec![1; 10]));
        kept.put_measured("c", vec![0; 15], 30, size); // 35: b, the least recently used, goes
        assert_eq!(kept.get(&"b"), None);
        assert_eq!(kept.sizes(size), 25);
        // noting: a panel's stays, the app's go; one made again while it notes is used
        crate::shared::as_a_panel(|| kept.put_measured("p", vec![0; 5], 30, size));
        kept.mark();
        kept.put_measured("c", vec![2; 15], 30, size);
        assert_eq!(kept.shed(), 0); // (their bytes are counted by sizes)
        assert_eq!(kept.sizes(size), 20);
        assert_eq!(kept.get(&"a"), None);
        assert_eq!(
            (kept.get(&"p"), kept.get(&"c")),
            (Some(vec![0; 5]), Some(vec![2; 15]))
        );
    }

    #[test]
    fn the_least_recently_used_go_and_the_newest_stays() {
        let mut kept = Kept::new(10);
        kept.put("a", 1, 4);
        kept.put("b", 2, 4);
        assert_eq!(kept.get(&"a"), Some(1));
        kept.put("c", 3, 4);
        assert_eq!(
            (kept.get(&"a"), kept.get(&"b"), kept.get(&"c")),
            (Some(1), None, Some(3))
        );
        kept.put("d", 4, 40);
        assert_eq!((kept.get(&"a"), kept.get(&"d")), (None, Some(4)));
    }
}
