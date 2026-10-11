// What every view can do with the theme being edited: open, save, save as,
// import, export, show it on the panel, fit it to another panel.

import { confirmDialog, promptDialog } from "../ui/dialog.js";
import { toast, toastError } from "../ui/toast.js";
import { endpoints } from "./api.js";
import { clone, commit, docSerial, markClean, openDoc, setSelection, touch, updateDoc } from "./doc.js";
import { t } from "./i18n.js";
import { store } from "./state.js";

export const THEME_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
export const PALETTE_NAME = /^[a-z][a-z0-9_-]{0,31}$/;

export const slug = (text) =>
  (text || "")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);

export async function refreshThemes() {
  try {
    store.set({ themes: await endpoints.themes() });
  } catch (error) {
    toastError(error);
  }
}

export async function refreshPages() {
  try {
    store.set({ pages: await endpoints.pages() });
  } catch {
    store.set({ pages: null }); // a broken config.toml: the pages are not shown
  }
}

/** The theme the panel shows normally (config.toml's), if known. */
export const normalTheme = () => store.get().pages?.pages?.[0]?.theme ?? null;

/** The images and fonts in the open theme's folder. */
export async function refreshAssets() {
  const { doc } = store.get();
  if (!doc?.id) return;
  const serial = docSerial();
  const assets = await endpoints.assets(doc.id).catch(() => []);
  if (serial === docSerial()) updateDoc({ assets });
}

/** The page of the panel a theme is shown on: "normal", "report", a mode's name, or null. */
export function pageOf(id) {
  const pages = store.get().pages?.pages || [];
  return (id && pages.find((page) => page.theme === id)?.id) || null;
}

/** Nothing unsaved, or the user agrees to lose it. */
export async function confirmDiscard() {
  if (!store.get().dirty) return true;
  return confirmDialog({
    title: t("Discard unsaved changes?"),
    message: t("The changes to this theme since it was last saved are lost."),
    confirm: t("Discard"),
    danger: true,
  });
}

/** Open a saved theme for editing (after asking about unsaved changes). */
export async function openTheme(id, { page, ask = true } = {}) {
  if (ask && !(await confirmDiscard())) return false;
  try {
    const data = await endpoints.theme(id);
    openDoc({ theme: data.theme, id, builtin: data.builtin });
    store.set({ page: page === undefined ? pageOf(id) : page });
    refreshAssets();
    if (data.warnings?.length) toast(t("Warning: {text}", { text: data.warnings.join(" · ") }), { kind: "warn" });
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

/** Start a new, unsaved theme. */
export async function startTheme(theme, { ask = true } = {}) {
  if (ask && !(await confirmDiscard())) return false;
  openDoc({ theme, dirty: true });
  store.set({ page: null });
  return true;
}

/** A theme without widgets for a panel model. */
export function blankTheme(model, orientation = "landscape", size = null) {
  const [width, height] = size || model?.[orientation] || [480, 320];
  return {
    format: "libre-panel-theme/1",
    name: t("My theme"),
    author: "",
    license: "CC-BY-4.0",
    description: "",
    display: { model: model?.id || "custom", orientation, width, height },
    palette: {
      bg: "#0b1016",
      text: "#f1f5f9",
      muted: "#8793a6",
      accent: "#22d3ee",
      accent_deep: "#0b6c85",
      card: "#101722",
      card2: "#0b1018",
      edge: "#1b2534",
    },
    font: store.get().specs?.default_font || "builtin:Barlow-Medium",
    background: { color: "@bg", image: null },
    refresh_ms: 1000,
    animation: { smoothing_ms: 400 },
    widgets: [],
  };
}

export async function save() {
  const { doc } = store.get();
  if (!doc) return false;
  if (!doc.id || doc.builtin) return saveAs();
  try {
    await endpoints.saveTheme(doc.id, doc.theme, doc.id);
    markClean();
    toast(t("Saved {id}", { id: doc.id }), { kind: "success" });
    refreshThemes();
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

export async function saveAs() {
  const { doc, themes } = store.get();
  if (!doc) return false;
  const suggestion = slug(doc.theme.name) + (doc.builtin ? "-custom" : "") || "my-theme";
  const id = await promptDialog({
    title: t("Save as…"),
    label: t("Name of the theme's folder"),
    hint: t("Letters, digits, - _ and ."),
    value: doc.builtin || !doc.id ? suggestion : doc.id,
    confirm: t("Save"),
    validate: (value) =>
      THEME_NAME.test(value) && !value.includes("..") ? null : t('"{id}" is not a valid theme name', { id: value }),
  });
  if (!id) return false;
  const mine = themes.some((theme) => theme.id === id && !theme.builtin);
  if (mine && id !== doc.id) {
    const sure = await confirmDialog({ title: t('Overwrite your theme "{id}"?', { id }), confirm: t("Overwrite"), danger: true });
    if (!sure) return false;
  }
  try {
    await endpoints.saveTheme(id, doc.theme, doc.id);
    updateDoc({ id, builtin: false });
    markClean();
    await refreshThemes();
    refreshAssets();
    toast(t("Saved {id}", { id }), { kind: "success" });
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

/** Save the theme and show it: on the page it was opened for (a mode, the round report), else
 * as the panel's theme. */
export async function applyToPanel() {
  const { doc, dirty } = store.get();
  if (!doc) return false;
  if ((dirty || !doc.id) && !(await save())) return false;
  const { doc: saved, page } = store.get();
  try {
    store.set({ pages: await endpoints.setPage(page || "normal", { theme: saved.id }) });
    if (!page) store.set({ page: "normal" });
    toast(t('"{id}" is now shown on the panel', { id: saved.id }), { kind: "success" });
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

/** Ask for a file (accept: ".json", "image/*", ...); null when none was picked. */
export function pickFile(accept) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.addEventListener("change", () => resolve(input.files[0] || null), { once: true });
    input.addEventListener("cancel", () => resolve(null), { once: true });
    input.click();
  });
}

export function exportTheme() {
  const { doc } = store.get();
  if (!doc) return;
  const blob = new Blob([`${JSON.stringify(doc.theme, null, 2)}\n`], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${slug(doc.theme.name) || "theme"}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 2000);
  toast(t("Exported {file}", { file: link.download }));
}

export async function importTheme(file) {
  const chosen = file || (await pickFile(".json,application/json"));
  if (!chosen || !(await confirmDiscard())) return false;
  try {
    const theme = JSON.parse(await chosen.text());
    await endpoints.render(theme); // checks it before anything is replaced
    openDoc({ theme, dirty: true });
    store.set({ page: null });
    toast(t("Imported {file}. Images and fonts of the original theme are not included; save it and add them.", { file: chosen.name }), {
      kind: "warn",
    });
    return true;
  } catch (error) {
    toast(t("Import failed: {error}", { error: error.message }), { kind: "error" });
    return false;
  }
}

/** Upload a file into the theme's folder; returns its path ("assets/logo.png") or null. */
export async function uploadAsset(accept, file) {
  const { doc } = store.get();
  if (!doc?.id || doc.builtin) {
    toast(t("Save the theme under your own name (Save as…) before adding files."), { kind: "warn" });
    return null;
  }
  const chosen = file || (await pickFile(accept));
  if (!chosen) return null;
  const name = chosen.name.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "");
  try {
    const data = await endpoints.uploadAsset(doc.id, name, await chosen.arrayBuffer());
    await refreshAssets();
    toast(t("Added {path}", { path: data.path }), { kind: "success" });
    return data.path;
  } catch (error) {
    toastError(error);
    return null;
  }
}

/** Replace the whole theme as one undo step (another panel, a look). */
export function replaceTheme(next, key = null) {
  commit(key);
  store.set((state) => ({ doc: { ...state.doc, theme: next } }));
  touch();
}

// The theme before a run of panel changes: flipping between panels never compounds.
let adaptBase = null;

/** Fit the theme to another panel: {model, orientation, mode: "scale"|"keep", width, height}. */
export async function adaptPanel(options) {
  const { doc, rev } = store.get();
  if (!doc) return;
  const fresh = !adaptBase || adaptBase.rev !== rev || adaptBase.serial !== docSerial();
  const base = fresh ? clone(doc.theme) : adaptBase.theme;
  try {
    const data = await endpoints.adapt({ theme: base, ...options });
    replaceTheme(data.theme, "adapt");
    setSelection([]);
    adaptBase = { theme: base, rev: store.get().rev, serial: docSerial() };
  } catch (error) {
    toastError(error);
  }
}
