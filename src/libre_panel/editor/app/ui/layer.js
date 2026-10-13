// Floating things (popovers, menus) above everything else: rendered into the
// page's #layers element, so no overflow or stacking order of a view cuts them.

import { bindKey } from "../core/shortcuts.js";
import { html, render, useLayoutEffect, useRef } from "../lib/preact.js";
import { Icon } from "./icons.js";
import { keyLabel } from "../core/shortcuts.js";

const host = () => document.getElementById("layers") || document.body;

/** Renders its children at the top of the page. */
export function Layer({ children }) {
  const box = useRef(null);
  if (!box.current) box.current = document.createElement("div");
  useLayoutEffect(() => {
    const node = box.current;
    host().append(node);
    return () => {
      render(null, node);
      node.remove();
    };
  }, []);
  useLayoutEffect(() => {
    render(children, box.current);
  });
  return null;
}

const FOCUSABLE = 'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

/**
 * Place `panel` next to the anchor's rectangle, flipped and kept inside the window. One that
 * fits neither below nor above (a tall popover in a low window) stays below its anchor and
 * scrolls, rather than covering it.
 */
export function placeNear(panel, rect, placement = "bottom-start", offset = 8) {
  const width = panel.offsetWidth;
  // its whole height, also where it scrolls now
  const height = Math.min(panel.scrollHeight + (panel.offsetHeight - panel.clientHeight), innerHeight - 16);
  const [side, align = "start"] = placement.split("-");
  let x = align === "end" ? rect.right - width : align === "center" ? rect.left + rect.width / 2 - width / 2 : rect.left;
  let y = side === "top" ? rect.top - height - offset : rect.bottom + offset;
  let room = "";
  if (side === "right") {
    x = rect.right + offset;
    y = rect.top;
  }
  const below = innerHeight - 8 - (rect.bottom + offset);
  if (side === "bottom" && height > below) {
    if (rect.top - offset - height >= 8 || below < 240) y = rect.top - height - offset;
    else room = `${Math.floor(below)}px`;
  }
  if (side === "top" && y < 8) y = rect.bottom + offset;
  if (panel.style.maxHeight !== room) panel.style.maxHeight = room;
  x = Math.max(8, Math.min(innerWidth - width - 8, x));
  y = Math.max(8, Math.min(innerHeight - (room ? parseFloat(room) : height) - 8, y));
  panel.style.left = `${Math.round(x)}px`;
  panel.style.top = `${Math.round(y)}px`;
}

/**
 * A floating panel next to `anchor` (a ref of an element). It closes on Escape
 * and on a click outside (`onClose`); focus moves into it and back afterwards.
 */
export function Popover({ anchor, open, onClose, placement = "bottom-start", label, class: className = "", width, children }) {
  const panel = useRef(null);
  useLayoutEffect(() => {
    if (!open || !panel.current) return undefined;
    const place = () => {
      const element = anchor?.current;
      if (element && panel.current) placeNear(panel.current, element.getBoundingClientRect(), placement);
    };
    place();
    const watcher = new ResizeObserver(place);
    watcher.observe(panel.current);
    addEventListener("resize", place);
    addEventListener("scroll", place, true);
    return () => {
      watcher.disconnect();
      removeEventListener("resize", place);
      removeEventListener("scroll", place, true);
    };
  });
  // at once, not after the next paint: an Escape that comes as soon as the popover shows must
  // close it, not reach what is behind it (the design view would let go of the selection)
  useLayoutEffect(() => {
    if (!open) return undefined;
    const before = document.activeElement;
    const outside = (event) => {
      if (!panel.current || panel.current.contains(event.target) || anchor?.current?.contains(event.target)) return;
      // a popover opened later (from this one) keeps it open; a click in an older one closes it
      const other = event.target.closest?.(".layer-keep");
      if (other && other.compareDocumentPosition(panel.current) & Node.DOCUMENT_POSITION_PRECEDING) return;
      onClose?.("outside");
    };
    const unbind = bindKey("escape", () => onClose?.("escape"), { inInputs: true });
    document.addEventListener("pointerdown", outside, true);
    const frame = requestAnimationFrame(() => {
      const target = panel.current?.querySelector("[autofocus]") || panel.current?.querySelector(FOCUSABLE);
      target?.focus({ preventScroll: true });
    });
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", outside, true);
      unbind();
      const inside = !document.activeElement || document.activeElement === document.body || panel.current?.contains(document.activeElement);
      if (inside && before instanceof HTMLElement && document.contains(before)) before.focus({ preventScroll: true });
    };
  }, [open]);
  if (!open) return null;
  return html`<${Layer}>
    <div
      ref=${panel}
      class=${`popover layer-keep ${className}`}
      role="dialog"
      aria-label=${label}
      style=${width ? { width: `${width}px` } : null}
    >
      ${children}
    </div>
  <//>`;
}

/** Arrow keys move between the items of a menu or list. */
export function arrowNavigation(event, selector = '[role="menuitem"]:not([disabled])') {
  const keys = { ArrowDown: 1, ArrowUp: -1, Home: "first", End: "last" };
  const step = keys[event.key];
  if (!step) return;
  const items = [...event.currentTarget.querySelectorAll(selector)];
  if (!items.length) return;
  event.preventDefault();
  const index = items.indexOf(document.activeElement);
  const next =
    step === "first" ? 0 : step === "last" ? items.length - 1 : (index + step + items.length) % items.length;
  items[next].focus();
}

/**
 * A menu of actions: [{label, icon, keys, run, danger, disabled, checked}] and "-" for a
 * line. Picking one closes the menu, then runs it.
 */
export function Menu({ items, onClose, label }) {
  return html`<div class="menu" role="menu" aria-label=${label} onKeyDown=${(event) => arrowNavigation(event)}>
    ${items.map((item, index) =>
      item === "-"
        ? html`<div class="menu-sep" role="separator" key=${`sep${index}`}></div>`
        : html`<button
            type="button"
            key=${item.label}
            role=${item.checked === undefined ? "menuitem" : "menuitemcheckbox"}
            aria-checked=${item.checked === undefined ? undefined : String(Boolean(item.checked))}
            class=${`menu-item${item.danger ? " danger" : ""}`}
            disabled=${item.disabled}
            onClick=${() => {
              onClose?.();
              item.run?.();
            }}
          >
            <span class="menu-icon">${item.icon ? html`<${Icon} name=${item.icon} />` : null}</span>
            <span class="menu-label">${item.label}</span>
            ${item.checked !== undefined ? html`<span class="menu-check">${item.checked ? html`<${Icon} name="check" />` : null}</span>` : null}
            ${item.keys ? html`<kbd class="kbd">${keyLabel(item.keys)}</kbd>` : null}
          </button>`,
    )}
  </div>`;
}
