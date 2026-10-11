// The app's one store. Views read it with useStore(selector); the theme being
// edited lives in `doc` and is changed through core/doc.js (undo and redo).

import { createStore, useSelector } from "./store.js";

const PREFS_KEY = "libre-panel-prefs";

function savedPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

export const store = createStore({
  ready: false,
  failed: null, // why the app could not start
  choosing: false, // a first start: a theme was chosen and is being taken on to Design
  lang: 0, // counts language switches: every text is rendered again
  route: { view: "home", rest: "" },
  specs: null, // /api/specs
  themes: [], // /api/themes
  sensors: [], // /api/sensors: [{key, unit, label}]
  rev: 0, // counts changes of the theme being edited
  doc: null, // {theme, id, builtin, assets}; id null: not saved yet
  selection: [], // widget ids
  dirty: false,
  canUndo: false,
  canRedo: false,
  render: {
    png: null,
    boxes: {},
    size: [480, 320],
    grid: null,
    gridActive: false,
    moduleBoxes: {},
    warnings: [],
  },
  app: { available: false }, // the background app (tray), when it serves the app
  pages: null, // the panel's pages: /api/pages
  page: null, // the page the open theme is shown on ("normal" or a mode), if any
  plugins: [], // plugin editor pages: /api/plugins/pages
  hardware: null, // the hardware's names (core/hardware.js); false: the server has none
  settings: null, // Settings: {config, catalog, restart} (core/settings.js)
  settingsError: null, // why config.toml cannot be read
  settingsDown: false, // ... because the server does not answer
  panelInfo: null, // Settings: the panel found, the video mode's state
  clipQuestion: null, // Start: {ask, name}, whether to ask for the panel's standby clip (core/settings.js)
  systemInfo: null, // Settings: starting with the system
  aboutInfo: null, // Settings: version, licence, notices, folders
  sourceStates: null, // Settings: what the running app says of its sensor sources
  serviceStates: null, // Settings: the services and their states
  prefs: {
    snap: 4, // px: 1, 4, 8 or 16; 0 = off
    guides: true,
    live: false, // previews with this computer's sensors
    panel: "library", // the open side panel of the design view (null: closed)
    ...savedPrefs(),
  },
});

export const useStore = (selector) => useSelector(store, selector);

/**
 * A first start: config.toml names no theme yet (or the one just chosen is being taken on to
 * Design), so Start asks "How should it look?". Nothing is known while config.toml cannot be
 * read (`pages` is null then), so it is no first start.
 */
export const firstStart = (state = store.get()) =>
  state.ready && Boolean(state.pages) && (state.pages.chosen === false || state.choosing);

/** Change a preference; it is remembered in this browser (or window). */
export function setPref(key, value) {
  const prefs = { ...store.get().prefs, [key]: value };
  store.set({ prefs });
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    // private window or blocked storage: the preference lasts until the page closes
  }
}
