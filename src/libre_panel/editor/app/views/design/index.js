// Gestalten (#/design): the panel in the middle; the library, layers, pages and
// pictures on the left; the inspector on the right; a toolbar over the
// selection. Everything here is a command too (Ctrl+K).

import { openTheme } from "../../core/actions.js";
import { registerCommands, registerProvider } from "../../core/commands.js";
import { edit, isModule, selectedWidgets, selection, setSelection, single, widgets } from "../../core/doc.js";
import { t, widgetLabel } from "../../core/i18n.js";
import { rootOf, themeEntry, versionBase, versionFor } from "../../core/orientation.js";
import { targetStatus } from "../../core/target.js";
import { bindKey } from "../../core/shortcuts.js";
import { setPref, store, useStore } from "../../core/state.js";
import { html, useRef, useState } from "../../lib/preact.js";
import { listShortcuts } from "../../shell/commands.js";
import { ApplyButton, SearchTrigger, UndoRedo } from "../../shell/header.js";
import { PanelChip } from "../../shell/panel-popover.js";
import { ThemeTitle } from "../../shell/theme-menu.js";
import { navigate, registerView } from "../../shell/views.js";
import { Row, Segmented, Toggle } from "../../ui/controls.js";
import { dialogOpen } from "../../ui/dialog.js";
import { Icon } from "../../ui/icons.js";
import { Popover } from "../../ui/layer.js";
import { paletteOpen } from "../../ui/palette.js";
import { Canvas, dropItem, setZoom, zoomStep } from "./canvas.js";
import { FINISH_NAMES, finishesFor } from "./fields.js";
import { Tips } from "./guide.js";
import { Images } from "./images.js";
import { Inspector, MODULE_ICONS, TYPE_ICONS } from "./inspector.js";
import { Layers } from "./layers.js";
import { Library } from "./library.js";
import {
  activeLook,
  align,
  applyLook,
  canPaste,
  copySelection,
  deleteSelection,
  detachModule,
  duplicateSelection,
  kindName,
  lookName,
  looks,
  moduleKinds,
  moveLayer,
  nudge,
  paste,
  selectAll,
  selectSameKind,
  setFlag,
  specOf,
} from "./ops.js";
import { PagesPanel } from "./pages.js";
import { sensorName } from "./sensors.js";

const PANELS = () => [
  { id: "library", icon: "blocks", label: t("Blocks"), title: t("Building blocks") },
  { id: "layers", icon: "layers", label: t("Layers"), title: t("Layers") },
  { id: "pages", icon: "pages", label: t("Pages"), title: t("Pages") },
  { id: "images", icon: "images", label: t("Pictures"), title: t("Pictures") },
];

/** How things move and snap on the canvas. Each choice has its name above it and a line of what
 * it does (the module grid's hint was a column of words beside its buttons). */
function ViewOptions() {
  const prefs = useStore((s) => s.prefs);
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  return html`<button ref=${anchor} type="button" class="rail-item rail-gear" title=${t("View and snapping")}
      aria-haspopup="dialog" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      <${Icon} name="sliders" size=${20} />
      <span>${t("View")}</span>
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} placement="right-end" label=${t("View and snapping")} width=${320}>
      <div class="pop-section view-options">
        <div class="pop-title">${t("View and snapping")}</div>
        <${Row} class="stacked" label=${t("Snap")} hint=${t("What you move or size goes in steps of this many pixels.")}>
          <${Segmented} label=${t("Snap")} value=${prefs.snap} onChange=${(value) => setPref("snap", value)}
            options=${[0, 1, 4, 8, 16].map((value) => ({ value, label: value ? `${value}` : t("off") }))} />
        <//>
        <${Row} class="stacked" label=${t("Steps of the modules")} hint=${t("How modules move and grow: coarse by whole cells, fine by each line of the grid.")}>
          <${Segmented} label=${t("Steps of the modules")} value=${prefs.cells || "medium"} onChange=${(value) => setPref("cells", value)}
            options=${[
              { value: "coarse", label: t("coarse") },
              { value: "medium", label: t("medium") },
              { value: "fine", label: t("fine") },
            ]} />
        <//>
        <${Row} label=${t("Guides")} hint=${t("Lines to the edges and centres of the others while you drag.")}>
          <${Toggle} label=${t("Guides")} checked=${prefs.guides} onChange=${(on) => setPref("guides", on)} />
        <//>
        <${Row} label=${t("Live values")} hint=${t("the preview with this computer's sensors")}>
          <${Toggle} label=${t("Live values")} checked=${prefs.live} onChange=${(on) => setPref("live", on)} />
        <//>
        <small class="field-hint">${t("Alt while dragging: free, without snapping or guides.")}</small>
      </div>
    <//>`;
}

function Rail() {
  const panel = useStore((s) => s.prefs.panel);
  return html`<nav class="rail" aria-label=${t("Tools")}>
    ${PANELS().map(
      (item) => html`<button key=${item.id} type="button" class="rail-item" aria-pressed=${String(panel === item.id)} title=${item.title}
        onClick=${() => setPref("panel", panel === item.id ? null : item.id)}>
        <${Icon} name=${item.icon} size=${20} />
        <span>${item.label}</span>
      </button>`,
    )}
    <div class="rail-flex"></div>
    <${Tips} />
    <${ViewOptions} />
  </nav>`;
}

function SidePanel({ panel }) {
  const title = PANELS().find((item) => item.id === panel)?.title;
  const body = { library: Library, layers: Layers, pages: PagesPanel, images: Images }[panel];
  return body ? html`<aside class="side" aria-label=${title}><${body} /></aside>` : null;
}

function DesignView() {
  const panel = useStore((s) => s.prefs.panel);
  return html`<div class="design">
    <${Rail} />
    <${SidePanel} panel=${panel} />
    <${Canvas} />
    <${Inspector} />
  </div>`;
}

const DesignActions = () => html`<${PanelChip} /><${UndoRedo} /><${ApplyButton} />`;
const WideSearch = () => html`<${SearchTrigger} wide />`;

registerView({
  id: "design",
  title: () => t("Design"),
  edits: true,
  order: 20,
  icon: "pencil",
  component: DesignView,
  header: { lead: ThemeTitle, center: WideSearch, actions: DesignActions },
});

// -- the panel turned ------------------------------------------------------------------------------

// Turned on end or back (Start, Settings), the panel shows another version of its theme (Lumen
// Vertical for Lumen): the open theme follows, so what is edited is what the panel shows, unless
// it holds work not saved yet.
let mountedAs;
store.subscribe((state) => {
  const way = state.settings?.config?.device?.orientation;
  if (way === undefined || way === mountedAs) return;
  const before = mountedAs;
  mountedAs = way;
  const { doc, dirty, page } = state;
  if (before === undefined || !way || !doc?.id || dirty) return;
  const theme = versionBase(doc.id) || doc.id;
  if (themeEntry(theme) && versionFor(theme) !== doc.id) openTheme(theme, { page, ask: false });
});

// With a companion beside it the panel shows the pair version of its theme (Lumen without its
// fans), without it the theme itself again: the open theme follows that too.
let followed;
store.subscribe((state) => {
  const shown = targetStatus(state)?.theme;
  const { doc, dirty, page } = state;
  const seen = `${shown}|${doc?.id}|${(state.versions || []).length}`; // (the versions come late)
  if (!shown || seen === followed) return;
  followed = seen;
  if (!doc?.id || dirty) return;
  const theme = rootOf(doc.id);
  if (shown && rootOf(shown) === theme && themeEntry(theme) && versionFor(theme) !== doc.id) {
    openTheme(theme, { page, ask: false });
  }
});

// -- keys ---------------------------------------------------------------------------------------

const here = () => store.get().route.view === "design" && Boolean(store.get().doc) && !dialogOpen() && !paletteOpen();
const chosen = () => selection().length > 0;

bindKey(["delete", "backspace"], () => (chosen() ? deleteSelection() : false), { when: here });
bindKey("escape", () => (chosen() ? setSelection([]) : false), { when: here });
bindKey("mod+a", () => selectAll(), { when: here });
bindKey("mod+c", () => (chosen() ? copySelection() : false), { when: here });
bindKey("mod+x", () => (chosen() ? (copySelection(), deleteSelection({ quiet: true })) : false), { when: here });
bindKey("mod+v", () => (canPaste() ? paste() : false), { when: here });
bindKey("mod+d", () => (chosen() ? duplicateSelection() : false), { when: here });
for (const [key, dx, dy] of [
  ["arrowleft", -1, 0],
  ["arrowright", 1, 0],
  ["arrowup", 0, -1],
  ["arrowdown", 0, 1],
]) {
  bindKey(key, () => (chosen() ? nudge(dx, dy) : false), { when: here });
  bindKey(`shift+${key}`, () => (chosen() ? nudge(dx * 10, dy * 10) : false), { when: here });
}
bindKey(["mod+plus", "mod+equal"], () => zoomStep(1), { when: here });
bindKey("mod+minus", () => zoomStep(-1), { when: here });
bindKey("mod+0", () => setZoom("fit"), { when: here });
bindKey("mod+1", () => setZoom(1), { when: here });
bindKey("]", () => (single() ? moveLayer(single().id, 1) : false), { when: here });
bindKey("[", () => (single() ? moveLayer(single().id, -1) : false), { when: here });

listShortcuts(() => t("Design"), [
  ["delete", () => t("Remove the selection")],
  ["arrowleft", () => t("Move by 1 px (Shift: 10 px); modules by a cell")],
  ["mod+a", () => t("Select all")],
  ["mod+c", () => t("Copy")],
  ["mod+x", () => t("Cut")],
  ["mod+v", () => t("Paste")],
  ["mod+d", () => t("Duplicate")],
  ["]", () => t("Bring forward")],
  ["[", () => t("Send backward")],
  ["mod+plus", () => t("Zoom in")],
  ["mod+minus", () => t("Zoom out")],
  ["mod+0", () => t("Fit")],
  ["escape", () => t("Select nothing")],
]);

// -- commands -------------------------------------------------------------------------------------

const toDesign = (run) => () => {
  if (store.get().route.view !== "design") navigate("design");
  return run();
};
const blocks = () => t("Building blocks");
const sel = () => t("Selection");
const view = () => t("View");

registerProvider(() => {
  const specs = store.get().specs;
  if (!specs || !store.get().doc) return [];
  const modules = Object.entries(moduleKinds()).map(([kind, info]) => ({
    id: `insert.module.${kind}`,
    title: () => t("Insert {name}", { name: info.name }),
    hint: () => t("module"),
    group: blocks,
    icon: MODULE_ICONS[kind] || "module",
    rank: 50,
    run: toDesign(() => dropItem({ kind: "module", module: kind }, null)),
  }));
  const presets = (specs.presets || []).map((preset) => ({
    id: `insert.preset.${preset.id}`,
    title: () => t("Insert {name}", { name: preset.name }),
    hint: () => t("building block"),
    group: blocks,
    icon: "blocks",
    rank: 51,
    run: toDesign(() => dropItem({ kind: "preset", preset }, null)),
  }));
  const types = Object.keys(specs.widgets || {})
    .filter((type) => type !== "module")
    .map((type) => ({
      id: `insert.widget.${type}`,
      title: () => t("Add {name}", { name: widgetLabel(type) }),
      hint: () => t("single widget"),
      group: blocks,
      icon: TYPE_ICONS[type] || "plugin",
      rank: 52,
      run: toDesign(() => dropItem({ kind: "widget", type }, null)),
    }));
  return [...modules, ...presets, ...types];
});

// the looks: a palette and a module style in one go
registerProvider(() => {
  if (!store.get().doc) return [];
  const active = activeLook();
  return Object.entries(looks())
    .filter(([key]) => key !== active)
    .map(([key, look]) => ({
      id: `look.${key}`,
      title: () => t("Look: {name}", { name: lookName(key) }),
      hint: () => t("colours and module style"),
      keywords: () => t("colours palette"),
      group: () => t("Theme"),
      icon: "palette",
      rank: 60,
      run: () => applyLook(key),
    }));
});

// "select all of a kind", with how many there are
registerProvider(() => {
  if (!store.get().doc) return [];
  const counts = new Map();
  for (const widget of widgets()) {
    const key = isModule(widget) ? `module:${widget.module}` : widget.type;
    if (!counts.has(key)) counts.set(key, { widget, count: 0 });
    counts.get(key).count += 1;
  }
  return [...counts.entries()]
    .filter(([, { count }]) => count > 1)
    .map(([key, { widget, count }]) => ({
      id: `select.kind.${key}`,
      title: () => t("Select all: {name}", { name: isModule(widget) ? kindName(widget.module) : widgetLabel(widget.type) }),
      hint: () => t("{count} on this page", { count }),
      group: sel,
      icon: "select-all",
      rank: 45,
      run: toDesign(() => selectSameKind(widget)),
    }));
});

// a sensor or a finish for what is selected
registerProvider(() => {
  const chosenWidgets = selectedWidgets();
  if (!chosenWidgets.length) return [];
  const commands = [];
  const withSensor = chosenWidgets.filter((widget) => "sensor" in specOf(widget.type) && !isModule(widget));
  if (withSensor.length) {
    for (const sensor of store.get().sensors) {
      commands.push({
        id: `sensor.${sensor.key}`,
        title: () => t("Show {sensor}", { sensor: sensorName(sensor.key, sensor.label) }),
        hint: () => sensor.key,
        group: () => t("Sensors"),
        icon: "sensor",
        rank: 70,
        run: () => edit(null, () => withSensor.forEach((widget) => (widget.sensor = sensor.key))),
      });
    }
  }
  const withFinish = chosenWidgets.filter((widget) => "finish" in specOf(widget.type));
  if (withFinish.length) {
    const shows = new Set(withFinish.flatMap((widget) => finishesFor(widget)));
    for (const [finish, name] of Object.entries(FINISH_NAMES()).filter(([finish]) => shows.has(finish))) {
      commands.push({
        id: `finish.${finish}`,
        title: () => t("Style: {name}", { name }),
        group: sel,
        icon: "ring",
        rank: 46,
        run: () => edit(null, () => withFinish.filter((widget) => finishesFor(widget).includes(finish)).forEach((widget) => (widget.finish = finish))),
      });
    }
  }
  return commands;
});

registerCommands([
  { id: "selection.duplicate", title: () => t("Duplicate"), group: sel, icon: "duplicate", keys: "mod+d", when: chosen, rank: 40, run: duplicateSelection },
  { id: "selection.copy", title: () => t("Copy"), group: sel, icon: "file", keys: "mod+c", when: chosen, rank: 41, run: copySelection },
  { id: "selection.paste", title: () => t("Paste"), group: sel, icon: "download", keys: "mod+v", when: canPaste, rank: 42, run: toDesign(paste) },
  { id: "selection.delete", title: () => t("Remove the selection"), group: sel, icon: "trash", keys: "delete", when: chosen, rank: 43, run: deleteSelection },
  { id: "selection.all", title: () => t("Select all"), group: sel, icon: "select-all", keys: "mod+a", when: () => Boolean(store.get().doc), rank: 44, run: toDesign(selectAll) },
  { id: "selection.none", title: () => t("Select nothing"), hint: () => t("the theme's settings"), group: sel, icon: "frame", keys: "escape", rank: 47, run: toDesign(() => setSelection([])) },
  {
    id: "selection.lock",
    title: () => (selectedWidgets().every((w) => w.locked) ? t("Unlock") : t("Lock")),
    group: sel,
    icon: "lock",
    when: chosen,
    rank: 48,
    run: () => setFlag(selection(), "locked", !selectedWidgets().every((w) => w.locked)),
  },
  {
    id: "selection.hide",
    title: () => (selectedWidgets().every((w) => w.visible === false) ? t("Show") : t("Hide")),
    group: sel,
    icon: "eye-off",
    when: chosen,
    rank: 48,
    run: () => setFlag(selection(), "visible", selectedWidgets().every((w) => w.visible === false)),
  },
  { id: "selection.forward", title: () => t("Bring forward"), group: sel, icon: "arrow-up", keys: "]", when: () => Boolean(single()), rank: 49, run: () => moveLayer(single().id, 1) },
  { id: "selection.backward", title: () => t("Send backward"), group: sel, icon: "arrow-down", keys: "[", when: () => Boolean(single()), rank: 49, run: () => moveLayer(single().id, -1) },
  { id: "selection.detach", title: () => t("Detach into widgets"), group: sel, icon: "detach", when: () => isModule(single()), rank: 49, run: () => detachModule(single()) },
  ...[
    ["left", "align-left", () => t("Align left edges")],
    ["center", "align-center", () => t("Align horizontal centres")],
    ["right", "align-right", () => t("Align right edges")],
    ["top", "align-top", () => t("Align top edges")],
    ["middle", "align-middle", () => t("Align vertical centres")],
    ["bottom", "align-bottom", () => t("Align bottom edges")],
    ["distribute-h", "distribute-h", () => t("Distribute horizontally")],
    ["distribute-v", "distribute-v", () => t("Distribute vertically")],
  ].map(([mode, icon, title]) => ({
    id: `align.${mode}`,
    title,
    group: sel,
    icon,
    when: () => selection().length > 1,
    rank: 55,
    run: () => align(mode),
  })),
  { id: "view.zoom-in", title: () => t("Zoom in"), group: view, icon: "plus", keys: "mod+plus", rank: 75, run: toDesign(() => zoomStep(1)) },
  { id: "view.zoom-out", title: () => t("Zoom out"), group: view, icon: "minus", keys: "mod+minus", rank: 75, run: toDesign(() => zoomStep(-1)) },
  { id: "view.fit", title: () => t("Fit to window"), group: view, icon: "fit", keys: "mod+0", rank: 75, run: toDesign(() => setZoom("fit")) },
  { id: "view.actual", title: () => t("Actual size (1 : 1)"), group: view, icon: "frame", keys: "mod+1", rank: 75, run: toDesign(() => setZoom(1)) },
  {
    id: "view.guides",
    title: () => (store.get().prefs.guides ? t("Guides: off") : t("Guides: on")),
    group: view,
    icon: "magnet",
    rank: 76,
    run: () => setPref("guides", !store.get().prefs.guides),
  },
  ...PANELS().map((panel) => ({
    id: `view.panel.${panel.id}`,
    title: () => t("Side panel: {name}", { name: PANELS().find((p) => p.id === panel.id).title }),
    group: view,
    icon: panel.icon,
    rank: 77,
    run: toDesign(() => setPref("panel", panel.id)),
  })),
]);
