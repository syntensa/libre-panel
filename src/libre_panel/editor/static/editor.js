"use strict";

// Libre Panel theme editor. The preview image comes from the same Python
// renderer that drives the panel, so what you see is what the panel shows.

const COMMON_FIELDS = { id: ["string", ""], x: ["int", 0], y: ["int", 0], visible: ["bool", true] };
const THEME_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const FORMAT_HINT = "{value:.0f}{unit} · {value:.1f} · {value:bytes}/s · {value:duration} · {label}";

const state = {
  specs: null,
  models: [],
  themes: [],
  sensors: [],
  themeId: null, // folder name of the loaded theme (also the asset base for rendering)
  builtin: false,
  theme: null,
  selected: null,
  boxes: {},
  size: [480, 320],
  zoom: 1,
  live: false,
  liveTimer: null,
  dirty: false,
  renderSeq: 0,
  drag: null,
  // Theme as it was before the current run of panel/orientation changes, so
  // flipping between models always scales from the original instead of
  // compounding rounding and shrinking.
  adaptBase: null,
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
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
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

// ---------------------------------------------------------------- helpers

const widgetById = (id) => state.theme.widgets.find((w) => w.id === id);
const currentModel = () => state.models.find((m) => m.id === state.theme?.display?.model);
const clone = (value) => JSON.parse(JSON.stringify(value));

function uniqueId(base) {
  const taken = new Set(state.theme.widgets.map((w) => w.id));
  for (let i = 1; ; i++) {
    const id = `${base}-${i}`;
    if (!taken.has(id)) return id;
  }
}

function slug(text) {
  return (text || "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
}

function markDirty() {
  state.adaptBase = null;
  state.dirty = true;
  document.title = "● Libre Panel Theme Editor";
}

function markClean() {
  state.adaptBase = null;
  state.dirty = false;
  document.title = "Libre Panel Theme Editor";
}

function confirmDiscard() {
  return !state.dirty || confirm("Discard unsaved changes?");
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
    if (data.warnings.length) setStatus("Warning: " + data.warnings.join(" · "), "warn");
    else setStatus(`${state.theme.name} · ${data.width}×${data.height}${state.dirty ? " · unsaved" : ""}`);
  } catch (error) {
    if (seq === state.renderSeq) setStatus(error.message, "error");
  }
}

function layoutStage() {
  const [w, h] = state.size;
  const stage = $("#stage");
  const zoom = Math.max(0.05, Math.min((stage.clientWidth - 48) / w, (stage.clientHeight - 48) / h, 4));
  state.zoom = zoom;
  const wrap = $("#canvas-wrap");
  wrap.style.width = `${Math.round(w * zoom)}px`;
  wrap.style.height = `${Math.round(h * zoom)}px`;
  wrap.classList.toggle("round", currentModel()?.shape === "round");
  $("#preview").classList.toggle("pixelated", zoom >= 2);
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
  const dragging = state.drag?.id;
  const keep = dragging ? overlay.querySelector(`[data-id="${CSS.escape(dragging)}"]`) : null;
  overlay.replaceChildren();
  for (const widget of state.theme.widgets) {
    if (widget.id === dragging && keep) {
      overlay.append(keep);
      continue;
    }
    const box = state.boxes[widget.id];
    if (!box) continue;
    const node = el("div", {
      class: "box" + (widget.id === state.selected ? " selected" : ""),
      title: `${widget.id} (${widget.type})`,
    });
    node.dataset.id = widget.id;
    placeBox(node, box);
    node.addEventListener("pointerdown", startDrag);
    overlay.append(node);
  }
}

// ---------------------------------------------------------------- drag & drop

function startDrag(event) {
  if (event.button !== 0) return;
  const node = event.currentTarget;
  const widget = widgetById(node.dataset.id);
  if (!widget) return;
  select(widget.id);
  state.drag = {
    id: widget.id,
    node,
    startX: event.clientX,
    startY: event.clientY,
    x0: widget.x,
    y0: widget.y,
    box: [...state.boxes[widget.id]],
    moved: false,
  };
  node.setPointerCapture(event.pointerId);
  node.addEventListener("pointermove", onDrag);
  node.addEventListener("pointerup", endDrag, { once: true });
  node.addEventListener("pointercancel", endDrag, { once: true });
  event.preventDefault();
}

function onDrag(event) {
  const drag = state.drag;
  if (!drag) return;
  const dx = Math.round((event.clientX - drag.startX) / state.zoom);
  const dy = Math.round((event.clientY - drag.startY) / state.zoom);
  if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 1) return;
  drag.moved = true;
  const widget = widgetById(drag.id);
  widget.x = drag.x0 + dx;
  widget.y = drag.y0 + dy;
  placeBox(drag.node, [drag.box[0] + dx, drag.box[1] + dy, drag.box[2], drag.box[3]]);
  syncPositionFields(widget);
  markDirty();
  scheduleRender(40);
}

function endDrag(event) {
  event.currentTarget.removeEventListener("pointermove", onDrag);
  const moved = state.drag?.moved;
  state.drag = null;
  if (moved) scheduleRender(0);
}

function nudge(dx, dy) {
  const widget = widgetById(state.selected);
  if (!widget) return;
  widget.x += dx;
  widget.y += dy;
  const box = state.boxes[widget.id];
  if (box) {
    state.boxes[widget.id] = [box[0] + dx, box[1] + dy, box[2], box[3]];
    drawOverlay();
  }
  syncPositionFields(widget);
  markDirty();
  scheduleRender(80);
}

// ---------------------------------------------------------------- selection & list

function select(id) {
  state.selected = id;
  for (const node of document.querySelectorAll("#overlay .box")) {
    node.classList.toggle("selected", node.dataset.id === id);
  }
  buildList();
  buildProps();
}

function buildList() {
  const list = $("#widget-list");
  list.replaceChildren();
  state.theme.widgets.forEach((widget, index) => {
    const act = (label, title, handler, cls = "") =>
      el("button", {
        type: "button",
        class: "small " + cls,
        title,
        text: label,
        onclick: (event) => {
          event.stopPropagation();
          handler(index);
        },
      });
    const item = el(
      "li",
      {
        class: [widget.id === state.selected ? "selected" : "", widget.visible === false ? "hidden-widget" : ""].join(" "),
        onclick: () => select(widget.id === state.selected ? null : widget.id),
      },
      el("span", { class: "type", text: widget.type }),
      el("span", { class: "wid", text: widget.id }),
      el(
        "span",
        { class: "actions" },
        act("↑", "Move backward", (i) => moveWidget(i, -1)),
        act("↓", "Move forward", (i) => moveWidget(i, 1)),
        act("⧉", "Duplicate", (i) => duplicateWidget(i)),
        act("✕", "Delete", (i) => deleteWidget(i), "danger"),
      ),
    );
    list.append(item);
  });
}

function moveWidget(index, delta) {
  const widgets = state.theme.widgets;
  const target = index + delta;
  if (target < 0 || target >= widgets.length) return;
  [widgets[index], widgets[target]] = [widgets[target], widgets[index]];
  markDirty();
  buildList();
  scheduleRender(0);
}

function duplicateWidget(index) {
  const copy = clone(state.theme.widgets[index]);
  copy.id = uniqueId(copy.id.replace(/-\d+$/, ""));
  copy.x += 10;
  copy.y += 10;
  state.theme.widgets.splice(index + 1, 0, copy);
  markDirty();
  select(copy.id);
  scheduleRender(0);
}

function deleteWidget(index) {
  const [removed] = state.theme.widgets.splice(index, 1);
  if (removed && removed.id === state.selected) state.selected = null;
  markDirty();
  buildList();
  buildProps();
  scheduleRender(0);
}

function addWidget() {
  const type = $("#add-type").value;
  const spec = state.specs.widgets[type];
  const [w, h] = state.size;
  const widget = { type, id: uniqueId(type) };
  for (const [key, [, fallback]] of Object.entries(spec)) widget[key] = clone(fallback);
  widget.x = Math.round(w * 0.1);
  widget.y = Math.round(h * 0.1);
  widget.visible = true;
  if ("w" in widget && widget.w > w * 0.8) widget.w = Math.round(w * 0.5);
  if ("h" in widget && widget.h > h * 0.8) widget.h = Math.round(h * 0.3);
  state.theme.widgets.push(widget);
  markDirty();
  select(widget.id);
  scheduleRender(0);
}

// ---------------------------------------------------------------- property forms

function field(label, control, hint) {
  return el("label", { class: "field" }, el("span", { text: label }), control, hint ? el("small", { text: hint }) : null);
}

function numberInput(value, onChange, { step = "1", allowEmpty = false } = {}) {
  return el("input", {
    type: "number",
    step,
    value: value ?? "",
    placeholder: allowEmpty ? "auto" : undefined,
    oninput: (event) => {
      const raw = event.target.value;
      if (raw === "" && allowEmpty) onChange(null);
      else if (raw !== "" && !Number.isNaN(Number(raw))) onChange(step === "1" ? Math.round(Number(raw)) : Number(raw));
    },
  });
}

function colorInput(value, onChange, optional) {
  const text = el("input", { type: "text", value: value ?? "", placeholder: optional ? "none" : "#rrggbb" });
  const picker = el("input", { type: "color", value: /^#[0-9a-f]{6}$/i.test(value || "") ? value : "#000000" });
  const none = optional ? el("input", { type: "checkbox", checked: value === null, title: "No color" }) : null;
  const apply = (next) => {
    if (next !== null && !/^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(next)) return;
    onChange(next);
  };
  picker.addEventListener("input", () => {
    text.value = picker.value;
    if (none) none.checked = false;
    apply(picker.value);
  });
  text.addEventListener("change", () => {
    const v = text.value.trim();
    if (v === "" && optional) {
      if (none) none.checked = true;
      apply(null);
      return;
    }
    if (/^#[0-9a-f]{6}$/i.test(v)) picker.value = v;
    if (none) none.checked = false;
    apply(v);
  });
  none?.addEventListener("change", () => apply(none.checked ? null : picker.value));
  return el("div", { class: "row" }, picker, text, none ? el("label", { class: "row" }, none, "none") : null);
}

function assetInput(value, onChange) {
  const text = el("input", { type: "text", value: value || "", placeholder: "assets/file.png" });
  text.addEventListener("change", () => onChange(text.value.trim()));
  const file = el("input", { type: "file", hidden: true, accept: ".png,.jpg,.jpeg,.gif,.webp,.ttf,.otf" });
  file.addEventListener("change", async () => {
    const chosen = file.files[0];
    if (!chosen) return;
    if (!state.themeId || state.builtin) {
      setStatus("Save the theme under your own name (Save as…) before adding files.", "warn");
      return;
    }
    try {
      const name = chosen.name.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "");
      const data = await api(
        "POST",
        `/api/themes/${encodeURIComponent(state.themeId)}/assets?name=${encodeURIComponent(name)}`,
        await chosen.arrayBuffer(),
        true,
      );
      text.value = data.path;
      onChange(data.path);
      setStatus(`Added ${data.path}`);
    } catch (error) {
      setStatus(error.message, "error");
    }
  });
  const upload = el("button", { type: "button", text: "Upload…", onclick: () => file.click() });
  return el("div", { class: "row" }, text, upload, file);
}

function rulesInput(rules, onChange) {
  const box = el("div", { class: "rules" });
  const redraw = () => {
    box.replaceChildren(
      ...rules.map((rule, i) =>
        el(
          "div",
          { class: "row" },
          "above",
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
            title: "Remove rule",
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
        text: "+ color rule",
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

function controlFor(kind, value, onChange) {
  const optional = kind.endsWith("?");
  const base = kind.replace(/\?$/, "");
  if (base === "int") return numberInput(value, onChange, { allowEmpty: optional });
  if (base === "number") return numberInput(value, onChange, { step: "any", allowEmpty: optional });
  if (base === "bool") return el("input", { type: "checkbox", checked: !!value, onchange: (e) => onChange(e.target.checked) });
  if (base === "color") return colorInput(value, onChange, optional);
  if (base === "rules") return rulesInput(value || [], (rules) => onChange(rules));
  if (base === "asset" || base === "font") return assetInput(value, onChange);
  if (base.startsWith("enum:")) {
    const select = el("select", { onchange: (e) => onChange(e.target.value) });
    for (const option of base.slice(5).split("|")) {
      select.append(el("option", { value: option, text: option, selected: option === value }));
    }
    return select;
  }
  if (base === "text") {
    return el("textarea", { oninput: (e) => onChange(e.target.value) }, value || "");
  }
  const input = el("input", { type: "text", value: value ?? "", oninput: (e) => onChange(e.target.value) });
  if (base === "sensor") input.setAttribute("list", "sensor-keys");
  return input;
}

function buildProps() {
  const form = $("#props");
  form.replaceChildren();
  const widget = widgetById(state.selected);
  if (!widget) return buildThemeProps(form);
  $("#props-title").textContent = `${widget.type} widget`;
  const spec = { ...COMMON_FIELDS, ...state.specs.widgets[widget.type] };
  const change = (key) => (value) => {
    if (key === "id") return renameWidget(widget, value);
    widget[key] = value;
    markDirty();
    if (key === "visible") buildList();
    scheduleRender();
  };
  const xy = el(
    "div",
    { class: "pair" },
    field("x", controlFor("int", widget.x, change("x"))),
    field("y", controlFor("int", widget.y, change("y"))),
  );
  xy.querySelectorAll("input")[0].id = "prop-x";
  xy.querySelectorAll("input")[1].id = "prop-y";
  form.append(field("id", controlFor("string", widget.id, change("id"))), xy);
  if ("w" in spec) {
    form.append(
      el(
        "div",
        { class: "pair" },
        field("width", controlFor("int", widget.w, change("w"))),
        field("height", controlFor("int", widget.h, change("h"))),
      ),
    );
  }
  for (const [key, [kind]] of Object.entries(spec)) {
    if (["id", "x", "y", "w", "h"].includes(key)) continue;
    const hint = kind === "format" ? FORMAT_HINT : key === "format" && widget.type === "clock" ? "strftime, e.g. %H:%M · %A %d %B" : null;
    form.append(field(key.replace(/_/g, " "), controlFor(kind, widget[key], change(key)), hint));
  }
}

function renameWidget(widget, value) {
  const next = value.trim();
  if (!next || state.theme.widgets.some((w) => w !== widget && w.id === next)) {
    setStatus(`Widget id "${next}" is empty or already used`, "error");
    return;
  }
  if (state.boxes[widget.id]) {
    state.boxes[next] = state.boxes[widget.id];
    delete state.boxes[widget.id];
  }
  widget.id = next;
  state.selected = next;
  markDirty();
  buildList();
  drawOverlay();
}

function syncPositionFields(widget) {
  if (widget.id !== state.selected) return;
  const x = $("#prop-x");
  const y = $("#prop-y");
  if (x) x.value = widget.x;
  if (y) y.value = widget.y;
}

function buildThemeProps(form) {
  $("#props-title").textContent = "Theme";
  const theme = state.theme;
  const set = (apply) => (value) => {
    apply(value);
    markDirty();
    scheduleRender();
  };
  theme.background = theme.background || { color: "#000000", image: null };
  form.append(
    field("name", controlFor("string", theme.name, set((v) => (theme.name = v)))),
    field("author", controlFor("string", theme.author, set((v) => (theme.author = v)))),
    field("license", controlFor("string", theme.license, set((v) => (theme.license = v))), "e.g. CC0-1.0, CC-BY-4.0"),
    field("description", controlFor("text", theme.description, set((v) => (theme.description = v)))),
    el("h2", { class: "section-title", text: "Background" }),
    field("color", controlFor("color", theme.background.color, set((v) => (theme.background.color = v)))),
    field("image", controlFor("asset", theme.background.image, set((v) => (theme.background.image = v || null)))),
    el("h2", { class: "section-title", text: "Timing" }),
    field("refresh (ms)", controlFor("int", theme.refresh_ms, set((v) => (theme.refresh_ms = Math.max(100, v || 1000))))),
  );
}

// ---------------------------------------------------------------- panel model menu

function buildModelMenu() {
  const select = $("#model-select");
  select.replaceChildren();
  const groups = new Map();
  for (const model of state.models) {
    if (!groups.has(model.vendor)) groups.set(model.vendor, el("optgroup", { label: model.vendor }));
    const size = `${model.landscape[0]}×${model.landscape[1]}`;
    const status = model.driver === "planned" ? " · driver planned" : model.driver === "experimental" ? " · experimental" : "";
    groups.get(model.vendor).append(el("option", { value: model.id, text: `${model.label} — ${size}${status}` }));
  }
  select.append(...groups.values(), el("optgroup", { label: "Other" }, el("option", { value: "custom", text: "Custom size" })));
}

function syncPanelBar() {
  const display = state.theme.display || {};
  const model = display.model || "custom";
  $("#model-select").value = state.models.some((m) => m.id === model) ? model : "custom";
  const orientation = display.orientation || (display.width >= display.height ? "landscape" : "portrait");
  $("#orientation-select").value = orientation;
  $("#custom-size").hidden = model !== "custom";
  $("#custom-w").value = display.width || state.size[0];
  $("#custom-h").value = display.height || state.size[1];
  const info = currentModel();
  $("#panel-info").textContent = info
    ? `${info.protocol_name} · driver ${info.driver}${info.notes ? " · " + info.notes : ""}`
    : "any size, e.g. for panels not in the list yet";
}

async function onPanelChange() {
  const model = $("#model-select").value;
  const orientation = $("#orientation-select").value;
  $("#custom-size").hidden = model !== "custom";
  const source = state.adaptBase || clone(state.theme);
  try {
    const data = await api("POST", "/api/adapt", {
      theme: source,
      model,
      orientation,
      mode: $("#adapt-mode").value,
      width: Number($("#custom-w").value) || state.size[0],
      height: Number($("#custom-h").value) || state.size[1],
    });
    state.theme = data.theme;
    markDirty();
    state.adaptBase = source;
    syncPanelBar();
    buildProps();
    scheduleRender(0);
  } catch (error) {
    setStatus(error.message, "error");
    syncPanelBar();
  }
}

// ---------------------------------------------------------------- themes

async function refreshThemeList(selectId) {
  state.themes = await api("GET", "/api/themes");
  const select = $("#theme-select");
  select.replaceChildren(
    ...state.themes.map((t) => el("option", { value: t.id, text: t.builtin ? `${t.id} (built-in)` : t.id })),
  );
  if (!state.themeId) select.append(el("option", { value: "", text: "(unsaved)" }));
  select.value = selectId ?? state.themeId ?? "";
}

async function loadTheme(id) {
  const data = await api("GET", `/api/themes/${encodeURIComponent(id)}`);
  state.themeId = id;
  state.builtin = data.builtin;
  state.theme = data.theme;
  state.selected = null;
  state.boxes = {};
  markClean();
  syncPanelBar();
  buildList();
  buildProps();
  await render();
  if (data.warnings.length) setStatus("Warning: " + data.warnings.join(" · "), "warn");
}

async function save() {
  if (!state.themeId || state.builtin) return saveAs();
  try {
    await api("POST", `/api/themes/${encodeURIComponent(state.themeId)}`, { theme: state.theme, source: state.themeId });
    markClean();
    setStatus(`Saved ${state.themeId}`);
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function saveAs() {
  const suggestion = slug(state.theme.name) + (state.builtin ? "-custom" : "");
  const id = prompt("Save theme as (letters, digits, - _ .):", suggestion || "my-theme");
  if (!id) return;
  if (!THEME_NAME_RE.test(id) || id.includes("..")) {
    setStatus(`"${id}" is not a valid theme name`, "error");
    return;
  }
  if (state.themes.some((t) => t.id === id && !t.builtin) && !confirm(`Overwrite your theme "${id}"?`)) return;
  try {
    await api("POST", `/api/themes/${encodeURIComponent(id)}`, { theme: state.theme, source: state.themeId });
    state.themeId = id;
    state.builtin = false;
    markClean();
    await refreshThemeList(id);
    setStatus(`Saved ${id}`);
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function activate() {
  // An unchanged built-in theme can be shown as it is; anything else is saved first.
  if (state.dirty || !state.themeId) {
    await save();
    if (state.dirty || !state.themeId) return; // save was cancelled or failed
  }
  try {
    await api("POST", "/api/activate", { id: state.themeId });
    setStatus(`"${state.themeId}" is now shown on the panel`);
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
    name: "My theme",
    author: "",
    license: "CC-BY-4.0",
    description: "",
    display: { model: model.id, orientation, width, height },
    background: { color: "#0b1016", image: null },
    refresh_ms: 1000,
    widgets: [
      { type: "clock", id: "clock", x: Math.round(width / 2), y: Math.round(height / 2 - 30), format: "%H:%M", font_size: 48, color: "#ffffff", align: "center" },
    ],
  };
  state.themeId = null;
  state.builtin = false;
  state.selected = null;
  markDirty();
  refreshThemeList("");
  syncPanelBar();
  buildList();
  buildProps();
  scheduleRender(0);
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
    state.selected = null;
    markDirty();
    await refreshThemeList("");
    syncPanelBar();
    buildList();
    buildProps();
    scheduleRender(0);
    setStatus(`Imported ${file.name}. Images/fonts of the original theme are not included; save and upload them.`);
  } catch (error) {
    setStatus(`Import failed: ${error.message}`, "error");
  }
}

// ---------------------------------------------------------------- setup

function setLive(on) {
  state.live = on;
  clearInterval(state.liveTimer);
  if (on) state.liveTimer = setInterval(() => scheduleRender(0), Math.max(500, state.theme?.refresh_ms || 1000));
  scheduleRender(0);
}

function onKey(event) {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName);
  const mod = event.ctrlKey || event.metaKey;
  if (mod && event.key.toLowerCase() === "s") {
    event.preventDefault();
    save();
    return;
  }
  if (typing) return;
  const step = event.shiftKey ? 10 : 1;
  const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  if (moves[event.key] && state.selected) {
    event.preventDefault();
    nudge(...moves[event.key]);
  } else if ((event.key === "Delete" || event.key === "Backspace") && state.selected) {
    event.preventDefault();
    deleteWidget(state.theme.widgets.findIndex((w) => w.id === state.selected));
  } else if (mod && event.key.toLowerCase() === "d" && state.selected) {
    event.preventDefault();
    duplicateWidget(state.theme.widgets.findIndex((w) => w.id === state.selected));
  } else if (event.key === "Escape") {
    select(null);
  }
}

async function init() {
  try {
    state.specs = await api("GET", "/api/specs");
    state.models = state.specs.models;
    $("#add-type").replaceChildren(...Object.keys(state.specs.widgets).map((t) => el("option", { value: t, text: t })));
    buildModelMenu();
    api("GET", "/api/sensors")
      .then((sensors) => {
        state.sensors = sensors;
        const list = el("datalist", { id: "sensor-keys" });
        for (const s of sensors) list.append(el("option", { value: s.key, label: `${s.label} (${s.unit})` }));
        document.body.append(list);
      })
      .catch(() => {});
    await refreshThemeList();
    const first = state.themes.find((t) => t.id === "libre-default") || state.themes[0];
    if (first) await loadTheme(first.id);
    else newTheme();
  } catch (error) {
    setStatus(`Could not start the editor: ${error.message}`, "error");
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
  $("#btn-add").addEventListener("click", addWidget);
  $("#live-toggle").addEventListener("change", (event) => setLive(event.target.checked));
  $("#model-select").addEventListener("change", onPanelChange);
  $("#orientation-select").addEventListener("change", onPanelChange);
  $("#custom-w").addEventListener("change", onPanelChange);
  $("#custom-h").addEventListener("change", onPanelChange);
  $("#stage").addEventListener("pointerdown", (event) => {
    if (event.target === event.currentTarget) select(null);
  });
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", () => {
    layoutStage();
    drawOverlay();
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) event.preventDefault();
  });
}

init();
