// A control for each kind of field in the theme's specs (theme/model.py):
// int, number, number?, string, text, color, color?, bool, sensor, format,
// font, asset, rules, icon and "enum:a|b|c". The inspector is built from these.

import { refreshAssets, uploadAsset } from "../../core/actions.js";
import { endpoints } from "../../core/api.js";
import { enumLabel, fieldLabel, iconLabel, t } from "../../core/i18n.js";
import { store, useStore } from "../../core/state.js";
import { html, useState } from "../../lib/preact.js";
import { Field, NumberField, Row, Segmented, Select, TextField, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { ColorSwatches, resolveColor } from "./color.js";
import { SensorButton } from "./sensors.js";

export const FORMAT_HINT = "{value:.0f}{unit} · {value:.1f} · {value:bytes}/s · {value:duration} · {label}";
const STEPS = { opacity: 0.05, glow: 0.05, stroke: 0.1, alpha: 0.05, strength: 0.05, sweep_seconds: 0.1 };
const UNIT_RANGE = new Set(["opacity", "glow", "alpha"]);
const PX = new Set(["x", "y", "w", "h", "size", "font_size", "radius", "thickness", "line_width", "glow_radius", "shadow_offset", "shadow_blur", "outline_width", "letter_spacing", "max_width", "segment_gap", "backdrop_blur"]);

/** A short help text under a field, where the field needs one. */
export function hintFor(widget, key, kind) {
  if (kind === "format") return FORMAT_HINT;
  if (key === "format" && widget?.type === "clock") return t("strftime, e.g. {examples}", { examples: "%H:%M · %H:%M:%S · %A %d %B" });
  if (key === "scale") return t("sqrt/log keep small values visible (network rates)");
  if (key === "hide_if_missing") return t("hide when the sensor has no value (e.g. weather off)");
  if (key === "needs") return t("show only while this sensor has a value (e.g. weather.temperature); with ! in front, only while it has none");
  if (key === "smooth" && widget?.type === "metric") return t("the number glides between readings, together with the bars and rings of its sensor");
  if (key === "seconds" && widget?.type === "graph") return t("video mode: the seconds the curve spans, at any frame rate; 0 = as long as the history readings take");
  if (key === "per_frame") return t("video mode: a point a pixel apart, as SPUR II draws its curves (seconds = width / 25)");
  if (key === "finish") return t("lit: SPUR II's light on rings, bars, graph lines and weather icons; glow then sets their glow");
  return null;
}

// -- fonts and files of the theme ------------------------------------------------------

export function FontControl({ value, onChange, empty = t("Theme font") }) {
  const { fonts, assets } = useStore((s) => ({ fonts: s.specs?.fonts || [], assets: s.doc?.assets || [] }));
  const own = assets.filter((path) => /\.(ttf|otf)$/i.test(path));
  const options = [
    { value: "", label: empty },
    ...fonts.map((font) => ({ value: font, label: font.replace(/^builtin:/, ""), group: t("Built-in") })),
    ...own.map((path) => ({ value: path, label: path, group: t("This theme") })),
  ];
  if (value && !options.some((option) => option.value === value)) options.push({ value, label: value });
  return html`<div class="control-row">
    <${Select} value=${value || ""} options=${options} onChange=${onChange} label=${t("Font")} />
    <button type="button" class="btn btn-quiet square" title=${t("Upload a font…")} aria-label=${t("Upload a font…")}
      onClick=${async () => {
        const path = await uploadAsset(".ttf,.otf");
        if (path) onChange(path);
      }}><${Icon} name="upload" size=${15} /></button>
  </div>`;
}

export function AssetControl({ value, onChange }) {
  const { assets, id } = useStore((s) => ({ assets: s.doc?.assets || [], id: s.doc?.id }));
  const images = assets.filter((path) => /\.(png|jpe?g|gif|webp)$/i.test(path));
  const options = [{ value: "", label: t("none") }, ...images.map((path) => ({ value: path, label: path }))];
  if (value && !images.includes(value)) options.push({ value, label: value });
  return html`<div class="control-row">
    ${value && id && images.includes(value) ? html`<img class="asset-thumb" alt="" src=${endpoints.assetUrl(id, value)} />` : null}
    <${Select} value=${value || ""} options=${options} onChange=${onChange} label=${t("Picture")} />
    <button type="button" class="btn btn-quiet square" title=${t("Upload a picture…")} aria-label=${t("Upload a picture…")}
      onClick=${async () => {
        const path = await uploadAsset(".png,.jpg,.jpeg,.gif,.webp");
        if (path) {
          onChange(path);
          refreshAssets();
        }
      }}><${Icon} name="upload" size=${15} /></button>
  </div>`;
}

// -- colour rules: another colour above a value --------------------------------------------

export function RulesControl({ value, onChange, unit = "" }) {
  const rules = Array.isArray(value) ? value : [];
  const [open, setOpen] = useState(false);
  const palette = useStore((s) => s.doc?.theme?.palette || {});
  const set = (next) => onChange(next.map((rule) => ({ ...rule })));
  const first = rules[0];
  return html`<div class="rules">
    <button type="button" class="rules-summary" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      <span>${t("Warning colour")}</span>
      <span class="rules-value mono" style=${first ? { color: resolveColor(first.color, palette) } : null}>
        ${first ? t("from {value}", { value: `${first.above}${unit ? ` ${unit}` : ""}` }) : t("off")}
        ${rules.length > 1 ? ` +${rules.length - 1}` : ""}
      </span>
      <${Icon} name=${open ? "chevron-up" : "chevron-down"} size=${14} />
    </button>
    ${open
      ? html`<div class="rules-list">
          ${rules.map(
            (rule, index) => html`<div class="rule" key=${index}>
              <span class="muted">${t("from")}</span>
              <${NumberField} label=${t("Threshold")} value=${rule.above} step=${0.5} digits=${1}
                onChange=${(above) => set(rules.map((r, i) => (i === index ? { ...r, above } : r)))} />
              <${ColorSwatches} value=${rule.color} hint=${false} max=${4} label=${t("Colour")}
                onChange=${(color) => color && set(rules.map((r, i) => (i === index ? { ...r, color } : r)))} />
              <button type="button" class="icon-btn" aria-label=${t("Remove rule")} title=${t("Remove rule")}
                onClick=${() => set(rules.filter((_, i) => i !== index))}><${Icon} name="close" size=${14} /></button>
            </div>`,
          )}
          <button type="button" class="btn btn-quiet" onClick=${() =>
            set([...rules, { above: rules.length ? rules[rules.length - 1].above + 10 : 80, color: "warn" in palette ? "@warn" : "#f87171" }])}>
            <${Icon} name="plus" size=${14} /> ${t("Add a colour rule")}
          </button>
        </div>`
      : null}
  </div>`;
}

// -- finishes: how a shape is drawn ------------------------------------------------------------

export const FINISH_NAMES = () => ({ lit: t("Light"), facet: t("Facet"), flat: t("Flat") });

/** The look of a finish, drawn small: a ring or a bar. */
export function FinishPicture({ finish, shape = "ring", size = 46 }) {
  if (shape === "bar") {
    const fill = { lit: "#B9FBF4", facet: "url(#facet)", flat: "#2EE6D6" }[finish];
    return html`<svg width=${size} height="14" viewBox="0 0 46 14" aria-hidden="true">
      <defs><linearGradient id="facet" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5FF0E4" /><stop offset="1" stop-color="#1BA79B" /></linearGradient></defs>
      <rect x="1" y="3" width="44" height="8" rx=${finish === "lit" ? 4 : finish === "flat" ? 2 : 0} fill="#16202A" />
      <rect x="1" y="3" width="29" height="8" rx=${finish === "lit" ? 4 : finish === "flat" ? 2 : 0} fill=${fill} />
    </svg>`;
  }
  const look = {
    lit: { track: "#1C2734", trackWidth: 6, arc: "#9FF7EE", arcWidth: 3, cap: "round" },
    facet: { track: "#16202A", trackWidth: 7, arc: "#2EE6D6", arcWidth: 6, cap: "butt" },
    flat: { track: "#1C2734", trackWidth: 5, arc: "#2EE6D6", arcWidth: 5, cap: "butt" },
  }[finish] || {};
  return html`<svg width=${size} height=${size} viewBox="0 0 46 46" aria-hidden="true">
    <circle cx="23" cy="23" r="17" fill="none" stroke=${look.track} stroke-width=${look.trackWidth} />
    <path d="M23 6 A17 17 0 0 1 39.2 28" fill="none" stroke=${look.arc} stroke-width=${look.arcWidth} stroke-linecap=${look.cap} />
  </svg>`;
}

export const finishShape = (widget) => (["bar", "list", "metric", "text", "graph"].includes(widget?.type) ? "bar" : "ring");

/** The finishes as tiles with a picture each (instead of a select). */
export function FinishTiles({ widget, value, options, onChange }) {
  const names = FINISH_NAMES();
  const order = ["lit", "facet", "flat"].filter((name) => options.includes(name));
  return html`<div class="finish-tiles" role="radiogroup" aria-label=${t("Style")}>
    ${order.map(
      (finish) => html`<button type="button" role="radio" key=${finish} aria-checked=${String(value === finish)}
        class="finish-tile" onClick=${() => onChange(finish)}>
        <${FinishPicture} finish=${finish} shape=${finishShape(widget)} />
        <span>${names[finish]}</span>
      </button>`,
    )}
  </div>`;
}

// -- one field ------------------------------------------------------------------------------

/** Options of an "enum:a|b" kind. */
export const enumOptions = (kind) => kind.replace(/\?$/, "").slice(5).split("|");

/**
 * The control for one field: `kind` from the specs, `value`, `onChange`. Returns
 * the control alone; FieldRow puts it with its label.
 */
export function FieldControl({ widget, name, kind, value, onChange }) {
  const optional = kind.endsWith("?");
  const base = kind.replace(/\?$/, "");
  const label = fieldLabel(name);
  if (base === "int" || base === "number") {
    const unit = UNIT_RANGE.has(name);
    return html`<${NumberField} label=${label} value=${value} allowEmpty=${optional} placeholder=${optional ? t("auto") : undefined}
      step=${base === "int" ? 1 : STEPS[name] || 0.1} digits=${base === "int" ? 0 : 2}
      min=${unit ? 0 : undefined} max=${unit ? 1 : undefined} unit=${PX.has(name) ? "px" : undefined} onChange=${onChange} />`;
  }
  if (base === "bool") return html`<${Toggle} label=${label} checked=${Boolean(value)} onChange=${onChange} />`;
  if (base === "color") return html`<${ColorSwatches} label=${label} value=${value} optional=${optional} onChange=${onChange} />`;
  if (base === "sensor") return html`<${SensorButton} value=${value} onPick=${onChange} />`;
  if (base === "rules") return html`<${RulesControl} value=${value} onChange=${onChange} />`;
  if (base === "font") return html`<${FontControl} value=${value} onChange=${onChange} />`;
  if (base === "asset") return html`<${AssetControl} value=${value} onChange=${onChange} />`;
  if (base === "icon") {
    const icons = store.get().specs?.icons || [];
    return html`<${Select} label=${label} value=${value} onChange=${onChange}
      options=${icons.map((icon) => ({ value: icon, label: icon === "weather" ? t("weather (live)") : iconLabel(icon) }))} />`;
  }
  if (base.startsWith("enum:")) {
    const options = enumOptions(base);
    if (name === "finish") return html`<${FinishTiles} widget=${widget} value=${value} options=${options} onChange=${onChange} />`;
    const short = options.length <= 3 && options.every((option) => enumLabel(option).length <= 11);
    return short
      ? html`<${Segmented} label=${label} value=${value} onChange=${onChange} options=${options.map((option) => ({ value: option, label: enumLabel(option) }))} />`
      : html`<${Select} label=${label} value=${value} onChange=${onChange} options=${options.map((option) => ({ value: option, label: enumLabel(option) }))} />`;
  }
  if (base === "text") return html`<${TextField} label=${label} value=${value} onChange=${onChange} multiline rows=${3} />`;
  return html`<${TextField} label=${label} value=${value} onChange=${onChange} mono=${base === "format"} />`;
}

/** A field with its name: switches as a row, everything else stacked. */
export function FieldRow({ widget, name, kind, value, onChange, label }) {
  const base = kind.replace(/\?$/, "");
  const own = fieldLabel(name);
  const text = label || own.charAt(0).toUpperCase() + own.slice(1);
  const hint = hintFor(widget, name, base);
  const control = html`<${FieldControl} widget=${widget} name=${name} kind=${kind} value=${value} onChange=${onChange} />`;
  if (base === "bool") return html`<${Row} class="row-box" label=${text} hint=${hint}>${control}<//>`;
  if (base === "rules") return control;
  return html`<${Field} label=${text} hint=${hint}>${control}<//>`;
}
