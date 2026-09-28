"use strict";

// Libre Panel theme editor. The preview image comes from the same Python
// renderer that drives the panel, so what you see is what the panel shows.

const THEME_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const PALETTE_NAME_RE = /^[a-z][a-z0-9_-]{0,31}$/;
const FORMAT_HINT = "{value:.0f}{unit} · {value:.1f} · {value:bytes}/s · {value:duration} · {label}";
const POSITION_FIELDS = ["x", "y", "w", "h", "size"];
const ADVANCED_FIELDS = ["id", "visible", "locked", "hide_if_missing"];
const STEPS = { opacity: 0.05, glow: 0.05, stroke: 0.1 };
const HISTORY_LIMIT = 200;

const state = {
  specs: null,
  models: [],
  themes: [],
  assets: [],
  themeId: null, // folder of the loaded theme (also the asset base for rendering)
  builtin: false,
  theme: null,
  selection: new Set(),
  boxes: {},
  size: [480, 320],
  zoom: 1,
  zoomMode: "fit",
  snap: 4,
  guides: true,
  live: false,
  liveTimer: null,
  dirty: false,
  renderSeq: 0,
  drag: null,
  marquee: null,
  adaptBase: null, // theme before a run of panel changes, so flipping models never compounds
  undo: [],
  redo: [],
  lastCommit: { key: null, time: 0 },
  clipboard: null,
  app: null, // the background app (tray/start) when it serves this editor
  appTimer: null,
};

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const ICON_PATHS = {
  eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"],
  "eye-off": ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z", "M4 4l16 16"],
  lock: ["M6 11h12v10H6z", "M8 11V7a4 4 0 0 1 8 0v4"],
  unlock: ["M6 11h12v10H6z", "M8 11V7a4 4 0 0 1 7.6-1.7"],
};

function svgIcon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "15");
  svg.setAttribute("height", "15");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const d of ICON_PATHS[name]) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

// ---------------------------------------------------------------- language

/** Translate an English text; {name} placeholders are filled from `vars`. */
function t(text, vars = {}) {
  const message = state.i18n?.messages?.[text] ?? text;
  return message.replace(/\{(\w+)\}/g, (all, name) => (name in vars ? String(vars[name]) : all));
}
const fieldLabel = (key) => state.i18n?.fields?.[key] ?? key.replace(/_/g, " ");
const widgetLabel = (type) => state.specs?.widget_labels?.[type] ?? state.i18n?.widgets?.[type] ?? type;
const enumLabel = (value) => state.i18n?.enums?.[value] ?? value;
const iconLabel = (name) => state.i18n?.icons?.[name] ?? name;

function applyI18n() {
  document.documentElement.lang = state.i18n?.language || "en";
  const clean = (text) => text.replace(/\s+/g, " ").trim();
  for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = t(clean(node.textContent));
  for (const node of document.querySelectorAll("[data-i18n-attr]")) {
    for (const attr of node.dataset.i18nAttr.split(" ")) node.setAttribute(attr, t(clean(node.getAttribute(attr))));
  }
}

const RESTORE_KEY = "libre-panel-restore";

/** Switching the language reloads the editor; unsaved work comes back after the reload. */
async function setLanguage(setting) {
  try {
    await api("POST", "/api/language", { language: setting });
  } catch (error) {
    setStatus(error.message, "error");
    return;
  }
  try {
    const keep = { theme: state.theme, themeId: state.themeId, builtin: state.builtin, dirty: state.dirty };
    sessionStorage.setItem(RESTORE_KEY, JSON.stringify(keep));
    state.dirty = false; // nothing is lost, so no "leave page?" question
  } catch {
    if (!confirmDiscard()) return;
    state.dirty = false;
  }
  location.reload();
}

function takeRestore() {
  try {
    const raw = sessionStorage.getItem(RESTORE_KEY);
    sessionStorage.removeItem(RESTORE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

async function restoreSession(saved) {
  state.theme = saved.theme;
  state.themeId = saved.themeId;
  state.builtin = saved.builtin;
  state.selection = new Set();
  state.boxes = {};
  await loadAssets();
  await refreshThemeList(saved.themeId ?? "");
  resetHistory();
  if (saved.dirty) markDirty();
  else markClean();
  refreshAll();
}

function setStatus(message, kind = "") {
  const bar = $("#status");
  bar.textContent = message;
  bar.className = "status" + (kind ? " " + kind : "");
  bar.title = message;
}

async function api(method, url, body, raw = false) {
  const options = { method, headers: {} };
  if (method !== "GET") options.headers["X-Libre-Panel"] = "1";
  if (body !== undefined) {
    options.body = raw ? body : JSON.stringify(body);
    options.headers["Content-Type"] = raw ? "application/octet-stream" : "application/json";
  }
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

const clone = (value) => JSON.parse(JSON.stringify(value));
const widgetById = (id) => state.theme.widgets.find((w) => w.id === id);
const selectedWidgets = () => state.theme.widgets.filter((w) => state.selection.has(w.id));
const currentModel = () => state.models.find((m) => m.id === state.theme?.display?.model);

// Strips of the frame that the panel's bezel hides, when the theme is exactly
// that panel's size: a guide only, nothing is cropped or moved.
function hiddenEdges() {
  const model = currentModel();
  const [w, h] = state.size || [0, 0];
  const orientation = w > h ? "landscape" : "portrait";
  if (!model?.hidden || model[orientation][0] !== w || model[orientation][1] !== h) return null;
  const edges = model.hidden[orientation];
  return Object.values(edges).some((px) => px > 0) ? edges : null;
}
const single = () => (state.selection.size === 1 ? widgetById([...state.selection][0]) : null);

function slug(text) {
  return (text || "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
}

function uniqueId(base) {
  const taken = new Set(state.theme.widgets.map((w) => w.id));
  const stem = base.replace(/-\d+$/, "") || "widget";
  if (!taken.has(stem)) return stem;
  for (let i = 2; ; i++) if (!taken.has(`${stem}-${i}`)) return `${stem}-${i}`;
}

// ---------------------------------------------------------------- history

function snapshot() {
  return JSON.stringify(state.theme);
}

/** Record the state before a change. Changes with the same key in quick
 * succession (typing into one field) become a single undo step. */
function commit(key = null) {
  const now = Date.now();
  const coalesce = key && key === state.lastCommit.key && now - state.lastCommit.time < 900;
  state.lastCommit = { key, time: now };
  if (key !== "adapt") state.adaptBase = null;
  if (!coalesce) {
    state.undo.push(snapshot());
    if (state.undo.length > HISTORY_LIMIT) state.undo.shift();
    state.redo = [];
  }
  markDirty();
  updateHistoryButtons();
}

function restore(json) {
  state.theme = JSON.parse(json);
  state.selection = new Set([...state.selection].filter((id) => widgetById(id)));
  state.adaptBase = null;
  state.lastCommit = { key: null, time: 0 };
  markDirty();
  refreshAll();
}

function undo() {
  if (!state.undo.length) return;
  state.redo.push(snapshot());
  restore(state.undo.pop());
  setStatus(t("Undone"));
}

function redo() {
  if (!state.redo.length) return;
  state.undo.push(snapshot());
  restore(state.redo.pop());
  setStatus(t("Redone"));
}

function updateHistoryButtons() {
  $("#btn-undo").disabled = !state.undo.length;
  $("#btn-redo").disabled = !state.redo.length;
}

function markDirty() {
  state.dirty = true;
  document.title = "● " + t("Libre Panel Theme Editor");
}

function markClean() {
  state.dirty = false;
  document.title = t("Libre Panel Theme Editor");
}

function confirmDiscard() {
  return !state.dirty || confirm(t("Discard unsaved changes?"));
}

function resetHistory() {
  state.undo = [];
  state.redo = [];
  state.adaptBase = null;
  state.lastCommit = { key: null, time: 0 };
  updateHistoryButtons();
}

function refreshAll() {
  syncPanelBar();
  buildList();
  buildProps();
  updateHistoryButtons();
  scheduleRender(0);
}

// ---------------------------------------------------------------- rendering

let renderTimer = null;

function scheduleRender(delay = 60) {
  clearTimeout(renderTimer);
  renderTimer = setTimeout(render, delay);
}

async function render() {
  if (!state.theme) return;
  const seq = ++state.renderSeq;
  try {
    const data = await api("POST", "/api/render", { theme: state.theme, base: state.themeId, live: state.live });
    if (seq !== state.renderSeq) return; // a newer render is on its way
    $("#preview").src = "data:image/png;base64," + data.png;
    state.boxes = data.boxes;
    state.size = [data.width, data.height];
    layoutStage();
    drawOverlay();
    if (data.warnings.length) setStatus(t("Warning: {text}", { text: data.warnings.join(" · ") }), "warn");
    else setStatus(`${state.theme.name} · ${data.width}×${data.height}${state.dirty ? " · " + t("unsaved") : ""}`);
  } catch (error) {
    if (seq === state.renderSeq) setStatus(error.message, "error");
  }
}

function layoutStage() {
  const [w, h] = state.size;
  const stage = $("#stage");
  const fit = Math.max(0.05, Math.min((stage.clientWidth - 48) / w, (stage.clientHeight - 48) / h, 4));
  state.zoom = state.zoomMode === "fit" ? fit : state.zoomMode;
  const wrap = $("#canvas-wrap");
  wrap.style.width = `${Math.round(w * state.zoom)}px`;
  wrap.style.height = `${Math.round(h * state.zoom)}px`;
  wrap.classList.toggle("round", currentModel()?.shape === "round");
  stage.classList.toggle("zoomed", state.zoomMode !== "fit" && state.zoom > fit);
  $("#preview").classList.toggle("pixelated", state.zoom >= 2);
  $("#zoom-label").textContent = `${Math.round(state.zoom * 100)}%`;
}

function setZoom(mode) {
  state.zoomMode = mode === "fit" ? "fit" : Math.max(0.1, Math.min(8, mode));
  layoutStage();
  drawOverlay();
}

function zoomStep(direction) {
  const steps = [0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.5, 2, 3, 4, 6, 8];
  const current = state.zoom;
  const next = direction > 0 ? steps.find((s) => s > current + 0.01) : [...steps].reverse().find((s) => s < current - 0.01);
  setZoom(next ?? current);
}

function placeBox(node, [x, y, w, h]) {
  const z = state.zoom;
  node.style.left = `${x * z}px`;
  node.style.top = `${y * z}px`;
  node.style.width = `${Math.max(w * z, 6)}px`;
  node.style.height = `${Math.max(h * z, 6)}px`;
}

function drawOverlay() {
  const overlay = $("#overlay");
  overlay.replaceChildren();
  const hidden = hiddenEdges();
  if (hidden) {
    const [w, h] = state.size;
    const rects = {
      top: [0, 0, w, hidden.top],
      right: [w - hidden.right, 0, hidden.right, h],
      bottom: [0, h - hidden.bottom, w, hidden.bottom],
      left: [0, 0, hidden.left, h],
    };
    for (const [edge, px] of Object.entries(hidden)) {
      if (!px) continue;
      const node = el("div", { class: `hidden-strip ${edge}`, title: t("Hidden behind the panel's frame: {px} px", { px }) });
      const [x, y, rw, rh] = rects[edge];
      const z = state.zoom;
      Object.assign(node.style, { left: `${x * z}px`, top: `${y * z}px`, width: `${rw * z}px`, height: `${rh * z}px` });
      overlay.append(node);
    }
  }
  for (const widget of state.theme.widgets) {
    const box = state.boxes[widget.id];
    if (!box) continue;
    const node = el("div", {
      class: ["box", state.selection.has(widget.id) ? "selected" : "", widget.locked ? "locked" : ""].join(" "),
      title: `${widget.id} (${widgetLabel(widget.type)})${widget.locked ? " — " + t("locked") : ""}`,
    });
    node.dataset.id = widget.id;
    placeBox(node, box);
    node.addEventListener("pointerdown", startDrag);
    overlay.append(node);
  }
  for (const guide of state.drag?.guides || []) {
    const line = el("div", { class: `guide ${guide.axis}` });
    if (guide.axis === "v") line.style.left = `${guide.at * state.zoom}px`;
    else line.style.top = `${guide.at * state.zoom}px`;
    overlay.append(line);
  }
  if (state.marquee) {
    const m = state.marquee;
    const node = el("div", { class: "marquee" });
    placeBox(node, [Math.min(m.x0, m.x1), Math.min(m.y0, m.y1), Math.abs(m.x1 - m.x0), Math.abs(m.y1 - m.y0)]);
    overlay.append(node);
  }
}

// ---------------------------------------------------------------- selection

function setSelection(ids) {
  state.selection = new Set(ids);
  for (const node of document.querySelectorAll("#overlay .box")) {
    node.classList.toggle("selected", state.selection.has(node.dataset.id));
  }
  buildList();
  buildProps();
}

function toggleSelection(id, additive) {
  if (!additive) return setSelection(id ? [id] : []);
  const ids = new Set(state.selection);
  if (ids.has(id)) ids.delete(id);
  else ids.add(id);
  setSelection([...ids]);
}

function unionBox(ids) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const id of ids) {
    const b = state.boxes[id];
    if (!b) continue;
    x0 = Math.min(x0, b[0]);
    y0 = Math.min(y0, b[1]);
    x1 = Math.max(x1, b[0] + b[2]);
    y1 = Math.max(y1, b[1] + b[3]);
  }
  return x0 === Infinity ? null : [x0, y0, x1 - x0, y1 - y0];
}

// ---------------------------------------------------------------- drag, snap, marquee

function canvasPoint(event) {
  const rect = $("#canvas-wrap").getBoundingClientRect();
  return [(event.clientX - rect.left) / state.zoom, (event.clientY - rect.top) / state.zoom];
}

function startDrag(event) {
  if (event.button !== 0) return;
  event.stopPropagation();
  event.preventDefault();
  const id = event.currentTarget.dataset.id;
  if (event.shiftKey || event.ctrlKey || event.metaKey) {
    toggleSelection(id, true);
    return;
  }
  if (!state.selection.has(id)) setSelection([id]);
  const movers = selectedWidgets().filter((w) => !w.locked);
  if (!movers.length) return; // everything selected is locked
  const [px, py] = canvasPoint(event);
  state.drag = {
    px,
    py,
    starts: movers.map((w) => ({ w, x: w.x, y: w.y })),
    bounds: unionBox(movers.map((w) => w.id)),
    boxes: Object.fromEntries(movers.map((w) => [w.id, state.boxes[w.id] && [...state.boxes[w.id]]])),
    moved: false,
    guides: [],
  };
  const target = $("#overlay");
  target.setPointerCapture(event.pointerId);
  target.addEventListener("pointermove", onDrag);
  target.addEventListener("pointerup", endDrag, { once: true });
  target.addEventListener("pointercancel", endDrag, { once: true });
}

function snapDelta(dx, dy, free) {
  const drag = state.drag;
  const b = drag.bounds;
  if (!b) return { dx: Math.round(dx), dy: Math.round(dy), guides: [] };
  let nx = b[0] + dx;
  let ny = b[1] + dy;
  const guides = [];
  if (!free && state.guides) {
    const threshold = 6 / state.zoom;
    const xs = [0, state.size[0] / 2, state.size[0]];
    const ys = [0, state.size[1] / 2, state.size[1]];
    const hidden = hiddenEdges();
    if (hidden) {
      xs.push(hidden.left, state.size[0] - hidden.right);
      ys.push(hidden.top, state.size[1] - hidden.bottom);
    }
    for (const [id, box] of Object.entries(state.boxes)) {
      if (state.selection.has(id)) continue;
      xs.push(box[0], box[0] + box[2] / 2, box[0] + box[2]);
      ys.push(box[1], box[1] + box[3] / 2, box[1] + box[3]);
    }
    const snapAxis = (pos, length, lines, axis) => {
      let best = null;
      for (const offset of [0, length / 2, length]) {
        for (const line of lines) {
          const distance = Math.abs(pos + offset - line);
          if (distance <= threshold && (!best || distance < best.distance)) {
            best = { distance, pos: line - offset, at: line };
          }
        }
      }
      if (!best) return null;
      guides.push({ axis, at: best.at });
      return best.pos;
    };
    const sx = snapAxis(nx, b[2], xs, "v");
    const sy = snapAxis(ny, b[3], ys, "h");
    if (sx !== null) nx = sx;
    else if (state.snap > 1) nx = Math.round(nx / state.snap) * state.snap;
    if (sy !== null) ny = sy;
    else if (state.snap > 1) ny = Math.round(ny / state.snap) * state.snap;
  } else if (!free && state.snap > 1) {
    nx = Math.round(nx / state.snap) * state.snap;
    ny = Math.round(ny / state.snap) * state.snap;
  }
  return { dx: Math.round(nx - b[0]), dy: Math.round(ny - b[1]), guides };
}

function onDrag(event) {
  const drag = state.drag;
  if (!drag) return;
  const [px, py] = canvasPoint(event);
  const raw = [px - drag.px, py - drag.py];
  if (!drag.moved) {
    if (Math.abs(raw[0]) + Math.abs(raw[1]) < 2 / state.zoom) return;
    commit();
    drag.moved = true;
  }
  const { dx, dy, guides } = snapDelta(raw[0], raw[1], event.altKey);
  drag.guides = guides;
  for (const start of drag.starts) {
    start.w.x = start.x + dx;
    start.w.y = start.y + dy;
    const box = drag.boxes[start.w.id];
    if (box) state.boxes[start.w.id] = [box[0] + dx, box[1] + dy, box[2], box[3]];
  }
  drawOverlay();
  syncPositionFields();
  scheduleRender(40);
}

function endDrag(event) {
  const target = $("#overlay");
  target.removeEventListener("pointermove", onDrag);
  const moved = state.drag?.moved;
  state.drag = null;
  drawOverlay();
  if (moved) scheduleRender(0);
}

function startMarquee(event) {
  if (event.button !== 0 || !state.theme) return;
  if (event.target.closest(".box")) return;
  const [x, y] = canvasPoint(event);
  state.marquee = { x0: x, y0: y, x1: x, y1: y, additive: event.shiftKey || event.ctrlKey || event.metaKey };
  const stage = $("#stage");
  stage.setPointerCapture(event.pointerId);
  const move = (e) => {
    const [mx, my] = canvasPoint(e);
    state.marquee.x1 = mx;
    state.marquee.y1 = my;
    drawOverlay();
  };
  stage.addEventListener("pointermove", move);
  stage.addEventListener(
    "pointerup",
    () => {
      stage.removeEventListener("pointermove", move);
      const m = state.marquee;
      state.marquee = null;
      const [x0, x1] = [Math.min(m.x0, m.x1), Math.max(m.x0, m.x1)];
      const [y0, y1] = [Math.min(m.y0, m.y1), Math.max(m.y0, m.y1)];
      if (x1 - x0 < 3 && y1 - y0 < 3) {
        if (!m.additive) setSelection([]);
        drawOverlay();
        return;
      }
      const hits = Object.entries(state.boxes)
        .filter(([id]) => !widgetById(id)?.locked)
        .filter(([, b]) => b[0] < x1 && b[0] + b[2] > x0 && b[1] < y1 && b[1] + b[3] > y0)
        .map(([id]) => id);
      setSelection(m.additive ? [...state.selection, ...hits] : hits);
      drawOverlay();
    },
    { once: true },
  );
}

function nudge(dx, dy) {
  const movers = selectedWidgets().filter((w) => !w.locked);
  if (!movers.length) return;
  commit("nudge");
  for (const w of movers) {
    w.x += dx;
    w.y += dy;
    const box = state.boxes[w.id];
    if (box) state.boxes[w.id] = [box[0] + dx, box[1] + dy, box[2], box[3]];
  }
  drawOverlay();
  syncPositionFields();
  scheduleRender(80);
}

// ---------------------------------------------------------------- widget operations

function addWidget() {
  const type = $("#add-type").value;
  const spec = state.specs.widgets[type];
  const [w, h] = state.size;
  commit();
  const widget = { type, id: uniqueId(type) };
  for (const [key, [, fallback]] of Object.entries(spec)) widget[key] = clone(fallback);
  widget.x = Math.round(w * 0.1);
  widget.y = Math.round(h * 0.1);
  if ("w" in widget && widget.w > w * 0.8) widget.w = Math.round(w * 0.5);
  if ("h" in widget && widget.h > h * 0.8) widget.h = Math.round(h * 0.3);
  state.theme.widgets.push(widget);
  setSelection([widget.id]);
  scheduleRender(0);
}

function resolveTokens(value) {
  const palette = state.theme.palette || {};
  if (typeof value === "string" && value.startsWith("$")) {
    const name = value.slice(1);
    return name in palette ? `@${name}` : state.specs.tokens[name];
  }
  if (Array.isArray(value)) return value.map(resolveTokens);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, resolveTokens(v)]));
  return value;
}

function insertPreset(preset) {
  commit();
  const [W, H] = state.size;
  const [pw, ph] = preset.size;
  const scale = Math.min(1, (W * 0.9) / pw, (H * 0.9) / ph);
  const ox = Math.round((W - pw * scale) / 2);
  const oy = Math.round((H - ph * scale) / 2);
  const ids = [];
  for (const raw of resolveTokens(clone(preset.widgets))) {
    const widget = { ...raw, id: uniqueId(`${preset.id}-${raw.id}`) };
    widget.x = ox + Math.round(raw.x * scale);
    widget.y = oy + Math.round(raw.y * scale);
    if (scale < 1) {
      for (const key of ["w", "h", "size", "font_size", "thickness"]) {
        if (typeof widget[key] === "number") widget[key] = Math.max(1, Math.round(widget[key] * scale));
      }
    }
    state.theme.widgets.push(widget);
    ids.push(widget.id);
  }
  setSelection(ids);
  scheduleRender(0);
  setStatus(t('Added "{name}" — drag it into place; all its parts move together while selected', { name: preset.name }));
}

function moveWidget(index, delta) {
  const widgets = state.theme.widgets;
  const target = index + delta;
  if (target < 0 || target >= widgets.length) return;
  commit();
  [widgets[index], widgets[target]] = [widgets[target], widgets[index]];
  buildList();
  scheduleRender(0);
}

function duplicateSelection() {
  const originals = selectedWidgets();
  if (!originals.length) return;
  commit();
  const ids = [];
  for (const original of originals) {
    const copy = clone(original);
    copy.id = uniqueId(original.id);
    copy.x += 10;
    copy.y += 10;
    copy.locked = false;
    const index = state.theme.widgets.indexOf(original);
    state.theme.widgets.splice(index + 1, 0, copy);
    ids.push(copy.id);
  }
  setSelection(ids);
  scheduleRender(0);
}

function deleteSelection() {
  if (!state.selection.size) return;
  commit();
  state.theme.widgets = state.theme.widgets.filter((w) => !state.selection.has(w.id));
  setSelection([]);
  scheduleRender(0);
}

function copySelection() {
  const widgets = selectedWidgets();
  if (!widgets.length) return;
  state.clipboard = clone(widgets);
  setStatus(widgets.length === 1 ? t("Copied 1 widget") : t("Copied {count} widgets", { count: widgets.length }));
}

function paste() {
  if (!state.clipboard?.length) return;
  commit();
  const ids = [];
  for (const raw of clone(state.clipboard)) {
    raw.id = uniqueId(raw.id);
    raw.x += 12;
    raw.y += 12;
    state.theme.widgets.push(raw);
    ids.push(raw.id);
  }
  state.clipboard = state.clipboard.map((w) => ({ ...w, x: w.x + 12, y: w.y + 12 }));
  setSelection(ids);
  scheduleRender(0);
}

function toggleFlag(widget, flag) {
  commit();
  widget[flag] = !(widget[flag] ?? (flag === "visible"));
  buildList();
  buildProps();
  drawOverlay();
  scheduleRender(0);
}

function align(mode) {
  const widgets = selectedWidgets().filter((w) => !w.locked && state.boxes[w.id]);
  if (widgets.length < 2) return;
  commit();
  const boxes = widgets.map((w) => state.boxes[w.id]);
  const left = Math.min(...boxes.map((b) => b[0]));
  const right = Math.max(...boxes.map((b) => b[0] + b[2]));
  const top = Math.min(...boxes.map((b) => b[1]));
  const bottom = Math.max(...boxes.map((b) => b[1] + b[3]));
  const shift = (w, dx, dy) => {
    w.x += Math.round(dx);
    w.y += Math.round(dy);
    const b = state.boxes[w.id];
    state.boxes[w.id] = [b[0] + Math.round(dx), b[1] + Math.round(dy), b[2], b[3]];
  };
  if (mode.startsWith("distribute")) {
    const horizontal = mode === "distribute-h";
    const sorted = [...widgets].sort((a, b) => state.boxes[a.id][horizontal ? 0 : 1] - state.boxes[b.id][horizontal ? 0 : 1]);
    const centre = (w) => (horizontal ? state.boxes[w.id][0] + state.boxes[w.id][2] / 2 : state.boxes[w.id][1] + state.boxes[w.id][3] / 2);
    const first = centre(sorted[0]);
    const last = centre(sorted[sorted.length - 1]);
    sorted.forEach((w, i) => {
      const target = first + ((last - first) * i) / (sorted.length - 1);
      const delta = target - centre(w);
      shift(w, horizontal ? delta : 0, horizontal ? 0 : delta);
    });
  } else {
    for (const w of widgets) {
      const b = state.boxes[w.id];
      const moves = {
        left: [left - b[0], 0],
        center: [(left + right) / 2 - (b[0] + b[2] / 2), 0],
        right: [right - (b[0] + b[2]), 0],
        top: [0, top - b[1]],
        middle: [0, (top + bottom) / 2 - (b[1] + b[3] / 2)],
        bottom: [0, bottom - (b[1] + b[3])],
      }[mode];
      shift(w, ...moves);
    }
  }
  drawOverlay();
  scheduleRender(0);
}

// ---------------------------------------------------------------- layer list

function buildList() {
  const list = $("#widget-list");
  list.replaceChildren();
  state.theme.widgets.forEach((widget, index) => {
    const button = (label, title, handler, cls = "icon-btn") =>
      el(
        "button",
        {
          type: "button",
          class: cls,
          title,
          "aria-label": title,
          onclick: (event) => {
            event.stopPropagation();
            handler();
          },
        },
        label in ICON_PATHS ? svgIcon(label) : label,
      );
    const visible = widget.visible !== false;
    list.append(
      el(
        "li",
        {
          class: [state.selection.has(widget.id) ? "selected" : "", visible ? "" : "hidden-widget"].join(" "),
          onclick: (event) => toggleSelection(widget.id, event.shiftKey || event.ctrlKey || event.metaKey),
        },
        el("span", { class: "type", text: widgetLabel(widget.type), title: widget.type }),
        el("span", { class: "wid", text: widget.id }),
        el(
          "span",
          { class: "actions" },
          button("↑", t("Move backward"), () => moveWidget(index, -1)),
          button("↓", t("Move forward"), () => moveWidget(index, 1)),
        ),
        el(
          "span",
          { class: "flags" },
          button(visible ? "eye" : "eye-off", visible ? t("Hide") : t("Show"), () => toggleFlag(widget, "visible"), `icon-btn ${visible ? "" : "on"}`),
          button(widget.locked ? "lock" : "unlock", widget.locked ? t("Unlock") : t("Lock"), () => toggleFlag(widget, "locked"), `icon-btn ${widget.locked ? "on" : ""}`),
        ),
      ),
    );
  });
}

// ---------------------------------------------------------------- form controls

function field(label, control, hint) {
  return el("label", { class: "field" }, el("span", { text: label }), control, hint ? el("small", { text: hint }) : null);
}

function numberInput(value, onChange, { step = "1", allowEmpty = false, min, max } = {}) {
  return el("input", {
    type: "number",
    step,
    min,
    max,
    value: value ?? "",
    placeholder: allowEmpty ? t("auto") : undefined,
    oninput: (event) => {
      const raw = event.target.value;
      if (raw === "" && allowEmpty) onChange(null);
      else if (raw !== "" && !Number.isNaN(Number(raw))) onChange(step === "1" ? Math.round(Number(raw)) : Number(raw));
    },
  });
}

function resolveColor(value) {
  if (typeof value === "string" && value.startsWith("@")) return state.theme.palette?.[value.slice(1)] || "#ff00ff";
  return value;
}

function colorInput(value, onChange, optional) {
  const text = el("input", { type: "text", value: value ?? "", placeholder: optional ? t("none") : t("#rrggbb or @name") });
  const hex = (v) => (/^#[0-9a-f]{6}/i.test(v || "") ? v.slice(0, 7) : "#000000");
  const picker = el("input", { type: "color", value: hex(resolveColor(value)), title: t("Pick a colour") });
  const names = Object.keys(state.theme.palette || {});
  const palette = names.length
    ? el(
        "select",
        { title: t("Use a palette colour"), class: "palette-pick" },
        el("option", { value: "", text: t("palette…") }),
        ...names.map((n) => el("option", { value: `@${n}`, text: n, selected: value === `@${n}` })),
      )
    : null;
  const none = optional ? el("input", { type: "checkbox", checked: value === null, title: t("No colour") }) : null;
  const valid = (v) => /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(v) || (v.startsWith("@") && names.includes(v.slice(1)));
  const apply = (next) => {
    if (next !== null && !valid(next)) return;
    onChange(next);
  };
  picker.addEventListener("input", () => {
    text.value = picker.value;
    if (none) none.checked = false;
    if (palette) palette.value = "";
    apply(picker.value);
  });
  text.addEventListener("change", () => {
    const v = text.value.trim();
    if (v === "" && optional) {
      if (none) none.checked = true;
      return apply(null);
    }
    picker.value = hex(resolveColor(v));
    if (none) none.checked = false;
    apply(v);
  });
  palette?.addEventListener("change", () => {
    if (!palette.value) return;
    text.value = palette.value;
    picker.value = hex(resolveColor(palette.value));
    if (none) none.checked = false;
    apply(palette.value);
  });
  none?.addEventListener("change", () => apply(none.checked ? null : text.value || picker.value));
  return el("div", { class: "row" }, picker, text, palette, none ? el("label", { class: "check" }, none, t("none")) : null);
}

async function uploadAsset(accept) {
  if (!state.themeId || state.builtin) {
    setStatus(t("Save the theme under your own name (Save as…) before adding files."), "warn");
    return null;
  }
  const input = el("input", { type: "file", accept });
  const chosen = await new Promise((resolve) => {
    input.addEventListener("change", () => resolve(input.files[0] || null), { once: true });
    input.click();
  });
  if (!chosen) return null;
  const name = chosen.name.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "");
  try {
    const data = await api("POST", `/api/themes/${encodeURIComponent(state.themeId)}/assets?name=${encodeURIComponent(name)}`, await chosen.arrayBuffer(), true);
    await loadAssets();
    setStatus(t("Added {path}", { path: data.path }));
    return data.path;
  } catch (error) {
    setStatus(error.message, "error");
    return null;
  }
}

function fontInput(value, onChange, emptyLabel = t("Theme font")) {
  const fonts = state.assets.filter((a) => /\.(ttf|otf)$/i.test(a));
  const select = el(
    "select",
    { onchange: (e) => onChange(e.target.value) },
    el("option", { value: "", text: emptyLabel, selected: !value }),
    el("optgroup", { label: t("Built-in") }, ...state.specs.fonts.map((f) => el("option", { value: f, text: f.slice(8), selected: f === value }))),
    fonts.length ? el("optgroup", { label: t("This theme") }, ...fonts.map((f) => el("option", { value: f, text: f, selected: f === value }))) : null,
  );
  if (value && !state.specs.fonts.includes(value) && !fonts.includes(value)) {
    select.append(el("option", { value, text: value, selected: true }));
  }
  const upload = el("button", {
    type: "button",
    text: t("Upload…"),
    onclick: async () => {
      const path = await uploadAsset(".ttf,.otf");
      if (path) {
        onChange(path);
        buildProps();
      }
    },
  });
  return el("div", { class: "row" }, select, upload);
}

function assetInput(value, onChange) {
  const images = state.assets.filter((a) => /\.(png|jpe?g|gif|webp)$/i.test(a));
  const select = el(
    "select",
    { onchange: (e) => onChange(e.target.value) },
    el("option", { value: "", text: t("none"), selected: !value }),
    ...images.map((a) => el("option", { value: a, text: a, selected: a === value })),
  );
  if (value && !images.includes(value)) select.append(el("option", { value, text: value, selected: true }));
  const upload = el("button", {
    type: "button",
    text: t("Upload…"),
    onclick: async () => {
      const path = await uploadAsset(".png,.jpg,.jpeg,.gif,.webp");
      if (path) {
        onChange(path);
        buildProps();
      }
    },
  });
  return el("div", { class: "row" }, select, upload);
}

function rulesInput(rules, onChange) {
  const box = el("div", { class: "rules" });
  const redraw = () => {
    box.replaceChildren(
      ...rules.map((rule, i) =>
        el(
          "div",
          { class: "row" },
          t("above"),
          numberInput(rule.above, (v) => {
            rule.above = v;
            onChange(rules);
          }, { step: "any" }),
          colorInput(rule.color, (v) => {
            rule.color = v;
            onChange(rules);
          }, false),
          el("button", {
            type: "button",
            class: "small danger",
            text: "✕",
            title: t("Remove rule"),
            onclick: () => {
              rules.splice(i, 1);
              onChange(rules);
              redraw();
            },
          }),
        ),
      ),
      el("button", {
        type: "button",
        class: "small",
        text: t("+ colour rule"),
        onclick: () => {
          rules.push({ above: rules.length ? rules[rules.length - 1].above + 10 : 80, color: "#f87171" });
          onChange(rules);
          redraw();
        },
      }),
    );
  };
  redraw();
  return box;
}

function controlFor(key, kind, value, onChange) {
  const optional = kind.endsWith("?");
  const base = kind.replace(/\?$/, "");
  if (base === "int") return numberInput(value, onChange, { allowEmpty: optional });
  if (base === "number") {
    const unit = key === "opacity" || key === "glow";
    return numberInput(value, onChange, { step: String(STEPS[key] || "any"), allowEmpty: optional, min: unit ? 0 : undefined, max: unit ? 1 : undefined });
  }
  if (base === "bool") return el("label", { class: "check" }, el("input", { type: "checkbox", checked: !!value, onchange: (e) => onChange(e.target.checked) }), t("on"));
  if (base === "color") return colorInput(value, onChange, optional);
  if (base === "rules") return rulesInput(value || [], (rules) => onChange(rules));
  if (base === "font") return fontInput(value, onChange);
  if (base === "asset") return assetInput(value, onChange);
  if (base === "icon") {
    return el("select", { onchange: (e) => onChange(e.target.value) }, ...state.specs.icons.map((i) => el("option", { value: i, text: i === "weather" ? t("weather (live)") : iconLabel(i), selected: i === value })));
  }
  if (base.startsWith("enum:")) {
    return el("select", { onchange: (e) => onChange(e.target.value) }, ...base.slice(5).split("|").map((o) => el("option", { value: o, text: enumLabel(o), selected: o === value })));
  }
  if (base === "text") return el("textarea", { oninput: (e) => onChange(e.target.value) }, value || "");
  const input = el("input", { type: "text", value: value ?? "", oninput: (e) => onChange(e.target.value) });
  if (base === "sensor") input.setAttribute("list", "sensor-keys");
  return input;
}

function hintFor(widget, key, kind) {
  if (kind === "format") return FORMAT_HINT;
  if (key === "format" && widget.type === "clock") return t("strftime, e.g. {examples}", { examples: "%H:%M · %H:%M:%S · %A %d %B" });
  if (key === "scale") return t("sqrt/log keep small values visible (network rates)");
  if (key === "hide_if_missing") return t("hide when the sensor has no value (e.g. weather off)");
  return null;
}

// ---------------------------------------------------------------- property panel

function buildProps() {
  const form = $("#props");
  form.replaceChildren();
  if (!state.theme) return;
  if (state.selection.size > 1) return buildMultiProps(form);
  const widget = single();
  if (!widget) return buildThemeProps(form);
  $("#props-title").textContent = t("{type} widget", { type: widgetLabel(widget.type) });
  const spec = { ...state.specs.common, ...state.specs.widgets[widget.type] };
  const change = (key) => (value) => {
    if (key === "id") return renameWidget(widget, value);
    commit(`prop:${widget.id}:${key}`);
    widget[key] = value;
    if (key === "visible" || key === "locked") {
      buildList();
      drawOverlay();
    }
    scheduleRender();
  };
  const row = (key) => {
    const [kind] = spec[key];
    const control = controlFor(key, kind, widget[key], change(key));
    if (POSITION_FIELDS.includes(key)) control.dataset.pos = key;
    return field(fieldLabel(key), control, hintFor(widget, key, kind));
  };
  const position = POSITION_FIELDS.filter((k) => k in spec);
  form.append(el("div", { class: "quad" }, ...position.map(row)));
  const effects = state.specs.effect_fields;
  const main = Object.keys(spec).filter((k) => !POSITION_FIELDS.includes(k) && !ADVANCED_FIELDS.includes(k) && !effects.includes(k));
  form.append(...main.map(row));
  const effectsActive = (widget.glow || 0) > 0 || !!widget.shadow || (widget.opacity ?? 1) < 1;
  form.append(el("details", { open: effectsActive }, el("summary", { text: t("Effects") }), el("div", { class: "fields" }, ...effects.map(row))));
  form.append(el("details", {}, el("summary", { text: t("Advanced") }), el("div", { class: "fields" }, ...ADVANCED_FIELDS.filter((k) => k in spec).map(row))));
}

function buildMultiProps(form) {
  const count = state.selection.size;
  $("#props-title").textContent = t("{count} widgets", { count });
  const button = (label, title, action) => el("button", { type: "button", text: label, title, onclick: action });
  form.append(
    el("h2", { text: t("Align") }),
    el(
      "div",
      { class: "align-grid" },
      button("⇤ " + t("Left"), t("Align left edges"), () => align("left")),
      button("↔ " + t("Center"), t("Align horizontal centres"), () => align("center")),
      button(t("Right") + " ⇥", t("Align right edges"), () => align("right")),
      button("⇹ " + t("Spread"), t("Distribute horizontally"), () => align("distribute-h")),
      button("⤒ " + t("Top"), t("Align top edges"), () => align("top")),
      button("↕ " + t("Middle"), t("Align vertical centres"), () => align("middle")),
      button(t("Bottom") + " ⤓", t("Align bottom edges"), () => align("bottom")),
      button("⇳ " + t("Spread"), t("Distribute vertically"), () => align("distribute-v")),
    ),
    el("h2", { class: "section-title", text: t("Selection") }),
    el(
      "div",
      { class: "align-grid" },
      button(t("Duplicate"), "Ctrl+D", duplicateSelection),
      button(t("Copy"), "Ctrl+C", copySelection),
      button(t("Lock"), t("Lock all"), () => setLock(true)),
      button(t("Unlock"), t("Unlock all"), () => setLock(false)),
    ),
    el("button", { type: "button", class: "danger", text: t("Delete {count} widgets", { count }), onclick: deleteSelection }),
  );
}

function setLock(locked) {
  commit();
  for (const w of selectedWidgets()) w.locked = locked;
  buildList();
  drawOverlay();
}

function renameWidget(widget, value) {
  const next = value.trim();
  if (!next || state.theme.widgets.some((w) => w !== widget && w.id === next)) {
    setStatus(t('Widget id "{id}" is empty or already used', { id: next }), "error");
    return;
  }
  commit(`rename:${widget.id}`);
  if (state.boxes[widget.id]) {
    state.boxes[next] = state.boxes[widget.id];
    delete state.boxes[widget.id];
  }
  state.selection.delete(widget.id);
  state.selection.add(next);
  widget.id = next;
  buildList();
  drawOverlay();
}

function syncPositionFields() {
  const widget = single();
  if (!widget) return;
  for (const input of document.querySelectorAll("#props [data-pos]")) {
    input.value = widget[input.dataset.pos];
  }
}

function buildThemeProps(form) {
  $("#props-title").textContent = t("Theme");
  const theme = state.theme;
  theme.background = theme.background || { color: "#000000", image: null };
  theme.animation = theme.animation || { smoothing_ms: 400 };
  theme.palette = theme.palette || {};
  const set = (key, apply) => (value) => {
    commit(`theme:${key}`);
    apply(value);
    scheduleRender();
  };
  form.append(
    field(t("name"), controlFor("name", "string", theme.name, set("name", (v) => (theme.name = v)))),
    field(t("author"), controlFor("author", "string", theme.author, set("author", (v) => (theme.author = v)))),
    field(t("license"), controlFor("license", "string", theme.license, set("license", (v) => (theme.license = v))), t("e.g. CC0-1.0, CC-BY-4.0")),
    field(t("description"), controlFor("description", "text", theme.description, set("description", (v) => (theme.description = v)))),
    el("h2", { class: "section-title", text: t("Look") }),
    field(fieldLabel("font"), fontInput(theme.font === state.specs.default_font ? "" : theme.font, set("font", (v) => (theme.font = v || state.specs.default_font)), t("Default (Barlow Medium)")), t("used by every text widget without its own font")),
    field(fieldLabel("background"), colorInput(theme.background.color, set("bg", (v) => (theme.background.color = v)), false)),
    field(t("background image"), assetInput(theme.background.image, set("bgimg", (v) => (theme.background.image = v || null)))),
    ...buildScreenFields(),
    el("h2", { class: "section-title", text: t("Palette") }),
    buildPaletteEditor(),
    el("h2", { class: "section-title", text: t("Timing") }),
    field(t("sensor refresh (ms)"), numberInput(theme.refresh_ms, set("refresh", (v) => (theme.refresh_ms = Math.max(100, v || 1000))))),
    field(t("smoothing (ms)"), numberInput(theme.animation.smoothing_ms, set("smooth", (v) => (theme.animation.smoothing_ms = Math.max(0, Math.min(5000, v ?? 0))))), t("how long bars and rings take to glide to a new value; 0 = jump")),
  );
}

// A screen from a plugin draws the whole frame under the widgets.
function buildScreenFields() {
  const screens = state.specs.screens || {};
  const theme = state.theme;
  const current = theme.screen?.name || "";
  if (!Object.keys(screens).length && !current) return [];
  const choices = [el("option", { value: "", text: t("none"), selected: !current })];
  for (const [name, info] of Object.entries(screens)) choices.push(el("option", { value: name, text: info.label, selected: name === current }));
  if (current && !screens[current]) choices.push(el("option", { value: current, text: t("{name} (not installed)", { name: current }), selected: true }));
  const select = el("select", {
    onchange: (event) => {
      commit("theme:screen");
      const name = event.target.value;
      if (name) {
        const spec = screens[name]?.options || {};
        theme.screen = { name, options: Object.fromEntries(Object.entries(spec).map(([k, [, d]]) => [k, clone(d)])) };
      } else delete theme.screen;
      buildProps();
      scheduleRender();
    },
  }, ...choices);
  const fields = [field(t("screen"), select, t("drawn by a plugin, under the widgets"))];
  for (const [key, [kind, fallback]] of Object.entries(screens[current]?.options || {})) {
    const value = theme.screen.options?.[key] ?? fallback;
    const onChange = (v) => {
      commit(`screen:${key}`);
      theme.screen.options = { ...theme.screen.options, [key]: v };
      scheduleRender();
    };
    fields.push(field(fieldLabel(key), controlFor(key, kind, value, onChange)));
  }
  return fields;
}

function replaceReferences(from, to) {
  const walk = (node) => {
    if (Array.isArray(node)) return node.map(walk);
    if (node && typeof node === "object") return Object.fromEntries(Object.entries(node).map(([k, v]) => [k, walk(v)]));
    return node === from ? to : node;
  };
  state.theme.widgets = walk(state.theme.widgets);
  state.theme.background = walk(state.theme.background);
}

function buildPaletteEditor() {
  const palette = state.theme.palette;
  const box = el("div", { class: "fields" });
  for (const [name, color] of Object.entries(palette)) {
    const picker = el("input", { type: "color", value: /^#[0-9a-f]{6}/i.test(color) ? color.slice(0, 7) : "#000000" });
    const hex = el("input", { type: "text", value: color });
    const label = el("input", { type: "text", value: name, title: t("Name (a-z, 0-9, - _)") });
    const setColor = (v) => {
      if (!/^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(v)) return;
      commit(`palette:${name}`);
      palette[name] = v;
      scheduleRender();
    };
    picker.addEventListener("input", () => {
      hex.value = picker.value;
      setColor(picker.value);
    });
    hex.addEventListener("change", () => setColor(hex.value.trim()));
    label.addEventListener("change", () => {
      const next = label.value.trim();
      if (next === name) return;
      if (!PALETTE_NAME_RE.test(next) || next in palette) {
        setStatus(t('"{name}" is not a valid or free palette name', { name: next }), "error");
        label.value = name;
        return;
      }
      commit();
      const entries = Object.entries(palette).map(([k, v]) => [k === name ? next : k, v]);
      state.theme.palette = Object.fromEntries(entries);
      replaceReferences(`@${name}`, `@${next}`);
      buildProps();
      scheduleRender();
    });
    const remove = el("button", {
      type: "button",
      class: "small danger",
      text: "✕",
      title: t("Remove (uses become the plain colour)"),
      onclick: () => {
        commit();
        replaceReferences(`@${name}`, palette[name]);
        delete palette[name];
        buildProps();
        scheduleRender();
      },
    });
    box.append(el("div", { class: "palette-row" }, picker, hex, label, remove));
  }
  box.append(
    el("button", {
      type: "button",
      class: "small",
      text: t("+ colour"),
      onclick: () => {
        let i = 1;
        while (`color${i}` in palette) i++;
        commit();
        palette[`color${i}`] = "#22d3ee";
        buildProps();
      },
    }),
    el("small", { class: "muted", text: t("Widgets use palette colours as @name; change a colour here and the whole theme follows.") }),
  );
  return box;
}

// ---------------------------------------------------------------- panel model menu

function buildModelMenu() {
  const select = $("#model-select");
  select.replaceChildren();
  const groups = new Map();
  for (const model of state.models) {
    if (!groups.has(model.vendor)) groups.set(model.vendor, el("optgroup", { label: model.vendor }));
    const size = `${model.landscape[0]}×${model.landscape[1]}`;
    const status = { planned: " · " + t("driver planned"), unverified: " · " + t("not yet confirmed") }[model.driver] || "";
    groups.get(model.vendor).append(el("option", { value: model.id, text: `${model.label} — ${size}${status}` }));
  }
  select.append(...groups.values(), el("optgroup", { label: t("Other") }, el("option", { value: "custom", text: t("Custom size") })));
}

function syncPanelBar() {
  const display = state.theme.display || {};
  const model = display.model || "custom";
  $("#model-select").value = state.models.some((m) => m.id === model) ? model : "custom";
  $("#orientation-select").value = display.orientation || (display.width >= display.height ? "landscape" : "portrait");
  $("#custom-size").hidden = model !== "custom";
  $("#custom-w").value = display.width || state.size[0];
  $("#custom-h").value = display.height || state.size[1];
  const info = currentModel();
  const driver = { supported: t("driver supported"), unverified: t("driver unverified"), planned: t("driver planned") };
  $("#panel-info").textContent = info
    ? `${info.protocol_name} · ${driver[info.driver] || info.driver}${info.notes ? " · " + info.notes : ""}`
    : t("any size, e.g. for panels not in the list yet");
}

async function onPanelChange() {
  const model = $("#model-select").value;
  $("#custom-size").hidden = model !== "custom";
  const source = state.adaptBase || clone(state.theme);
  try {
    const data = await api("POST", "/api/adapt", {
      theme: source,
      model,
      orientation: $("#orientation-select").value,
      mode: $("#adapt-mode").value,
      width: Number($("#custom-w").value) || state.size[0],
      height: Number($("#custom-h").value) || state.size[1],
    });
    commit("adapt");
    state.theme = data.theme;
    state.adaptBase = source;
    state.selection.clear();
    refreshAll();
  } catch (error) {
    setStatus(error.message, "error");
    syncPanelBar();
  }
}

// ---------------------------------------------------------------- themes

async function refreshThemeList(selectId) {
  state.themes = await api("GET", "/api/themes");
  const select = $("#theme-select");
  select.replaceChildren(...state.themes.map((theme) => el("option", { value: theme.id, text: theme.builtin ? t("{id} (built-in)", { id: theme.id }) : theme.id })));
  if (!state.themeId) select.append(el("option", { value: "", text: t("(unsaved)") }));
  select.value = selectId ?? state.themeId ?? "";
}

async function loadAssets() {
  state.assets = state.themeId ? await api("GET", `/api/themes/${encodeURIComponent(state.themeId)}/assets`).catch(() => []) : [];
}

async function loadTheme(id) {
  const data = await api("GET", `/api/themes/${encodeURIComponent(id)}`);
  state.themeId = id;
  const select = $("#theme-select");
  for (const option of [...select.options]) if (!option.value) option.remove();
  select.value = id;
  state.builtin = data.builtin;
  state.theme = data.theme;
  state.selection = new Set();
  state.boxes = {};
  await loadAssets();
  resetHistory();
  markClean();
  refreshAll();
  await render();
  if (data.warnings.length) setStatus(t("Warning: {text}", { text: data.warnings.join(" · ") }), "warn");
}

async function save() {
  if (!state.themeId || state.builtin) return saveAs();
  try {
    await api("POST", `/api/themes/${encodeURIComponent(state.themeId)}`, { theme: state.theme, source: state.themeId });
    markClean();
    setStatus(t("Saved {id}", { id: state.themeId }));
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function saveAs() {
  const suggestion = slug(state.theme.name) + (state.builtin ? "-custom" : "");
  const id = prompt(t("Save theme as (letters, digits, - _ .):"), suggestion || "my-theme");
  if (!id) return;
  if (!THEME_NAME_RE.test(id) || id.includes("..")) {
    setStatus(t('"{id}" is not a valid theme name', { id }), "error");
    return;
  }
  if (state.themes.some((theme) => theme.id === id && !theme.builtin) && !confirm(t('Overwrite your theme "{id}"?', { id }))) return;
  try {
    await api("POST", `/api/themes/${encodeURIComponent(id)}`, { theme: state.theme, source: state.themeId });
    state.themeId = id;
    state.builtin = false;
    markClean();
    await loadAssets();
    await refreshThemeList(id);
    setStatus(t("Saved {id}", { id }));
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function activate() {
  // An unchanged built-in theme can be shown as it is; anything else is saved first.
  if (state.dirty || !state.themeId) {
    await save();
    if (state.dirty || !state.themeId) return;
  }
  try {
    await api("POST", "/api/activate", { id: state.themeId });
    setStatus(t('"{id}" is now shown on the panel', { id: state.themeId }));
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function newTheme() {
  if (!confirmDiscard()) return;
  const model = state.models.find((m) => m.id === $("#model-select").value) || state.models[0];
  const orientation = $("#orientation-select").value;
  const [width, height] = model[orientation];
  state.theme = {
    format: "libre-panel-theme/1",
    name: t("My theme"),
    author: "",
    license: "CC-BY-4.0",
    description: "",
    display: { model: model.id, orientation, width, height },
    palette: { bg: "#0b1016", text: "#f1f5f9", muted: "#8793a6", accent: "#22d3ee", accent_deep: "#0b6c85", card: "#101722", card2: "#0b1018", edge: "#1b2534" },
    font: state.specs.default_font,
    background: { color: "@bg", image: null },
    refresh_ms: 1000,
    animation: { smoothing_ms: 400 },
    widgets: [],
  };
  state.themeId = null;
  state.builtin = false;
  state.selection = new Set();
  state.assets = [];
  state.size = [width, height];
  resetHistory();
  markDirty();
  refreshThemeList("");
  const clock = state.specs.presets.find((p) => p.id === "clock");
  if (clock) insertPreset(clock);
  resetHistory();
  refreshAll();
}

function exportTheme() {
  const blob = new Blob([JSON.stringify(state.theme, null, 2) + "\n"], { type: "application/json" });
  const link = el("a", { href: URL.createObjectURL(blob), download: `${slug(state.theme.name) || "theme"}.json` });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

async function importTheme(file) {
  if (!file || !confirmDiscard()) return;
  try {
    const theme = JSON.parse(await file.text());
    await api("POST", "/api/render", { theme }); // validates before we replace anything
    state.theme = theme;
    state.themeId = null;
    state.builtin = false;
    state.selection = new Set();
    state.assets = [];
    resetHistory();
    markDirty();
    await refreshThemeList("");
    refreshAll();
    setStatus(t("Imported {file}. Images/fonts of the original theme are not included; save and upload them.", { file: file.name }));
  } catch (error) {
    setStatus(t("Import failed: {error}", { error: error.message }), "error");
  }
}

// ---------------------------------------------------------------- setup

// -- background app: status, pause, brightness, autostart, quit ---------------

const appStateText = (key) =>
  ({
    starting: t("Starting"),
    showing: t("Showing"),
    waiting: t("Waiting for the panel"),
    paused: t("Paused"),
    error: t("Needs attention"),
    stopped: t("Stopped"),
  })[key] || key;

function renderApp(app) {
  state.app = app;
  const chip = $("#app-chip");
  if (!app.available) {
    chip.hidden = true;
    clearInterval(state.appTimer);
    return;
  }
  const panel = app.panel;
  const label = appStateText(panel.state);
  chip.hidden = false;
  chip.dataset.state = panel.state;
  $("#app-popover").dataset.state = panel.state;
  $("#app-chip-text").textContent = `${t("Panel")} · ${label}`;
  chip.title = `${panel.target || t("Panel")}: ${label}${panel.detail ? ` – ${panel.detail}` : ""}`;
  $("#app-state").textContent = label;
  $("#app-detail").textContent = panel.detail || "";
  $("#app-detail").hidden = !panel.detail;
  $("#app-target").textContent = panel.target || "–";
  $("#app-theme").textContent = panel.theme || "–";
  $("#app-mode").textContent = panel.mode || "";
  $("#app-mode").hidden = $("#app-mode-label").hidden = !panel.mode;
  const slider = $("#app-brightness");
  if (document.activeElement !== slider && app.brightness !== null) slider.value = app.brightness;
  slider.disabled = app.brightness === null;
  $("#app-brightness-value").textContent = `${slider.value} %`;
  const autostart = $("#app-autostart");
  autostart.checked = Boolean(app.autostart);
  autostart.disabled = app.autostart === null;
  $("#app-autostart-where").textContent = app.autostart_location || "";
  $("#app-pause").textContent = panel.state === "paused" ? t("Resume panel") : t("Pause panel");
}

async function pollApp() {
  if (document.hidden || state.appQuit) return;
  try {
    renderApp(await api("GET", "/api/app"));
  } catch {
    $("#app-chip").dataset.state = "gone";
    $("#app-chip-text").textContent = `${t("Panel")} · ${t("not running")}`;
  }
}

async function appAction(action, value) {
  try {
    renderApp(await api("POST", "/api/app", { action, value }));
    return true;
  } catch (error) {
    setStatus(error.message, "error");
    return false;
  }
}

function toggleAppPopover(open = $("#app-popover").hidden) {
  const popover = $("#app-popover");
  // Open towards the side with room (the top bar wraps on narrow windows).
  popover.classList.toggle("from-left", $("#app-chip").getBoundingClientRect().right < 320);
  popover.hidden = !open;
  $("#app-chip").setAttribute("aria-expanded", String(open));
  if (open) pollApp();
}

// ---------------------------------------------------------------- plugin pages

async function loadPages() {
  const pages = await api("GET", "/api/plugins/pages");
  $("#pages-wrap").hidden = !pages.length;
  $("#pages-menu").replaceChildren(
    ...pages.map((page) => el("button", { type: "button", role: "menuitem", text: page.title, onclick: () => openPage(page) })),
  );
}

function togglePagesMenu(open = $("#pages-menu").hidden) {
  $("#pages-menu").hidden = !open;
  $("#pages-button").setAttribute("aria-expanded", String(open));
}

function openPage(page) {
  togglePagesMenu(false);
  $("#plugin-title").textContent = page.title;
  $("#plugin-frame").src = `/plugins/${encodeURIComponent(page.id)}/`;
  $("main.layout").hidden = $(".panelbar").hidden = true;
  $("#plugin-view").hidden = false;
  document.body.classList.add("showing-page");
}

function closePage() {
  $("#plugin-view").hidden = true;
  $("#plugin-frame").src = "about:blank";
  $("main.layout").hidden = $(".panelbar").hidden = false;
  document.body.classList.remove("showing-page");
  layoutStage();
  drawOverlay();
}

async function quitApp() {
  if (!confirm(t("Quit Libre Panel? The panel stops updating until you start it again."))) return;
  if (!(await appAction("quit"))) return;
  state.appQuit = true;
  clearInterval(state.appTimer);
  toggleAppPopover(false);
  $("#app-chip").dataset.state = "gone";
  $("#app-chip-text").textContent = `${t("Panel")} · ${t("quit")}`;
  setStatus(t("Libre Panel has quit. You can close this tab."), "warn");
}

function setupApp() {
  $("#app-chip").addEventListener("click", () => toggleAppPopover());
  $("#app-pause").addEventListener("click", () => appAction(state.app?.panel.state === "paused" ? "resume" : "pause"));
  $("#app-quit").addEventListener("click", quitApp);
  $("#app-autostart").addEventListener("change", (event) => appAction("autostart", event.target.checked));
  const slider = $("#app-brightness");
  slider.addEventListener("input", () => ($("#app-brightness-value").textContent = `${slider.value} %`));
  slider.addEventListener("change", () => appAction("brightness", Number(slider.value)));
  document.addEventListener("pointerdown", (event) => {
    if (!$("#app-popover").hidden && !event.target.closest(".app-wrap")) toggleAppPopover(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#app-popover").hidden) toggleAppPopover(false);
  });
  pollApp();
  state.appTimer = setInterval(pollApp, 2000);
}

function setLive(on) {
  state.live = on;
  clearInterval(state.liveTimer);
  if (on) state.liveTimer = setInterval(() => scheduleRender(0), Math.max(500, state.theme?.refresh_ms || 1000));
  scheduleRender(0);
}

function onKey(event) {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName);
  const mod = event.ctrlKey || event.metaKey;
  const key = event.key.toLowerCase();
  if (mod && key === "s") {
    event.preventDefault();
    save();
    return;
  }
  if (typing) return;
  if (mod && key === "z" && !event.shiftKey) return event.preventDefault(), undo();
  if (mod && (key === "y" || (key === "z" && event.shiftKey))) return event.preventDefault(), redo();
  if (mod && key === "a") return event.preventDefault(), setSelection(state.theme.widgets.filter((w) => !w.locked).map((w) => w.id));
  if (mod && key === "c") return copySelection();
  if (mod && key === "v") return event.preventDefault(), paste();
  if (mod && key === "d") return event.preventDefault(), duplicateSelection();
  const step = event.shiftKey ? 10 : 1;
  const moves = { arrowleft: [-step, 0], arrowright: [step, 0], arrowup: [0, -step], arrowdown: [0, step] };
  if (moves[key] && state.selection.size) {
    event.preventDefault();
    nudge(...moves[key]);
  } else if ((key === "delete" || key === "backspace") && state.selection.size) {
    event.preventDefault();
    deleteSelection();
  } else if (key === "escape") {
    setSelection([]);
  }
}

async function init() {
  try {
    state.i18n = await api("GET", "/api/i18n");
    applyI18n();
    markClean();
    $("#lang-select").value = state.i18n.setting;
    state.specs = await api("GET", "/api/specs");
    state.models = state.specs.models;
    $("#add-type").replaceChildren(...Object.keys(state.specs.widgets).map((type) => el("option", { value: type, text: widgetLabel(type) })));
    $("#presets").replaceChildren(
      ...state.specs.presets.map((p) => el("button", { type: "button", text: p.name, title: t('Insert "{name}"', { name: p.name }), onclick: () => insertPreset(p) })),
    );
    buildModelMenu();
    api("GET", "/api/sensors")
      .then((sensors) => {
        const list = el("datalist", { id: "sensor-keys" });
        for (const s of sensors) list.append(el("option", { value: s.key, label: `${s.label}${s.unit ? ` (${s.unit})` : ""}` }));
        document.body.append(list);
      })
      .catch(() => {});
    await refreshThemeList();
    const restored = takeRestore(); // after a language switch
    if (restored?.theme) {
      await restoreSession(restored);
    } else {
      // Start with the theme the panel shows.
      const active = await api("GET", "/api/active").then((a) => a.theme, () => null);
      const first =
        state.themes.find((theme) => theme.id === active) || state.themes.find((theme) => theme.id === "libre-default") || state.themes[0];
      if (first) await loadTheme(first.id);
      else newTheme();
    }
  } catch (error) {
    setStatus(t("Could not start the editor: {error}", { error: error.message }), "error");
  }

  $("#theme-select").addEventListener("change", async (event) => {
    if (!event.target.value) return;
    if (!confirmDiscard()) {
      event.target.value = state.themeId ?? "";
      return;
    }
    try {
      await loadTheme(event.target.value);
    } catch (error) {
      setStatus(error.message, "error");
    }
  });
  $("#lang-select").addEventListener("change", (event) => setLanguage(event.target.value));
  $("#btn-new").addEventListener("click", newTheme);
  $("#btn-save").addEventListener("click", save);
  $("#btn-save-as").addEventListener("click", saveAs);
  $("#btn-activate").addEventListener("click", activate);
  $("#btn-export").addEventListener("click", exportTheme);
  $("#btn-import").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", (event) => {
    importTheme(event.target.files[0]);
    event.target.value = "";
  });
  $("#pages-button").addEventListener("click", () => togglePagesMenu());
  $("#plugin-back").addEventListener("click", closePage);
  document.addEventListener("click", (event) => {
    if (!$("#pages-menu").hidden && !event.target.closest(".pages-wrap")) togglePagesMenu(false);
  });
  loadPages().catch((error) => setStatus(error.message, "error"));
  $("#btn-undo").addEventListener("click", undo);
  $("#btn-redo").addEventListener("click", redo);
  $("#btn-add").addEventListener("click", addWidget);
  $("#live-toggle").addEventListener("change", (event) => setLive(event.target.checked));
  $("#model-select").addEventListener("change", onPanelChange);
  $("#orientation-select").addEventListener("change", onPanelChange);
  $("#custom-w").addEventListener("change", onPanelChange);
  $("#custom-h").addEventListener("change", onPanelChange);
  $("#zoom-fit").addEventListener("click", () => setZoom("fit"));
  $("#zoom-in").addEventListener("click", () => zoomStep(1));
  $("#zoom-out").addEventListener("click", () => zoomStep(-1));
  $("#snap-select").addEventListener("change", (event) => (state.snap = Number(event.target.value)));
  $("#guides-toggle").addEventListener("change", (event) => (state.guides = event.target.checked));
  $("#stage").addEventListener("pointerdown", startMarquee);
  $("#stage").addEventListener(
    "wheel",
    (event) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      zoomStep(event.deltaY < 0 ? 1 : -1);
    },
    { passive: false },
  );
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", () => {
    layoutStage();
    drawOverlay();
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) event.preventDefault();
  });
  updateHistoryButtons();
  setupApp();
}

init();
