// Libre Panel's app: the shell, its views (Start, Design, Look, Live,
// Settings) and the theme being edited. See README.md in this folder.

import { blankTheme, openTheme, refreshModes, refreshThemes } from "./core/actions.js";
import { endpoints } from "./core/api.js";
import { startAppPolling } from "./core/app.js";
import * as commands from "./core/commands.js";
import * as doc from "./core/doc.js";
import { loadLanguage, t } from "./core/i18n.js";
import "./core/preview.js";
import { store } from "./core/state.js";
import { html, render } from "./lib/preact.js";
import { App } from "./shell/app.js";
import { registerAppCommands } from "./shell/commands.js";
import "./shell/plugin-view.js";
import "./views/design/index.js";
import "./views/home/index.js";
import "./views/live/index.js";
import "./views/look/index.js";
import "./views/settings/index.js";

render(html`<${App} />`, document.getElementById("root"));

async function start() {
  try {
    await loadLanguage();
    document.title = "Libre Panel";
    store.set({ specs: await endpoints.specs() });
    await Promise.all([refreshThemes(), refreshModes()]);
    endpoints.sensors().then((sensors) => store.set({ sensors }), () => {});
    endpoints.pluginPages().then((plugins) => store.set({ plugins }), () => {});
    startAppPolling();
    registerAppCommands();
    // the theme on the panel, else the default one, else any
    const { themes, modes, specs } = store.get();
    const first = [modes?.theme, "libre-default", themes[0]?.id].find((id) => id && themes.some((theme) => theme.id === id));
    if (first) await openTheme(first, { ask: false });
    if (!store.get().doc) doc.openDoc({ theme: blankTheme(specs.models[0]), dirty: true });
    store.set({ ready: true });
  } catch (error) {
    store.set({ failed: error.message || t("unknown error") });
  }
}

start();

// for the tests and for looking around in the developer tools
window.libre = { store, doc, commands };
