//! Large blocks kept for the next frame (as the app's heap.rs keeps them). A frame's boxes of
//! pixels (Scene::frame) and the layers that come for it (wire.rs) are blocks of tens to
//! hundreds of kilobytes, made and let go again every frame. glibc keeps such a block for the
//! next one asked; Windows' heap gives the memory of large free blocks back to the system and
//! asks for it again the next time, whose pages the system then zeroes and maps one by one as
//! they are first touched: a hundred page faults a frame.
//!
//! [`Keeper`] keeps blocks from [`LARGE`] on (their sizes rounded up to [`GRAIN`]) when they
//! are let go, up to [`SLOTS`] of them and [`MOST`] bytes, and hands one of the same size out
//! again before it asks the allocator below. A block not asked for again within [`AGE_MS`]
//! goes back (at the next block let go, or at [`tick`]), so what is kept is what the last
//! frames used. Smaller blocks, and those of an alignment above 16, go straight through.
//!
//! It is on where [`install`] finds it wanted, once at the start before any large block is
//! had: on Windows unless `LP_HEAP_KEEP=0`, elsewhere only with `LP_HEAP_KEEP=1`.

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};

/// The size from which a block is kept.
pub const LARGE: usize = 64 << 10;
/// What sizes are rounded up to, so that blocks of nearly the same size meet.
pub const GRAIN: usize = 16 << 10;
/// How many blocks are kept at most.
pub const SLOTS: usize = 64;
/// How many bytes are kept at most.
pub const MOST: usize = 16 << 20;
/// How long a block is kept unasked (milliseconds on the clock [`Keeper::tick`] gives).
pub const AGE_MS: u64 = 2000;
/// The alignment of a kept block (the system allocator's own on 64-bit computers).
const ALIGN: usize = 16;

/// A block kept: (rounded size, address, when it was let go).
type Block = (usize, usize, u64);

/// The blocks let go and kept, oldest first.
struct Kept {
    blocks: [Block; SLOTS],
    count: usize,
    bytes: usize,
}

impl Kept {
    fn remove(&mut self, at: usize) -> Block {
        let block = self.blocks[at];
        let count = self.count;
        self.blocks.copy_within(at + 1..count, at);
        self.count -= 1;
        self.bytes -= block.0;
        block
    }
}

/// What the keeper did, counted while it is on.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Counts {
    /// large blocks asked for
    pub asked: u64,
    /// of them, handed out from those kept
    pub kept: u64,
    /// bytes kept now
    pub holding: u64,
}

/// An allocator that keeps large blocks for the next frame, over `A`.
pub struct Keeper<A> {
    below: A,
    on: AtomicBool,
    kept: Mutex<Kept>,
    now_ms: AtomicU64,
    asked: AtomicU64,
    reused: AtomicU64,
}

impl<A> Keeper<A> {
    pub const fn new(below: A) -> Keeper<A> {
        Keeper {
            below,
            on: AtomicBool::new(false),
            kept: Mutex::new(Kept {
                blocks: [(0, 0, 0); SLOTS],
                count: 0,
                bytes: 0,
            }),
            now_ms: AtomicU64::new(0),
            asked: AtomicU64::new(0),
            reused: AtomicU64::new(0),
        }
    }

    pub fn counts(&self) -> Counts {
        let holding = self.kept.lock().map_or(0, |kept| kept.bytes as u64);
        Counts {
            asked: self.asked.load(Ordering::Relaxed),
            kept: self.reused.load(Ordering::Relaxed),
            holding,
        }
    }

    /// The rounded size of a block of `layout`, if it is one the keeper keeps.
    fn kept_size(&self, layout: Layout) -> Option<usize> {
        (layout.size() >= LARGE && layout.align() <= ALIGN && self.on.load(Ordering::Relaxed))
            .then(|| layout.size().div_ceil(GRAIN) * GRAIN)
    }

    /// The newest of the blocks kept of `size`, taken.
    fn take(&self, size: usize) -> Option<*mut u8> {
        let mut kept = self.kept.lock().ok()?;
        let at = kept.blocks[..kept.count]
            .iter()
            .rposition(|&(s, _, _)| s == size)?;
        Some(kept.remove(at).1 as *mut u8)
    }
}

impl<A: GlobalAlloc> Keeper<A> {
    /// Keeping on or off: set once, before any large block is had (see the module's words).
    fn set_on(&self, on: bool) {
        self.on.store(on, Ordering::Relaxed);
    }

    /// The clock moved on to `now_ms`; the blocks kept longer than [`AGE_MS`] go back.
    pub fn tick(&self, now_ms: u64) {
        self.now_ms.fetch_max(now_ms, Ordering::Relaxed);
        self.let_go(|kept, now| kept.count > 0 && now.saturating_sub(kept.blocks[0].2) > AGE_MS);
    }

    /// Every block kept given back to the allocator below.
    #[cfg(test)]
    pub fn release(&self) {
        self.let_go(|kept, _| kept.count > 0);
    }

    /// The oldest blocks given back while `more` says so, a few at a time outside the lock.
    fn let_go(&self, more: impl Fn(&Kept, u64) -> bool) {
        loop {
            let mut gone = [(0, 0, 0); 8];
            let mut going = 0;
            if let Ok(mut kept) = self.kept.lock() {
                let now = self.now_ms.load(Ordering::Relaxed);
                while going < gone.len() && more(&kept, now) {
                    gone[going] = kept.remove(0);
                    going += 1;
                }
            }
            for &block in &gone[..going] {
                // SAFETY: blocks had from `below` with this layout that no one holds now
                unsafe { self.free_below(block) };
            }
            if going < gone.len() {
                return;
            }
        }
    }

    /// # Safety
    /// A block of `below` had with (size, ALIGN) that no one holds any more.
    unsafe fn free_below(&self, (size, address, _): Block) {
        // SAFETY: (the caller's)
        unsafe {
            self.below.dealloc(
                address as *mut u8,
                Layout::from_size_align_unchecked(size, ALIGN),
            );
        }
    }

    /// `ptr`, a block of the rounded `size`, kept; the oldest go where they are too old or
    /// there is no room, and `ptr` itself where there is still none.
    ///
    /// # Safety
    /// A block of `below` had with (size, ALIGN) that no one holds any more.
    unsafe fn keep(&self, ptr: *mut u8, size: usize) {
        let mut gone = [(0, 0, 0); 8];
        let mut going = 0;
        let mut kept_it = false;
        if let Ok(mut kept) = self.kept.lock() {
            let now = self.now_ms.load(Ordering::Relaxed);
            while going < gone.len()
                && kept.count > 0
                && (kept.count == SLOTS
                    || kept.bytes + size > MOST
                    || now.saturating_sub(kept.blocks[0].2) > AGE_MS)
            {
                gone[going] = kept.remove(0);
                going += 1;
            }
            if kept.count < SLOTS && kept.bytes + size <= MOST {
                let count = kept.count;
                kept.blocks[count] = (size, ptr as usize, now);
                kept.count += 1;
                kept.bytes += size;
                kept_it = true;
            }
        }
        // SAFETY: (the caller's, and those taken from the list, which no one holds)
        unsafe {
            for &block in &gone[..going] {
                self.free_below(block);
            }
            if !kept_it {
                self.free_below((size, ptr as usize, 0));
            }
        }
    }
}

// SAFETY: a block is handed out once (taken from the list under its lock) and is always of at
// least the size and the alignment asked for; every block the keeper gives back to `below` was
// had from it with the layout it is given back with (the rounded size and ALIGN for large
// blocks while the keeper is on, which it is from before the first large block to the end).
unsafe impl<A: GlobalAlloc> GlobalAlloc for Keeper<A> {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let Some(size) = self.kept_size(layout) else {
            // SAFETY: (the caller's)
            return unsafe { self.below.alloc(layout) };
        };
        self.asked.fetch_add(1, Ordering::Relaxed);
        if let Some(ptr) = self.take(size) {
            self.reused.fetch_add(1, Ordering::Relaxed);
            return ptr;
        }
        // SAFETY: a size above zero, a power of two for the alignment
        unsafe {
            self.below
                .alloc(Layout::from_size_align_unchecked(size, ALIGN))
        }
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        let Some(size) = self.kept_size(layout) else {
            // SAFETY: (the caller's)
            return unsafe { self.below.alloc_zeroed(layout) };
        };
        self.asked.fetch_add(1, Ordering::Relaxed);
        if let Some(ptr) = self.take(size) {
            self.reused.fetch_add(1, Ordering::Relaxed);
            // SAFETY: the block holds at least the size asked for
            unsafe { ptr.write_bytes(0, layout.size()) };
            return ptr;
        }
        // SAFETY: as in alloc
        unsafe {
            self.below
                .alloc_zeroed(Layout::from_size_align_unchecked(size, ALIGN))
        }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        match self.kept_size(layout) {
            // SAFETY: a block the keeper handed out, had from below with (size, ALIGN)
            Some(size) => unsafe { self.keep(ptr, size) },
            // SAFETY: (the caller's)
            None => unsafe { self.below.dealloc(ptr, layout) },
        }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        // SAFETY: a size above zero with the old alignment (the caller's)
        let grown = unsafe { Layout::from_size_align_unchecked(new_size, layout.align()) };
        let (old, new) = (self.kept_size(layout), self.kept_size(grown));
        if old.is_none() && new.is_none() {
            // SAFETY: (the caller's)
            return unsafe { self.below.realloc(ptr, layout, new_size) };
        }
        if old.is_some() && old == new {
            return ptr; // (the same rounded block holds it)
        }
        // SAFETY: (the caller's); the new block holds the lesser of the two sizes
        unsafe {
            let moved = self.alloc(grown);
            if !moved.is_null() {
                std::ptr::copy_nonoverlapping(ptr, moved, layout.size().min(new_size));
                self.dealloc(ptr, layout);
            }
            moved
        }
    }
}

/// The engine's allocator (main.rs makes one the global allocator and hands it to
/// [`install`]).
pub type Global = Keeper<System>;

static GLOBAL: OnceLock<&'static Global> = OnceLock::new();

/// The global allocator `global` known to the engine, and switched on where wanted
/// (`LP_HEAP_KEEP`: on Windows on unless "0", elsewhere off unless "1"). First thing in main.
pub fn install(global: &'static Global) {
    let wanted = match std::env::var_os("LP_HEAP_KEEP") {
        Some(v) if v == "0" => false,
        Some(v) if v == "1" => true,
        _ => cfg!(windows),
    };
    if GLOBAL.set(global).is_ok() {
        global.set_on(wanted);
    }
}

/// The global keeper's clock moved on (each frame): blocks unasked too long go back. Where
/// LP_PROFILE is set, what it did is said every ten seconds.
pub fn tick() {
    static BEGAN: OnceLock<std::time::Instant> = OnceLock::new();
    static SAID: Mutex<(u64, Counts)> = Mutex::new((
        0,
        Counts {
            asked: 0,
            kept: 0,
            holding: 0,
        },
    ));
    let Some(global) = GLOBAL.get() else {
        return;
    };
    let now = BEGAN
        .get_or_init(std::time::Instant::now)
        .elapsed()
        .as_millis() as u64;
    global.tick(now);
    static PROFILING: OnceLock<bool> = OnceLock::new();
    let profiling =
        *PROFILING.get_or_init(|| std::env::var_os("LP_PROFILE").is_some_and(|v| !v.is_empty()));
    if !profiling || !global.on.load(Ordering::Relaxed) {
        return;
    }
    let Ok(mut said) = SAID.lock() else {
        return;
    };
    if now >= said.0 + 10_000 {
        let counts = global.counts();
        let span = (now - said.0) as f64 / 1000.0;
        eprintln!(
            "libre-panel-engine: heap: {:.0} large blocks a second, {:.0} of them kept ones; \
             {:.1} MB kept",
            (counts.asked - said.1.asked) as f64 / span,
            (counts.kept - said.1.kept) as f64 / span,
            counts.holding as f64 / f64::from(1 << 20)
        );
        *said = (now, counts);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn layout(size: usize) -> Layout {
        Layout::from_size_align(size, 8).unwrap()
    }

    fn rounded(size: usize) -> u64 {
        (size.div_ceil(GRAIN) * GRAIN) as u64
    }

    #[test]
    fn a_large_block_let_go_is_handed_out_again_for_the_same_rounded_size() {
        let keeper = Keeper::new(System);
        keeper.set_on(true);
        unsafe {
            let a = keeper.alloc(layout(1_000_000));
            a.write_bytes(7, 1_000_000);
            keeper.dealloc(a, layout(1_000_000));
            assert_eq!(keeper.counts().holding, rounded(1_000_000));
            let b = keeper.alloc(layout(1_000_100)); // (the same rounded size)
            assert_eq!(a, b);
            let z = keeper.alloc_zeroed(layout(2_000_000));
            assert!(std::slice::from_raw_parts(z, 2_000_000)
                .iter()
                .all(|&v| v == 0));
            z.write_bytes(9, 2_000_000);
            keeper.dealloc(z, layout(2_000_000));
            let y = keeper.alloc_zeroed(layout(2_000_000)); // (kept, and zeroed again)
            assert_eq!(y, z);
            assert!(std::slice::from_raw_parts(y, 2_000_000)
                .iter()
                .all(|&v| v == 0));
            keeper.dealloc(y, layout(2_000_000));
            keeper.dealloc(b, layout(1_000_100));
            let counts = keeper.counts();
            assert_eq!((counts.asked, counts.kept), (4, 2));
            assert_eq!(counts.holding, rounded(1_000_000) + rounded(2_000_000));
            keeper.release();
            assert_eq!(keeper.counts().holding, 0);
        }
    }

    #[test]
    fn small_blocks_and_an_off_keeper_go_straight_through() {
        let keeper = Keeper::new(System);
        unsafe {
            let a = keeper.alloc(layout(1_000_000));
            keeper.dealloc(a, layout(1_000_000));
        }
        keeper.set_on(true);
        unsafe {
            let s = keeper.alloc(layout(1000));
            keeper.dealloc(s, layout(1000));
            let odd = Layout::from_size_align(1 << 20, 4096).unwrap(); // (aligned above 16)
            let o = keeper.alloc(odd);
            assert_eq!(o as usize % 4096, 0);
            keeper.dealloc(o, odd);
        }
        assert_eq!(keeper.counts(), Counts::default());
    }

    #[test]
    fn what_is_kept_stays_within_its_bytes_and_slots_the_oldest_going_first() {
        let keeper = Keeper::new(System);
        keeper.set_on(true);
        let size = 8 << 20;
        unsafe {
            let blocks: Vec<*mut u8> = (0..16).map(|_| keeper.alloc(layout(size))).collect();
            for &b in &blocks {
                keeper.dealloc(b, layout(size));
            }
            assert_eq!(keeper.counts().holding as usize, MOST / size * size);
            let again = keeper.alloc(layout(size)); // (the newest kept first)
            assert_eq!(again, blocks[15]);
            keeper.dealloc(again, layout(size));
            let small: Vec<*mut u8> = (0..SLOTS + 8)
                .map(|_| keeper.alloc(layout(LARGE)))
                .collect();
            for &b in &small {
                keeper.dealloc(b, layout(LARGE));
            }
            {
                let kept = keeper.kept.lock().unwrap();
                assert!(kept.count <= SLOTS && kept.bytes <= MOST);
                assert_eq!(kept.blocks[kept.count - 1].1, small[SLOTS + 7] as usize);
            }
            keeper.release();
            assert_eq!(keeper.counts().holding, 0);
        }
    }

    #[test]
    fn blocks_unasked_for_a_while_go_back() {
        let keeper = Keeper::new(System);
        keeper.set_on(true);
        unsafe {
            keeper.tick(10_000);
            let a = keeper.alloc(layout(1 << 20));
            let b = keeper.alloc(layout(3 << 20));
            keeper.dealloc(a, layout(1 << 20));
            keeper.tick(11_000);
            keeper.dealloc(b, layout(3 << 20));
            keeper.tick(12_000 + 1); // (a kept 2001 ms: gone; b 1001 ms: kept)
            assert_eq!(keeper.counts().holding, 3 << 20);
            keeper.tick(13_500);
            assert_eq!(keeper.counts().holding, 0);
        }
    }

    #[test]
    fn a_block_grown_or_shrunk_keeps_its_bytes() {
        let keeper = Keeper::new(System);
        keeper.set_on(true);
        unsafe {
            let a = keeper.alloc(layout(100_000));
            for i in 0..100_000 {
                *a.add(i) = (i % 251) as u8;
            }
            let b = keeper.realloc(a, layout(100_000), 3_000_000); // small to large
            let c = keeper.realloc(b, layout(3_000_000), 3_010_000); // the same rounded block
            assert_eq!(b, c);
            let d = keeper.realloc(c, layout(3_010_000), 6_000_000); // large to larger
            let e = keeper.realloc(d, layout(6_000_000), 50_000); // large to small
            for i in 0..50_000 {
                assert_eq!(*e.add(i), (i % 251) as u8);
            }
            keeper.dealloc(e, layout(50_000));
            keeper.release();
        }
    }
}
