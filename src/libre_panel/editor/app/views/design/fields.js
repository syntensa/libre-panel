// A control for each kind of field in the theme's specs (theme/model.py):
// int, number, number?, string, text, color, color?, bool, sensor, format,
// font, asset, rules, icon, box? and "enum:a|b|c". The inspector is built from these.

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
const STEPS = { opacity: 0.05, glow: 0.05, stroke: 0.1, alpha: 0.05, strength: 0.05, sweep_seconds: 0.1, speed: 0.25 };
const UNIT_RANGE = new Set(["opacity", "glow", "alpha"]);
const PX = new Set(["x", "y", "w", "h", "size", "font_size", "radius", "thickness", "line_width", "glow_radius", "shadow_offset", "shadow_blur", "outline_width", "letter_spacing", "max_width", "segment_gap", "backdrop_blur"]);

/** A short help text under a field, where the field needs one. */
export function hintFor(widget, key, kind) {
  if (kind === "format") return FORMAT_HINT;
  if (key === "format" && widget?.type === "clock") return t("strftime, e.g. {examples}", { examples: "%H:%M · %H:%M:%S · %A %d %B" });
  if (key === "scale") return t("sqrt/log keep small values visible (network rates)");
  if (key === "hide_if_missing") return t("hide when the sensor has no value (e.g. weather off)");
  if (key === "needs") return t("show only while this sensor has a value (e.g. weather.temperature); with ! in front, only while it has none");
  if (key === "hide_unused") return t("fans: none that has stood still since Libre Panel started (a header of the mainboard with nothing on it); the graphics card's stays");
  if (key === "smooth" && widget?.type === "metric") return t("the number glides between readings, together with the bars and rings of its sensor");
  if (key === "seconds" && widget?.type === "image") return t("a video or an animated picture plays to its end, round by round, at least this long");
  if (key === "speed" && widget?.type === "image") return t("of its videos and animated pictures: 0.5 is half as fast, 2 twice");
  if (key === "seconds" && widget?.type === "graph") return t("video mode: the seconds the curve spans, at any frame rate; 0 = a pixel per point, 25 a second, as SPUR II's graphs (the most fluid)");
  if (key === "sensor" && widget?.type === "module" && widget.source === "fan") return t("which fan; empty: the graphics card's (gpu.fan)");
  if (key === "per_frame") return t("older themes: a point a pixel apart, as every graph whose seconds are 0 now");
  if (key === "digits") return t("what changes on the second: it jumps, rolls (the old digit up and out, the new one in from below) or fades, as SPUR II's clocks do");
  if (key === "digits_seconds") return t("how long a change takes after the second, at most 1 (SPUR II's: 0.42)");
  if (key === "end_dot" && widget?.type === "bar") return t("a dot of this radius at the bar's end, its head; 0 = none (time.second runs through the minute)");
  if (key === "fill_alpha") return t("how opaque the fill is, 0 to 1; 0 = as before (a flat fill 0.25, a fade 0.45 at its top)");
  if (key === "finish") {
    return [
      t("lit: SPUR II's light on rings, bars, graph lines, texts and weather icons; glow then sets their glow"),
      t("facet: a flat top with a lit, bevelled rim (text from 20 px, bars from 6 px)"),
      t("acrylic: lettering of acrylic lit from within, text from 20 px"),
    ].join(" · ");
  }
  if (key === "max_rpm") return t("the speed at which the ring is full; the rotor turns the faster the closer it gets");
  if (key === "glow_area") return t("x, y, w, h on the panel: with the lit finish the glow stays inside (a bar's worked out as SPUR II's glow zones); empty: all around");
  if (key === "ridge") return t("a name makes the rect a ridge that catches the light; rects with the same name are one ridge");
  if (key === "inset") return t("the line stays a line's width inside the box; off: the range spans the box, as SPUR II's charts");
  if (key === "direction" && widget?.type === "graph") return t("down: the curve hangs from the top and is filled from there, below an upward one it mirrors it");
  if (key === "rotate") {
    return widget?.type === "module"
      ? t("degrees clockwise, all of it about its middle")
      : t("degrees clockwise, about its middle; a text about the point it is aligned at, so a changing value stays put");
  }
  return null;
}

// -- a turn ------------------------------------------------------------------------------------

/** How far a widget is turned, degrees clockwise: typed as you like, or in steps of 15° with
 * the buttons and the arrow keys (Shift: 150°). */
export function TurnControl({ label, value, onChange }) {
  const turn = (by) => onChange((((Number(value) || 0) + by) % 360 + 360) % 360);
  return html`<div class="control-row">
    <${NumberField} label=${label} value=${value || 0} step=${15} digits=${1} min=${-360} max=${360} unit="°" onChange=${onChange} />
    <button type="button" class="btn btn-quiet square" title=${t("Turn 15° to the left")} aria-label=${t("Turn 15° to the left")}
      onClick=${() => turn(-15)}><${Icon} name="turn-left" size=${15} /></button>
    <button type="button" class="btn btn-quiet square" title=${t("Turn 15° to the right")} aria-label=${t("Turn 15° to the right")}
      onClick=${() => turn(15)}><${Icon} name="turn-right" size=${15} /></button>
  </div>`;
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
        const path = await uploadAsset(".png,.jpg,.jpeg,.gif,.webp,.mp4,.webm,.mov,.m4v,.mkv");
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

export const FINISH_NAMES = () => ({ lit: t("Light"), facet: t("Facet"), acrylic: t("Acrylic"), flat: t("Flat") });
const FINISH_ORDER = ["lit", "facet", "acrylic", "flat"];

/** The finishes that show on a widget (specs.finishes; any other draws it flat), and the
 * one it has, in the order of the tiles. */
export function finishesFor(widget) {
  const shows = store.get().specs?.finishes;
  return FINISH_ORDER.filter((name) => name === "flat" || name === widget?.finish || !shows || shows[name]?.includes(widget?.type));
}

/** The look of a finish, drawn small: a ring, a bar or lettering. */
export function FinishPicture({ finish, shape = "ring", size = 46 }) {
  if (shape === "text") {
    const fill = { lit: "#C9FCF6", facet: "url(#facet-text)", acrylic: "#EFFFFD", flat: "#2EE6D6" }[finish] || "#2EE6D6";
    return html`<svg width=${size} height=${size} viewBox="0 0 46 46" aria-hidden="true">
      <defs>
        <linearGradient id="facet-text" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#F2FFFE" /><stop offset="0.55" stop-color="#7FA5A8" /><stop offset="1" stop-color="#D5ECEC" />
        </linearGradient>
        <filter id="lit-text" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="2.4" /></filter>
      </defs>
      ${finish === "lit" ? html`<text x="23" y="32" text-anchor="middle" font-size="25" font-weight="700" fill="#2EE6D6" filter="url(#lit-text)">42</text>` : null}
      <text x="23" y="32" text-anchor="middle" font-size="25" font-weight="700" fill=${fill}
        stroke=${finish === "acrylic" ? "#5FF0E4" : finish === "facet" ? "#2C3A40" : "none"} stroke-width=${finish === "acrylic" ? 1.1 : 0.6}>42</text>
    </svg>`;
  }
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

const LETTERING = ["text", "metric", "clock", "countdown", "weather"];

export const finishShape = (widget) =>
  LETTERING.includes(widget?.type) ? "text" : ["bar", "list", "graph"].includes(widget?.type) ? "bar" : "ring";

/** The finishes as tiles with a picture each (instead of a select). */
export function FinishTiles({ widget, value, options, onChange }) {
  const names = FINISH_NAMES();
  const order = finishesFor(widget).filter((name) => options.includes(name));
  return html`<div class=${`finish-tiles${order.length > 3 ? " four" : ""}`} role="radiogroup" aria-label=${t("Style")}>
    ${order.map(
      (finish) => html`<button type="button" role="radio" key=${finish} aria-checked=${String(value === finish)}
        class="finish-tile" onClick=${() => onChange(finish)}>
        <${FinishPicture} finish=${finish} shape=${finish === "acrylic" ? "text" : finishShape(widget)} />
        <span>${names[finish]}</span>
      </button>`,
    )}
  </div>`;
}

// -- an area on the panel ---------------------------------------------------------------------

/** x, y, w, h on the panel (a "box" field), or none where the field may be empty. */
export function BoxControl({ widget, label, value, optional, onChange }) {
  const box = Array.isArray(value) && value.length === 4 ? value : null;
  const parts = t("x, y, w, h").split(/,\s*/);
  if (!box) {
    // the box the widget is drawn in, with room around it
    const [x, y, w, h] = store.get().render.boxes?.[widget?.id] || [widget?.x || 0, widget?.y || 0, widget?.w || 100, widget?.h || 40];
    const start = [Math.max(0, Math.round(x - 8)), Math.max(0, Math.round(y - 8)), Math.round(w + 16), Math.round(h + 16)];
    return html`<button type="button" class="btn btn-dashed" onClick=${() => onChange(start)}>
      <${Icon} name="plus" size=${14} /> ${t("Set an area")}
    </button>`;
  }
  const set = (index) => (number) => {
    if (!Number.isFinite(number)) return;
    const next = [...box];
    next[index] = Math.round(number);
    if (next[2] > 0 && next[3] > 0) onChange(next);
  };
  return html`<div class="box-field">
    <div class="quad">
      ${box.map((number, index) => html`<${Field} key=${index} label=${parts[index] || ""}>
        <${NumberField} label=${`${label}: ${parts[index] || index}`} value=${number} min=${index > 1 ? 1 : undefined} unit="px" onChange=${set(index)} />
      <//>`)}
    </div>
    ${optional
      ? html`<button type="button" class="btn btn-quiet" onClick=${() => onChange(null)}><${Icon} name="close" size=${14} /> ${t("Remove the area")}</button>`
      : null}
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
  if (name === "rotate") return html`<${TurnControl} label=${label} value=${value} onChange=${onChange} />`;
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
  if (base === "box") return html`<${BoxControl} widget=${widget} label=${label} value=${value} optional=${optional} onChange=${onChange} />`;
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
