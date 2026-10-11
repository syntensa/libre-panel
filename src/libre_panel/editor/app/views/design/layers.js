// The layers: every widget, the frontmost on top. A click selects (Shift or
// Ctrl: several), the eye hides, the lock keeps it from being moved on the
// canvas (it is still picked here). Drag a row to change the order.

import { isModule, setSelection, toggleSelected, widgets } from "../../core/doc.js";
import { t, widgetLabel } from "../../core/i18n.js";
import { useStore } from "../../core/state.js";
import { html, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { arrowNavigation } from "../../ui/layer.js";
import { iconOf } from "./inspector.js";
import { kindName, moveLayer, placeLayer, setFlag } from "./ops.js";

export function Layers() {
  const { selection } = useStore((s) => ({ selection: s.selection, rev: s.rev }));
  const [dragging, setDragging] = useState(null);
  const [over, setOver] = useState(null);
  const list = widgets();
  const rows = [...list].reverse(); // front first
  const chosen = new Set(selection);
  const drop = (event) => {
    event.preventDefault();
    if (dragging === null || over === null) return;
    // rows are reversed: row r is list index n - 1 - r; above a row is in front of it
    const from = list.findIndex((widget) => widget.id === dragging);
    const index = list.length - 1 - over.row;
    let at = over.after ? index : index + 1;
    if (from < at) at -= 1;
    placeLayer(dragging, at);
    setDragging(null);
    setOver(null);
  };
  return html`<div class="side-scroll">
    <h2 class="side-title">${t("Layers")} <span class="muted">${list.length}</span></h2>
    ${rows.length
      ? html`<div class="layer-list" role="listbox" aria-multiselectable="true" aria-label=${t("Layers")}
          onKeyDown=${(event) => {
            arrowNavigation(event, ".layer-row");
            const id = event.target?.dataset?.id;
            if (id && event.altKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
              event.preventDefault();
              moveLayer(id, event.key === "ArrowUp" ? 1 : -1);
            }
          }}>
          ${rows.map((widget, row) => {
            const visible = widget.visible !== false;
            const selected = chosen.has(widget.id);
            const marker = over && over.row === row ? (over.after ? " drop-after" : " drop-before") : "";
            return html`<div
              key=${widget.id}
              class=${`layer${selected ? " selected" : ""}${visible ? "" : " invisible"}${marker}`}
              draggable="true"
              onDragStart=${(event) => {
                setDragging(widget.id);
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("text/plain", widget.id);
              }}
              onDragOver=${(event) => {
                if (dragging === null) return;
                event.preventDefault();
                const rect = event.currentTarget.getBoundingClientRect();
                setOver({ row, after: event.clientY > rect.top + rect.height / 2 });
              }}
              onDrop=${drop}
              onDragEnd=${() => (setDragging(null), setOver(null))}
            >
              <button type="button" class="layer-row" role="option" aria-selected=${String(selected)} data-id=${widget.id}
                onClick=${(event) => (event.shiftKey || event.ctrlKey || event.metaKey ? toggleSelected(widget.id) : setSelection([widget.id]))}>
                <${Icon} name=${iconOf(widget)} size=${15} />
                <span class="layer-name">${widget.id}</span>
                <span class="layer-kind">${isModule(widget) ? kindName(widget.module) : widgetLabel(widget.type)}</span>
              </button>
              <button type="button" class=${`layer-flag${visible ? "" : " on"}`} aria-pressed=${String(!visible)}
                aria-label=${visible ? t("Hide") : t("Show")} title=${visible ? t("Hide") : t("Show")}
                onClick=${() => setFlag([widget.id], "visible", !visible)}>
                <${Icon} name=${visible ? "eye" : "eye-off"} size=${15} />
              </button>
              <button type="button" class=${`layer-flag${widget.locked ? " on" : ""}`} aria-pressed=${String(Boolean(widget.locked))}
                aria-label=${widget.locked ? t("Unlock") : t("Lock")} title=${widget.locked ? t("Unlock") : t("Lock")}
                onClick=${() => setFlag([widget.id], "locked", !widget.locked)}>
                <${Icon} name=${widget.locked ? "lock" : "unlock"} size=${15} />
              </button>
            </div>`;
          })}
        </div>`
      : html`<p class="side-hint">${t("No widgets yet: drag one from the building blocks.")}</p>`}
    <p class="side-hint">${t("The top one is in front. Drag a row to change the order (or Alt+↑ ↓). Locked layers ignore clicks on the canvas; pick them here.")}</p>
  </div>`;
}
