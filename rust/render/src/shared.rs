//! What every renderer of the process draws with and keeps between frames: the fans' rotors
//! and frames, the weather's symbols, the moon's discs and halos, the agents' signs. The
//! app's pictures of every theme fill them with what those themes draw with, for good: this
//! is light mode's part in them (render/kept.py's mark_all, unmark_all and shed_all).
//!
//! [`mark`] has them note from then on what is drawn with; a while later [`shed`] lets go of
//! the rest. So what the panel draws with stays, and nothing of it has to be made again.
//!
//! What a panel has drawn with stays whether or not it was asked for in that while
//! ([`for_a_panel`]): a fan that stands still, a moon whose light does not change and the
//! night's symbols by day are drawn from what the renderer keeps of them, and ask for
//! nothing here until they move again. (kept.py tells by the while alone, and makes those
//! again: a rotor's layers take the panel some tens of milliseconds, the moon's face 90.)

use std::cell::Cell;

thread_local! {
    /// this thread draws for a panel (its loop said so): what it draws with is a panel's
    static FOR_A_PANEL: Cell<bool> = const { Cell::new(false) };
}

/// The thread that calls draws for a panel from now on (a panel's loop says so, each
/// frame): what it draws with and keeps here is a panel's, and stays at [`shed`].
pub fn for_a_panel() {
    FOR_A_PANEL.with(|own| own.set(true));
}

/// Whether this thread draws for a panel (else: a picture for the app).
pub(crate) fn a_panels() -> bool {
    FOR_A_PANEL.with(Cell::get)
}

/// What `draw` draws with is a panel's, on this thread and for as long as it draws (tests).
#[cfg(test)]
pub(crate) fn as_a_panel<T>(draw: impl FnOnce() -> T) -> T {
    let before = FOR_A_PANEL.with(|own| own.replace(true));
    let drawn = draw();
    FOR_A_PANEL.with(|own| own.set(before));
    drawn
}

/// mark_all: from now on the shared caches note what is drawn with.
pub fn mark() {
    crate::fan::mark();
    crate::symbols::mark();
    crate::moon::mark();
    crate::agent::mark();
}

/// unmark_all: they no longer note (the app was opened again before they let go: all stays).
pub fn unmark() {
    crate::fan::unmark();
    crate::symbols::unmark();
    crate::moon::unmark();
    crate::agent::unmark();
}

/// shed_all: what was not drawn with since [`mark`], and never by a panel, goes. The bytes
/// of the fans' and the symbols' that went, and how many pictures of the moon and of the
/// agents' signs.
pub fn shed() -> (usize, usize) {
    let bytes = crate::fan::shed() + crate::symbols::shed();
    let pictures = crate::moon::shed() + crate::agent::shed();
    (bytes, pictures)
}

/// The bytes the shared caches hold at the moment (the app's load report says them: with
/// the app closed a while, what the panels draw with).
pub fn bytes() -> usize {
    crate::fan::bytes() + crate::symbols::bytes() + crate::moon::bytes() + crate::agent::bytes()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_thread_draws_for_a_panel_once_it_says_so_and_only_it() {
        let drew = std::thread::spawn(|| {
            let before = a_panels();
            for_a_panel();
            let another = std::thread::spawn(a_panels).join().unwrap_or(true);
            (before, a_panels(), another)
        });
        assert_eq!(drew.join().ok(), Some((false, true, false)));
        // for as long as a test draws as one
        let before = a_panels();
        assert!(as_a_panel(a_panels));
        assert_eq!(a_panels(), before);
    }
}
