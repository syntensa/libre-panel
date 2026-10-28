// The canvas: the panel's picture from the renderer, and over it the boxes of
// the widgets. Click selects (Shift or Ctrl: several), dragging moves with
// snapping guides (Alt: free), the corners resize, a frame drawn on the empty
// panel selects what it touches. Modules move, swap and resize by cells.

import { setPref, store, useStore } from "../../core/state.js";
import {
  commit,
  isModule,
  selectedWidgets,
  setSelection,
  single,
  theme as currentTheme,
  toggleSelected,
  touch,
  widgetById,
  widgets,
} from "../../core/doc.js";
import { t } from "../../core/i18n.js";
import { requestRender } from "../../core/preview.js";
import { createStore, useSelector } from "../../core/store.js";
import { html, useEffect, useLayoutEffect, useRef, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { Popover, Menu } from "../../ui/layer.js";
import { currentModel } from "../../shell/panel-popover.js";
import {
  blockers,
  cellAt,
  cellBox,
  hiddenEdges,
  intersects,
  onStep,
  placed,
  resizeBox,
  snapLines,
  snapMove,
  snapTo,
  unionBox,
} from "./geometry.js";
import { addModule, addPicture, addWidget, boxOf, insertPreset, kindName, moduleGrid, moduleKinds, moduleSteps, setModuleArea, specOf, spotFor } from "./ops.js";
import { FloatingToolbar, widgetMenu } from "./toolbar.js";
import { PageTabs } from "./pages.js";

const ZOOM_STEPS = [0.1, 0.15, 0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.5, 2, 3, 4, 6, 8];
const MARGIN = { x: 40, top: 70, bottom: 104 }; // room around the panel for tabs, toolbar and zoom
const FRAME = 22; // the frame around the picture: padding and border

/** What the canvas is doing now: zoom, a drag in progress. */
export const canvasState = createStore({ zoomMode: "fit", zoom: 1, interaction: null, libDrag: null });
const useCanvas = (selector) => useSelector(canvasState, selector);

export function setZoom(mode) {
  canvasState.set({ zoomMode: mode === "fit" ? "fit" : Math.max(0.05, Math.min(8, mode)) });
}

export function zoomStep(direction) {
  const current = canvasState.get().zoom;
  const next =
    direction > 0 ? ZOOM_STEPS.find((step) => step > current + 0.01) : [...ZOOM_STEPS].reverse().find((step) => step < current - 0.01);
  setZoom(next ?? current);
}

/** The panel point under a pointer event (in panel pixels), or null without a canvas. */
let pointToPanel = () => null;

// -- dragging from the library ---------------------------------------------------------------

/**
 * Start dragging an item of the library: {kind: "module", module} | {kind: "preset",
 * preset} | {kind: "widget", type} | {kind: "picture", path}. A click without a drag
 * adds it at the usual place.
 */
export function startLibraryDrag(event, item, picture = null) {
  if (event.button !== 0 || !store.get().doc) return;
  event.preventDefault();
  const x0 = event.clientX;
  const y0 = event.clientY;
  let ghost = null;
  let moved = false;
  const target = event.currentTarget;
  target.setPointerCapture?.(event.pointerId);
  const zoneAt = (e) => {
    const point = pointToPanel(e, true);
    if (!point) return null;
    if (item.kind !== "module") return { point, ok: true };
    const grid = moduleGrid();
    if (!grid) return null;
    const cell = cellAt(grid, point[0], point[1]);
    const spot = spotFor(item.module, cell);
    const [sx, sy] = moduleSteps(grid);
    return spot ? { area: spot, ok: true } : { area: [cell[0], cell[1], sx, sy], ok: false };
  };
  const move = (e) => {
    if (!moved) {
      if (Math.hypot(e.clientX - x0, e.clientY - y0) < 5) return;
      moved = true;
      ghost = document.createElement("div");
      ghost.className = "drag-ghost";
      if (picture) {
        const image = document.createElement("img");
        image.src = picture;
        image.alt = "";
        ghost.append(image);
      } else ghost.textContent = item.label || "";
      document.body.append(ghost);
    }
    ghost.style.left = `${e.clientX + 14}px`;
    ghost.style.top = `${e.clientY + 14}px`;
    canvasState.set({ libDrag: { item, zone: zoneAt(e) } });
  };
  const end = (e) => {
    target.removeEventListener("pointermove", move);
    target.removeEventListener("pointerup", end);
    target.removeEventListener("pointercancel", end);
    ghost?.remove();
    const zone = canvasState.get().libDrag?.zone;
    canvasState.set({ libDrag: null });
    if (!moved) return dropItem(item, null);
    if (e.type === "pointerup" && zone?.ok) dropItem(item, zone);
  };
  target.addEventListener("pointermove", move);
  target.addEventListener("pointerup", end);
  target.addEventListener("pointercancel", end);
}

/** Add a library item: where it was dropped, or at the usual place. */
export function dropItem(item, zone) {
  if (item.kind === "module") return addModule(item.module, zone ? zone.area : undefined);
  const at = zone?.point || null;
  if (item.kind === "preset") return insertPreset(item.preset, at);
  if (item.kind === "picture") return addPicture(item.path, at);
  return addWidget(item.type, at);
}

// -- the canvas ---------------------------------------------------------------------------------

/** The boxes as they are shown: the renderer's, and those being dragged where they are now. */
function shownBoxes(boxes, interaction) {
  if (!interaction || interaction.kind !== "move" || !interaction.moved) return boxes;
  const shown = { ...boxes };
  const [dx, dy] = interaction.delta;
  for (const [id, box] of Object.entries(interaction.startBoxes)) {
    if (box) shown[id] = [box[0] + dx, box[1] + dy, box[2], box[3]];
  }
  return shown;
}

// (a turned widget's corners are not its box's: it is sized in the inspector)
const resizable = (widget) => {
  if (!widget || widget.locked || isModule(widget) || (Number(widget.rotate) || 0) % 360) return false;
  const spec = specOf(widget.type);
  return ("w" in spec && "h" in spec) || "size" in spec || "font_size" in spec;
};

export function Canvas() {
  const { render, selection, rev, prefs, renderError } = useStore((s) => ({
    render: s.render,
    selection: s.selection,
    rev: s.rev,
    prefs: s.prefs,
    renderError: s.renderError,
  }));
  const { zoomMode, interaction, libDrag } = useCanvas((s) => ({ zoomMode: s.zoomMode, interaction: s.interaction, libDrag: s.libDrag }));
  const stage = useRef(null);
  const canvas = useRef(null);
  const [area, setArea] = useState([900, 600]);
  const [menu, setMenu] = useState(null);
  const theme = currentTheme();
  const [width, height] = render.size;

  useLayoutEffect(() => {
    const watcher = new ResizeObserver(([entry]) => setArea([entry.contentRect.width, entry.contentRect.height]));
    watcher.observe(stage.current);
    return () => watcher.disconnect();
  }, []);

  const fit = Math.max(0.05, Math.min((area[0] - 2 * MARGIN.x - FRAME) / width, (area[1] - MARGIN.top - MARGIN.bottom - FRAME) / height, 4));
  const zoom = zoomMode === "fit" ? fit : zoomMode;
  useEffect(() => {
    if (canvasState.get().zoom !== zoom) canvasState.set({ zoom });
  });

  pointToPanel = (event, insideOnly = false) => {
    const rect = canvas.current?.getBoundingClientRect();
    if (!rect) return null;
    if (insideOnly && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) return null;
    return [(event.clientX - rect.left) / zoom, (event.clientY - rect.top) / zoom];
  };

  const model = currentModel();
  const hidden = hiddenEdges(model, render.size);
  const boxes = shownBoxes(render.boxes, interaction);
  const chosen = new Set(selection);
  const list = widgets();
  // (a module dragged onto a theme without a grid: the fine one it gets, ops.fineGrid)
  const grid = libDrag?.item?.kind === "module" ? moduleGrid() : render.grid;
  const gridShown = Boolean(grid) && (render.gridActive || list.some(isModule) || libDrag?.item?.kind === "module");
  const one = single();

  // -- pointer work ---------------------------------------------------------------------------

  const begin = (event, state, onMove, onEnd) => {
    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
    canvasState.set({ interaction: state });
    let frame = 0;
    let last = null;
    const move = (e) => {
      last = e;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        onMove(last, canvasState.get().interaction);
      });
    };
    const end = (e) => {
      cancelAnimationFrame(frame);
      if (last) onMove(last, canvasState.get().interaction);
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", end);
      target.removeEventListener("pointercancel", end);
      const done = canvasState.get().interaction;
      canvasState.set({ interaction: null });
      onEnd?.(done, e);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", end);
    target.addEventListener("pointercancel", end);
  };

  const startMove = (event, widget) => {
    if (!chosen.has(widget.id)) setSelection([widget.id]);
    const ids = chosen.has(widget.id) ? selection : [widget.id];
    const movers = ids.map(widgetById).filter((w) => w && !w.locked && !isModule(w));
    if (!movers.length) return;
    const [px, py] = pointToPanel(event);
    const startBoxes = Object.fromEntries(movers.map((w) => [w.id, render.boxes[w.id] && [...render.boxes[w.id]]]));
    const skip = new Set(movers.map((w) => w.id));
    begin(
      event,
      {
        kind: "move",
        point: [px, py],
        starts: movers.map((w) => [w, w.x, w.y]),
        startBoxes,
        bounds: unionBox(Object.values(startBoxes)),
        lines: snapLines(render.size, render.boxes, skip, hidden),
        moved: false,
        delta: [0, 0],
        guides: [],
      },
      (e, state) => {
        const [x, y] = pointToPanel(e);
        const raw = [x - state.point[0], y - state.point[1]];
        if (!state.moved && Math.abs(raw[0]) + Math.abs(raw[1]) < 3 / canvasState.get().zoom) return;
        if (!state.moved) commit();
        const snapped = snapMove(state.bounds, raw[0], raw[1], {
          lines: state.lines,
          threshold: 6 / canvasState.get().zoom,
          step: e.altKey ? 0 : store.get().prefs.snap,
          guides: store.get().prefs.guides && !e.altKey,
        });
        for (const [w, sx, sy] of state.starts) {
          w.x = sx + snapped.dx;
          w.y = sy + snapped.dy;
        }
        canvasState.set({ interaction: { ...state, moved: true, delta: [snapped.dx, snapped.dy], guides: snapped.guides } });
        touch();
      },
      (state) => {
        if (!state?.moved) return;
        const shown = shownBoxes(store.get().render.boxes, state);
        store.set({ render: { ...store.get().render, boxes: shown } });
        requestRender(0);
      },
    );
  };

  const startResize = (event, widget, edge) => {
    event.stopPropagation();
    const box = render.boxes[widget.id];
    if (!box) return;
    const spec = specOf(widget.type);
    const mode = "w" in spec && "h" in spec ? "box" : "size" in spec ? "size" : "font";
    const fields = { x: widget.x, y: widget.y, w: widget.w, h: widget.h, size: widget.size, font_size: widget.font_size };
    const [px, py] = pointToPanel(event);
    begin(event, { kind: "resize", id: widget.id, edge, point: [px, py], box, fields, mode, moved: false }, (e, state) => {
      const [x, y] = pointToPanel(e);
      const dx = x - state.point[0];
      const dy = y - state.point[1];
      if (!state.moved && Math.abs(dx) + Math.abs(dy) < 2 / canvasState.get().zoom) return;
      if (!state.moved) commit();
      const keep = e.shiftKey || state.mode !== "box";
      const next = resizeBox(state.box, edge, dx, dy, { keep, min: 4 });
      const step = e.altKey ? 0 : store.get().prefs.snap;
      if (state.mode === "box") {
        // a box of 0 × 0 is the picture's own size: start from what is drawn
        widget.w = Math.max(4, snapTo(next[2], step));
        widget.h = Math.max(4, snapTo(next[3], step));
        widget.x = state.fields.x + (next[0] - state.box[0]);
        widget.y = state.fields.y + (next[1] - state.box[1]);
      } else {
        const factor = next[3] / Math.max(1, state.box[3]);
        if (state.mode === "size") widget.size = Math.max(4, Math.round(state.fields.size * factor));
        else widget.font_size = Math.max(4, Math.round(state.fields.font_size * factor));
        widget.x = state.fields.x + (edge.includes("w") ? next[0] - state.box[0] : 0);
        widget.y = state.fields.y + (edge.includes("n") ? next[1] - state.box[1] : 0);
      }
      const shown = { ...store.get().render.boxes, [widget.id]: next };
      store.set({ render: { ...store.get().render, boxes: shown } });
      canvasState.set({ interaction: { ...state, moved: true } });
      touch();
    }, (state) => state?.moved && requestRender(0));
  };

  const startCellMove = (event, widget) => {
    if (selection.length !== 1 || selection[0] !== widget.id) setSelection([widget.id]);
    if (widget.locked || !grid) return;
    const start = placed(grid, widget);
    const grab = cellAt(grid, ...pointToPanel(event));
    const [sx, sy] = moduleSteps(grid);
    begin(event, { kind: "cells", id: widget.id, start, offset: [grab[0] - start[0], grab[1] - start[1]], zone: null }, (e, state) => {
      const [c, r] = cellAt(grid, ...pointToPanel(e));
      // in steps of the grid mode (Alt: cell by cell)
      const col = onStep(c - state.offset[0], e.altKey ? 1 : sx, 0, grid.columns - start[2]);
      const row = onStep(r - state.offset[1], e.altKey ? 1 : sy, 0, grid.rows - start[3]);
      let zone = null;
      if (col !== start[0] || row !== start[1]) {
        const area = [col, row, start[2], start[3]];
        const others = blockers(widgets(), grid, area, widget.id);
        if (!others.length) zone = { area, ok: true };
        else if (others.length === 1) {
          // a swap: the other one goes where this one was, at its own size
          const other = others[0];
          const [, , oc, or] = placed(grid, other);
          const there = [start[0], start[1], Math.min(oc, grid.columns - start[0]), Math.min(or, grid.rows - start[1])];
          const ok =
            !intersects(there, area) &&
            !blockers(widgets(), grid, there, widget.id, other.id).length &&
            !blockers(widgets(), grid, area, widget.id, other.id).length;
          zone = { area, ok, swap: ok ? { id: other.id, there } : null };
        } else zone = { area, ok: false };
      }
      canvasState.set({ interaction: { ...state, zone } });
    }, (state) => {
      const zone = state?.zone;
      if (!zone?.ok) return;
      commit();
      setModuleArea(widget, zone.area);
      if (zone.swap) setModuleArea(widgetById(zone.swap.id), zone.swap.there);
      touch();
    });
  };

  const startCellResize = (event, widget, edge) => {
    event.stopPropagation();
    if (widget.locked || !grid) return;
    const start = placed(grid, widget);
    const steps = moduleSteps(grid);
    let committed = false;
    begin(event, { kind: "cell-resize", id: widget.id }, (e) => {
      const [c, r] = cellAt(grid, ...pointToPanel(e));
      const [sx, sy] = e.altKey ? [1, 1] : steps; // in steps of the grid mode (Alt: cell by cell)
      let [col, row, cols, rows] = start;
      let right = col + cols - 1;
      let bottom = row + rows - 1;
      // an edge goes to the nearest step from the other edge: the size is whole steps
      if (edge.includes("e")) right = col + onStep(c - col + 1, sx, Math.min(sx, grid.columns - col), grid.columns - col) - 1;
      if (edge.includes("w")) col = right + 1 - onStep(right + 1 - c, sx, Math.min(sx, right + 1), right + 1);
      if (edge.includes("s")) bottom = row + onStep(r - row + 1, sy, Math.min(sy, grid.rows - row), grid.rows - row) - 1;
      if (edge.includes("n")) row = bottom + 1 - onStep(bottom + 1 - r, sy, Math.min(sy, bottom + 1), bottom + 1);
      const area = [col, row, right - col + 1, bottom - row + 1];
      const now = placed(grid, widget);
      if (area.every((v, i) => v === now[i]) || blockers(widgets(), grid, area, widget.id).length) return;
      if (!committed) {
        commit();
        committed = true;
      }
      setModuleArea(widget, area);
      touch();
    }, () => committed && requestRender(0));
  };

  const startMarquee = (event) => {
    if (event.button !== 0 || !theme) return;
    if (event.target.closest(".box, .handle, .floating-toolbar, .page-tabs, .zoom-bar, .canvas-foot, .popover")) return;
    const point = pointToPanel(event);
    if (!point) return;
    const additive = event.shiftKey || event.ctrlKey || event.metaKey;
    begin(event, { kind: "marquee", from: point, to: point, additive }, (e, state) => {
      canvasState.set({ interaction: { ...state, to: pointToPanel(e) } });
    }, (state) => {
      if (!state) return;
      const [x0, x1] = [Math.min(state.from[0], state.to[0]), Math.max(state.from[0], state.to[0])];
      const [y0, y1] = [Math.min(state.from[1], state.to[1]), Math.max(state.from[1], state.to[1])];
      if (x1 - x0 < 3 && y1 - y0 < 3) {
        if (!state.additive) setSelection([]);
        return;
      }
      const hits = Object.entries(store.get().render.boxes)
        .filter(([id]) => {
          const widget = widgetById(id);
          return widget && !widget.locked && widget.visible !== false;
        })
        .filter(([, b]) => intersects(b, [x0, y0, x1 - x0, y1 - y0]))
        .map(([id]) => id);
      setSelection(state.additive ? [...selection, ...hits] : hits);
    });
  };

  const pointerOnBox = (event, widget) => {
    if (event.button === 2) return;
    if (event.button !== 0) return;
    event.stopPropagation();
    event.preventDefault();
    if (event.shiftKey || event.ctrlKey || event.metaKey) return toggleSelected(widget.id);
    if (isModule(widget)) return startCellMove(event, widget);
    startMove(event, widget);
  };

  const contextMenu = (event, widget) => {
    event.preventDefault();
    if (!chosen.has(widget.id)) setSelection([widget.id]);
    const rect = new DOMRect(event.clientX, event.clientY, 0, 0);
    setMenu({ anchor: { current: { getBoundingClientRect: () => rect, contains: () => false } } });
  };

  const wheel = (event) => {
    if (!(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    zoomStep(event.deltaY < 0 ? 1 : -1);
  };
  useEffect(() => {
    const node = stage.current;
    node.addEventListener("wheel", wheel, { passive: false });
    return () => node.removeEventListener("wheel", wheel);
  }, []);

  // -- drawing ------------------------------------------------------------------------------------

  const z = zoom;
  const place = (box) => ({
    left: `${box[0] * z}px`,
    top: `${box[1] * z}px`,
    width: `${Math.max(box[2] * z, 6)}px`,
    height: `${Math.max(box[3] * z, 6)}px`,
  });
  const cells = [];
  if (gridShown) {
    const busy = libDrag || interaction?.kind === "cells" || interaction?.kind === "cell-resize";
    for (let row = 0; row < grid.rows; row++) {
      for (let col = 0; col < grid.columns; col++) {
        if (!busy && blockers(list, grid, [col, row, 1, 1]).length) continue;
        cells.push(html`<div class="cell" key=${`c${col}-${row}`} style=${place(cellBox(grid, col, row))}></div>`);
      }
    }
  }
  const zone = libDrag?.zone?.area ? libDrag.zone : interaction?.kind === "cells" ? interaction.zone : null;
  const dropKind = libDrag?.item?.module || (interaction?.kind === "cells" ? widgetById(interaction.id)?.module : null);
  const handles = [];
  // (the handle being dragged stays: a pointer captured by an element that is gone sends nothing)
  if (one && !one.locked && interaction?.kind !== "cells" && !libDrag) {
    // a module's handles stand on its cells, which change at once: the drawn box comes a
    // render later, and a handle grabbed right after a resize would sit at the old edge
    const onCells = isModule(one) && grid && [one.col, one.row, one.cols, one.rows].every(Number.isFinite);
    const box = onCells ? cellBox(grid, one.col, one.row, one.cols, one.rows) : boxes[one.id] || boxOf(one);
    if (box && isModule(one) && grid) {
      for (const edge of ["n", "s", "e", "w", "ne", "nw", "se", "sw"]) {
        const x = box[0] + (edge.includes("w") ? 0 : edge.includes("e") ? box[2] : box[2] / 2);
        const y = box[1] + (edge.includes("n") ? 0 : edge.includes("s") ? box[3] : box[3] / 2);
        handles.push(html`<div key=${edge} class=${`handle ${edge}${edge.length === 1 ? " side" : ""}`} title=${t("Drag to change the size")}
          style=${{ left: `${x * z}px`, top: `${y * z}px` }} onPointerDown=${(event) => startCellResize(event, one, edge)}></div>`);
      }
    } else if (box && resizable(one) && interaction?.kind !== "move") {
      for (const edge of ["nw", "ne", "sw", "se"]) {
        const x = box[0] + (edge.includes("w") ? 0 : box[2]);
        const y = box[1] + (edge.includes("n") ? 0 : box[3]);
        handles.push(html`<div key=${edge} class=${`handle ${edge}`} title=${t("Drag to change the size; Shift keeps the shape")}
          style=${{ left: `${x * z}px`, top: `${y * z}px` }} onPointerDown=${(event) => startResize(event, one, edge)}></div>`);
      }
    }
  }
  const strips = [];
  if (hidden) {
    const rects = {
      top: [0, 0, width, hidden.top],
      right: [width - hidden.right, 0, hidden.right, height],
      bottom: [0, height - hidden.bottom, width, hidden.bottom],
      left: [0, 0, hidden.left, height],
    };
    for (const [edge, px] of Object.entries(hidden)) {
      if (!px) continue;
      const [x, y, w, h] = rects[edge];
      strips.push(html`<div key=${edge} class=${`hidden-strip ${edge}`} title=${t("Hidden behind the panel's frame: {px} px", { px })}
        style=${{ left: `${x * z}px`, top: `${y * z}px`, width: `${w * z}px`, height: `${h * z}px` }}></div>`);
    }
  }
  const hiddenText = hidden
    ? Object.entries(hidden)
        .filter(([, px]) => px)
        .map(([edge, px]) => ({ top: t("top {px} px", { px }), right: t("right {px} px", { px }), bottom: t("bottom {px} px", { px }), left: t("left {px} px", { px }) })[edge])
        .join(", ")
    : "";
  const marquee =
    interaction?.kind === "marquee"
      ? [
          Math.min(interaction.from[0], interaction.to[0]),
          Math.min(interaction.from[1], interaction.to[1]),
          Math.abs(interaction.to[0] - interaction.from[0]),
          Math.abs(interaction.to[1] - interaction.from[1]),
        ]
      : null;
  const selectionBox = unionBox(selection.map((id) => boxes[id] || (widgetById(id) && boxOf(widgetById(id)))));
  const round = model?.shape === "round";

  return html`<main class="stage" ref=${stage} onPointerDown=${startMarquee} aria-label=${t("Panel")}>
    <div class="stage-scroll">
      <div class="stage-inner">
        <div class="panel-frame">
          <div
            ref=${canvas}
            class=${`panel-canvas${round ? " round" : ""}${z >= 2 ? " pixelated" : ""}`}
            style=${{ width: `${Math.round(width * z)}px`, height: `${Math.round(height * z)}px` }}
          >
            ${render.png
              ? html`<img class="preview" src=${render.png} alt=${t("Preview of the panel")} draggable="false" />`
              : html`<div class="preview-wait">${renderError ? null : html`<span class="spinner"></span>`}</div>`}
            <div class="overlay" data-rev=${rev}>
              ${strips}
              ${cells}
              ${list.map((widget) => {
                const box = boxes[widget.id] || (isModule(widget) && grid ? cellBox(grid, ...placed(grid, widget)) : null);
                if (!box) return null;
                const classes = ["box", isModule(widget) ? "module" : "", chosen.has(widget.id) ? "selected" : "", widget.locked ? "locked" : "", widget.visible === false ? "invisible" : ""];
                return html`<div
                  key=${widget.id}
                  class=${classes.join(" ")}
                  data-id=${widget.id}
                  title=${widget.locked ? `${widget.id} — ${t("locked")}` : widget.id}
                  style=${place(box)}
                  onPointerDown=${(event) => (widget.locked && !event.shiftKey ? null : pointerOnBox(event, widget))}
                  onContextMenu=${(event) => contextMenu(event, widget)}
                ></div>`;
              })}
              ${selection.length > 1 && selectionBox ? html`<div class="selection-frame" style=${place(selectionBox)}></div>` : null}
              ${zone
                ? html`<div class=${`drop${zone.ok ? "" : " blocked"}${zone.swap ? " swap" : ""}`} style=${place(cellBox(grid, ...zone.area))}>
                    <span>${zone.swap ? t("Swap") : `${kindName(dropKind)} · ${zone.area[2]} × ${zone.area[3]}`}</span>
                  </div>`
                : null}
              ${zone?.swap ? html`<div class="drop swap" style=${place(cellBox(grid, ...zone.swap.there))}></div>` : null}
              ${libDrag?.zone?.point
                ? html`<div class="drop-point" style=${{ left: `${libDrag.zone.point[0] * z}px`, top: `${libDrag.zone.point[1] * z}px` }}></div>`
                : null}
              ${handles}
              ${(interaction?.guides || []).map(
                (guide, i) => html`<div key=${`g${i}`} class=${`guide ${guide.axis}`}
                  style=${guide.axis === "v" ? { left: `${guide.at * z}px` } : { top: `${guide.at * z}px` }}></div>`,
              )}
              ${marquee ? html`<div class="marquee" style=${place(marquee)}></div>` : null}
            </div>
            ${!list.length && render.png && !libDrag ? html`<${EmptyPage} libraryOpen=${prefs.panel === "library"} />` : null}
          </div>
        </div>
        <div class="canvas-foot">
          <span class="canvas-size">
            <span class="mono">${width} × ${height}</span>
            ${hiddenText ? html` · ${t("{edges} hidden by the frame", { edges: hiddenText })}` : null}
          </span>
          ${renderError ? html`<span class="render-error" role="alert"><${Icon} name="warning" size=${13} /> ${renderError}</span>` : null}
          ${render.warnings.length && !renderError ? html`<${Warnings} warnings=${render.warnings} />` : null}
          <span class="grow"></span>
          <button type="button" role="switch" aria-checked=${String(prefs.live)} class=${`live-toggle${prefs.live ? " on" : ""}`}
            title=${t("the preview with this computer's sensors")} onClick=${() => setPref("live", !prefs.live)}>
            <span class="dot"></span>${t("Live values")}
          </button>
        </div>
      </div>
    </div>
    <${PageTabs} />
    ${selection.length && selectionBox && !libDrag && interaction?.kind !== "marquee"
      ? html`<${FloatingToolbar} box=${selectionBox} zoom=${z} canvas=${canvas} stage=${stage} hidden=${Boolean(interaction)} />`
      : null}
    <div class="zoom-bar" role="toolbar" aria-label=${t("Zoom")}>
      <button type="button" class="icon-btn" aria-label=${t("Zoom out")} title=${t("Zoom out")} onClick=${() => zoomStep(-1)}><${Icon} name="minus" /></button>
      <span class="zoom-value mono" aria-live="polite">${Math.round(z * 100)} %</span>
      <button type="button" class="icon-btn" aria-label=${t("Zoom in")} title=${t("Zoom in")} onClick=${() => zoomStep(1)}><${Icon} name="plus" /></button>
      <span class="zoom-sep"></span>
      <button type="button" class="zoom-text" aria-pressed=${String(zoomMode === "fit")} onClick=${() => setZoom("fit")}>${t("Fit")}</button>
      <button type="button" class="zoom-text" aria-pressed=${String(zoomMode === 1)} onClick=${() => setZoom(1)}>1 : 1</button>
    </div>
    ${menu
      ? html`<${Popover} anchor=${menu.anchor} open=${true} onClose=${() => setMenu(null)} label=${t("Widget")} width=${240}>
          <${Menu} items=${widgetMenu()} onClose=${() => setMenu(null)} />
        <//>`
      : null}
  </main>`;
}

/** A theme with nothing on it: what to do, over the empty panel (clicks and drops go through to
 * the canvas, but for the way to the blocks where they are not open). */
function EmptyPage({ libraryOpen }) {
  return html`<div class="canvas-empty" role="status">
    <strong>${t("This theme is still empty")}</strong>
    <span>${libraryOpen ? t("Click a block on the left to add it, or drag it here.") : t("Add what it should show from the blocks.")}</span>
    ${libraryOpen
      ? null
      : html`<button type="button" class="btn" onPointerDown=${(event) => event.stopPropagation()}
          onClick=${() => setPref("panel", "library")}><${Icon} name="blocks" size=${15} /> ${t("Show the blocks")}</button>`}
  </div>`;
}

function Warnings({ warnings }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  return html`<button ref=${anchor} type="button" class="render-warn" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      <${Icon} name="warning" size=${13} /> ${warnings.length === 1 ? t("1 note") : t("{count} notes", { count: warnings.length })}
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${t("Notes")} width=${380} placement="top-start">
      <ul class="warning-list">${warnings.map((text, i) => html`<li key=${i}>${text}</li>`)}</ul>
    <//>`;
}

export { selectedWidgets };
