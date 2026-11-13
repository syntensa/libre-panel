//! What Python's functools.lru_cache keeps, for the pictures the renderer makes again and
//! again: the results of the last `most` calls by their arguments, the one asked for longest ago
//! going first. Each is shared by the panels' threads, as the module-level caches of the Python
//! app are by its threads.

use std::collections::HashMap;
use std::hash::Hash;
use std::sync::{Mutex, PoisonError};

/// The results kept, each with when it was last asked for and whether a panel has asked for
/// it (shared.rs).
pub struct Lru<K, V> {
    most: usize,
    clock: u64,
    kept: HashMap<K, (V, u64, bool)>,
    /// the clock at mark(): what was asked for after it stays at shed() (None: not noting)
    marked: Option<u64>,
}

impl<K: Hash + Eq + Clone, V: Clone> Lru<K, V> {
    pub fn new(most: usize) -> Lru<K, V> {
        Lru {
            most,
            clock: 0,
            kept: HashMap::new(),
            marked: None,
        }
    }

    /// Light mode (kept.rs): from now on it notes what is asked for.
    pub fn mark(&mut self) {
        self.marked = Some(self.clock);
    }

    pub fn unmark(&mut self) {
        self.marked = None;
    }

    /// What was not asked for since mark(), and never by a panel, goes; how many went.
    pub fn shed(&mut self) -> usize {
        let Some(mark) = self.marked.take() else {
            return 0;
        };
        let before = self.kept.len();
        self.kept
            .retain(|_, (_, asked, a_panels)| *asked > mark || *a_panels);
        before - self.kept.len()
    }

    /// What the results kept hold together, each as `size` says.
    pub fn sizes(&self, size: impl Fn(&V) -> usize) -> usize {
        self.kept.values().map(|(value, ..)| size(value)).sum()
    }

    /// The result kept for `key`, now the one asked for last.
    pub fn get(&mut self, key: &K) -> Option<V> {
        self.clock += 1;
        let clock = self.clock;
        self.kept.get_mut(key).map(|(value, asked, a_panels)| {
            *asked = clock;
            *a_panels |= crate::shared::a_panels();
            value.clone()
        })
    }

    /// `value` kept for `key`; the oldest go while more than `most` are kept.
    pub fn put(&mut self, key: K, value: V) {
        self.clock += 1;
        let a_panels = crate::shared::a_panels() || self.kept.get(&key).is_some_and(|kept| kept.2);
        self.kept.insert(key, (value, self.clock, a_panels));
        while self.kept.len() > self.most {
            let oldest = self
                .kept
                .iter()
                .min_by_key(|(_, (_, asked, _))| *asked)
                .map(|(key, _)| key.clone());
            match oldest {
                Some(key) => self.kept.remove(&key),
                None => break,
            };
        }
    }
}

/// A cache of a module (`static NAME: Shared<K, V> = Shared::new(most)`): `make`'s result for
/// `key`, made once while it stays among the last `most` asked for.
pub struct Shared<K, V> {
    most: usize,
    lru: Mutex<Option<Lru<K, V>>>,
}

impl<K: Hash + Eq + Clone, V: Clone> Shared<K, V> {
    pub const fn new(most: usize) -> Shared<K, V> {
        Shared {
            most,
            lru: Mutex::new(None),
        }
    }

    /// Light mode: the cache notes what is asked for from now on.
    pub fn mark(&self) {
        let mut lru = self.lru.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(lru) = lru.as_mut() {
            lru.mark();
        }
    }

    pub fn unmark(&self) {
        let mut lru = self.lru.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(lru) = lru.as_mut() {
            lru.unmark();
        }
    }

    /// What was not asked for since mark() goes; how many went.
    pub fn shed(&self) -> usize {
        let mut lru = self.lru.lock().unwrap_or_else(PoisonError::into_inner);
        lru.as_mut().map_or(0, Lru::shed)
    }

    /// What the results kept hold together, each as `size` says.
    pub fn sizes(&self, size: impl Fn(&V) -> usize) -> usize {
        let lru = self.lru.lock().unwrap_or_else(PoisonError::into_inner);
        lru.as_ref().map_or(0, |lru| lru.sizes(size))
    }

    /// The result for `key`: kept, or made by `make` (outside the lock: another thread may
    /// make the same meanwhile, and the same comes out) and kept.
    pub fn get_or<E>(&self, key: K, make: impl FnOnce() -> Result<V, E>) -> Result<V, E> {
        {
            let mut lru = self.lru.lock().unwrap_or_else(PoisonError::into_inner);
            if let Some(value) = lru.get_or_insert_with(|| Lru::new(self.most)).get(&key) {
                return Ok(value);
            }
        }
        let value = make()?;
        let mut lru = self.lru.lock().unwrap_or_else(PoisonError::into_inner);
        lru.get_or_insert_with(|| Lru::new(self.most))
            .put(key, value.clone());
        Ok(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_one_asked_for_longest_ago_goes_first() {
        let mut lru = Lru::new(2);
        lru.put(1, "a");
        lru.put(2, "b");
        assert_eq!(lru.get(&1), Some("a")); // (1 asked for last now)
        lru.put(3, "c");
        assert_eq!(lru.get(&2), None);
        assert_eq!((lru.get(&1), lru.get(&3)), (Some("a"), Some("c")));
    }

    #[test]
    fn it_notes_what_is_asked_for_and_lets_go_of_the_rest() {
        let mut lru = Lru::new(8);
        for (key, value) in [(1, "a"), (2, "b"), (3, "c")] {
            lru.put(key, value);
        }
        assert_eq!(lru.shed(), 0); // (not noting)
        lru.mark();
        assert_eq!(lru.get(&2), Some("b"));
        lru.put(4, "d");
        assert_eq!(lru.shed(), 2);
        assert_eq!((lru.get(&1), lru.get(&3)), (None, None));
        assert_eq!((lru.get(&2), lru.get(&4)), (Some("b"), Some("d")));
        lru.mark();
        lru.unmark();
        assert_eq!(lru.shed(), 0);
    }

    #[test]
    fn what_a_panel_asked_for_stays_though_it_was_not_asked_for_meanwhile() {
        let mut lru = Lru::new(8);
        lru.put(1, "the app's");
        crate::shared::as_a_panel(|| lru.put(2, "a panel's"));
        lru.put(3, "both's");
        crate::shared::as_a_panel(|| assert_eq!(lru.get(&3), Some("both's")));
        lru.mark();
        // (nothing asked for while it notes: a moon whose light does not change)
        assert_eq!(lru.shed(), 1);
        assert_eq!(
            (lru.get(&1), lru.get(&2), lru.get(&3)),
            (None, Some("a panel's"), Some("both's"))
        );
        // made again by the app in its place: a panel's still
        lru.put(2, "anew");
        lru.mark();
        assert_eq!(lru.shed(), 0);
        assert_eq!(lru.get(&2), Some("anew"));
    }

    #[test]
    fn a_shared_cache_makes_each_once() {
        static KEPT: Shared<i32, i32> = Shared::new(4);
        let mut made = 0;
        for _ in 0..3 {
            let got: Result<i32, ()> = KEPT.get_or(7, || {
                made += 1;
                Ok(49)
            });
            assert_eq!(got, Ok(49));
        }
        assert_eq!(made, 1);
    }
}
