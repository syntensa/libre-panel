// Form controls in the app's look: switches, segmented choices, chips, number
// and text fields, sliders, and the rows and sections that hold them.

import { formatNumber } from "../core/i18n.js";
import { keyLabel } from "../core/shortcuts.js";
import { html, useEffect, useRef, useState } from "../lib/preact.js";
import { Icon } from "./icons.js";

export function Kbd({ keys }) {
  return keys ? html`<kbd class="kbd">${keyLabel(keys)}</kbd>` : null;
}

/** An icon-only button; `label` is its name for screen readers and its tooltip. */
export function IconButton({ icon, label, size = 16, class: className = "", pressed, ...rest }) {
  return html`<button
    type="button"
    class=${`icon-btn ${className}`}
    aria-label=${label}
    title=${label}
    aria-pressed=${pressed === undefined ? undefined : String(Boolean(pressed))}
    ...${rest}
  >
    <${Icon} name=${icon} size=${size} />
  </button>`;
}

/** An on/off switch (instead of a checkbox). */
export function Toggle({ checked, onChange, label, disabled, id }) {
  return html`<button
    type="button"
    role="switch"
    id=${id}
    class="switch"
    aria-checked=${String(Boolean(checked))}
    aria-label=${label}
    disabled=${disabled}
    onClick=${() => onChange(!checked)}
  >
    <span class="switch-knob"></span>
  </button>`;
}

/** One of a few choices side by side: options [{value, label, icon, title}]. */
export function Segmented({ options, value, onChange, label, class: className = "", disabled }) {
  const move = (event) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    const index = options.findIndex((option) => option.value === value);
    const next = options[(index + step + options.length) % options.length];
    onChange(next.value);
    requestAnimationFrame(() => event.currentTarget?.querySelector('[aria-checked="true"]')?.focus());
  };
  // one button is in the tab order: the chosen one, else (a value none of the options has) the first
  const known = options.some((option) => option.value === value);
  return html`<div class=${`segmented ${className}`} role="radiogroup" aria-label=${label} onKeyDown=${move}>
    ${options.map(
      (option) => html`<button
        type="button"
        key=${String(option.value)}
        role="radio"
        aria-checked=${String(option.value === value)}
        tabindex=${option.value === value || (!known && option === options[0]) ? 0 : -1}
        title=${option.title}
        disabled=${disabled}
        onClick=${() => onChange(option.value)}
      >
        ${option.icon ? html`<${Icon} name=${option.icon} size=${15} />` : null}
        ${option.label ? html`<span>${option.label}</span>` : null}
      </button>`,
    )}
  </div>`;
}

/** Pill buttons that wrap; `value` marks the chosen one (or a list: several). */
export function Chips({ options, value, onChange, label }) {
  const chosen = (option) => (Array.isArray(value) ? value.includes(option.value) : option.value === value);
  return html`<div class="chips" role="group" aria-label=${label}>
    ${options.map(
      (option) => html`<button
        type="button"
        key=${String(option.value)}
        class="chip"
        aria-pressed=${String(chosen(option))}
        title=${option.title}
        onClick=${() => onChange(option.value)}
      >
        ${option.label}
      </button>`,
    )}
  </div>`;
}

const clamp = (value, min, max) => Math.min(max ?? Infinity, Math.max(min ?? -Infinity, value));

/**
 * A number typed or stepped (arrow keys, Shift ×10); it is applied on Enter, on
 * leaving the field and on each step. `allowEmpty`: empty means null ("auto").
 */
export function NumberField({ value, onChange, min, max, step = 1, unit, allowEmpty = false, label, placeholder, digits }) {
  const [text, setText] = useState(null); // null: show the value
  const decimals = digits ?? (String(step).split(".")[1]?.length || 0);
  const shown = text ?? (value === null || value === undefined ? "" : formatNumber(value, decimals, { grouping: false }));
  const commit = (raw) => {
    setText(null);
    const cleaned = raw.trim().replace(",", ".");
    if (cleaned === "") {
      if (allowEmpty) onChange(null);
      return;
    }
    const number = Number(cleaned);
    if (Number.isNaN(number)) return;
    const next = clamp(decimals ? Number(number.toFixed(decimals)) : Math.round(number), min, max);
    if (next !== value) onChange(next);
  };
  const keydown = (event) => {
    if (event.key === "Enter") {
      commit(event.currentTarget.value);
      return;
    }
    const direction = { ArrowUp: 1, ArrowDown: -1 }[event.key];
    if (!direction) return;
    event.preventDefault();
    const base = typeof value === "number" ? value : min ?? 0;
    const next = clamp(base + direction * step * (event.shiftKey ? 10 : 1), min, max);
    setText(null);
    onChange(decimals ? Number(next.toFixed(decimals)) : Math.round(next));
  };
  return html`<span class="number-field">
    <input
      class="input mono"
      type="text"
      inputmode="decimal"
      aria-label=${label}
      value=${shown}
      placeholder=${placeholder}
      spellcheck="false"
      onInput=${(event) => setText(event.currentTarget.value)}
      onChange=${(event) => commit(event.currentTarget.value)}
      onKeyDown=${keydown}
    />
    ${unit ? html`<span class="unit">${unit}</span>` : null}
  </span>`;
}

/** Text that is applied as you type (`live`) or when you leave the field. */
export function TextField({ value, onChange, live = true, multiline = false, rows = 3, label, placeholder, mono = false }) {
  const [draft, setDraft] = useState(null);
  useEffect(() => setDraft(null), [value]);
  const props = {
    class: `input${mono ? " mono" : ""}`,
    "aria-label": label,
    placeholder,
    spellcheck: "false",
    value: draft ?? value ?? "",
    onInput: (event) => {
      const text = event.currentTarget.value;
      if (live) onChange(text);
      else setDraft(text);
    },
    onChange: (event) => !live && onChange(event.currentTarget.value),
    onKeyDown: (event) => {
      if (!multiline && event.key === "Enter" && !live) onChange(event.currentTarget.value);
    },
  };
  return multiline ? html`<textarea rows=${rows} ...${props}></textarea>` : html`<input type="text" ...${props} />`;
}

/** A native select in the app's look: options [{value, label, group}]. */
export function Select({ value, options, onChange, label, class: className = "" }) {
  const groups = [];
  for (const option of options) {
    const group = groups.find((g) => g.name === (option.group || ""));
    if (group) group.options.push(option);
    else groups.push({ name: option.group || "", options: [option] });
  }
  const render = (option) => html`<option value=${option.value} selected=${option.value === value} key=${option.value}>${option.label}</option>`;
  return html`<span class=${`select ${className}`}>
    <select aria-label=${label} onChange=${(event) => onChange(event.currentTarget.value)}>
      ${groups.map((group) => (group.name ? html`<optgroup label=${group.name}>${group.options.map(render)}</optgroup>` : group.options.map(render)))}
    </select>
    <${Icon} name="chevron-down" size=${14} />
  </span>`;
}

/**
 * A slider; `onChange` while it moves, `onCommit` when it is let go. Where it was let go it
 * stays until `onCommit` (it may return a promise) is done, so it does not go back to the old
 * `value` while a panel or the server takes the new one; after that `value` is what it shows.
 */
export function Slider({ value, min = 0, max = 100, step = 1, onChange, onCommit, label, format }) {
  const [moving, setMoving] = useState(null);
  const moves = useRef(0); // a move that began later keeps the slider where the hand is
  const shown = moving ?? value;
  return html`<span class="slider">
    <input
      type="range"
      min=${min}
      max=${max}
      step=${step}
      value=${shown}
      aria-label=${label}
      onInput=${(event) => {
        const next = Number(event.currentTarget.value);
        moves.current += 1;
        setMoving(next);
        onChange?.(next);
      }}
      onChange=${async (event) => {
        const mine = (moves.current += 1);
        try {
          await onCommit?.(Number(event.currentTarget.value));
        } finally {
          if (moves.current === mine) setMoving(null);
        }
      }}
    />
    <output class="mono">${format ? format(shown) : shown}</output>
  </span>`;
}

/** A labelled control stacked: label above, hint below. */
export function Field({ label, hint, children, error }) {
  return html`<div class="field">
    ${label ? html`<span class="field-label">${label}</span>` : null}
    ${children}
    ${hint || error ? html`<small class=${`field-hint${error ? " error" : ""}`}>${error || hint}</small>` : null}
  </div>`;
}

/** A row: the name on the left, the control on the right (as the inspector's switches). */
export function Row({ label, hint, children, class: className = "" }) {
  return html`<div class=${`row ${className}`}>
    <span class="row-label">${label}${hint ? html`<small>${hint}</small>` : null}</span>
    <span class="row-control">${children}</span>
  </div>`;
}

/** A titled group of fields, as in the inspector ("ZEIGT", "STIL", ...). */
export function Section({ title, action, children, class: className = "" }) {
  return html`<section class=${`section ${className}`}>
    ${title || action ? html`<div class="section-head"><h3 class="section-title">${title}</h3>${action || null}</div>` : null}
    ${children}
  </section>`;
}

/** Calls `handler` once the user is idle for `ms` after the last call. */
export function useDebounced(handler, ms) {
  const timer = useRef(null);
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => () => clearTimeout(timer.current), []);
  return (...args) => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => latest.current(...args), ms);
  };
}
