// Which panel the app is about (the header's "Panel 1 | Panel 2", there while more panels are
// set up): Live shows its picture, Design edits the theme it shows, in its size and the way it is
// mounted, and puts what is saved on that panel and its pages. Start and Settings are about all of
// them. A choice of a panel that is gone is the first panel again.

import { endpoints } from "./api.js";
import { setPref, store, useStore } from "./state.js";

/** The number of the panel the app is about: 1 for the first, 2 for the first beside it, ... */
export function target(state = store.get()) {
  const chosen = Number(state.prefs?.target) || 1;
  const panels = state.settings?.config?.panels;
  if (!panels) return chosen; // (before config.toml is read)
  const running = state.app?.panel?.more?.length || 0; // (Live goes by what the app says)
  return chosen >= 1 && chosen <= 1 + Math.max(panels.length, running) ? chosen : 1;
}

export const useTarget = () => useStore((s) => target(s));

/** The ``[[panels]]`` table of the panel the app is about; null for the first. */
export function targetTable(state = store.get()) {
  const n = target(state);
  return n > 1 ? state.settings?.config?.panels?.[n - 2] || null : null;
}

/** What the running app says of the panel the app is about: its state, the theme it shows, its
 * model and the size of its frames (null while it says nothing of it). */
export function targetStatus(state = store.get()) {
  const n = target(state);
  const panel = state.app?.panel || null;
  if (n === 1) return panel;
  return (panel?.more || []).find((item) => item.index === n - 2) || null;
}

/** The pages Design works with: the first panel's (``pages``), else those of the panel chosen
 * (``beside``: its theme, and those for a game, music and an alarm). */
export const designPages = (state = store.get()) => (target(state) > 1 ? state.beside : state.pages);

/** Write a page of the panel chosen ({theme}, and for the first panel {on, fps, ...}). */
export async function setDesignPage(page, change) {
  const n = target();
  const data = await endpoints.setPage(page, change, n);
  store.set(n > 1 ? { beside: data } : { pages: data });
  return data;
}

/** The pages of the panel chosen, beside the first's (Design's tabs and pages). */
export async function refreshBeside() {
  const n = target();
  if (n === 1) return store.set({ beside: null });
  try {
    store.set({ beside: await endpoints.pages(n) });
  } catch {
    store.set({ beside: null });
  }
}

/** Make the app about panel ``n``; ``then`` (Design) opens what it shows. */
export async function chooseTarget(n, then = null) {
  setPref("target", n);
  await refreshBeside();
  if (then) await then(n);
}
