// Look's side panel: the colours of the theme, how its shapes are drawn (finish and glow), the
// grid background and the notices, each said in a line. Every change is one undo step through
// core/doc.js, and the preview follows; a choice the pointer rests on is tried on first
// (stage.js, peekAt).

import { clone, edit, isModule, setSelection, theme as currentTheme, widgets } from "../../core/doc.js";
import { fieldLabel, formatNumber, t } from "../../core/i18n.js";
import { versionBase } from "../../core/orientation.js";
import { requestRender } from "../../core/preview.js";
import { loadSettings, saveSettings, useConfig } from "../../core/settings.js";
import { store, useStore } from "../../core/state.js";
import { targetTable, useTarget } from "../../core/target.js";
import { VOLUME_LOOKS, chooseVolumeLook, showVolume, volumeNotice } from "../../core/volume.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { navigate } from "../../shell/views.js";
import { Field, NumberField, Section, Segmented, Slider, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { ColorSwatches } from "../design/color.js";
import { FINISH_NAMES, FinishPicture, finishesFor } from "../design/fields.js";
import { Background, PaletteEditor } from "../design/inspector.js";
import { inspectorState } from "../design/inspector-state.js";
import { activeLook, applyLook, lookName, looks, specOf, wearLook } from "../design/ops.js";
import { CardsChoice } from "../home/cards.js";
import { peeks } from "./stage.js";

const cap = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : text);
const label = (key) => cap(fieldLabel(key));
const specs = () => store.get().specs;

/** The range a look field accepts (the server's LOOK_RANGES). */
const range = (block, key) => specs()?.look_ranges?.[block]?.[key] ?? [0, 1];
const percent = (value) => `${formatNumber(Math.round(value * 100))} %`;

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

// -- the theme's own switches (its "switches": frames around every part, ...) --------------------

/** What the theme lets the person switch, each as config.toml has it or as the theme begins with
 * it. A change holds for every screen that has the switch ([switches]), so frames go on or off
 * everywhere at once; one this theme was given by itself ([switches.<theme>]) is taken out. */
function Switches() {
  const config = useConfig();
  useEffect(() => {
    if (!store.get().settings) loadSettings();
  }, []);
  const offered = Object.entries(currentTheme()?.switches || {});
  const id = store.get().doc?.id;
  if (!offered.length || !id || !config) return null;
  const base = versionBase(id) || id;
  const chosen = (name) => config.switches?.[base]?.[name] ?? config.switches?.["*"]?.[name];
  const turn = async (name, on) => {
    const own = config.switches?.[base]?.[name] !== undefined;
    const changes = { [`switches.${name}`]: on, ...(own ? { [`switches.${base}.${name}`]: null } : {}) };
    if (await saveSettings(changes)) requestRender(0); // drawn as switched
  };
  // (each with its name beside it: a switch says its name only to a screen reader)
  return html`<${Section} title=${t("This theme's switches")}>
    ${offered.map(
      ([name, spec]) => html`<div class="look-switch" key=${name}>
        <span>${t(spec.label)}</span>
        <${Toggle} label=${t(spec.label)} checked=${chosen(name) ?? spec.on !== false} onChange=${(on) => turn(name, on)} />
      </div>`,
    )}
    <small class="field-hint">${t("They hold for every screen that has them.")}</small>
  <//>`;
}

// -- the cards of the card slot (config.toml cards), where the theme leaves them to the person ------

function Cards() {
  const follows = widgets().some((widget) => widget.type === "module" && widget.module === "cards" && !String(widget.items || "").trim());
  if (!follows) return null;
  return html`<${Section} title=${t("Cards on the panel")}>
    <small class="field-hint">${t("The choice holds for every theme that leaves the cards to you.")}</small>
    <${CardsChoice} />
  <//>`;
}

// -- the panel ------------------------------------------------------------------------------------

/** A panel beside the first in panel 1's look (Settings → Panels, "Look as panel 1's"): its
 * colours, fonts and module frames are panel 1's there, so what is changed of them here does
 * not show on it. It says so, with the way to a look of its own. */
function SameLookNote() {
  const number = useTarget();
  const same = useStore((s) => targetTable(s)?.same_look === true);
  if (number < 2 || !same) return null;
  const own = () =>
    saveSettings((config) => ({
      panels: (config?.panels || []).map((panel, i) => (i === number - 2 ? { ...panel, same_look: false } : panel)),
    }));
  return html`<div class="look-note" data-same-look>
    <span>${t("Panel {number} takes the colours, fonts and module frames of panel 1's theme: what you change of them here shows on it once it has a look of its own.", { number })}</span>
    <button type="button" class="btn" onClick=${own}>${t("A look of its own")}</button>
  </div>`;
}

export function LookPanel() {
  useStore((s) => [s.rev, s.lang, s.render.size[0], s.render.size[1]]); // the sections read the theme as it is now
  if (!currentTheme()) return null;
  return html`<aside class="look-side" aria-label=${t("Look")}>
    <p class="look-intro">${t("Rest the pointer on a choice to try it on the picture; a click keeps it.")}</p>
    <${SameLookNote} />
    <${Colours} />
    <${Background} theme=${currentTheme()} />
    <${Switches} />
    <${Cards} />
    <${Finish} />
    <${Grid} />
    <${Notices} />
    <button type="button" class="link-btn look-all" onClick=${() => {
      setSelection([]);
      inspectorState.set({ page: "details" });
      navigate("design");
    }}>${t("All theme settings")} <${Icon} name="chevron-right" size=${13} /></button>
  </aside>`;
}
