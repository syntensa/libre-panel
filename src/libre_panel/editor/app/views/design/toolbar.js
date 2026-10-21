// The floating toolbar above the selection: what it shows (sensor or source),
// how it is drawn (finish), its colour, its size, and more in a menu.

import { edit, isModule, selectedWidgets, single } from "../../core/doc.js";
import { enumLabel, fieldLabel, t, widgetLabel } from "../../core/i18n.js";
import { store, useStore } from "../../core/state.js";
import { html, useLayoutEffect, useRef, useState } from "../../lib/preact.js";
import { Chips, IconButton, NumberField } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { Menu, Popover } from "../../ui/layer.js";
import { ColorButton } from "./color.js";
import { FINISH_NAMES, enumOptions, finishesFor } from "./fields.js";
import { inspectorState } from "./inspector-state.js";
import {
  align,
  canPaste,
  copySelection,
  deleteSelection,
  detachModule,
  duplicateSelection,
  kindName,
  moduleKinds,
  moveLayer,
  paste,
  resizeModuleBy,
  selectSameKind,
  sendModules,
  setFlag,
  sourceLabels,
  specOf,
} from "./ops.js";
import { SensorButton, sensorName } from "./sensors.js";

const change = (widget, key) => (value) =>
  edit(`tool:${widget.id}:${key}`, () => {
    widget[key] = value;
  });

/** The menu of the selection (the toolbar's "more", and a right click on the canvas). */
export function widgetMenu() {
  const chosen = selectedWidgets();
  const one = chosen.length === 1 ? chosen[0] : null;
  const ids = chosen.map((widget) => widget.id);
  const locked = chosen.length > 0 && chosen.every((widget) => widget.locked);
  const hidden = chosen.length > 0 && chosen.every((widget) => widget.visible === false);
  const items = [
    { icon: "duplicate", label: t("Duplicate"), keys: "mod+d", run: duplicateSelection },
    { icon: "file", label: t("Copy"), keys: "mod+c", run: copySelection },
  ];
  if (canPaste()) items.push({ icon: "download", label: t("Paste"), keys: "mod+v", run: paste });
  items.push("-");
  if (one && !isModule(one)) {
    items.push(
      { icon: "arrow-up", label: t("Bring forward"), run: () => moveLayer(one.id, 1) },
      { icon: "arrow-down", label: t("Send backward"), run: () => moveLayer(one.id, -1) },
    );
  }
  items.push(
    { icon: locked ? "unlock" : "lock", label: locked ? t("Unlock") : t("Lock"), run: () => setFlag(ids, "locked", !locked) },
    { icon: hidden ? "eye" : "eye-off", label: hidden ? t("Show") : t("Hide"), run: () => setFlag(ids, "visible", hidden) },
  );
  if (one) {
    const name = isModule(one) ? kindName(one.module) : widgetLabel(one.type);
    items.push({ icon: "select-all", label: t("Select all like this ({name})", { name }), run: () => selectSameKind(one) });
  }
  if (one && isModule(one)) items.push({ icon: "detach", label: t("Detach into widgets"), run: () => detachModule(one) });
  if (one) items.push({ icon: "sliders", label: t("Fine-tuning"), run: () => inspectorState.set({ page: "details" }) });
  // modules to the theme another panel shows: copied, or moved (Settings → Panel → More panels)
  const others = (store.get().settings?.config?.panels || []).map((_, n) => n + 2);
  if (others.length && chosen.length && chosen.every(isModule)) {
    items.push("-");
    for (const number of others) {
      items.push(
        { icon: "to-panel", label: t("Copy to panel {number}", { number }), run: () => sendModules(number, chosen, false) },
        { icon: "to-panel", label: t("Move to panel {number}", { number }), run: () => sendModules(number, chosen, true) },
      );
    }
  }
  items.push("-", { icon: "trash", label: t("Remove"), keys: "delete", danger: true, run: deleteSelection });
  return items;
}

function MoreButton() {
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  return html`<button ref=${anchor} type="button" class="tool-icon" aria-label=${t("More options")} title=${t("More options")}
      aria-haspopup="menu" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      <${Icon} name="more" size=${18} />
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${t("More options")} width=${250} placement="bottom-end">
      <${Menu} items=${widgetMenu()} onClose=${() => setOpen(false)} />
    <//>`;
}

/** A module's source: CPU, GPU, ... or any sensor. */
function SourceChip({ widget }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  const sources = moduleKinds()[widget.module]?.sources || [];
  const names = sourceLabels();
  const name = widget.source === "sensor" ? sensorName(widget.sensor) || names.sensor : names[widget.source] || widget.source;
  return html`<button ref=${anchor} type="button" class="tool-sensor" aria-haspopup="dialog" aria-expanded=${String(open)}
      title=${t("What it shows")} onClick=${() => setOpen(!open)}>
      <span class="dot"></span><span class="sensor-button-name">${name}</span>
      <${Icon} name="chevron-down" size=${12} strokeWidth=${2.4} />
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${t("What it shows")} width=${300}>
      <div class="pop-section">
        <div class="pop-title">${t("Shows")}</div>
        <${Chips} label=${t("Shows")} value=${widget.source} onChange=${change(widget, "source")}
          options=${sources.map((source) => ({ value: source, label: names[source] ?? source }))} />
        ${widget.source === "sensor" ? html`<${SensorButton} value=${widget.sensor} onPick=${change(widget, "sensor")} />` : null}
      </div>
    <//>`;
}

/** The size: cells of a module, a box's width and height, an icon's size or a text's. */
function SizeControl({ widget }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  const spec = specOf(widget.type);
  let text;
  let body;
  if (isModule(widget)) {
    text = `${widget.cols} × ${widget.rows}`;
    body = html`<div class="size-steps">
      <span class="muted">${t("Wide")}</span>
      <${IconButton} icon="minus" label=${t("Narrower")} onClick=${() => resizeModuleBy(widget, -1, 0)} />
      <strong class="mono">${widget.cols}</strong>
      <${IconButton} icon="plus" label=${t("Wider")} onClick=${() => resizeModuleBy(widget, 1, 0)} />
      <span class="muted">${t("High")}</span>
      <${IconButton} icon="minus" label=${t("Lower")} onClick=${() => resizeModuleBy(widget, 0, -1)} />
      <strong class="mono">${widget.rows}</strong>
      <${IconButton} icon="plus" label=${t("Taller")} onClick=${() => resizeModuleBy(widget, 0, 1)} />
    </div>`;
  } else if ("w" in spec && "h" in spec) {
    text = `${widget.w || "auto"} × ${widget.h || "auto"}`;
    body = html`<div class="quad">
      <label class="field"><span class="field-label">${fieldLabel("w")}</span>
        <${NumberField} label=${fieldLabel("w")} value=${widget.w} min=${0} unit="px" onChange=${change(widget, "w")} /></label>
      <label class="field"><span class="field-label">${fieldLabel("h")}</span>
        <${NumberField} label=${fieldLabel("h")} value=${widget.h} min=${0} unit="px" onChange=${change(widget, "h")} /></label>
    </div>`;
  } else if ("size" in spec || "font_size" in spec) {
    const key = "size" in spec ? "size" : "font_size";
    text = `${widget[key]} px`;
    body = html`<label class="field"><span class="field-label">${fieldLabel(key)}</span>
      <${NumberField} label=${fieldLabel(key)} value=${widget[key]} min=${4} max=${2000} unit="px" onChange=${change(widget, key)} /></label>`;
  } else return null;
  return html`<button ref=${anchor} type="button" class="tool-text mono" title=${t("Size")} aria-haspopup="dialog" aria-expanded=${String(open)}
      onClick=${() => setOpen(!open)}>${text}</button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${t("Size")} width=${260}>
      <div class="pop-section"><div class="pop-title">${t("Size")}</div>${body}</div>
    <//>`;
}

function SingleTools({ widget }) {
  const spec = specOf(widget.type);
  const parts = [];
  // a module shows what it is made to (its source), or is named: its sensor field is a source's
  // ("No sensor" on a card slot or a clock said it lacked one)
  if (isModule(widget) && moduleKinds()[widget.module]?.sources?.length) parts.push(html`<${SourceChip} key="source" widget=${widget} />`);
  else if (!isModule(widget) && "sensor" in spec && spec.sensor[0] === "sensor" && widget.type !== "icon")
    parts.push(html`<${SensorButton} key="sensor" compact value=${widget.sensor} onPick=${change(widget, "sensor")} />`);
  else parts.push(html`<span key="name" class="tool-name">${isModule(widget) ? kindName(widget.module) : widgetLabel(widget.type)}</span>`);
  if ("finish" in spec) {
    const names = FINISH_NAMES();
    const options = finishesFor(widget).filter((name) => enumOptions(spec.finish[0]).includes(name));
    parts.push(html`<span key="s1" class="tool-sep"></span>`);
    parts.push(html`<span key="finish" class="tool-group" role="radiogroup" aria-label=${fieldLabel("finish")}>
      ${options.map((finish) => html`<button key=${finish} type="button" role="radio" aria-checked=${String(widget.finish === finish)}
        class="tool-text" title=${enumLabel(finish)} onClick=${() => change(widget, "finish")(finish)}>${names[finish]}</button>`)}
    </span>`);
  }
  parts.push(html`<span key="s2" class="tool-sep"></span>`);
  if ("color" in spec) {
    parts.push(html`<${ColorButton} key="color" value=${widget.color} optional=${spec.color[0].endsWith("?")} onChange=${change(widget, "color")} />`);
  }
  parts.push(html`<${SizeControl} key="size" widget=${widget} />`);
  return parts;
}

function MultiTools({ count }) {
  const tools = [
    ["align-left", "left", t("Align left edges")],
    ["align-center", "center", t("Align horizontal centres")],
    ["align-right", "right", t("Align right edges")],
    ["align-top", "top", t("Align top edges")],
    ["align-middle", "middle", t("Align vertical centres")],
    ["align-bottom", "bottom", t("Align bottom edges")],
  ];
  return html`<span class="tool-name">${t("{count} widgets", { count })}</span>
    <span class="tool-sep"></span>
    ${tools.map(([icon, mode, label]) => html`<button key=${mode} type="button" class="tool-icon" aria-label=${label} title=${label}
      onClick=${() => align(mode)}><${Icon} name=${icon} size=${17} /></button>`)}
    <span class="tool-sep"></span>`;
}

/** Placed above the selection's box (below it when there is no room above). */
export function FloatingToolbar({ box, zoom, canvas, stage, hidden }) {
  useStore((s) => s.rev);
  const bar = useRef(null);
  const one = single();
  const chosen = selectedWidgets();
  useLayoutEffect(() => {
    const place = () => {
      if (!bar.current || !canvas.current || !stage.current) return;
      const c = canvas.current.getBoundingClientRect();
      const s = stage.current.getBoundingClientRect();
      const width = bar.current.offsetWidth;
      const height = bar.current.offsetHeight;
      const centre = c.left - s.left + (box[0] + box[2] / 2) * zoom;
      let top = c.top - s.top + box[1] * zoom - height - 14;
      if (top < 60) top = c.top - s.top + (box[1] + box[3]) * zoom + 14;
      const left = Math.max(8, Math.min(s.width - width - 8, centre - width / 2));
      bar.current.style.left = `${Math.round(left)}px`;
      bar.current.style.top = `${Math.round(Math.max(8, Math.min(s.height - height - 8, top)))}px`;
    };
    place();
    const scroller = stage.current?.querySelector(".stage-scroll");
    scroller?.addEventListener("scroll", place);
    return () => scroller?.removeEventListener("scroll", place);
  });
  if (!chosen.length) return null;
  return html`<div ref=${bar} class=${`floating-toolbar${hidden ? " quiet" : ""}`} role="toolbar" aria-label=${t("Selection")}
    onPointerDown=${(event) => event.stopPropagation()}>
    ${one ? html`<${SingleTools} widget=${one} />` : html`<${MultiTools} count=${chosen.length} />`}
    <${MoreButton} />
  </div>`;
}
