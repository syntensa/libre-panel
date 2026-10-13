// Short messages at the bottom right: toast(t("Saved {id}", { id }), { kind: "success" }).

import { createStore, useSelector } from "../core/store.js";
import { t } from "../core/i18n.js";
import { html } from "../lib/preact.js";
import { Icon } from "./icons.js";

const toasts = createStore({ list: [] });
let next = 1;

const ICON = { info: "info", success: "check", warn: "warning", error: "warning" };

/**
 * Show a message. Options: kind ("info", "success", "warn", "error"), timeout in ms
 * (0: until closed), action {label, run}. Returns its id.
 */
export function toast(message, { kind = "info", timeout, action } = {}) {
  const id = next++;
  const wait = timeout ?? (kind === "error" ? 9000 : kind === "warn" ? 6500 : 4000);
  toasts.set((state) => ({ list: [...state.list, { id, message, kind, action }].slice(-4) }));
  if (wait) setTimeout(() => dismiss(id), wait);
  return id;
}

export function dismiss(id) {
  toasts.set((state) => ({ list: state.list.filter((item) => item.id !== id) }));
}

/** An error as a message: its text, never an empty box (a failure without words says so). */
export const toastError = (error) =>
  toast(error?.message || (error && typeof error !== "object" ? String(error) : t("Something went wrong.")), { kind: "error" });

export function Toasts() {
  const list = useSelector(toasts, (state) => state.list);
  return html`<div class="toasts" aria-live="polite">
    ${list.map(
      (item) => html`<div class=${`toast ${item.kind}`} key=${item.id} role=${item.kind === "error" ? "alert" : "status"}>
        <span class="toast-icon"><${Icon} name=${ICON[item.kind] || "info"} /></span>
        <span class="toast-text">${item.message}</span>
        ${item.action
          ? html`<button type="button" class="toast-action" onClick=${() => (dismiss(item.id), item.action.run())}>
              ${item.action.label}
            </button>`
          : null}
        <button type="button" class="toast-close" aria-label=${t("Close")} onClick=${() => dismiss(item.id)}>
          <${Icon} name="close" size=${14} />
        </button>
      </div>`,
    )}
  </div>`;
}
