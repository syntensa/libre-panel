//! What Python's functools.lru_cache keeps, for the pictures the renderer makes again and
//! again: the results of the last `most` calls by their arguments, the one asked for longest ago
//! going first. Each is shared by the panels' threads, as the module-level caches of the Python
//! app are by its threads.

use std::collections::HashMap;
use std::hash::Hash;
use std::sync::{Mutex, PoisonError};

/// The results kept, each with when it was last asked for.
pub struct Lru<K, V> {
    most: usize,
    clock: u64,
    kept: HashMap<K, (V, u64)>,
}

impl<K: Hash + Eq + Clone, V: Clone> Lru<K, V> {
    pub fn new(most: usize) -> Lru<K, V> {
        Lru {
            most,
            clock: 0,
            kept: HashMap::new(),
        }
    }

    /// The result kept for `key`, now the one asked for last.
    pub fn get(&mut self, key: &K) -> Option<V> {
        self.clock += 1;
        let clock = self.clock;
        self.kept.get_mut(key).map(|(value, asked)| {
            *asked = clock;
            value.clone()
        })
    }

    /// `value` kept for `key`; the oldest go while more than `most` are kept.
    pub fn put(&mut self, key: K, value: V) {
        self.clock += 1;
        self.kept.insert(key, (value, self.clock));
        while self.kept.len() > self.most {
            let oldest = self
                .kept
                .iter()
                .min_by_key(|(_, (_, asked))| *asked)
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
