// Modal dialogs in the app's look, as promises:
//   if (await confirmDialog({ title, message, confirm: t("Delete"), danger: true })) …
//   const name = await promptDialog({ title, text, label, value });   // null when cancelled
//   const result = await openDialog((close) => html`…`, { label, wide: true });

import { createStore, useSelector } from "../core/store.js";
import { t } from "../core/i18n.js";
import { bindKey } from "../core/shortcuts.js";
import { html, useLayoutEffect, useRef, useState } from "../lib/preact.js";

const dialogs = createStore({ stack: [] });
let next = 1;

export const dialogOpen = () => dialogs.get().stack.length > 0;

/** Show `content(close)` in a dialog; resolves with what close(value) is given (Escape: null). */
export function openDialog(content, { label = "", wide = false } = {}) {
  return new Promise((resolve) => {
    const id = next++;
    const close = (value = null) => {
      dialogs.set((state) => ({ stack: state.stack.filter((d) => d.id !== id) }));
      resolve(value);
    };
    dialogs.set((state) => ({ stack: [...state.stack, { id, content, close, label, wide }] }));
  });
}

export function confirmDialog({ title, message, confirm = t("OK"), cancel = t("Cancel"), danger = false }) {
  return openDialog(
    (close) => html`
      <h2 class="dialog-title">${title}</h2>
      ${message ? html`<p class="dialog-text">${message}</p>` : null}
      <div class="dialog-actions">
        <button type="button" class="btn" onClick=${() => close(false)}>${cancel}</button>
        <button type="button" class=${`btn ${danger ? "btn-danger" : "btn-primary"}`} autofocus onClick=${() => close(true)}>
          ${confirm}
        </button>
      </div>
    `,
    { label: title },
  ).then(Boolean);
}

function PromptBody({ close, title, text: about, label, value, placeholder, hint, confirm, validate }) {
  const [text, setText] = useState(value || "");
  const problem = text.trim() && validate ? validate(text.trim()) : null;
  const submit = (event) => {
    event.preventDefault();
    if (text.trim() && !problem) close(text.trim());
  };
  return html`<form onSubmit=${submit}>
    <h2 class="dialog-title">${title}</h2>
    ${about ? html`<p class="dialog-text">${about}</p>` : null}
    <label class="field">
      <span class="field-label">${label}</span>
      <input
        class="input"
        type="text"
        value=${text}
        placeholder=${placeholder}
        autofocus
        spellcheck="false"
        aria-invalid=${problem ? "true" : "false"}
        onInput=${(event) => setText(event.currentTarget.value)}
      />
      <small class=${`field-hint${problem ? " error" : ""}`}>${problem || hint || " "}</small>
    </label>
    <div class="dialog-actions">
      <button type="button" class="btn" onClick=${() => close(null)}>${t("Cancel")}</button>
      <button type="submit" class="btn btn-primary" disabled=${!text.trim() || Boolean(problem)}>${confirm || t("OK")}</button>
    </div>
  </form>`;
}

export function promptDialog(options) {
  return openDialog((close) => html`<${PromptBody} close=${close} ...${options} />`, { label: options.title });
}

const FOCUSABLE = 'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

function DialogBox({ dialog, top }) {
  const box = useRef(null);
  // at once, not after the next paint, as the popover and the palette: the focus it gives
  // back is the one before it, and an Escape right after it shows closes it
  useLayoutEffect(() => {
    const before = document.activeElement;
    const frame = requestAnimationFrame(() => {
      const target = box.current?.querySelector("[autofocus]") || box.current?.querySelector(FOCUSABLE);
      target?.focus();
    });
    return () => {
      cancelAnimationFrame(frame);
      if (before instanceof HTMLElement && document.contains(before)) before.focus({ preventScroll: true });
    };
  }, []);
  useLayoutEffect(() => {
    if (!top) return undefined;
    return bindKey("escape", () => dialog.close(null), { inInputs: true });
  }, [top]);
  // Tab stays inside the dialog
  const trap = (event) => {
    if (event.key !== "Tab") return;
    const items = [...box.current.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  return html`<div class="dialog-backdrop" onPointerDown=${(event) => event.target === event.currentTarget && dialog.close(null)}>
    <div
      ref=${box}
      class=${`dialog${dialog.wide ? " wide" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-label=${dialog.label}
      onKeyDown=${trap}
    >
      ${dialog.content(dialog.close)}
    </div>
  </div>`;
}

export function Dialogs() {
  const stack = useSelector(dialogs, (state) => state.stack);
  return html`${stack.map((dialog, index) => html`<${DialogBox} key=${dialog.id} dialog=${dialog} top=${index === stack.length - 1} />`)}`;
}
