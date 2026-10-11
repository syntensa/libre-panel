// The theme being edited, its selection and its history (undo, redo).
//
//   edit("prop:clock:color", (theme) => { widgetById("clock").color = "@accent"; });
//
// edit() records the theme before the change, so it is one undo step; changes
// with the same key in quick succession (typing into one field) become one.
// For a change in many small steps (dragging), call commit() once at its start
// and touch() after each step.

import { store } from "./state.js";

const HISTORY_LIMIT = 200;
const TOGETHER_MS = 900;

let undoStack = [];
let redoStack = [];
let last = { key: null, time: 0 };
let serial = 0;

/** Counts the documents opened: an answer about an earlier one is dropped. */
export const docSerial = () => serial;

export const theme = () => store.get().doc?.theme ?? null;
export const widgets = () => theme()?.widgets ?? [];
export const widgetById = (id) => widgets().find((widget) => widget.id === id) ?? null;
export const selection = () => store.get().selection;
export const selectedWidgets = () => {
  const ids = new Set(selection());
  return widgets().filter((widget) => ids.has(widget.id));
};
export const single = () => (selection().length === 1 ? widgetById(selection()[0]) : null);
export const isModule = (widget) => widget?.type === "module";

const historyState = () => ({ canUndo: undoStack.length > 0, canRedo: redoStack.length > 0 });

/** Record the theme as it is now, before a change. */
export function commit(key = null) {
  if (!theme()) return;
  const now = Date.now();
  const together = key !== null && key === last.key && now - last.time < TOGETHER_MS;
  last = { key, time: now };
  if (!together) {
    undoStack.push(JSON.stringify(theme()));
    if (undoStack.length > HISTORY_LIMIT) undoStack.shift();
    redoStack = [];
  }
  store.set({ dirty: true, ...historyState() });
}

/** The theme was changed in place: views and the preview follow. */
export function touch() {
  store.set((state) => ({ rev: state.rev + 1 }));
}

/** One undoable change of the theme. */
export function edit(key, change) {
  const current = theme();
  if (!current) return;
  commit(key);
  change(current);
  touch();
}

function restore(json) {
  const restored = JSON.parse(json);
  const ids = new Set((restored.widgets || []).map((widget) => widget.id));
  last = { key: null, time: 0 };
  store.set((state) => ({
    doc: { ...state.doc, theme: restored },
    selection: state.selection.filter((id) => ids.has(id)),
    dirty: true,
    rev: state.rev + 1,
    ...historyState(),
  }));
}

export function undo() {
  if (!undoStack.length) return false;
  redoStack.push(JSON.stringify(theme()));
  restore(undoStack.pop());
  return true;
}

export function redo() {
  if (!redoStack.length) return false;
  undoStack.push(JSON.stringify(theme()));
  restore(redoStack.pop());
  return true;
}

/** Open a theme for editing: {theme, id (null: not saved), builtin, assets, dirty}. */
export function openDoc({ theme: opened, id = null, builtin = false, assets = [], dirty = false }) {
  serial++;
  undoStack = [];
  redoStack = [];
  last = { key: null, time: 0 };
  store.set((state) => ({
    doc: { theme: opened, id, builtin, assets },
    selection: [],
    dirty,
    rev: state.rev + 1,
    ...historyState(),
  }));
}

/** Change what is known about the open document (after "save as": its id). */
export function updateDoc(patch) {
  store.set((state) => ({ doc: state.doc && { ...state.doc, ...patch } }));
}

export function markClean() {
  store.set({ dirty: false });
}

export function setSelection(ids) {
  const known = new Set(widgets().map((widget) => widget.id));
  store.set({ selection: [...new Set(ids)].filter((id) => known.has(id)) });
}

export function toggleSelected(id) {
  const ids = selection();
  setSelection(ids.includes(id) ? ids.filter((other) => other !== id) : [...ids, id]);
}

/** An id that no widget has yet: "ring", then "ring-2", "ring-3", ... */
export function uniqueId(base) {
  const taken = new Set(widgets().map((widget) => widget.id));
  const stem = String(base || "widget").replace(/-\d+$/, "") || "widget";
  if (!taken.has(stem)) return stem;
  for (let i = 2; ; i++) if (!taken.has(`${stem}-${i}`)) return `${stem}-${i}`;
}

export const clone = (value) => JSON.parse(JSON.stringify(value));
