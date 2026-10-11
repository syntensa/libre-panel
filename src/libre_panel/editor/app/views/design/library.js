// The library: modules (they lay themselves out for the cells they get),
// building blocks (groups of widgets) and single widgets, with pictures in the
// theme's own look. Drag one onto the panel, or click it.

import { endpoints } from "../../core/api.js";
import { t, widgetLabel } from "../../core/i18n.js";
import { store, useStore } from "../../core/state.js";
import { html, useEffect, useMemo, useState } from "../../lib/preact.js";
import { Chips } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { dropItem, startLibraryDrag } from "./canvas.js";
import { TYPE_ICONS } from "./inspector.js";
import { moduleKinds } from "./ops.js";

const MODULE_GROUPS = {
  system: ["ring", "stat", "graph", "bars", "network", "system", "temps", "cores", "drives", "processes", "netinfo", "battery", "values"],
  time: ["clock", "date", "analog", "world", "countdown", "sun", "weather", "agenda"],
  media: ["music", "image", "text"],
  game: ["game"],
};

const groupOf = (kind) => Object.entries(MODULE_GROUPS).find(([, kinds]) => kinds.includes(kind))?.[0] || "system";

// pictures of the modules and building blocks, in the look they would get
let pictures = { key: null, modules: {}, presets: {} };
const listeners = new Set();

async function refreshPictures(palette, style) {
  const key = JSON.stringify([palette, style]);
  if (key === pictures.key) return;
  pictures = { ...pictures, key };
  try {
    const [modules, presets] = await Promise.all([endpoints.modulePreviews(palette, style), endpoints.presetPreviews(palette)]);
    if (pictures.key !== key) return;
    pictures = { key, modules: modules.previews, presets: presets.previews };
    listeners.forEach((listener) => listener());
  } catch {
    pictures = { ...pictures, key: null }; // try again with the next change
  }
}

function usePictures() {
  const [, rerender] = useState(0);
  const look = useStore((s) => JSON.stringify([s.doc?.theme?.palette || {}, s.doc?.theme?.style || null]));
  useEffect(() => {
    const listener = () => rerender((n) => n + 1);
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => {
      const [palette, style] = JSON.parse(look);
      refreshPictures(palette, style);
    }, 250);
    return () => clearTimeout(timer);
  }, [look]);
  return pictures;
}

function Tile({ item, picture, icon, name, small = false }) {
  const src = picture ? `data:image/png;base64,${picture}` : null;
  return html`<button
    type="button"
    class=${`tile${small ? " small" : ""}`}
    data-item=${item.key}
    title=${t("Drag onto the panel, or click to add")}
    onPointerDown=${(event) => startLibraryDrag(event, { ...item, label: name }, src)}
    onClick=${(event) => event.detail === 0 && dropItem(item, null)}
    onKeyDown=${(event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        dropItem(item, null);
      }
    }}
  >
    <span class="tile-picture">
      ${src ? html`<img src=${src} alt="" draggable="false" />` : html`<${Icon} name=${icon} size=${small ? 22 : 26} />`}
    </span>
    <span class="tile-name">${name}</span>
  </button>`;
}

export function Library() {
  const specs = useStore((s) => s.specs);
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("all");
  const shots = usePictures();
  const q = query.trim().toLowerCase();
  const matches = (...words) => !q || words.some((word) => String(word || "").toLowerCase().includes(q));

  const modules = useMemo(
    () => Object.entries(moduleKinds()).map(([kind, info]) => ({ kind, name: info.name, group: groupOf(kind) })),
    [specs],
  );
  const shownModules = modules.filter((m) => (group === "all" || group === m.group) && matches(m.name, m.kind));
  const showFree = group === "all";
  const presets = showFree ? (specs?.presets || []).filter((p) => matches(p.name, p.id)) : [];
  const types = showFree ? Object.keys(specs?.widgets || {}).filter((type) => type !== "module" && matches(widgetLabel(type), type)) : [];
  const groups = [
    ["all", t("All")],
    ["system", t("System")],
    ["time", t("Time and date")],
    ["media", t("Media")],
    ["game", t("Game")],
  ];
  const nothing = !shownModules.length && !presets.length && !types.length;
  return html`<div class="library">
    <label class="search-field">
      <${Icon} name="search" size=${15} />
      <input type="text" value=${query} placeholder=${t("Find a building block")} aria-label=${t("Find a building block")}
        onInput=${(event) => setQuery(event.currentTarget.value)} />
      ${query ? html`<button type="button" class="icon-btn" aria-label=${t("Clear")} onClick=${() => setQuery("")}><${Icon} name="close" size=${13} /></button>` : null}
    </label>
    <${Chips} label=${t("Categories")} value=${group} onChange=${setGroup} options=${groups.map(([value, label]) => ({ value, label }))} />
    <div class="library-scroll">
      ${shownModules.length
        ? html`<div class="tile-grid" role="list" aria-label=${t("Modules")}>
            ${shownModules.map((m) => html`<${Tile} key=${m.kind} item=${{ kind: "module", module: m.kind, key: `module:${m.kind}` }}
              picture=${shots.modules[m.kind]} icon="module" name=${m.name} />`)}
          </div>`
        : null}
      ${presets.length
        ? html`<h3 class="library-heading">${t("Building blocks")}</h3>
            <div class="tile-grid">
              ${presets.map((p) => html`<${Tile} key=${p.id} item=${{ kind: "preset", preset: p, key: `preset:${p.id}` }}
                picture=${shots.presets[p.id]} icon="blocks" name=${p.name} />`)}
            </div>`
        : null}
      ${types.length
        ? html`<h3 class="library-heading">${t("Single widgets")}</h3>
            <div class="tile-grid three">
              ${types.map((type) => html`<${Tile} key=${type} small item=${{ kind: "widget", type, key: `widget:${type}` }}
                icon=${TYPE_ICONS[type] || "plugin"} name=${widgetLabel(type)} />`)}
            </div>`
        : null}
      ${nothing ? html`<p class="side-hint">${t("Nothing of that name.")}</p>` : null}
    </div>
    <p class="library-hint">${t("Drag onto the panel. A module fits itself to the room you give it.")}</p>
  </div>`;
}

export { store };
