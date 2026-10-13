// Commands that belong to the whole app: going to a view, the theme's file
// actions, undo and redo, the panel's controls, the language, the help. The features that live
// in the cards of Start and Settings are in features.js.

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
import { bindKey } from "../core/shortcuts.js";
import { setPref, store } from "../core/state.js";
import { dialogOpen } from "../ui/dialog.js";
import { closePalette, paletteOpen } from "../ui/palette.js";
import { toastError } from "../ui/toast.js";
import { registerFeatureCommands } from "./features.js";
import { listShortcuts, openHelp } from "./help.js";
import { pageName, panelName, shownPages } from "./panel-popover.js";
import { newThemeDialog } from "./theme-menu.js";
import { allViews, navigate, showsTheme, viewHint, viewKeywords } from "./views.js";

export { listShortcuts };

const hasDoc = () => Boolean(store.get().doc);
/** Undo and redo change what a view shows, so they act where the theme is shown (not behind Settings). */
const canEdit = () => hasDoc() && showsTheme();
const appRuns = () => store.get().app.available && !store.get().app.quit;

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
        hint: () => viewHint(view),
        keywords: () => viewKeywords(view),
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
    {
      id: "theme.apply",
      title: () => t("To the panel"),
      hint: () => t("save and show"),
      group: theme,
      icon: "to-panel",
      when: hasDoc,
      rank: 11,
      suggest: () => (showsTheme() ? 1 : null), // where the theme being changed is shown
      run: applyToPanel,
    },
    { id: "edit.undo", title: () => t("Undo"), group: theme, icon: "undo", keys: "mod+z", when: () => canEdit() && store.get().canUndo, rank: 12, run: undo },
    { id: "edit.redo", title: () => t("Redo"), group: theme, icon: "redo", keys: "mod+y", when: () => canEdit() && store.get().canRedo, rank: 13, run: redo },
    {
      id: "app.pause",
      title: () => (store.get().app.panel?.state === "paused" ? t("Resume panel") : t("Pause panel")),
      hint: () => (store.get().app.panel?.state === "paused" ? "" : t("frees the panel for another program")),
      keywords: () => t("pause stop freeze hold resume continue"),
      group: panel,
      icon: "pause",
      when: appRuns,
      rank: 12,
      suggest: 9,
      run: () => appAction(store.get().app.panel?.state === "paused" ? "resume" : "pause"),
    },
    {
      // the running app's switch; without one, the card in Settings that has it
      id: "app.autostart",
      title: () =>
        appRuns() ? (store.get().app.autostart ? t("Start with system: off") : t("Start with system: on")) : t("Start with system"),
      hint: () => (appRuns() ? "" : t("Settings → Start and system")),
      keywords: () => t("autostart startup boot login sign in start automatically with windows"),
      group: panel,
      icon: "power",
      rank: 34,
      run: () => (appRuns() ? appAction("autostart", !store.get().app.autostart) : navigate("settings", "system/start")),
    },
    { id: "app.quit", title: () => t("Quit Libre Panel"), keywords: () => t("quit exit close end"), group: panel, icon: "power", when: appRuns, rank: 90, run: quitApp },
    {
      id: "view.live-values",
      title: () => (store.get().prefs.live ? t("Live values: off") : t("Live values: on")),
      hint: () => t("the preview with this computer's sensors"),
      group: settings,
      icon: "pulse",
      run: () => setPref("live", !store.get().prefs.live),
    },
  ]);
  registerFeatureCommands();

  // a theme by name, the brightness, the panel model, the language
  registerProvider(() =>
    store.get().themes.map((item) => ({
      id: `theme.open.${item.id}`,
      title: () => t("Open theme {name}", { name: item.name || item.id }),
      hint: () => (item.id === normalTheme() ? t("shown on the panel") : item.builtin ? t("built in") : t("yours")),
      keywords: () => `${item.id} ${t("edit change design")}`,
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
          keywords: () => t("dim bright darker lighter backlight light"),
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
    // these change the theme being edited, not the panel: its size and its layout
    const models = (store.get().specs?.models || []).map((model) => ({
      id: `panel.model.${model.id}`,
      title: () => t("Fit this theme to {name}", { name: panelName(model) }),
      hint: () => `${model[orientation][0]} × ${model[orientation][1]}`,
      keywords: () => t("panel size model adapt"),
      group: theme,
      icon: model.shape === "round" ? "sensor" : "landscape",
      rank: 70,
      when: () => model.id !== display.model,
      run: () => adaptPanel({ model: model.id, orientation, mode }),
    }));
    const flip = orientation === "landscape" ? "portrait" : "landscape";
    return [
      {
        id: "panel.orientation",
        title: () => (flip === "portrait" ? t("Lay this theme out in portrait") : t("Lay this theme out in landscape")),
        hint: () => t("changes the theme, not the panel"),
        keywords: () => t("orientation portrait landscape upright layout format"),
        group: theme,
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

  bindKey(["mod+z"], () => undo(), { when: () => !dialogOpen() && canEdit() });
  bindKey(["mod+y", "mod+shift+z"], () => redo(), { when: () => !dialogOpen() && canEdit() });
  bindKey("?", () => void openHelp(), { when: () => !dialogOpen() });
  // F1 also while typing, and from the palette, which it closes
  bindKey("f1", () => {
    if (paletteOpen()) closePalette();
    setTimeout(openHelp, 0);
  }, { inInputs: true, when: () => !dialogOpen() });

  listShortcuts(() => t("Everywhere"), [
    ["mod+k", () => t("Find anything: features, settings, themes")],
    ["mod+s", () => t("Save")],
    ["mod+shift+s", () => t("Save as…")],
    ["mod+z", () => t("Undo")],
    ["mod+y", () => t("Redo")],
    ["?", () => t("Help and keyboard shortcuts")],
    ["escape", () => t("Close a dialog or a menu; leave Settings")],
  ], { first: true });
}
