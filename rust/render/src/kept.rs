//! render/kept.py's Kept: values by key, the most recently used kept while all of them hold at
//! most a budget of bytes; the newest always stays, also when it alone is larger. What goes is
//! only made again: the pixels are the same either way.

use std::collections::HashMap;
use std::hash::Hash;

pub(crate) struct Kept<K, V> {
    budget: usize,
    size: usize,
    tick: u64,
    items: HashMap<K, (V, usize, u64)>,
}

impl<K: Hash + Eq + Clone, V: Clone> Kept<K, V> {
    pub(crate) fn new(budget: usize) -> Kept<K, V> {
        Kept {
            budget,
            size: 0,
            tick: 0,
            items: HashMap::new(),
        }
    }

    /// The value of `key`, now the most recently used.
    pub(crate) fn get(&mut self, key: &K) -> Option<V> {
        self.tick += 1;
        let tick = self.tick;
        let item = self.items.get_mut(key)?;
        item.2 = tick;
        Some(item.0.clone())
    }

    /// Keep `value` as the newest, `size` bytes; the least recently used go while more than
    /// the budget is kept.
    pub(crate) fn put(&mut self, key: K, value: V, size: usize) {
        self.tick += 1;
        if let Some(old) = self.items.insert(key.clone(), (value, size, self.tick)) {
            self.size -= old.1;
        }
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

#[cfg(test)]
mod tests {
    use super::*;

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
