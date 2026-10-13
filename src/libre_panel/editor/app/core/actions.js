// What every view can do with the theme being edited: open, save, save as,
// import, export, show it on the panel, fit it to another panel.

import { html } from "../lib/preact.js";
import { confirmDialog, openDialog, promptDialog } from "../ui/dialog.js";
import { toast, toastError } from "../ui/toast.js";
import { endpoints } from "./api.js";
import { clone, commit, docSerial, markClean, openDoc, setSelection, touch, updateDoc } from "./doc.js";
import { t } from "./i18n.js";
import { VERTICAL, isVersion, versionFor } from "./orientation.js";
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
    // a theme's version for a panel on end ("lumen-vertical") is not listed: it is chosen as
    // its theme, and shown in its place where the panel stands on end
    const all = await endpoints.themes();
    store.set({ themes: all.filter((item) => !isVersion(item, all)), versions: all.filter((item) => isVersion(item, all)) });
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

/** A theme's name from the theme list (its id where it has none). */
export const themeName = (id) =>
  [...store.get().themes, ...(store.get().versions || [])].find((theme) => theme.id === id)?.name || id;

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

/** Nothing unsaved, or the user saves it first or agrees to lose it. Keeping the work is one
 * click (Save, the default) instead of Cancel, save and the same way again. */
export async function confirmDiscard() {
  const { dirty, doc } = store.get();
  if (!dirty) return true;
  const title = t("Discard unsaved changes?");
  const choice = await openDialog(
    (close) => html`
      <h2 class="dialog-title">${title}</h2>
      <p class="dialog-text">${t("Your changes to “{name}” are not saved yet: save them, or they are lost.", { name: doc.theme.name || themeName(doc.id) })}</p>
      <div class="dialog-actions">
        <button type="button" class="btn" onClick=${() => close(null)}>${t("Cancel")}</button>
        <span class="grow"></span>
        <button type="button" class="btn btn-danger" onClick=${() => close("discard")}>${t("Discard")}</button>
        <button type="button" class="btn btn-primary" autofocus onClick=${() => close("save")}>${t("Save")}</button>
      </div>
    `,
    { label: title },
  );
  return choice === "save" ? save() : choice === "discard";
}

/** Open a saved theme for editing (after asking about unsaved changes). */
export async function openTheme(id, { page, ask = true } = {}) {
  if (ask && !(await confirmDiscard())) return false;
  try {
    const shown = versionFor(id); // the version the panel shows (Lumen Vertical on a bar on end)
    const data = await endpoints.theme(shown);
    openDoc({ theme: data.theme, id: shown, builtin: data.builtin });
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
    toast(t("Saved {id}", { id: doc.theme.name || doc.id }), { kind: "success" });
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
  // a copy of a built-in theme that kept its name was a second "Lumen" in every list: it is
  // "Lumen (mine)", and its folder is named after that, free of the others'
  const copy = doc.builtin && doc.theme.name === themeName(doc.id) ? t("{name} (mine)", { name: doc.theme.name }) : null;
  const all = [...themes, ...(store.get().versions || [])];
  const taken = new Set(all.map((theme) => theme.id));
  // a copy of a version for a panel on end keeps its ending after the copy's name:
  // "lumen-mine-vertical" goes with "lumen-mine", a copy of Lumen, as "lumen-vertical" with "lumen"
  const end = doc.builtin && doc.id && isVersion({ id: doc.id }, all) ? VERTICAL : "";
  const first = end
    ? slug(copy ? t("{name} (mine)", { name: themeName(doc.id.slice(0, -end.length)) }) : doc.theme.name)
    : slug(copy || doc.theme.name) + (doc.builtin && !copy ? "-custom" : "") || "my-theme";
  let suggestion = first + end;
  for (let n = 2; doc.builtin && taken.has(suggestion); n++) suggestion = `${first}-${n}${end}`;
  const id = await promptDialog({
    title: t("Save as…"),
    // why a person who only wanted it on the panel is asked for a name
    text: doc.builtin
      ? t("“{name}” is built in and cannot be changed: your changes are saved as a theme of your own.", { name: themeName(doc.id) })
      : null,
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
    if (copy) {
      commit("theme:name");
      doc.theme.name = copy;
      touch();
    }
    await endpoints.saveTheme(id, doc.theme, doc.id);
    updateDoc({ id, builtin: false });
    markClean();
    await refreshThemes();
    refreshAssets();
    toast(t("Saved {id}", { id: doc.theme.name || id }), { kind: "success" });
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

/**
 * Where the open theme stands with the panel ("To the panel" says it): "shown" when it is saved
 * and the theme of the page it is open for (its version for a panel on end counts), and that
 * page is on; "changed" when it is that page's theme with changes not saved; else "other".
 */
export function panelState({ doc, dirty, page, pages } = store.get()) {
  const target = (pages?.pages || []).find((item) => item.id === (page || "normal"));
  const there = Boolean(doc?.id && target?.theme && (target.theme === doc.id || versionFor(target.theme) === doc.id));
  if (!there || (target.id !== "normal" && !target.on)) return "other";
  return dirty ? "changed" : "shown";
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
    toast(t('"{id}" is now shown on the panel', { id: saved.theme.name || saved.id }), { kind: "success" });
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

/**
 * Show a saved theme on the panel as its normal theme, without opening it first (Start's
 * gallery). The open theme follows when nothing of it would be lost, so Design and Look
 * show what is on the panel. True when it is done.
 */
export async function showOnPanel(id) {
  try {
    store.set({ pages: await endpoints.setPage("normal", { theme: id }) });
  } catch (error) {
    toastError(error);
    return false;
  }
  const { doc, dirty } = store.get();
  if (!dirty && doc?.id !== id) await openTheme(id, { ask: false, page: "normal" });
  else if (doc?.id === id && !store.get().page) store.set({ page: "normal" });
  toast(t('"{id}" is now shown on the panel', { id: themeName(id) }), { kind: "success" });
  return true;
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
  const serial = docSerial();
  try {
    const data = await endpoints.adapt({ theme: base, ...options });
    if (docSerial() !== serial) return; // another theme was opened meanwhile: it stays
    replaceTheme(data.theme, "adapt");
    setSelection([]);
    adaptBase = { theme: base, rev: store.get().rev, serial: docSerial() };
  } catch (error) {
    toastError(error);
  }
}
