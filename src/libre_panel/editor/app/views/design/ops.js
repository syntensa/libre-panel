// What can be done to the widgets of the theme: add, insert building blocks
// and modules, duplicate, copy, paste, delete, lock, hide, order, align,
// rename, detach; and its look. Every change is one undo step.

import { endpoints } from "../../core/api.js";
import {
  clone,
  commit,
  docSerial,
  edit,
  isModule,
  selectedWidgets,
  selection,
  setSelection,
  single,
  theme,
  touch,
  uniqueId,
  widgetById,
  widgets,
} from "../../core/doc.js";
import { t } from "../../core/i18n.js";
import { store } from "../../core/state.js";
import { toast, toastError } from "../../ui/toast.js";
import { blockers, cellBox, freeSpot, placed, unionBox } from "./geometry.js";

export const specs = () => store.get().specs;
export const moduleKinds = () => specs()?.modules?.kinds || {};
export const kindName = (kind) => moduleKinds()[kind]?.name ?? kind;
export const panelSize = () => store.get().render.size;
export const grid = () => store.get().render.grid;

/** The box a widget is drawn in (a module's cells before the first drawing). */
export function boxOf(widget) {
  const box = store.get().render.boxes[widget.id];
  if (box) return box;
  const cells = grid();
  return isModule(widget) && cells ? cellBox(cells, ...placed(cells, widget)) : null;
}

/** What a module can show, in words ("fan" is the fan instrument's own). */
export const sourceLabels = () => ({
  cpu: "CPU",
  gpu: "GPU",
  mem: t("Memory"),
  disk: t("Disk"),
  net: t("Network"),
  sensor: t("Any sensor"),
  fan: t("Fan"),
});

/** The fields of a widget type: the common ones and its own ({key: [kind, default]}). */
export const specOf = (type) => ({ ...(specs()?.common || {}), ...(specs()?.widgets?.[type] || {}) });

// -- adding ----------------------------------------------------------------------

/** A widget of a type with its defaults; `at` is where its top left goes. */
function newWidget(type, at) {
  const [width, height] = panelSize();
  const widget = { type, id: uniqueId(type) };
  for (const [key, [, fallback]] of Object.entries(specOf(type))) if (key !== "id") widget[key] = clone(fallback);
  widget.x = Math.round(at ? at[0] : width * 0.1);
  widget.y = Math.round(at ? at[1] : height * 0.1);
  if (typeof widget.w === "number" && widget.w > width * 0.8) widget.w = Math.round(width * 0.5);
  if (typeof widget.h === "number" && widget.h > height * 0.8) widget.h = Math.round(height * 0.3);
  return widget;
}

export function addWidget(type, at = null) {
  const widget = newWidget(type, at);
  edit(null, (current) => current.widgets.push(widget));
  setSelection([widget.id]);
  return widget;
}

/** Building blocks name colours as "$accent": the theme's own when it has one. */
function resolveTokens(value) {
  const palette = theme()?.palette || {};
  if (typeof value === "string" && value.startsWith("$")) {
    const name = value.slice(1);
    return name in palette ? `@${name}` : specs().tokens?.[name];
  }
  if (Array.isArray(value)) return value.map(resolveTokens);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, resolveTokens(v)]));
  return value;
}

/** Insert a building block (a group of widgets), centred at `at` or on the panel. */
export function insertPreset(preset, at = null) {
  const [W, H] = panelSize();
  const [pw, ph] = preset.size;
  const scale = Math.min(1, (W * 0.9) / pw, (H * 0.9) / ph);
  const cx = at ? at[0] : W / 2;
  const cy = at ? at[1] : H / 2;
  const ox = Math.round(Math.max(0, Math.min(W - pw * scale, cx - (pw * scale) / 2)));
  const oy = Math.round(Math.max(0, Math.min(H - ph * scale, cy - (ph * scale) / 2)));
  const ids = [];
  edit(null, (current) => {
    for (const raw of resolveTokens(clone(preset.widgets))) {
      const widget = { ...raw, id: uniqueId(`${preset.id}-${raw.id}`) };
      widget.x = ox + Math.round(raw.x * scale);
      widget.y = oy + Math.round(raw.y * scale);
      if (scale < 1) {
        for (const key of ["w", "h", "size", "font_size", "thickness"]) {
          if (typeof widget[key] === "number") widget[key] = Math.max(1, Math.round(widget[key] * scale));
        }
      }
      current.widgets.push(widget);
      ids.push(widget.id);
    }
  });
  setSelection(ids);
  toast(t('Added "{name}": all its parts move together while they are selected', { name: preset.name }));
}

function newModule(kind, area) {
  const widget = { type: "module", id: uniqueId(kind) };
  for (const [key, [, fallback]] of Object.entries(specOf("module"))) if (key !== "id") widget[key] = clone(fallback);
  widget.module = kind;
  widget.source = moduleKinds()[kind]?.sources?.[0] || "cpu";
  [widget.col, widget.row, widget.cols, widget.rows] = area;
  return widget;
}

/** The free cells where a module of a kind fits best (at a cell, or anywhere). */
export function spotFor(kind, prefer = null, exceptId = null) {
  const cells = grid();
  if (!cells) return null;
  const span = moduleKinds()[kind]?.span || [1, 1];
  return freeSpot(widgets(), cells, span[0], span[1], prefer, exceptId);
}

export function addModule(kind, area = spotFor(kind)) {
  if (!area) {
    toast(t("No free cells left: make a module smaller or remove one"), { kind: "warn" });
    return null;
  }
  const widget = newModule(kind, area);
  edit(null, (current) => {
    if (!current.grid) current.grid = { columns: 0, rows: 0, gap: 0, margin: 0 };
    current.widgets.push(widget);
  });
  setModuleArea(widget, area);
  setSelection([widget.id]);
  return widget;
}

/** Put a module on cells (and its box, until the next drawing). */
export function setModuleArea(widget, [col, row, cols, rows]) {
  Object.assign(widget, { col, row, cols, rows });
  const cells = grid();
  if (!cells) return;
  const box = cellBox(cells, col, row, cols, rows);
  const render = store.get().render;
  store.set({ render: { ...render, boxes: { ...render.boxes, [widget.id]: box }, moduleBoxes: { ...render.moduleBoxes, [widget.id]: box } } });
}

/**
 * Another kind for a module (one undo step). What it shows stays where the new kind has
 * it too; else it is the new kind's first source, so a big number that showed the network
 * does not keep that as a ring (which has no network and would draw the processor).
 */
export function setModuleKind(widget, kind) {
  edit(`prop:${widget.id}:module`, () => {
    widget.module = kind;
    const sources = moduleKinds()[kind]?.sources || [];
    if (sources.length && !sources.includes(widget.source)) widget.source = sources[0];
  });
}

/** An image widget showing a picture of the theme's folder. */
export function addPicture(path, at = null) {
  const widget = newWidget("image", at);
  widget.src = path;
  widget.w = 0;
  widget.h = 0;
  edit(null, (current) => current.widgets.push(widget));
  setSelection([widget.id]);
}

// -- the selection -----------------------------------------------------------------

let clipboard = null;

export function duplicateSelection() {
  const originals = selectedWidgets();
  if (!originals.length) return;
  const ids = [];
  edit(null, (current) => {
    for (const original of originals) {
      const copy = clone(original);
      copy.id = uniqueId(original.id);
      copy.x += 10;
      copy.y += 10;
      copy.locked = false;
      if (isModule(copy)) {
        const spot = grid() && freeSpot(current.widgets, grid(), copy.cols, copy.rows);
        if (!spot) {
          toast(t("No free cells left: make a module smaller or remove one"), { kind: "warn" });
          continue;
        }
        [copy.col, copy.row, copy.cols, copy.rows] = spot;
      }
      current.widgets.splice(current.widgets.indexOf(original) + 1, 0, copy);
      ids.push(copy.id);
    }
  });
  setSelection(ids);
}

export function deleteSelection() {
  const ids = new Set(selection());
  if (!ids.size) return;
  edit(null, (current) => {
    current.widgets = current.widgets.filter((widget) => !ids.has(widget.id));
  });
  setSelection([]);
}

export function copySelection() {
  const chosen = selectedWidgets();
  if (!chosen.length) return;
  clipboard = clone(chosen);
  toast(chosen.length === 1 ? t("Copied 1 widget") : t("Copied {count} widgets", { count: chosen.length }));
}

export const canPaste = () => Boolean(clipboard?.length);

export function paste() {
  if (!clipboard?.length) return;
  const ids = [];
  edit(null, (current) => {
    for (const raw of clone(clipboard)) {
      raw.id = uniqueId(raw.id);
      raw.x += 12;
      raw.y += 12;
      if (isModule(raw)) {
        const spot = grid() && freeSpot(current.widgets, grid(), raw.cols, raw.rows);
        if (!spot) continue;
        [raw.col, raw.row, raw.cols, raw.rows] = spot;
      }
      current.widgets.push(raw);
      ids.push(raw.id);
    }
  });
  clipboard = clipboard.map((widget) => ({ ...widget, x: widget.x + 12, y: widget.y + 12 }));
  setSelection(ids);
}

export function selectAll() {
  setSelection(widgets().filter((widget) => !widget.locked && widget.visible !== false).map((widget) => widget.id));
}

/** Every widget of the same kind as `widget` (the same module kind, or type). */
export function selectSameKind(widget) {
  const same = (other) => (isModule(widget) ? isModule(other) && other.module === widget.module : other.type === widget.type);
  setSelection(widgets().filter((other) => !other.locked && same(other)).map((other) => other.id));
}

/** Lock or hide (`flag`: "locked", "visible") widgets; value undefined: the opposite of now. */
export function setFlag(ids, flag, value) {
  const chosen = ids.map(widgetById).filter(Boolean);
  if (!chosen.length) return;
  const next = value ?? !(chosen[0][flag] ?? flag === "visible");
  edit(null, () => {
    for (const widget of chosen) widget[flag] = next;
  });
}

/** Earlier in the list is further back. `delta`: -1 back, 1 forward, -Infinity to the back. */
export function moveLayer(id, delta) {
  const list = widgets();
  const index = list.findIndex((widget) => widget.id === id);
  if (index < 0) return;
  const target = Math.max(0, Math.min(list.length - 1, delta === Infinity ? list.length - 1 : delta === -Infinity ? 0 : index + delta));
  if (target === index) return;
  edit(null, (current) => {
    const [widget] = current.widgets.splice(index, 1);
    current.widgets.splice(target, 0, widget);
  });
}

/** Move a layer to another place in the list (drag and drop in the layers panel). */
export function placeLayer(id, target) {
  const list = widgets();
  const index = list.findIndex((widget) => widget.id === id);
  if (index < 0 || target === index) return;
  edit(null, (current) => {
    const [widget] = current.widgets.splice(index, 1);
    current.widgets.splice(Math.max(0, Math.min(current.widgets.length, target)), 0, widget);
  });
}

export function renameWidget(widget, value) {
  const next = value.trim();
  if (!next || widgets().some((other) => other !== widget && other.id === next)) {
    toast(t('Widget id "{id}" is empty or already used', { id: next }), { kind: "error" });
    return false;
  }
  if (next === widget.id) return true;
  const before = widget.id;
  edit(`rename:${before}`, () => {
    widget.id = next;
  });
  const render = store.get().render;
  if (render.boxes[before]) {
    const boxes = { ...render.boxes, [next]: render.boxes[before] };
    delete boxes[before];
    store.set({ render: { ...render, boxes } });
  }
  setSelection(selection().map((id) => (id === before ? next : id)));
  return true;
}

/** Move widgets by (dx, dy) pixels; modules by cells. */
export function nudge(dx, dy) {
  const module = single();
  if (isModule(module)) return moveModuleBy(module, Math.sign(dx), Math.sign(dy));
  const movers = selectedWidgets().filter((widget) => !widget.locked && !isModule(widget));
  if (!movers.length) return;
  commit("nudge");
  const render = store.get().render;
  const boxes = { ...render.boxes };
  for (const widget of movers) {
    widget.x += dx;
    widget.y += dy;
    const box = boxes[widget.id];
    if (box) boxes[widget.id] = [box[0] + dx, box[1] + dy, box[2], box[3]];
  }
  store.set({ render: { ...render, boxes } });
  touch();
}

export function moveModuleBy(widget, dcol, drow) {
  const cells = grid();
  if (!cells || widget.locked) return;
  const [col, row, cols, rows] = placed(cells, widget);
  const area = [
    Math.max(0, Math.min(cells.columns - cols, col + dcol)),
    Math.max(0, Math.min(cells.rows - rows, row + drow)),
    cols,
    rows,
  ];
  if (blockers(widgets(), cells, area, widget.id).length) return;
  commit("nudge");
  setModuleArea(widget, area);
  touch();
}

export function resizeModuleBy(widget, dcols, drows) {
  const cells = grid();
  if (!cells) return;
  const [col, row, cols, rows] = placed(cells, widget);
  const area = [col, row, Math.max(1, Math.min(cells.columns - col, cols + dcols)), Math.max(1, Math.min(cells.rows - row, rows + drows))];
  if (blockers(widgets(), cells, area, widget.id).length) {
    toast(t("Another module is in the way"), { kind: "warn" });
    return;
  }
  commit();
  setModuleArea(widget, area);
  touch();
}

/** Line up the selection: left, center, right, top, middle, bottom, distribute-h, distribute-v. */
export function align(mode) {
  const chosen = selectedWidgets().filter((widget) => !widget.locked && !isModule(widget) && boxOf(widget));
  if (chosen.length < 2) return;
  const render = store.get().render;
  const boxes = { ...render.boxes };
  const shift = (widget, dx, dy) => {
    const box = boxes[widget.id];
    widget.x += Math.round(dx);
    widget.y += Math.round(dy);
    boxes[widget.id] = [box[0] + Math.round(dx), box[1] + Math.round(dy), box[2], box[3]];
  };
  edit(null, () => {
    const all = unionBox(chosen.map((widget) => boxes[widget.id]));
    if (mode.startsWith("distribute")) {
      const horizontal = mode === "distribute-h";
      const centre = (w) => (horizontal ? boxes[w.id][0] + boxes[w.id][2] / 2 : boxes[w.id][1] + boxes[w.id][3] / 2);
      const sorted = [...chosen].sort((a, b) => centre(a) - centre(b));
      const first = centre(sorted[0]);
      const last = centre(sorted[sorted.length - 1]);
      sorted.forEach((widget, i) => {
        const delta = first + ((last - first) * i) / (sorted.length - 1) - centre(widget);
        shift(widget, horizontal ? delta : 0, horizontal ? 0 : delta);
      });
    } else {
      const [left, top, width, height] = all;
      for (const widget of chosen) {
        const b = boxes[widget.id];
        const moves = {
          left: [left - b[0], 0],
          center: [left + width / 2 - (b[0] + b[2] / 2), 0],
          right: [left + width - (b[0] + b[2]), 0],
          top: [0, top - b[1]],
          middle: [0, top + height / 2 - (b[1] + b[3] / 2)],
          bottom: [0, top + height - (b[1] + b[3])],
        }[mode];
        shift(widget, ...moves);
      }
    }
  });
  store.set({ render: { ...store.get().render, boxes } });
}

/** A module as plain widgets, to change each part. */
export async function detachModule(widget) {
  const { doc } = store.get();
  const { id } = widget;
  const serial = docSerial();
  try {
    const data = await endpoints.detach(doc.theme, doc.id, id);
    // the answer took a moment: another theme was opened, or the module went (removed, undone)
    if (serial !== docSerial() || !widgetById(id)) return;
    const parts = [];
    edit(null, (current) => {
      const index = current.widgets.findIndex((other) => other.id === id);
      for (const part of data.widgets) {
        part.id = uniqueId(part.id);
        parts.push(part);
      }
      current.widgets.splice(index, 1, ...parts);
    });
    setSelection(parts.map((part) => part.id));
    toast(t("Detached into {count} widgets", { count: parts.length }));
  } catch (error) {
    toastError(error);
  }
}

// -- looks -----------------------------------------------------------------------------

export const looks = () => specs()?.modules?.looks || {};

/** The look the theme wears now: its background, accent and cards are the look's. */
export function activeLook(current = theme()) {
  const palette = current?.palette || {};
  const style = current?.style || {};
  for (const [key, look] of Object.entries(looks())) {
    if (look.palette.bg === palette.bg && look.palette.accent === palette.accent && look.style.card === style.card) return key;
  }
  return null;
}

/** A look's colours and module style for the theme; its own palette names stay. */
export function applyLook(key) {
  const look = looks()[key];
  if (!look || !store.get().doc) return;
  edit(null, (current) => {
    current.palette = { ...(current.palette || {}), ...clone(look.palette) };
    current.style = clone(look.style);
    current.background = { ...(current.background || {}), color: "@bg" };
  });
  toast(t("Look: {name}", { name: look.name }));
}
