// What the sections of Settings are made of: cards with rows (a name and a line about
// it on the left, the control on the right), status marks, notes, a card that opens.

import { t } from "../../core/i18n.js";
import { restartPanel } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { toast } from "../../ui/toast.js";

/** Put a text on the clipboard and say so; where that is not allowed, say how to. */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast(t("Copied."), { kind: "success" });
  } catch {
    toast(t("Copying is not allowed here: select the text and copy it."), { kind: "warn" });
  }
}

/** A card: an optional head (title, a line about it, an action) and the rows. */
export function Card({ id, title, text, action, class: className = "", children }) {
  return html`<section id=${id} class=${`settings-card ${className}`} aria-labelledby=${title ? `${id}-title` : null}>
    ${title
      ? html`<div class="settings-card-head split">
          <div>
            <h2 id=${`${id}-title`}>${title}</h2>
            ${text ? html`<p>${text}</p>` : null}
          </div>
          ${action || null}
        </div>`
      : null}
    ${children}
  </section>`;
}

/**
 * A row: `title` and `hint` on the left, the control(s) on the right (`stacked`: below).
 * `status` is a line under the hint (a `Status`, or what it is about).
 */
export function SettingRow({ title, hint, status, stacked = false, class: className = "", children, ...rest }) {
  return html`<div class=${`settings-row${stacked ? " stacked" : ""} ${className}`} ...${rest}>
    <div class="settings-row-text">
      <span class="settings-row-title">${title}</span>
      ${hint ? html`<span class="settings-row-hint">${hint}</span>` : null}
      ${status ? html`<span class="settings-row-status">${status}</span>` : null}
    </div>
    ${children ? html`<div class="settings-row-control">${children}</div>` : null}
  </div>`;
}

/**
 * A text that is kept when it is left or Enter is pressed (Escape gives the old one back):
 * `onCommit(text)` hears of a text that differs from `value`, trimmed.
 */
export function CommitField({ value, onCommit, label, placeholder, mono = false, class: className = "" }) {
  const [draft, setDraft] = useState(null);
  useEffect(() => setDraft(null), [value]);
  const commit = (event) => {
    const text = event.currentTarget.value.trim();
    if (text !== (value ?? "")) Promise.resolve(onCommit(text)).finally(() => setDraft(null));
    else setDraft(null);
  };
  return html`<input
    type="text"
    class=${`input${mono ? " mono" : ""} ${className}`}
    aria-label=${label}
    value=${draft ?? value ?? ""}
    placeholder=${placeholder}
    spellcheck="false"
    onInput=${(event) => setDraft(event.currentTarget.value)}
    onChange=${commit}
    onKeyDown=${(event) => {
      if (event.key === "Enter") event.currentTarget.blur();
      else if (event.key === "Escape") {
        event.stopPropagation();
        setDraft(null);
        event.currentTarget.blur();
      }
    }}
  />`;
}

/** Call `load` every `ms` while the component and the page are shown. */
export function usePolling(load, ms) {
  useEffect(() => {
    const timer = setInterval(() => !document.hidden && load(), ms);
    return () => clearInterval(timer);
  }, []);
}

/** A dot and a few words: tone is "ok", "warn", "error" or "off". */
export function Status({ tone = "off", children }) {
  return html`<span class=${`status ${tone}`}><span class="dot"></span>${children}</span>`;
}

/** A note in a card or between cards: tone "info", "warn" or "error"; `action` is a button. */
export function Note({ tone = "info", icon, action, children }) {
  return html`<div class=${`note ${tone}`} role=${tone === "error" ? "alert" : null}>
    <${Icon} name=${icon || (tone === "info" ? "info" : "warning")} size=${16} />
    <div class="note-text">${children}</div>
    ${action || null}
  </div>`;
}

/** A card that shows its rows only while it is open. */
export function Disclosure({ id, title, hint, open, onToggle, children }) {
  return html`<section class="settings-card">
    <button type="button" class="settings-disclosure" aria-expanded=${String(open)} aria-controls=${id} onClick=${onToggle}>
      <span class="settings-row-text">
        <span class="settings-row-title">${title}</span>
        ${hint ? html`<span class="settings-row-hint">${hint}</span>` : null}
      </span>
      <${Icon} name=${open ? "chevron-up" : "chevron-down"} size=${16} />
    </button>
    ${open ? html`<div id=${id} class="settings-disclosure-body">${children}</div>` : null}
  </section>`;
}

/**
 * "This takes effect after a restart", while a written setting whose name starts with
 * `prefix` waits for the panel to start again; the button does that where the app runs.
 */
export function RestartNote({ prefix }) {
  const { waiting, canRestart } = useStore((s) => ({
    waiting: (s.settings?.restart || []).some((key) => key.startsWith(prefix)),
    canRestart: s.app.available && !s.app.quit,
  }));
  const [busy, setBusy] = useState(false);
  if (!waiting) return null;
  const restart = async () => {
    setBusy(true);
    await restartPanel();
    setBusy(false);
  };
  return html`<${Note}
    tone="warn"
    action=${canRestart
      ? html`<button type="button" class="btn" disabled=${busy} onClick=${restart}>${t("Restart the panel")}</button>`
      : null}
  >
    ${canRestart ? t("This takes effect after a restart of the panel.") : t("This takes effect after Libre Panel is started again.")}
  <//>`;
}
