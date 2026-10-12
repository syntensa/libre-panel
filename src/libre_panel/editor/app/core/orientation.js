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

/** The version of a theme the panel shows: "lumen-vertical" for "lumen" on a bar on end, and
 * the other way round, where that version is installed; else the theme itself. */
export function versionFor(id, themes = installed(), way = mounted()) {
  if (!id) return id;
  if (way === "portrait" && !id.endsWith(VERTICAL) && known(themes, id + VERTICAL)) return id + VERTICAL;
  const base = id.endsWith(VERTICAL) ? id.slice(0, -VERTICAL.length) : null;
  if (way === "landscape" && base && known(themes, base)) return base;
  return id;
}

/** Whether a theme is another's version ("lumen-vertical" beside "lumen"): lists show it as
 * that one. */
export const isVersion = (item, themes) => item.id.endsWith(VERTICAL) && known(themes, item.id.slice(0, -VERTICAL.length));

/** A theme's entry in the list of all, a version's too. */
export const themeEntry = (id) => installed().find((item) => item.id === id) || null;

/** The picture of a theme as the panel would show it (its version for the panel). */
export const shownPreview = (id, width, previewUrl) => previewUrl(versionFor(id), width);
