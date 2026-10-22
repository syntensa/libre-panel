// How the panel is mounted (config.toml's device.orientation): on its side ("landscape"), on
// end ("portrait"), or "" as each theme is made. Each theme shows in its version for it:
// "lumen-vertical" is Lumen for a bar on end. Lists show a theme once, by the name it is
// chosen by; the panel, its pictures and Design show the version for the panel.

import { loadSettings } from "./settings.js";
import { store, useStore } from "./state.js";
import { targetStatus, targetTable } from "./target.js";

export const VERTICAL = "-vertical";

let asked = false;

/** How the panel is mounted ("" until config.toml has been read): the panel the app is about
 * (the header's choice, target.js). */
export function mounted() {
  const settings = store.get().settings;
  if (!settings && !asked) {
    asked = true;
    loadSettings();
  }
  const beside = targetTable();
  return (beside ? beside.orientation : settings?.config?.device?.orientation) || "";
}

export const useMounted = () => {
  useStore((s) => (targetTable(s) || s.settings?.config?.device)?.orientation);
  return mounted();
};

const known = (themes, id) => themes.some((item) => item.id === id);

/** Every theme installed: those the lists show and their versions. */
const installed = () => [...store.get().themes, ...(store.get().versions || [])];

// "lumen-1920x720" is Lumen for a panel of that size (the 12.3"): it comes first on one
const SIZED = /^(.+)-(\d{2,5})x(\d{2,5})$/;

// "lumen-pair" is Lumen while a companion shows beside it on another panel (theme.model.PAIR)
const PAIR = "-pair";

/** The theme a version is one of ("lumen" for "lumen-vertical", "lumen-1920x720" and
 * "lumen-pair"), as its name says; null for a theme that is no version. */
export function versionBase(id) {
  const sized = SIZED.exec(id || "");
  if (sized) return sized[1];
  for (const ending of [VERTICAL, PAIR]) if (id?.endsWith(ending) && id.length > ending.length) return id.slice(0, -ending.length);
  return null;
}

/** The size of the panel's frames, where the running app knows it (of the panel the app is
 * about). */
export const panelSize = () => targetStatus()?.size || null;

/** The version of a theme the panel shows: its version for the panel's size ("lumen-1920x720"
 * on the 12.3"), else "lumen-vertical" for "lumen" on a bar on end, and the other way round,
 * where that version is installed; else the theme itself (as theme.model.theme_for). */
export function versionFor(id, themes = installed(), way = mounted(), size = panelSize()) {
  const version = plainVersion(id, themes, way, size);
  // while a companion runs beside it the panel shows the pair version ("lumen-pair-1920x720"):
  // Design edits, and the pictures show, what the panel shows
  const shown = targetStatus()?.theme;
  const base = versionBase(version) || version;
  return shown && version && shown === `${base}${PAIR}${version.slice(base.length)}` && known(themes, shown) ? shown : version;
}

function plainVersion(id, themes, way, size) {
  if (!id) return id;
  // turned as it is mounted (as the app's panel_size), also before the app has said its new size
  if (size && ((way === "portrait" && size[0] > size[1]) || (way === "landscape" && size[0] < size[1]))) {
    size = [size[1], size[0]];
  }
  const sized = SIZED.exec(id);
  const same = sized && size && Number(sized[2]) === size[0] && Number(sized[3]) === size[1];
  if (sized && !same && known(themes, sized[1])) id = sized[1];
  if (size) {
    const own = `${versionBase(id) || id}-${size[0]}x${size[1]}`;
    if (own === id || known(themes, own)) return own;
  }
  if (way === "portrait" && !id.endsWith(VERTICAL) && known(themes, id + VERTICAL)) return id + VERTICAL;
  const base = id.endsWith(VERTICAL) ? id.slice(0, -VERTICAL.length) : null;
  if (way === "landscape" && base && known(themes, base)) return base;
  return id;
}

/** The theme a version is one of through all its endings: "lumen" for "lumen-pair-1920x720". */
export function rootOf(id) {
  let root = id;
  for (let base = versionBase(root); base; base = versionBase(root)) root = base;
  return root;
}

/** Whether a theme is another's version ("lumen-vertical" or "lumen-1920x720" beside
 * "lumen"): lists show it as that one. */
export const isVersion = (item, themes) => {
  const base = versionBase(item.id);
  return Boolean(base) && known(themes, base);
};

/** A theme's entry in the list of all, a version's too. */
export const themeEntry = (id) => installed().find((item) => item.id === id) || null;

/** The picture of a theme as the panel would show it (its version for the panel). */
export const shownPreview = (id, width, previewUrl) => previewUrl(versionFor(id), width);
