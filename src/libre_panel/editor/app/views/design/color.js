// Colours: the theme's palette as swatches first ("@accent": the whole theme
// follows when the palette changes), any colour second.

import { t } from "../../core/i18n.js";
import { useStore } from "../../core/state.js";
import { html, useRef, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { Popover } from "../../ui/layer.js";

export const COLOR = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;

/** A colour value as #rrggbb for the eye ("@accent" looked up in the palette). */
export function resolveColor(value, palette = {}) {
  if (typeof value === "string" && value.startsWith("@")) return palette[value.slice(1)] || "#ff00ff";
  return value || null;
}

const hex6 = (value) => {
  if (!value || !COLOR.test(value)) return "#000000";
  if (value.length <= 5) return `#${[...value.slice(1, 4)].map((c) => c + c).join("")}`;
  return value.slice(0, 7);
};

/** Any colour: a picker, its code and (optionally) none. */
function CustomColor({ value, palette, onChange, optional }) {
  const [text, setText] = useState(value && !String(value).startsWith("@") ? value : resolveColor(value, palette) || "");
  const valid = COLOR.test(text);
  return html`<div class="custom-color">
    <div class="custom-color-row">
      <input type="color" aria-label=${t("Pick a colour")} value=${hex6(resolveColor(value, palette))}
        onInput=${(event) => {
          setText(event.currentTarget.value);
          onChange(event.currentTarget.value);
        }} />
      <input class="input mono" type="text" value=${text} aria-label=${t("Colour code")} spellcheck="false"
        placeholder="#rrggbb" aria-invalid=${String(Boolean(text) && !valid)}
        onInput=${(event) => setText(event.currentTarget.value.trim())}
        onChange=${(event) => COLOR.test(event.currentTarget.value.trim()) && onChange(event.currentTarget.value.trim())}
        onKeyDown=${(event) => event.key === "Enter" && valid && onChange(text)} />
    </div>
    ${Object.keys(palette).length
      ? html`<div class="palette-names" role="list">
          ${Object.entries(palette).map(
            ([name, color]) => html`<button type="button" role="listitem" key=${name} class="palette-name"
              aria-pressed=${String(value === `@${name}`)} onClick=${() => onChange(`@${name}`)}>
              <i style=${{ background: color }}></i><span>${name}</span>
            </button>`,
          )}
        </div>`
      : null}
    ${optional
      ? html`<button type="button" class="btn btn-quiet" aria-pressed=${String(value === null)} onClick=${() => onChange(null)}>
          ${t("No colour")}
        </button>`
      : null}
  </div>`;
}

/**
 * The palette's colours as swatches, the chosen one ringed, and one more for
 * any colour. `optional`: none is a choice too (null).
 */
export function ColorSwatches({ value, onChange, optional = false, label, hint = true, max = 7 }) {
  const palette = useStore((s) => s.doc?.theme?.palette || {});
  const [open, setOpen] = useState(false);
  const more = useRef(null);
  const names = Object.keys(palette).filter((name) => COLOR.test(palette[name]));
  const shown = names.slice(0, max);
  const own = value && !String(value).startsWith("@");
  const picked = typeof value === "string" && value.startsWith("@") ? value.slice(1) : null;
  return html`<div class="swatches" role="group" aria-label=${label}>
    ${shown.map(
      (name) => html`<button type="button" key=${name} class="swatch" title=${name} aria-label=${name}
        aria-pressed=${String(picked === name)} style=${{ "--swatch": palette[name] }} onClick=${() => onChange(`@${name}`)}></button>`,
    )}
    <button ref=${more} type="button" class=${`swatch more${own || (picked && !shown.includes(picked)) ? " own" : ""}`}
      title=${own ? value : t("Another colour")} aria-label=${t("Another colour")} aria-haspopup="dialog" aria-expanded=${String(open)}
      aria-pressed=${String(Boolean(own))} style=${own ? { "--swatch": resolveColor(value, palette) } : null}
      onClick=${() => setOpen(!open)}>
      ${own ? null : html`<${Icon} name="plus" size=${13} />`}
    </button>
    ${optional && value === null ? html`<span class="muted small">${t("none")}</span>` : null}
    ${hint && shown.length && !optional ? html`<span class="muted small swatch-hint">${t("from the look")}</span>` : null}
    <${Popover} anchor=${more} open=${open} onClose=${() => setOpen(false)} label=${t("Colour")} width=${260}>
      <${CustomColor} value=${value} palette=${palette} optional=${optional}
        onChange=${(next) => {
          onChange(next);
          if (next === null || String(next).startsWith("@")) setOpen(false);
        }} />
    <//>
  </div>`;
}

/** A round button showing a colour; it opens the swatches (for the floating toolbar). */
export function ColorButton({ value, onChange, optional = false, label }) {
  const palette = useStore((s) => s.doc?.theme?.palette || {});
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  const shown = resolveColor(value, palette);
  return html`
    <button ref=${anchor} type="button" class="tool-color" aria-label=${label || t("Colour")} title=${label || t("Colour")}
      aria-haspopup="dialog" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      <span class=${`tool-color-dot${shown ? "" : " empty"}`} style=${shown ? { "--swatch": shown } : null}></span>
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${label || t("Colour")} width=${292} placement="bottom-center">
      <div class="pop-section">
        <div class="pop-title">${label || t("Colour")}</div>
        <${ColorSwatches} value=${value} optional=${optional} hint=${false} max=${9}
          onChange=${(next) => {
            onChange(next);
          }} />
      </div>
    <//>
  `;
}
