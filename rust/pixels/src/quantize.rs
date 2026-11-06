//! Image.quantize(colors, method=MEDIANCUT) and Image.getcolors (Quant.c, QuantHash.c,
//! QuantHeap.c and GetBBox.c's getcolors32, Pillow 13.0), as render/cover.py asks for them: a
//! cover cut down to a few colours, and how many pixels each has.
//!
//! Every order Pillow's answer depends on is Pillow's: its hash table's (buckets by the hash
//! modulo its length, which grows by _findPrime; chains ascending by the hash; colours whose
//! hashes are the same counted as one), the lists made from it (each new one put first), its
//! merge sort (on a tie, the second half's first), its heap of boxes (by pixels; on a tie the
//! left child) and the boxes' numbering (the tree's leaves, left first).

use crate::{error, Image, Mode, Result};

/// Quant.c's MAX_HASH_ENTRIES: past this many colours, each is counted at half the precision.
const MAX_HASH_ENTRIES: usize = 65536;

/// QuantHash.c's smallest length.
const MIN_LENGTH: u32 = 11;

/// PIXEL_HASH, in C's unsigned arithmetic.
fn pixel_hash(p: [u8; 3], scale: u32) -> u32 {
    let (r, g, b) = (
        u32::from(p[0] >> scale),
        u32::from(p[1] >> scale),
        u32::from(p[2] >> scale),
    );
    r.wrapping_mul(463) ^ (g << 8).wrapping_mul(10069) ^ (b << 16).wrapping_mul(64997)
}

/// _findPrime as it is: its test of a divisor (`!start % t`) never holds, so it finds the first
/// number from `start` on (by `dir`) whose low four bits are 1, 3, 7, 9, 11 or 13.
fn find_prime(mut start: u32, dir: i32) -> u32 {
    const UNIT: [bool; 16] = [
        false, true, false, true, false, false, false, true, false, true, false, true, false, true,
        false, false,
    ];
    while start > 1 {
        if UNIT[(start & 0x0f) as usize] {
            break;
        }
        start = start.wrapping_add_signed(dir);
    }
    start
}

/// QuantHash.c's table with Quant.c's pixel_hash and pixel_cmp: keys are equal where their
/// hashes are (at the table's scale), each chain kept in ascending order of the hash.
struct Table {
    buckets: Vec<Vec<Node>>,
    count: usize,
    scale: u32,
}

#[derive(Clone, Copy)]
struct Node {
    hash: u32,
    key: [u8; 3],
    value: u32,
}

impl Table {
    fn new() -> Table {
        Table {
            buckets: vec![Vec::new(); MIN_LENGTH as usize],
            count: 0,
            scale: 0,
        }
    }

    fn length(&self) -> u32 {
        self.buckets.len() as u32
    }

    /// Where a key of `hash` is in its chain (Ok), or would go (Err).
    fn find(&self, hash: u32) -> (usize, std::result::Result<usize, usize>) {
        let at = (hash % self.length()) as usize;
        let chain = &self.buckets[at];
        for (i, n) in chain.iter().enumerate() {
            if n.hash == hash {
                return (at, Ok(i));
            }
            if n.hash > hash {
                return (at, Err(i));
            }
        }
        (at, Err(chain.len()))
    }

    /// _hashtable_rehash: every node again, in the old order, into `length` buckets (the hash
    /// at the table's scale now); where two meet, rehash_collide: the later one's key and
    /// both counts.
    fn rehash(&mut self, length: u32) {
        let old = std::mem::replace(&mut self.buckets, vec![Vec::new(); length as usize]);
        self.count = 0;
        for node in old.into_iter().flatten() {
            let hash = pixel_hash(node.key, self.scale);
            match self.find(hash) {
                (at, Ok(i)) => {
                    let n = &mut self.buckets[at][i];
                    n.key = node.key;
                    n.hash = hash;
                    n.value += node.value;
                }
                (at, Err(i)) => {
                    self.buckets[at].insert(i, Node { hash, ..node });
                    self.count += 1;
                }
            }
        }
    }

    /// _hashtable_resize, after each new node.
    fn resize(&mut self) {
        let length = self.length();
        let count = self.count as u64;
        let mut size = length;
        if count * 3 < u64::from(length) {
            size = find_prime(length / 2 - 1, -1);
        } else if u64::from(length) * 3 < count {
            size = find_prime(length * 2 + 1, 1);
        }
        if size < MIN_LENGTH {
            size = length;
        }
        if size != length {
            self.rehash(size);
        }
    }

    /// hashtable_insert_or_update_computed with new_count_func and exists_count_func.
    fn count_in(&mut self, key: [u8; 3]) {
        let hash = pixel_hash(key, self.scale);
        match self.find(hash) {
            (at, Ok(i)) => self.buckets[at][i].value += 1,
            (at, Err(i)) => {
                self.buckets[at].insert(
                    i,
                    Node {
                        hash,
                        key,
                        value: 1,
                    },
                );
                self.count += 1;
                self.resize();
            }
        }
    }

    /// hashtable_lookup.
    fn lookup(&self, key: [u8; 3]) -> Option<u32> {
        let (at, found) = self.find(pixel_hash(key, self.scale));
        found.ok().map(|i| self.buckets[at][i].value)
    }

    /// hashtable_insert: the value of a key there, or a new node.
    fn set(&mut self, key: [u8; 3], value: u32) {
        let hash = pixel_hash(key, self.scale);
        match self.find(hash) {
            (at, Ok(i)) => self.buckets[at][i].value = value,
            (at, Err(i)) => {
                self.buckets[at].insert(i, Node { hash, key, value });
                self.count += 1;
                self.resize();
            }
        }
    }
}

/// A colour of the hash's lists: its scaled pixel and how many pixels it counts.
struct Entry {
    p: [u8; 3],
    count: u32,
}

/// mergesort_pixels on `list` (indexes into the entries) by `axis`, largest first; on a tie
/// the second half's goes first, as Quant.c's merge takes it.
fn merge_sort(list: &[usize], entries: &[Entry], axis: usize) -> Vec<usize> {
    if list.len() <= 1 {
        return list.to_vec();
    }
    let half = list.len().div_ceil(2);
    let a = merge_sort(&list[..half], entries, axis);
    let b = merge_sort(&list[half..], entries, axis);
    let mut out = Vec::with_capacity(list.len());
    let (mut i, mut j) = (0, 0);
    while i < a.len() && j < b.len() {
        if entries[a[i]].p[axis] > entries[b[j]].p[axis] {
            out.push(a[i]);
            i += 1;
        } else {
            out.push(b[j]);
            j += 1;
        }
    }
    out.extend_from_slice(&a[i..]);
    out.extend_from_slice(&b[j..]);
    out
}

/// A box of the median cut: its colours in each axis's order (largest first), its pixels,
/// and the two it was cut into.
struct BoxNode {
    lists: [Vec<usize>; 3],
    pixels: u32,
    volume: Option<i32>,
    children: Option<(usize, usize)>,
}

impl BoxNode {
    /// compute_box_volume: the box's extent along each axis, multiplied.
    fn volume(&mut self, entries: &[Entry]) -> i32 {
        if let Some(v) = self.volume {
            return v;
        }
        let v = if self.lists[0].is_empty() {
            0
        } else {
            let span = |axis: usize| {
                let list = &self.lists[axis];
                i32::from(entries[list[0]].p[axis])
                    - i32::from(entries[list[list.len() - 1]].p[axis])
                    + 1
            };
            span(0) * span(1) * span(2)
        };
        self.volume = Some(v);
        v
    }
}

/// QuantHeap.c's heap of boxes, the one of most pixels on top.
struct Heap {
    slots: Vec<usize>,
}

impl Heap {
    fn cmp(boxes: &[BoxNode], a: usize, b: usize) -> i64 {
        i64::from(boxes[a].pixels as i32) - i64::from(boxes[b].pixels as i32)
    }

    fn add(&mut self, boxes: &[BoxNode], val: usize) {
        self.slots.push(val);
        let mut k = self.slots.len() - 1;
        while k != 1 {
            if Heap::cmp(boxes, val, self.slots[k / 2]) <= 0 {
                break;
            }
            self.slots[k] = self.slots[k / 2];
            k >>= 1;
        }
        self.slots[k] = val;
    }

    fn remove(&mut self, boxes: &[BoxNode]) -> Option<usize> {
        let count = self.slots.len() - 1;
        if count == 0 {
            return None;
        }
        let top = self.slots[1];
        let v = self.slots.pop().expect("a box");
        let count = count - 1;
        if count == 0 {
            return Some(top);
        }
        let mut k = 1;
        while k * 2 <= count {
            let mut l = k * 2;
            if l < count && Heap::cmp(boxes, self.slots[l], self.slots[l + 1]) < 0 {
                l += 1;
            }
            if Heap::cmp(boxes, v, self.slots[l]) > 0 {
                break;
            }
            self.slots[k] = self.slots[l];
            k = l;
        }
        self.slots[k] = v;
        Some(top)
    }
}

/// split (and splitlists): the box cut across the axis its colours spread widest along (by
/// luminance's weights), at the colour where half its pixels are passed.
fn split(boxes: &mut Vec<BoxNode>, at: usize, entries: &[Entry]) {
    let node = &boxes[at];
    let span = |axis: usize| {
        let list = &node.lists[axis];
        i32::from(entries[list[0]].p[axis]) - i32::from(entries[list[list.len() - 1]].p[axis])
    };
    let f = [span(0) * 77, span(1) * 150, span(2) * 29];
    let mut axis = 0;
    for (i, &v) in f.iter().enumerate().skip(1) {
        if f[axis] < v {
            axis = i;
        }
    }
    let list = &node.lists[axis];
    let value = |i: usize| entries[list[i]].p[axis];
    let mut right = vec![false; entries.len()];
    let (mut left_count, mut right_count) = (0u32, 0u32);
    let mut c = 0;
    let mut passed = 0u32;
    while c < list.len() {
        passed += entries[list[c]].count;
        left_count += entries[list[c]].count;
        c += 1;
        if u64::from(passed) * 2 > u64::from(node.pixels) {
            break;
        }
    }
    if c < list.len() {
        let colour = value(c - 1);
        while c < list.len() && value(c) == colour {
            left_count += entries[list[c]].count;
            c += 1;
        }
    }
    let mut n_right = 0;
    for &e in &list[c..] {
        right[e] = true;
        n_right += 1;
        right_count += entries[e].count;
    }
    if n_right == 0 {
        let last = value(list.len() - 1);
        for &e in list.iter().rev() {
            if entries[e].p[axis] != last {
                break;
            }
            right[e] = true;
            left_count -= entries[e].count;
            right_count += entries[e].count;
        }
    }
    let mut halves: [[Vec<usize>; 3]; 2] = Default::default();
    for (i, list) in node.lists.iter().enumerate() {
        for &e in list {
            halves[usize::from(right[e])][i].push(e);
        }
    }
    let [l, r] = halves;
    let left = boxes.len();
    boxes.push(BoxNode {
        lists: l,
        pixels: left_count,
        volume: None,
        children: None,
    });
    boxes.push(BoxNode {
        lists: r,
        pixels: right_count,
        volume: None,
        children: None,
    });
    let node = &mut boxes[at];
    node.children = Some((left, left + 1));
    node.lists = Default::default();
}

/// The leaves of the tree, left first (annotate_hash_table's numbering).
fn leaves(boxes: &[BoxNode], at: usize, out: &mut Vec<usize>) {
    match boxes[at].children {
        Some((l, r)) => {
            leaves(boxes, l, out);
            leaves(boxes, r, out);
        }
        None => out.push(at),
    }
}

/// A colour getcolors found: how many pixels have it, and its bands.
pub type Colour = (u32, Vec<u8>);

/// _DISTSQR.
fn dist(a: [u8; 3], b: [u8; 3]) -> u32 {
    let d = |i: usize| (i32::from(a[i]) - i32::from(b[i])).pow(2);
    (d(0) + d(1) + d(2)) as u32
}

impl Image {
    /// Image.quantize(colors, method=MEDIANCUT) (kmeans 0) of an L or RGB picture: each pixel's
    /// index into the palette, and the palette (as many colours as the median cut found, at
    /// most `colors`). RGBA (which Pillow cuts only by octree or libimagequant) is refused.
    pub fn quantize(&self, colors: usize) -> Result<(Vec<u8>, Vec<[u8; 3]>)> {
        if !(1..=256).contains(&colors) {
            return error("bad number of colors");
        }
        let pixels: Vec<[u8; 3]> = match self.mode {
            Mode::L => self.data.iter().map(|&v| [v, v, v]).collect(),
            Mode::RGB => self
                .data
                .chunks_exact(3)
                .map(|p| [p[0], p[1], p[2]])
                .collect(),
            _ => return error("image has wrong mode"),
        };
        // 1. the colours counted in the hash, at half the precision past 65536 of them
        let mut table = Table::new();
        for &p in &pixels {
            table.count_in(p);
            while table.count > MAX_HASH_ENTRIES {
                table.scale += 1;
                let length = table.length();
                table.rehash(length);
            }
        }
        // 2. the lists, each colour put first as the hash is gone through (so the last one
        // first), then sorted along each axis
        let scale = table.scale;
        let mut entries = Vec::with_capacity(table.count);
        for node in table.buckets.iter().flatten() {
            entries.push(Entry {
                p: node.key.map(|v| v >> scale),
                count: node.value,
            });
        }
        if entries.is_empty() {
            return error("quantization error");
        }
        let start: Vec<usize> = (0..entries.len()).rev().collect();
        let lists = [0, 1, 2].map(|axis| merge_sort(&start, &entries, axis));
        // 3. the median cut: the box of most pixels cut in two, colors - 1 times (a box of
        // one colour is put aside)
        let mut boxes = vec![BoxNode {
            lists,
            pixels: pixels.len() as u32,
            volume: None,
            children: None,
        }];
        let mut heap = Heap { slots: vec![0] };
        heap.add(&boxes, 0);
        'cut: for _ in 1..colors {
            let at = loop {
                let Some(at) = heap.remove(&boxes) else {
                    break 'cut;
                };
                if boxes[at].volume(&entries) != 1 {
                    break at;
                }
            };
            split(&mut boxes, at, &entries);
            let (l, r) = boxes[at].children.expect("cut");
            heap.add(&boxes, l);
            heap.add(&boxes, r);
        }
        // 4. each colour of the hash given its box's number
        let mut order = Vec::new();
        leaves(&boxes, 0, &mut order);
        let mut n = 0u32;
        for &leaf in &order {
            let list = &boxes[leaf].lists[0];
            for &e in list {
                table.set(entries[e].p.map(|v| v << scale), n);
            }
            if !list.is_empty() {
                n += 1;
            }
        }
        let n = n as usize;
        // 5. each box's colour: its pixels' average
        // (the sums in 32 bits, as Quant.c's: past 16 million pixels a box's wrap round)
        let mut sums = vec![[0u32; 3]; n];
        let mut counts = vec![0u32; n];
        let mut boxed = Vec::with_capacity(pixels.len());
        for &p in &pixels {
            let b = table.lookup(p).map(|b| b as usize).filter(|&b| b < n);
            let Some(b) = b else {
                return error("quantization error");
            };
            for i in 0..3 {
                sums[b][i] = sums[b][i].wrapping_add(u32::from(p[i]));
            }
            counts[b] = counts[b].wrapping_add(1);
            boxed.push(b);
        }
        let palette: Vec<[u8; 3]> = (0..n)
            .map(|b| {
                // (C's cast of .5 + the average: a count of 0 makes it NaN, which is 0)
                sums[b].map(|s| (0.5 + f64::from(s) / f64::from(counts[b])) as i64 as u8)
            })
            .collect();
        // 6. each pixel to the nearest colour, looked for from its box's in the order of the
        // colours' distances from it (and their numbers), no farther than twice its own
        let distances: Vec<u32> = (0..n * n)
            .map(|k| dist(palette[k / n], palette[k % n]))
            .collect();
        let sorted: Vec<Vec<usize>> = (0..n)
            .map(|i| {
                let mut row: Vec<usize> = (0..n).collect();
                row.sort_by_key(|&j| (distances[i * n + j], j));
                row
            })
            .collect();
        let indices = pixels
            .iter()
            .zip(&boxed)
            .map(|(&p, &b)| {
                let mut best_dist = dist(palette[b], p);
                let mut best = b;
                let limit = best_dist << 2;
                for &j in &sorted[b] {
                    if distances[b * n + j] > limit {
                        break;
                    }
                    let d = dist(palette[j], p);
                    if d < best_dist {
                        best_dist = d;
                        best = j;
                    }
                }
                best as u8
            })
            .collect();
        Ok((indices, palette))
    }

    /// Image.getcolors(maxcolors) of an RGB or RGBA picture (getcolors32): each colour and how
    /// many pixels have it, in the order of Pillow's open-addressed table; None where there
    /// are more than `maxcolors`. (L's, Pillow's histogram, is in the order of the values.)
    pub fn getcolors(&self, maxcolors: usize) -> Result<Option<Vec<Colour>>> {
        const SIZES: [(u64, u32); 29] = [
            (4, 3),
            (8, 3),
            (16, 3),
            (32, 5),
            (64, 3),
            (128, 3),
            (256, 29),
            (512, 17),
            (1024, 9),
            (2048, 5),
            (4096, 83),
            (8192, 27),
            (16384, 43),
            (32768, 3),
            (65536, 45),
            (131072, 9),
            (262144, 39),
            (524288, 39),
            (1048576, 9),
            (2097152, 5),
            (4194304, 3),
            (8388608, 33),
            (16777216, 27),
            (33554432, 9),
            (67108864, 71),
            (134217728, 39),
            (268435456, 9),
            (536870912, 5),
            (1073741824, 83),
        ];
        let bands = self.bands();
        if self.mode == Mode::L {
            let mut counts = [0u32; 256];
            for &v in &self.data {
                counts[usize::from(v)] += 1;
            }
            let out: Vec<(u32, Vec<u8>)> = (0..=255u8)
                .filter(|&v| counts[usize::from(v)] > 0)
                .map(|v| (counts[usize::from(v)], vec![v]))
                .collect();
            return Ok((out.len() <= maxcolors).then_some(out));
        }
        let Some(&(size, poly)) = SIZES.iter().find(|&&(s, _)| s > maxcolors as u64) else {
            return error("out of memory");
        };
        let mask = (size - 1) as u32;
        // (count, the pixel as Pillow's 32-bit word: RGB's fourth byte masked off)
        let mut table: Vec<(u32, u32)> = vec![(0, 0); size as usize];
        let mut colours = 0usize;
        'pixels: for p in self.data.chunks_exact(bands) {
            let pixel = u32::from(p[0])
                | u32::from(p[1]) << 8
                | u32::from(p[2]) << 16
                | if bands == 4 { u32::from(p[3]) << 24 } else { 0 };
            let mut i = !pixel & mask;
            if table[i as usize].0 == 0 {
                if colours == maxcolors {
                    colours += 1;
                    break 'pixels;
                }
                colours += 1;
                table[i as usize] = (1, pixel);
                continue;
            } else if table[i as usize].1 == pixel {
                table[i as usize].0 += 1;
                continue;
            }
            let mut incr = (pixel ^ (pixel >> 3)) & mask;
            if incr == 0 {
                incr = mask;
            }
            loop {
                i = (i + incr) & mask;
                let v = &mut table[i as usize];
                if v.0 == 0 {
                    if colours == maxcolors {
                        colours += 1;
                        break 'pixels;
                    }
                    colours += 1;
                    *v = (1, pixel);
                    break;
                } else if v.1 == pixel {
                    v.0 += 1;
                    break;
                }
                incr <<= 1;
                if incr > mask {
                    incr ^= poly;
                }
            }
        }
        if colours > maxcolors {
            return Ok(None);
        }
        Ok(Some(
            table
                .iter()
                .filter(|v| v.0 > 0)
                .map(|&(count, pixel)| (count, pixel.to_le_bytes()[..bands].to_vec()))
                .collect(),
        ))
    }
}
