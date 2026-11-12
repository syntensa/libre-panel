//! libre-panel: Libre Panel's program for the terminal (lib.rs is all of it).

/// Large blocks kept for the next frame (heap.rs), on Windows.
#[global_allocator]
static HEAP: lp_app::heap::Global = lp_app::heap::Global::new(std::alloc::System);

fn main() -> std::process::ExitCode {
    lp_app::heap::install(&HEAP);
    lp_app::main("start")
}
