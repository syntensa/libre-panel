// Commands that belong to the whole app: going to a view, the theme's file
// actions, undo and redo, the panel's controls, the language.

import {
  adaptPanel,
  applyToPanel,
  exportTheme,
  importTheme,
  normalTheme,
  openTheme,
  save,
  saveAs,
} from "../core/actions.js";
import { appAction, quitApp } from "../core/app.js";
import { registerCommands, registerProvider } from "../core/commands.js";
import { redo, undo } from "../core/doc.js";
import { languageSetting, setLanguage, t } from "../core/i18n.js";
import { bindKey, keyLabel } from "../core/shortcuts.js";
import { setPref, store } from "../core/state.js";
import { html } from "../lib/preact.js";
import { dialogOpen, openDialog } from "../ui/dialog.js";
import { toastError } from "../ui/toast.js";
import { pageName, panelName, shownPages } from "./panel-popover.js";
import { newThemeDialog } from "./theme-menu.js";
import { allViews, navigate } from "./views.js";

const hasDoc = () => Boolean(store.get().doc);
const appRuns = () => store.get().app.available && !store.get().app.quit;

/** Shortcuts every view has; a view lists its own in `shortcuts` (shown by "Keyboard shortcuts"). */
export const SHORTCUTS = [];

export function listShortcuts(group, items) {
  SHORTCUTS.push({ group, items });
}

function ShortcutsHelp({ close }) {
  return html`<h2 class="dialog-title">${t("Keyboard shortcuts")}</h2>
    <div class="shortcut-groups">
      ${SHORTCUTS.map(
        (group) => html`<section key=${group.group()}>
          <h3 class="section-title">${group.group()}</h3>
          <dl class="shortcut-list">
            ${group.items.map(
              ([keys, label]) => html`<div key=${keys}><dt><kbd class="kbd">${keyLabel(keys)}</kbd></dt><dd>${label()}</dd></div>`,
            )}
          </dl>
        </section>`,
      )}
    </div>
    <div class="dialog-actions"><button type="button" class="btn btn-primary" onClick=${() => close()}>${t("Close")}</button></div>`;
}

export function registerAppCommands() {
  const go = () => t("Go to");
  const theme = () => t("Theme");
  const panel = () => t("Panel");
  const settings = () => t("Settings");
  const icons = { home: "home", design: "pencil", look: "palette", live: "pulse", settings: "gear" };

  registerProvider(() =>
    allViews()
      .filter((view) => view.tab || view.id === "settings")
      .map((view) => ({
        id: `view.${view.id}`,
        title: typeof view.title === "function" ? view.title : () => view.title,
        group: go,
        icon: view.icon || icons[view.id] || "chevron-right",
        rank: 5,
        run: () => navigate(view.id),
      })),
  );

  registerCommands([
    { id: "theme.new", title: () => t("New theme…"), group: theme, icon: "plus", rank: 20, run: newThemeDialog },
    { id: "theme.save", title: () => t("Save"), group: theme, icon: "save", keys: "mod+s", bind: true, inInputs: true, when: hasDoc, rank: 10, run: save },
    { id: "theme.save-as", title: () => t("Save as…"), group: theme, icon: "file", keys: "mod+shift+s", bind: true, inInputs: true, when: hasDoc, rank: 21, run: saveAs },
    { id: "theme.import", title: () => t("Import a theme file…"), group: theme, icon: "upload", rank: 22, run: () => importTheme() },
    { id: "theme.export", title: () => t("Export as a file"), group: theme, icon: "download", when: hasDoc, rank: 23, run: exportTheme },
    { id: "theme.apply", title: () => t("To the panel"), hint: () => t("save and show"), group: theme, icon: "to-panel", when: hasDoc, rank: 11, run: applyToPanel },
    { id: "edit.undo", title: () => t("Undo"), group: theme, icon: "undo", keys: "mod+z", when: () => store.get().canUndo, rank: 12, run: undo },
    { id: "edit.redo", title: () => t("Redo"), group: theme, icon: "redo", keys: "mod+y", when: () => store.get().canRedo, rank: 13, run: redo },
    {
      id: "app.pause",
      title: () => (store.get().app.panel?.state === "paused" ? t("Resume panel") : t("Pause panel")),
      group: panel,
      icon: "pause",
      when: appRuns,
      run: () => appAction(store.get().app.panel?.state === "paused" ? "resume" : "pause"),
    },
    {
      id: "app.upside-down",
      title: () => (store.get().app.upside_down ? t("Upside down: off") : t("Upside down: on")),
      group: panel,
      icon: "swap",
      when: appRuns,
      run: () => appAction("upside_down", !store.get().app.upside_down),
    },
    {
      id: "app.autostart",
      title: () => (store.get().app.autostart ? t("Start with system: off") : t("Start with system: on")),
      group: panel,
      icon: "power",
      when: appRuns,
      run: () => appAction("autostart", !store.get().app.autostart),
    },
    { id: "app.quit", title: () => t("Quit Libre Panel"), group: panel, icon: "power", when: appRuns, rank: 90, run: quitApp },
    {
      id: "view.live-values",
      title: () => (store.get().prefs.live ? t("Live values: off") : t("Live values: on")),
      hint: () => t("the preview with this computer's sensors"),
      group: settings,
      icon: "pulse",
      run: () => setPref("live", !store.get().prefs.live),
    },
    {
      id: "help.shortcuts",
      title: () => t("Keyboard shortcuts"),
      group: settings,
      icon: "keyboard",
      keys: "?",
      run: () => openDialog((close) => html`<${ShortcutsHelp} close=${close} />`, { label: t("Keyboard shortcuts") }),
    },
  ]);

  // a theme by name, the brightness, the panel model, the language
  registerProvider(() =>
    store.get().themes.map((item) => ({
      id: `theme.open.${item.id}`,
      title: () => t("Open theme {name}", { name: item.name || item.id }),
      hint: () => (item.id === normalTheme() ? t("shown on the panel") : item.builtin ? t("built in") : t("yours")),
      keywords: () => item.id,
      group: theme,
      icon: "file",
      rank: 30,
      run: async () => {
        if (await openTheme(item.id)) navigate("design");
      },
    })),
  );
  registerProvider(() =>
    appRuns()
      ? [0, 10, 25, 50, 75, 100].map((value) => ({
          id: `app.brightness.${value}`,
          title: () => (value ? t("Brightness {value} %", { value }) : t("Brightness off")),
          group: panel,
          icon: "sun",
          rank: 60,
          run: () => appAction("brightness", value),
        }))
      : [],
  );
  registerProvider(() => {
    const doc = store.get().doc;
    if (!doc) return [];
    const display = doc.theme.display || {};
    const orientation = display.orientation || "landscape";
    const mode = store.get().prefs.adapt || "scale";
    const models = (store.get().specs?.models || []).map((model) => ({
      id: `panel.model.${model.id}`,
      title: () => t("Panel: {name}", { name: panelName(model) }),
      hint: () => `${model[orientation][0]} × ${model[orientation][1]}`,
      group: panel,
      icon: model.shape === "round" ? "sensor" : "landscape",
      rank: 70,
      when: () => model.id !== display.model,
      run: () => adaptPanel({ model: model.id, orientation, mode }),
    }));
    const flip = orientation === "landscape" ? "portrait" : "landscape";
    return [
      {
        id: "panel.orientation",
        title: () => (flip === "portrait" ? t("Orientation: portrait") : t("Orientation: landscape")),
        group: panel,
        icon: flip,
        rank: 65,
        run: () => adaptPanel({ model: display.model || "custom", orientation: flip, mode, width: display.width, height: display.height }),
      },
      ...models.filter((command) => command.when()),
    ];
  });
  registerProvider(() =>
    Object.entries({ auto: () => t("System language"), en: () => "English", de: () => "Deutsch" })
      .filter(([value]) => value !== languageSetting())
      .map(([value, name]) => ({
        id: `language.${value}`,
        title: () => t("Language: {name}", { name: name() }),
        group: settings,
        icon: "globe",
        rank: 80,
        run: () => setLanguage(value).catch(toastError),
      })),
  );
  registerProvider(() =>
    (store.get().plugins || []).map((page) => ({
      id: `plugin.${page.id}`,
      title: () => page.title,
      hint: () => t("plugin page"),
      group: () => t("Plugins"),
      icon: "plugin",
      rank: 85,
      run: () => navigate("plugin", page.id),
    })),
  );
  registerProvider(() =>
    shownPages().map((page) => ({
      id: `page.${page.id}`,
      title: () => t("Page: {name}", { name: pageName(page.id) }),
      hint: () => page.theme,
      group: () => t("Pages"),
      icon: "pages",
      rank: 40,
      run: async () => {
        if (await openTheme(page.theme, { page: page.id })) navigate("design");
      },
    })),
  );

  bindKey(["mod+z"], () => undo(), { when: () => !dialogOpen() && hasDoc() });
  bindKey(["mod+y", "mod+shift+z"], () => redo(), { when: () => !dialogOpen() && hasDoc() });
  bindKey("?", () => {
    openDialog((close) => html`<${ShortcutsHelp} close=${close} />`, { label: t("Keyboard shortcuts") });
  }, { when: () => !dialogOpen() });

  listShortcuts(() => t("Everywhere"), [
    ["mod+k", () => t("Commands and search")],
    ["mod+s", () => t("Save")],
    ["mod+shift+s", () => t("Save as…")],
    ["mod+z", () => t("Undo")],
    ["mod+y", () => t("Redo")],
    ["?", () => t("Keyboard shortcuts")],
  ]);
}
