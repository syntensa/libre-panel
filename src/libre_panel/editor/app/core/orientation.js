// How the panel is mounted (config.toml's device.orientation): on its side ("landscape"), on
// end ("portrait"), or "" as each theme is made. Each theme shows in its version for it:
// "lumen-vertical" is Lumen for a bar on end. Lists show a theme once, by the name it is
// chosen by; the panel, its pictures and Design show the version for the panel.

import { loadSettings } from "./settings.js";
import { store, useStore } from "./state.js";

export const VERTICAL = "-vertical";

let asked = false;

/** How the panel is mounted ("" until config.toml has been read). */
export function mounted() {
  const settings = store.get().settings;
  if (!settings && !asked) {
    asked = true;
    loadSettings();
  }
  return settings?.config?.device?.orientation || "";
}

export const useMounted = () => {
  useStore((s) => s.settings?.config?.device?.orientation);
  return mounted();
};

const known = (themes, id) => themes.some((item) => item.id === id);

/** Every theme installed: those the lists show and their versions. */
const installed = () => [...store.get().themes, ...(store.get().versions || [])];

// "lumen-1920x720" is Lumen for a panel of that size (the 12.3"): it comes first on one
const SIZED = /^(.+)-(\d{2,5})x(\d{2,5})$/;

/** The theme a version is one of ("lumen" for "lumen-vertical" and "lumen-1920x720"), as its
 * name says; null for a theme that is no version. */
export function versionBase(id) {
  const sized = SIZED.exec(id || "");
  if (sized) return sized[1];
  return id?.endsWith(VERTICAL) ? id.slice(0, -VERTICAL.length) : null;
}

/** The size of the panel's frames, where the running app knows it. */
export const panelSize = () => store.get().app?.panel?.size || null;

/** The version of a theme the panel shows: its version for the panel's size ("lumen-1920x720"
 * on the 12.3"), else "lumen-vertical" for "lumen" on a bar on end, and the other way round,
 * where that version is installed; else the theme itself (as theme.model.theme_for). */
export function versionFor(id, themes = installed(), way = mounted(), size = panelSize()) {
  if (!id) return id;
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
