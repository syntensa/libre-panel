// Look's side panel: the colours of the theme, how its shapes are drawn (finish and glow), the
// panel light, the depth (grid and dust) and the notices, each said in a line. Every change is
// one undo step through core/doc.js, and the preview follows; a choice the pointer rests on is
// tried on first (stage.js, peekAt).

import { clone, edit, isModule, setSelection, theme as currentTheme, widgets } from "../../core/doc.js";
import { fieldLabel, formatNumber, t } from "../../core/i18n.js";
import { loadSettings, useConfig } from "../../core/settings.js";
import { store, useStore } from "../../core/state.js";
import { VOLUME_LOOKS, chooseVolumeLook, showVolume, volumeNotice } from "../../core/volume.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { navigate } from "../../shell/views.js";
import { Field, IconButton, NumberField, Section, Segmented, Select, Slider, TextField, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toast } from "../../ui/toast.js";
import { COLOR, ColorSwatches } from "../design/color.js";
import { FINISH_NAMES, FinishPicture, finishesFor } from "../design/fields.js";
import { PaletteEditor } from "../design/inspector.js";
import { inspectorState } from "../design/inspector-state.js";
import { activeLook, applyLook, lookName, looks, specOf, wearLook } from "../design/ops.js";
import { peeks } from "./stage.js";

const cap = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : text);
const label = (key) => cap(fieldLabel(key));
const specs = () => store.get().specs;

/** The range a look field accepts (the server's LOOK_RANGES). */
const range = (block, key) => specs()?.look_ranges?.[block]?.[key] ?? [0, 1];
const percent = (value) => `${formatNumber(Math.round(value * 100))} %`;
const seconds = (value) => `${formatNumber(value, 1, { fixed: true })} s`;
const decimal = (value) => formatNumber(value, 1, { fixed: true });

// -- the colours ---------------------------------------------------------------------------------

function Colours() {
  const theme = currentTheme();
  const active = activeLook(theme);
  const all = Object.entries(looks());
  return html`<${Section} title=${t("Colour world")}>
    ${all.length
      ? html`<div class="look-grid" role="radiogroup" aria-label=${t("Colour world")}>
          ${all.map(
            ([key, item]) => html`<button type="button" role="radio" key=${key} aria-checked=${String(active === key)} class="look-tile"
              title=${t("Use the {name} look", { name: lookName(key) })} onClick=${() => applyLook(key)}
              ...${active === key ? {} : peeks((theme) => wearLook(theme, key))}>
              <span class="look-dots">
                ${["accent", "mem", "net", "surface"].map((role) => html`<i key=${role} style=${{ background: item.palette[role] || item.palette.accent }}></i>`)}
              </span>
              <span>${lookName(key)}</span>
            </button>`,
          )}
        </div>
        <small class="field-hint">${t("The colours of the whole theme in one click.")}</small>`
      : null}
    <details class="look-more">
      <summary>${t("Colours of this theme")}</summary>
      <${PaletteEditor} theme=${theme} />
    <//>
  <//>`;
}

// -- how shapes are drawn: finish and glow --------------------------------------------------------

const GROUPS = () => [
  { id: "ring", title: t("Rings"), types: ["gauge"], shape: "ring" },
  { id: "bar", title: t("Bars"), types: ["bar"], shape: "bar" },
  { id: "graph", title: t("Graphs"), types: ["graph"], shape: "bar" },
  { id: "text", title: t("Lettering"), types: ["text", "metric", "clock", "countdown", "weather"], shape: "text" },
  { id: "icon", title: t("Icons"), types: ["icon", "symbol"], shape: "ring" },
  { id: "module", title: t("Modules"), types: ["module", "downloads"], shape: "ring" },
];

/** The style the modules of the theme wear: the specs' defaults under the theme's own. */
const styleOf = (theme) => ({ ...(specs()?.modules?.style || {}), ...(theme.style || {}) });

/** The glow of some widgets as one number: their mean, and the modules' style counts too. */
function glowOf(members, theme) {
  const values = members.filter((widget) => !isModule(widget)).map((widget) => widget.glow ?? 0);
  if (members.some(isModule)) values.push(styleOf(theme).glow ?? 0);
  return values.length ? Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 20) / 20 : 0;
}

/** Whether a widget's finish field may hold `finish` ("auto" is a module's own: its kind's). */
const allows = (widget, finish) =>
  (specOf(widget.type).finish?.[0] || "").replace(/^enum:/, "").split("|").includes(finish);
const supports = (widget, finish) => (finish === "auto" ? allows(widget, "auto") : finishesFor(widget).includes(finish));
const finishOf = (widget) => widget.finish ?? specOf(widget.type).finish?.[1] ?? "flat";

/** What "automatic" looks like on a tile: a ring that is not drawn yet, left to the module. */
function AutoPicture() {
  return html`<svg width="46" height="46" viewBox="0 0 46 46" aria-hidden="true">
    <circle cx="23" cy="23" r="17" fill="none" stroke="#2B3846" stroke-width="3" stroke-dasharray="5 4" />
    <text x="23" y="29" text-anchor="middle" font-size="17" font-weight="700" fill="#93A1B2">A</text>
  </svg>`;
}

function Finish() {
  const theme = currentTheme();
  const groups = GROUPS()
    .map((group) => ({ ...group, members: widgets().filter((widget) => group.types.includes(widget.type)) }))
    .filter((group) => group.members.length);
  const [chosen, setChosen] = useState("all");
  const group = groups.find((item) => item.id === chosen);
  const members = group ? group.members : groups.flatMap((item) => item.members);
  const scope = group ? group.id : "all";
  const names = { auto: t("Automatic"), ...FINISH_NAMES() };
  const offered = Object.keys(names).filter((finish) => members.some((widget) => supports(widget, finish)));
  const shape = group ? group.shape : "ring";
  // a finish is the theme's when every element that can show it has it
  const current = (finish) => {
    const those = members.filter((widget) => supports(widget, finish));
    return those.length > 0 && those.every((widget) => finishOf(widget) === finish);
  };
  const set = (finish) =>
    edit(`finish:${scope}`, () => {
      for (const widget of members) if (supports(widget, finish)) widget.finish = finish;
    });
  // the same on a copy of the theme, to try it on
  const ids = new Set(members.map((widget) => widget.id));
  const wear = (finish) => (copy) => {
    for (const widget of copy.widgets) if (ids.has(widget.id) && supports(widget, finish)) widget.finish = finish;
  };
  const setGlow = (value) =>
    edit(`glow:${scope}`, (theme) => {
      for (const widget of members) if (!isModule(widget)) widget.glow = value;
      if (members.some(isModule)) theme.style = { ...styleOf(theme), ...(theme.style || {}), glow: value };
    });
  if (!groups.length) {
    return html`<${Section} title=${t("Finish and glow")}>
      <p class="look-about">${t("Add rings, bars, graphs or numbers in Design to choose how they are drawn.")}</p>
    <//>`;
  }
  return html`<${Section} title=${t("Finish and glow")}>
    <p class="look-about">${t("How rings, bars and numbers are drawn, and how much they glow.")}</p>
    ${groups.length > 1
      ? html`<${Segmented} class="look-scope" label=${t("Elements")} value=${scope} onChange=${setChosen}
          options=${[{ value: "all", label: t("All") }, ...groups.map((item) => ({ value: item.id, label: item.title }))]} />`
      : html`<span class="look-about">${groups[0].title}</span>`}
    <div class=${`finish-tiles${offered.length > 4 ? " five" : offered.length > 3 ? " four" : ""}`} role="radiogroup" aria-label=${t("Style")}>
      ${offered.map(
        (finish) => html`<button type="button" role="radio" key=${finish} aria-checked=${String(current(finish))} class="finish-tile"
          onClick=${() => set(finish)} ...${current(finish) ? {} : peeks(wear(finish))}>
          ${finish === "auto"
            ? html`<${AutoPicture} />`
            : html`<${FinishPicture} finish=${finish} shape=${finish === "acrylic" ? "text" : shape} />`}
          <span>${names[finish]}</span>
        </button>`,
      )}
    </div>
    ${new Set(members.map(finishOf)).size > 1 && !offered.some(current)
      ? html`<small class="field-hint">${t("Different on different elements: pick one to make them alike.")}</small>`
      : null}
    <div class="look-slider">
      <span>${label("glow")}</span>
      <${Slider} label=${label("glow")} min=${0} max=${1} step=${0.05} value=${glowOf(members, theme)} format=${percent} onChange=${setGlow} />
    </div>
  <//>`;
}

// -- the blocks of the look (theme "look"): each is on or off --------------------------------------

export function toggleBlock(block, on) {
  edit(`look:${block}`, (theme) => setBlockOn(theme, block, on));
}

const setBlock = (block, key) => (value) =>
  edit(`look:${block}:${key}`, (theme) => {
    theme.look[block] = { ...theme.look[block], [key]: value };
  });

/** A block of the look switched on (with its defaults) or off, on a theme. */
function setBlockOn(theme, block, on) {
  const look = { ...(theme.look || {}) };
  if (on) look[block] = Object.fromEntries(Object.entries(specs().look[block]).map(([key, [, fallback]]) => [key, clone(fallback)]));
  else delete look[block];
  if (Object.keys(look).length) theme.look = look;
  else delete theme.look;
}

/** A titled block with its switch, which tries on what it would do while the pointer rests on
 * it; `render(settings)` draws what is in it while it is on. */
function BlockSection({ block, title, about, render }) {
  const settings = currentTheme()?.look?.[block];
  return html`<${Section} class="look-block" title=${title}
    action=${html`<span class="look-switch" ...${peeks((theme) => setBlockOn(theme, block, !settings))}>
      <${Toggle} label=${title} checked=${Boolean(settings)} onChange=${(on) => toggleBlock(block, on)} />
    </span>`}>
    ${about ? html`<p class="look-about">${about}</p>` : null}
    ${settings ? render(settings) : null}
  <//>`;
}

/** A slider for a number of a block, within the range the theme accepts. */
function BlockSlider({ block, settings, name, step, format, text }) {
  const [min, max] = range(block, name);
  const fallback = specs().look[block][name][1];
  return html`<div class="look-slider">
    <span>${text || label(name)}</span>
    <${Slider} label=${text || label(name)} min=${min} max=${max} step=${step} value=${settings[name] ?? fallback} format=${format}
      onChange=${setBlock(block, name)} />
  </div>`;
}

function BlockNumber({ block, settings, name, step = 1, digits, unit }) {
  const [min, max] = range(block, name);
  return html`<${Field} label=${label(name)}>
    <${NumberField} label=${label(name)} value=${settings[name] ?? specs().look[block][name][1]} min=${min} max=${max} step=${step} digits=${digits} unit=${unit}
      onChange=${(value) => setBlock(block, name)(value ?? specs().look[block][name][1])} />
  <//>`;
}

function OptionalColour({ block, settings, name, text }) {
  return html`<${Field} label=${text || label(name)}>
    <${ColorSwatches} label=${text || label(name)} value=${settings[name] ?? null} optional hint=${false} onChange=${setBlock(block, name)} />
  <//>`;
}

const TRIGGERS = () => [
  ["on_start", t("Start"), t("when the panel starts")],
  ["on_hour", t("Full hour"), t("on the full hour")],
  ["on_track", t("New track"), t("when a new track plays")],
  ["on_transition", t("Transition"), t("when a tube or entrance transition ends")],
];

function SunPath() {
  return html`<svg class="sun-path" width="40" height="22" viewBox="0 0 40 22" aria-hidden="true">
    <path d="M2 20 A18 18 0 0 1 38 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 3" opacity="0.5" />
    <circle cx="27" cy="5.5" r="3.5" fill="#F5C46B" />
  </svg>`;
}

function Light() {
  return html`<${BlockSection} block="light" title=${t("Panel light")}
    about=${t("One light over the whole panel: it glints on edges and sweeps across now and then.")}
    render=${(settings) => html`
      <div class="look-sun">
        <${SunPath} />
        <span class="look-sun-text">
          <strong>${label("sun")}</strong>
          <small>${t("from the weather place; off: noon")}</small>
        </span>
        <${Toggle} label=${label("sun")} checked=${settings.sun} onChange=${setBlock("light", "sun")} />
      </div>
      <${BlockSlider} block="light" settings=${settings} name="strength" step=${0.05} format=${percent} />
      <div class="look-triggers" role="group" aria-label=${t("Sweep of light at")}>
        <span class="muted">${t("Sweep of light at")}</span>
        ${TRIGGERS().map(
          ([key, text, title]) => html`<button type="button" key=${key} class="look-chip" aria-pressed=${String(Boolean(settings[key]))} title=${title}
            onClick=${() => setBlock("light", key)(!settings[key])}>${text}</button>`,
        )}
      </div>
      <${BlockSlider} block="light" settings=${settings} name="sweep_seconds" step=${0.1} format=${seconds} />
      <${OptionalColour} block="light" settings=${settings} name="color" text=${t("Tint of the sweeps")} />
    `} />`;
}

function Grid() {
  return html`<${BlockSection} block="grid" title=${t("Grid background")}
    about=${t("Fine lines behind everything, as on graph paper.")}
    render=${(settings) => html`
      <${BlockSlider} block="grid" settings=${settings} name="alpha" step=${0.05} format=${percent} text=${t("Visibility")} />
      <details class="look-more">
        <summary>${t("Lines and colours")}</summary>
        <div class="quad">
          <${BlockNumber} block="grid" settings=${settings} name="spacing" unit="px" />
          <${BlockNumber} block="grid" settings=${settings} name="major" />
        </div>
        <small class="field-hint">${t("every so many lines a stronger one; 0 = none")}</small>
        <${OptionalColour} block="grid" settings=${settings} name="color" />
        <${OptionalColour} block="grid" settings=${settings} name="major_color" />
        <small class="field-hint">${t("empty: the palette's grid colour, else a breath away from the background")}</small>
      </details>
    `} />`;
}

/** The dust's colours: #rrggbb or @name, separated by commas. */
function DustColours({ dust }) {
  const limit = specs().look_limits?.dust?.colors ?? 8;
  const names = Object.keys(currentTheme().palette || {});
  const usable = (value) => COLOR.test(value) || (value.startsWith("@") && names.includes(value.slice(1)));
  return html`<${Field} label=${t("dust colours")}
    hint=${t("empty: each grain takes the colour of the element it falls from; else #rrggbb or @name, separated by commas")}>
    <${TextField} label=${t("dust colours")} live=${false} mono value=${(dust.colors || []).join(", ")} placeholder=${t("each from its element")}
      onChange=${(text) => {
        const list = text.split(",").map((colour) => colour.trim()).filter(Boolean);
        const bad = list.find((colour) => !usable(colour));
        if (bad || list.length > limit) return toast(t("Not a usable colour list: {text}", { text: bad || text }), { kind: "warn" });
        return setBlock("dust", "colors")(list);
      }} />
  <//>`;
}

/** Rectangles where there is dust, or none. */
function DustAreas({ dust }) {
  const limit = specs().look_limits?.dust?.areas ?? 64;
  const areas = dust.areas || [];
  const set = (next) => setBlock("dust", "areas")(next);
  const change = (index, patch) => set(areas.map((area, i) => (i === index ? { ...area, ...patch } : area)));
  return html`<div class="field">
    <span class="field-label">${t("dust areas")}</span>
    ${areas.map(
      (area, index) => html`<div class="dust-area" key=${index}>
        <div class="quad">
          ${["x", "y", "w", "h"].map(
            (key) => html`<${Field} key=${key} label=${label(key)}>
              <${NumberField} label=${`${t("dust areas")}: ${label(key)}`} value=${area[key]} min=${key === "w" || key === "h" ? 1 : undefined} unit="px"
                onChange=${(value) => change(index, { [key]: value ?? (key === "w" || key === "h" ? 1 : 0) })} />
            <//>`,
          )}
        </div>
        <div class="control-row">
          <${Select} label=${t("dust areas")} value=${area.dust === false ? "no" : "yes"}
            options=${[{ value: "yes", label: t("dust here") }, { value: "no", label: t("no dust here") }]}
            onChange=${(value) => change(index, { dust: value === "yes" })} />
          <${IconButton} icon="close" size=${14} label=${t("Remove area")} onClick=${() => set(areas.filter((_, i) => i !== index))} />
        </div>
      </div>`,
    )}
    ${areas.length < limit
      ? html`<button type="button" class="btn btn-quiet" onClick=${() => {
          const [width, height] = store.get().render.size;
          set([...areas, { x: 0, y: 0, w: Math.round(width / 2), h: height, dust: false }]);
        }}><${Icon} name="plus" size=${14} /> ${t("+ area")}</button>`
      : null}
    <small class="field-hint">${t('rectangles; with a "dust here" area there is dust only inside, else everywhere but in a "no dust" area')}</small>
  </div>`;
}

function Dust() {
  return html`<${BlockSection} block="dust" title=${t("Depth dust")}
    about=${t("Fine grains drift from the elements into the depth. The panel shows them with smooth motion only; the preview here always does.")}
    render=${(settings) => html`
      <${BlockSlider} block="dust" settings=${settings} name="strength" step=${0.1} format=${decimal} />
      <details class="look-more">
        <summary>${t("Grains and areas")}</summary>
        <div class="quad">
          <${BlockNumber} block="dust" settings=${settings} name="count" step=${100} />
          <${BlockNumber} block="dust" settings=${settings} name="rate" />
          <${BlockNumber} block="dust" settings=${settings} name="depth" step=${0.1} digits=${1} />
        </div>
        <small class="field-hint">${t("on a 1920 × 480 panel; other sizes scale with their area")}</small>
        <${DustColours} dust=${settings} />
        <${DustAreas} dust=${settings} />
      </details>
    `} />`;
}

// -- notices --------------------------------------------------------------------------------------

function Notices() {
  const theme = currentTheme();
  const styles = specs()?.toasts || {};
  const current = theme.toast?.style || "";
  const options = [
    { value: "", label: t("Card"), hint: t("a small card with an icon") },
    ...Object.entries(styles).map(([name, info]) => ({ value: name, label: info.label, hint: name === "band" ? t("a glowing edge, the band rolls down from it") : "" })),
  ];
  if (current && !styles[current]) options.push({ value: current, label: t("{name} (not installed)", { name: current }), hint: "" });
  const wear = (name) => (th) => {
    th.toast = { ...(th.toast || {}) };
    if (name) {
      th.toast.style = name;
      th.toast.options = Object.fromEntries(Object.entries(styles[name]?.options || {}).map(([key, [, fallback]]) => [key, clone(fallback)]));
    } else {
      delete th.toast.style;
      delete th.toast.options;
    }
  };
  const choose = (name) => edit("toast:style", wear(name));
  // a style is tried on with a notice on show: one no theme shows by itself (the volume and
  // what plays are left out where the theme has them) and this one does not switch off
  const sample = ["game", "panel", "device"].find((kind) => !(theme.toast?.off || []).includes(kind)) || "game";
  return html`<${Section} title=${t("Notices")}>
    <p class="look-about">${t("How short notices look on the panel, such as the volume, a new track or a warning.")}</p>
    <div class="look-notices" role="radiogroup" aria-label=${t("How notices look")}>
      ${options.map(
        (option) => html`<button type="button" role="radio" key=${option.value} aria-checked=${String(current === option.value)} class="look-notice"
          onClick=${() => choose(option.value)} ...${peeks(wear(option.value), sample)}>
          <strong>${option.label}</strong>
          ${option.hint ? html`<span>${option.hint}</span>` : null}
        </button>`,
      )}
    </div>
    <${Volume} />
  <//>`;
}

// -- the volume: on every screen, in one look for all of them --------------------------------------

/**
 * Whether turning the volume shows on the panel, and how: settings of the notices service, so
 * for every theme at once (not undone with the theme's changes). The music page's screen
 * shows the volume itself and leaves the message out.
 */
function Volume() {
  const config = useConfig();
  useEffect(() => {
    if (!store.get().settings) loadSettings();
  }, []);
  const { on, look } = volumeNotice(config);
  if (on === undefined) return null;
  return html`<div class="look-volume" data-look-volume>
    <div class="look-volume-head">
      <div>
        <strong>${t("Volume")}</strong>
        <span>${t("When you turn it, on every screen; the music page's shows it itself.")}</span>
      </div>
      <${Toggle} label=${t("Show the volume")} checked=${on} onChange=${showVolume} />
    </div>
    <div class="look-notices" role="radiogroup" aria-label=${t("How the volume looks")}>
      ${VOLUME_LOOKS().map(
        (option) => html`<button type="button" role="radio" key=${option.value} aria-checked=${String(look === option.value)} class="look-notice"
          onClick=${() => chooseVolumeLook(option.value)} ...${peeks(() => {}, { kind: "volume", look: option.value })}>
          <strong>${option.label}</strong>
          <span>${option.hint}</span>
        </button>`,
      )}
    </div>
  </div>`;
}

// -- ridge frames: not offered, but a theme that has them can drop them -----------------------------

function Ridges() {
  const rects = widgets().filter((widget) => widget.type === "rect" && widget.ridge);
  if (!rects.length) return null;
  return html`<details class="look-more look-advanced">
    <summary>${t("Advanced")}</summary>
    <p class="look-about">${t("{count} frames are drawn as ridges: raised edges the light catches.", { count: rects.length })}</p>
    <button type="button" class="btn" onClick=${() => edit("look:ridges", () => rects.forEach((widget) => (widget.ridge = "")))}>
      ${t("Make the frames flat")}
    </button>
  </details>`;
}

// -- the panel ------------------------------------------------------------------------------------

export function LookPanel() {
  useStore((s) => [s.rev, s.lang, s.render.size[0], s.render.size[1]]); // the sections read the theme as it is now
  if (!currentTheme()) return null;
  return html`<aside class="look-side" aria-label=${t("Look")}>
    <p class="look-intro">${t("Rest the pointer on a choice to try it on the picture; a click keeps it.")}</p>
    <${Colours} />
    <${Finish} />
    <${Light} />
    <${Grid} />
    <${Dust} />
    <${Notices} />
    <${Ridges} />
    <button type="button" class="link-btn look-all" onClick=${() => {
      setSelection([]);
      inspectorState.set({ page: "details" });
      navigate("design");
    }}>${t("All theme settings")} <${Icon} name="chevron-right" size=${13} /></button>
  </aside>`;
}
