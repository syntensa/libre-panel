// The inspector: what is selected, short. A widget shows its primary fields
// (specs "primary", theme/model.py PRIMARY_FIELDS) in sections; the rest are on
// the "Details" page. With nothing selected it is the theme's settings.

import { PALETTE_NAME, themeName } from "../../core/actions.js";
import { edit, isModule, selectedWidgets, setSelection, theme as currentTheme, widgetById } from "../../core/doc.js";
import { enumLabel, fieldLabel, t, widgetLabel } from "../../core/i18n.js";
import { store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Chips, Field, IconButton, NumberField, Row, Section, Segmented, Select, TextField, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { useSelector } from "../../core/store.js";
import { toast } from "../../ui/toast.js";
import { inspectorState } from "./inspector-state.js";
import { ColorSwatches } from "./color.js";
import { FieldRow, FontControl, AssetControl, FinishTiles, enumOptions } from "./fields.js";
import {
  activeLook,
  align,
  applyLook,
  deleteSelection,
  detachModule,
  reassembleModule,
  selectGroup,
  duplicateSelection,
  copySelection,
  kindName,
  lookName,
  looks,
  moduleKinds,
  renameWidget,
  resizeModuleBy,
  setFlag,
  setModuleKind,
  sourceLabels,
  specOf,
} from "./ops.js";
import { QUICK_SENSORS, SensorButton, Sparkline, formatValue, sensorName, shortSensorName, useReadings } from "./sensors.js";
import { uploadAsset } from "../../core/actions.js";
import { hardwareParts, partOfSensor } from "../../core/hardware.js";
import { versionBase } from "../../core/orientation.js";
import { panelName, currentModel } from "../../shell/panel-popover.js";
import { HardwareNameField } from "../settings/hardware.js";
import { navigate } from "../../shell/views.js";

const cap = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : text);
const label = (key) => cap(fieldLabel(key));

export const TYPE_ICONS = {
  rect: "rect",
  "text": "text",
  metric: "metric",
  bar: "bar",
  gauge: "ring",
  graph: "graph",
  clock: "clock",
  analog: "analog",
  countdown: "countdown",
  moon: "moon",
  image: "image",
  weather: "weather",
  icon: "icon",
  calendar: "calendar",
  list: "list",
  module: "module",
};

export const MODULE_ICONS = {
  clock: "clock",
  date: "calendar",
  weather: "weather",
  ring: "ring",
  stat: "metric",
  graph: "graph",
  bars: "bar",
  network: "download",
  system: "info",
  "text": "text",
  temps: "sun",
  cores: "grid",
  drives: "save",
  processes: "list",
  netinfo: "globe",
  battery: "bar",
  values: "list",
  sun: "sun",
  analog: "analog",
  world: "globe",
  countdown: "countdown",
  image: "image",
  music: "music",
  agenda: "calendar",
  game: "game",
  ping: "pulse",
  timer: "countdown",
  power: "power",
};

export const iconOf = (widget) => (isModule(widget) ? MODULE_ICONS[widget.module] || "module" : TYPE_ICONS[widget.type] || "plugin");

// Which section a primary field goes to; others: "behaviour".
const SECTION_OF = {
  sensor: "shows",
  source: "shows",
  module: "shows",
  "text": "shows",
  format: "shows",
  field: "shows",
  target: "shows",
  part: "shows",
  items: "shows",
  src: "shows",
  slides: "shows",
  icon: "shows",
  timezone: "shows",
  first_day: "shows",
  finish: "style",
  style: "style",
  fit: "style",
  card: "style",
  radius: "style",
  outline: "style",
  gradient: "style",
  color: "colour",
  color2: "colour",
  muted: "colour",
  background: "colour",
  font_size: "size",
  size: "size",
};
const SECTION_TITLES = () => ({
  shows: t("Shows"),
  style: t("Style"),
  colour: t("Colour"),
  size: t("Size"),
  behaviour: t("Behaviour"),
});
const SECTION_ORDER = ["shows", "style", "colour", "size", "behaviour"];

// The details page, in groups.
const GROUPS = () => [
  { id: "position", title: t("Position and size"), short: t("Position"), keys: ["x", "y", "w", "h", "size"] },
  { id: "text", title: t("Text"), short: t("Font"), keys: ["font", "font_size", "align", "letter_spacing", "tabular", "max_width", "fit", "uppercase", "label_font"] },
  {
    id: "scale",
    title: t("Scale and shape"),
    short: t("Scale"),
    keys: ["min", "max", "scale", "start_angle", "end_angle", "thickness", "cap", "ticks", "tick_color", "direction", "segments", "segment_gap", "line_width", "history", "seconds", "per_frame", "inset", "grid", "grid_color", "fill_fade", "fill_alpha", "columns", "max_items", "levels"],
  },
  { id: "effects", title: t("Glow and shadow"), short: t("Glow"), keys: [...(store.get().specs?.effect_fields || []), "glow_area", "ridge"] },
  { id: "advanced", title: t("Advanced"), short: t("Advanced"), keys: ["id", "visible", "locked", "hide_if_missing", "needs"] },
];

/** The primary fields of a widget type: from the specs, else the first few that matter. */
export function primaryFields(type) {
  const specs = store.get().specs;
  const spec = specOf(type);
  const listed = specs?.primary?.[type];
  if (listed) return listed.filter((key) => key in spec);
  const own = Object.keys(specs?.widgets?.[type] || {});
  const wanted = own.filter((key) => ["sensor", "text", "finish", "color"].includes(key));
  return (wanted.length ? wanted : own).slice(0, 4);
}

// -- pieces -------------------------------------------------------------------------------

function Header({ icon, title, subtitle, sensor }) {
  return html`<div class="inspector-head">
    <span class="inspector-icon"><${Icon} name=${icon} size=${20} strokeWidth=${2.2} /></span>
    <div class="inspector-heading">
      <div class="inspector-title">${title}</div>
      ${subtitle ? html`<div class="inspector-subtitle">${subtitle}</div>` : null}
    </div>
    ${sensor ? html`<${SensorSpark} sensor=${sensor} />` : null}
  </div>`;
}

function Footer({ summary, onDetails, onDuplicate, onRemove, removeLabel }) {
  return html`<div class="inspector-foot">
    ${onDetails
      ? html`<button type="button" class="details-button" onClick=${onDetails}>
          <span>${summary}</span><${Icon} name="chevron-right" size=${14} strokeWidth=${2.4} />
        </button>`
      : null}
    ${onRemove
      ? html`<div class="foot-actions">
          ${onDuplicate ? html`<button type="button" class="btn" onClick=${onDuplicate}>${t("Duplicate")}</button>` : null}
          <button type="button" class="btn btn-danger-quiet" onClick=${onRemove}>${removeLabel || t("Remove")}</button>
        </div>`
      : null}
  </div>`;
}

function DetailsHead({ title, onBack }) {
  return html`<div class="inspector-head details">
    <${IconButton} icon="chevron-left" label=${t("Back")} onClick=${onBack} />
    <div class="inspector-heading">
      <div class="inspector-title">${t("Fine-tuning")}</div>
      <div class="inspector-subtitle">${title}</div>
    </div>
  </div>`;
}

/** A sensor's name and its value now, for the header. */
function SensorLine({ sensor }) {
  const live = useReadings(sensor ? [sensor] : [], sensor ? [sensor] : []);
  const sensors = useStore((s) => s.sensors);
  if (!sensor) return null;
  const reading = live.values[sensor];
  const name = sensorName(sensor, sensors.find((item) => item.key === sensor)?.label);
  if (reading?.sample && live.live) {
    // the panel shows nothing for it on this computer: a sample would pretend it does
    return html`<span>${name} · <span class="muted">${t("no value on this computer")}</span></span>`;
  }
  return html`<span>${name}${reading ? html` · <span class="mono strong">${formatValue(reading.value, reading.unit)}</span>` : null}</span>`;
}

function SensorSpark({ sensor }) {
  const live = useReadings([], sensor ? [sensor] : []);
  return sensor ? html`<${Sparkline} values=${live.history[sensor]} />` : null;
}

/** Common sensors next to the chosen one, one click away. */
function QuickSensors({ value, onPick }) {
  const sensors = useStore((s) => s.sensors);
  const known = new Set(sensors.map((sensor) => sensor.key));
  const quick = QUICK_SENSORS.filter((key) => key !== value && known.has(key)).slice(0, 3);
  if (!quick.length) return null;
  return html`<${Chips} label=${t("Other sensors")} value=${value} onChange=${onPick}
    options=${quick.map((key) => ({ value: key, label: shortSensorName(key), title: key }))} />`;
}

// -- a widget --------------------------------------------------------------------------------

/** The hardware's names a widget shows (cpu, gpu, memory): changed where they are seen. */
function namesShown(widget) {
  if (isModule(widget)) {
    if (widget.module === "system") return ["cpu", "gpu"];
    if (widget.source === "sensor") return [partOfSensor(widget.sensor)?.id].filter(Boolean);
    return widget.module === "ring" && ["cpu", "gpu"].includes(widget.source) ? [widget.source] : [];
  }
  return [partOfSensor(widget.sensor)?.id].filter(Boolean);
}

function NameFields({ widget }) {
  const parts = namesShown(widget);
  const labels = Object.fromEntries(hardwareParts().map((part) => [part.id, part.label]));
  return parts.map(
    (part, index) => html`<${HardwareNameField} key=${part} part=${part}
      label=${parts.length > 1 ? `${t("Change the name")}: ${labels[part]}` : t("Change the name")}
      hint=${index === parts.length - 1 ? t("For every theme, not just this one") : null} />`,
  );
}

function widgetChange(widget, key) {
  return (value) => {
    if (key === "id") return renameWidget(widget, value);
    edit(`prop:${widget.id}:${key}`, () => {
      widget[key] = value;
    });
  };
}

function PrimarySections({ widget, keys, spec }) {
  const titles = SECTION_TITLES();
  const sections = {};
  for (const key of keys) {
    const section = SECTION_OF[key] || "behaviour";
    (sections[section] ||= []).push(key);
  }
  return SECTION_ORDER.filter((id) => sections[id]).map((id) => {
    const fields = sections[id];
    return html`<${Section} title=${titles[id]} key=${id}>
      ${fields.map((key) => {
        const [kind] = spec[key];
        const change = widgetChange(widget, key);
        if (kind === "sensor") {
          return html`<div class="field" key=${key}>
              ${fields.length > 1 ? html`<span class="field-label">${label(key)}</span>` : null}
              <${SensorButton} value=${widget[key]} onPick=${change} />
              <${QuickSensors} value=${widget[key]} onPick=${change} />
            </div>
            ${key === "sensor" ? html`<${NameFields} key="names" widget=${widget} />` : null}`;
        }
        if (key === "finish") {
          return html`<${FinishTiles} key=${key} widget=${widget} value=${widget.finish} options=${enumOptions(kind)} onChange=${change} />`;
        }
        if (kind.replace("?", "") === "color" && fields.filter((k) => spec[k][0].startsWith("color")).length === 1) {
          return html`<${ColorSwatches} key=${key} label=${label(key)} value=${widget[key]} optional=${kind.endsWith("?")} onChange=${change} />`;
        }
        return html`<${FieldRow} key=${key} widget=${widget} name=${key} kind=${kind} value=${widget[key]} onChange=${change} />`;
      })}
    <//>`;
  });
}

function DetailFields({ widget, spec, skip }) {
  const groups = GROUPS();
  const used = new Set(skip);
  const sections = [];
  for (const group of groups) {
    const keys = group.keys.filter((key) => key in spec && !used.has(key));
    keys.forEach((key) => used.add(key));
    if (keys.length) sections.push({ ...group, keys });
  }
  const rest = Object.keys(spec).filter((key) => !used.has(key));
  if (rest.length) sections.splice(sections.length - 1, 0, { id: "more", title: t("More"), short: t("More"), keys: rest });
  return sections;
}

const usePage = () => [useSelector(inspectorState, (state) => state.page), (page) => inspectorState.set({ page })];

/** A part of a detached module: all its parts at once, or the module back as it was. */
function DetachedGroup({ widget }) {
  const group = widget.group;
  const module = group ? store.get().doc?.theme?.detached?.[group] : null;
  if (!module) return null;
  return html`<div class="inspector-aside detached-group">
    <span>${t("A part of the {name} module, detached into widgets.", { name: kindName(module.module) })}</span>
    <button type="button" class="link-btn" onClick=${() => selectGroup(group)}>${t("Select all its parts")}</button>
    <button type="button" class="btn" title=${t("Its parts go, with what was changed on them")} onClick=${() => reassembleModule(group)}>
      <${Icon} name="module" size=${15} /> ${t("Put the module back together")}
    </button>
  </div>`;
}

function WidgetInspector({ widget }) {
  const [page, setPage] = usePage();
  const spec = specOf(widget.type);
  const primary = primaryFields(widget.type);
  const details = DetailFields({ widget, spec, skip: primary });
  const sensor = "sensor" in spec ? widget.sensor : null;
  if (page === "details") {
    return html`
      <${DetailsHead} title=${widgetLabel(widget.type)} onBack=${() => setPage("main")} />
      <div class="inspector-body">
        ${details.map(
          (group) => html`<${Section} title=${group.title} key=${group.id}>
            ${group.id === "position"
              ? html`<div class="quad">
                  ${group.keys.map((key) => html`<${Field} key=${key} label=${label(key)}>
                    <${NumberField} label=${label(key)} value=${widget[key]} unit="px" onChange=${widgetChange(widget, key)} />
                  <//>`)}
                </div>`
              : group.keys.map((key) =>
                  key === "id"
                    ? html`<${Field} key=${key} label=${label(key)}><${TextField} label=${label(key)} value=${widget.id} live=${false} mono
                        onChange=${widgetChange(widget, "id")} /><//>`
                    : html`<${FieldRow} key=${key} widget=${widget} name=${key} kind=${spec[key][0]} value=${widget[key]}
                        onChange=${widgetChange(widget, key)} />`,
                )}
          <//>`,
        )}
      </div>
    `;
  }
  const summary = `${t("Fine-tuning")}: ${details
    .map((group) => group.short)
    .slice(0, 3)
    .join(", ")}`;
  return html`
    <${Header}
      icon=${iconOf(widget)}
      title=${widgetLabel(widget.type)}
      subtitle=${sensor ? html`<${SensorLine} sensor=${sensor} />` : html`<span class="mono">${widget.id}</span>`}
      sensor=${sensor}
    />
    <div class="inspector-body">
      <${DetachedGroup} widget=${widget} />
      <${PrimarySections} widget=${widget} keys=${primary} spec=${spec} />
    </div>
    <${Footer} summary=${summary} onDetails=${() => setPage("details")} onDuplicate=${duplicateSelection} onRemove=${deleteSelection} />
  `;
}

// -- a module ---------------------------------------------------------------------------------

// The kinds that hold cards themselves; the cards a slot can hold are every other kind, and
// the network as the Studio's card has it ("net"), the cards after SPUR II's first.
const SLOTS = ["cards", "netcard"];
const CARDS = ["playback", "forecast", "download", "instrument", "net"];

/** A card's name in words (a line of a slot's items: "forecast", "date if weather.temp"). */
const cardOf = (line) => line.split("#")[0].split(" if ")[0].trim();
const cardName = (card) => (card === "net" ? t("Network") : kindName(card));

/** What a card slot holds (its items, one card a line; the first with something to show is on
 * show): named in words, each moved up or taken out, one more from a list. A line's condition
 * ("forecast if weather.temp") stays with its card. It was a box of words a sensor's hint
 * explained ("a sensor (cpu.temp), a pattern"), which a slot does not take. */
function SlotCards({ widget, onChange }) {
  const lines = (widget.items || moduleKinds()[widget.module]?.items || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const set = (next) => onChange(next.join("\n")); // each its own step back (Ctrl+Z)
  const others = Object.keys(moduleKinds()).filter((kind) => !SLOTS.includes(kind) && !CARDS.includes(kind));
  const options = [
    { value: "", label: t("Add a card…") },
    ...CARDS.map((card) => ({ value: card, label: cardName(card), group: t("Cards") })),
    ...others.map((kind) => ({ value: kind, label: kindName(kind), group: t("Modules") })),
  ];
  return html`<${Field} label=${t("Cards")} hint=${t("The first one with something to show is on show: what plays while music plays, else the next.")}>
    <ol class="slot-cards">
      ${lines.map(
        (line, index) => html`<li key=${`${index}:${line}`} class="slot-card">
          <span class="slot-card-name" title=${line}>${cardName(cardOf(line))}</span>
          <${IconButton} icon="arrow-up" size=${14} label=${t("Move up")} disabled=${index === 0}
            onClick=${() => set([...lines.slice(0, index - 1), line, lines[index - 1], ...lines.slice(index + 1)])} />
          <${IconButton} icon="close" size=${14} label=${t("Take out")} disabled=${lines.length < 2}
            onClick=${() => set(lines.filter((_, i) => i !== index))} />
        </li>`,
      )}
    </ol>
    <${Select} key=${lines.length} label=${t("Add a card…")} value="" options=${options}
      onChange=${(card) => card && set([...lines, card])} />
  <//>`;
}

/** The second line of a module's head: what it shows, or the cards a slot holds. */
function moduleSubtitle(widget, sensor, sourceName) {
  if (sensor) return html`<${SensorLine} sensor=${sensor} />`;
  if (sourceName) return sourceName;
  if (widget.module === "cards") {
    const lines = (widget.items || moduleKinds().cards?.items || "").split("\n").map(cardOf).filter(Boolean);
    return lines.map(cardName).join(" · ");
  }
  return null;
}

function ModuleInspector({ widget }) {
  const [page, setPage] = usePage();
  const info = moduleKinds()[widget.module] || { sources: [] };
  const kinds = moduleKinds();
  const change = (key) => widgetChange(widget, key);
  const sources = sourceLabels();
  const sensor = widget.source === "sensor" || (widget.source === "fan" && widget.sensor) ? widget.sensor : null;
  const sourceName = info.sources.length ? (widget.source === "sensor" ? null : sources[widget.source]) : null;

  if (page === "details") {
    const fallbacks = { auto: t("automatic (the disk for a missing GPU)"), none: t("leave the cells empty") };
    for (const s of ["cpu", "gpu", "mem", "disk"]) fallbacks[s] = t("show {source}", { source: sources[s] });
    return html`
      <${DetailsHead} title=${kindName(widget.module)} onBack=${() => setPage("main")} />
      <div class="inspector-body">
        <${Section} title=${t("Shows")}>
          ${!["text", "clock", "date", "moon", "outlook"].includes(widget.module)
            ? html`<${Field} label=${t("Title")}><${TextField} label=${t("Title")} value=${widget.title} placeholder=${t("automatic")} onChange=${change("title")} /><//>`
            : null}
          ${info.sources.length
            ? html`<${Field} label=${t("When its readings are missing")}>
                <${Select} label=${t("When its readings are missing")} value=${widget.fallback} onChange=${change("fallback")}
                  options=${Object.entries(fallbacks).map(([value, text]) => ({ value, label: text }))} />
              <//>`
            : null}
          ${widget.module === "weather" && ["hours", "both", "auto"].includes(widget.forecast)
            ? html`<${Field} label=${t("Hours between columns")}>
                <${NumberField} label=${t("Hours between columns")} value=${widget.step} min=${1} max=${6} onChange=${change("step")} />
              <//>`
            : null}
          ${widget.module === "image"
            ? html`<${Field} label=${t("Seconds per picture")}>
                <${NumberField} label=${t("Seconds per picture")} value=${widget.seconds} min=${1} max=${3600} onChange=${change("seconds")} />
              <//>`
            : null}
        <//>
        <${Section} title=${t("Advanced")}>
          <${Field} label=${label("id")}><${TextField} label=${label("id")} value=${widget.id} live=${false} mono onChange=${change("id")} /><//>
          <${FieldRow} widget=${widget} name="visible" kind="bool" value=${widget.visible !== false} onChange=${change("visible")} />
          <${FieldRow} widget=${widget} name="locked" kind="bool" value=${Boolean(widget.locked)} onChange=${change("locked")} />
          <${FieldRow} widget=${widget} name="needs" kind="sensor" value=${widget.needs} onChange=${change("needs")} />
          <button type="button" class="btn" title=${t("Turn it into single widgets to edit each one")} onClick=${() => detachModule(widget)}>
            <${Icon} name="detach" size=${15} /> ${t("Detach into widgets")}
          </button>
        <//>
      </div>
    `;
  }

  const items = widget.module === "cards"
    ? html`<${SlotCards} widget=${widget} onChange=${(value) => edit(null, () => (widget.items = value))} />`
    : info.items
    ? html`<${Field}
        label=${widget.module === "world" ? t("Places") : t("Readings")}
        hint=${widget.module === "world"
          ? t('one per line: a time zone and a name if you like ("Asia/Tokyo = Tokyo"); empty: as shown')
          : t('one per line: a sensor ("cpu.temp"), a pattern ("temp.*") and a name if you like ("cpu.temp = CPU"); empty: as shown')}
      >
        <${TextField} multiline rows=${4} mono label=${t("Readings")} value=${widget.items} placeholder=${info.items} onChange=${change("items")} />
      <//>`
    : null;
  return html`
    <${Header}
      icon=${iconOf(widget)}
      title=${kindName(widget.module)}
      subtitle=${moduleSubtitle(widget, sensor, sourceName)}
      sensor=${sensor}
    />
    <div class="inspector-body">
      <${Section} title=${t("Shows")}>
        <${Select} label=${t("Module")} value=${widget.module} onChange=${(kind) => setModuleKind(widget, kind)}
          options=${Object.entries(kinds).map(([kind, item]) => ({ value: kind, label: item.name }))} />
        ${info.sources.length
          ? html`<${Chips} label=${t("Shows")} value=${widget.source} onChange=${change("source")}
              options=${info.sources.map((source) => ({ value: source, label: sources[source] ?? source }))} />`
          : null}
        ${(widget.source === "sensor" || widget.source === "fan") && info.sources.length
          ? html`<${FieldRow} widget=${widget} name="sensor" kind="sensor" value=${widget.sensor} onChange=${change("sensor")} />`
          : null}
        ${widget.source === "sensor" && info.sources.length
          ? html`<${FieldRow} widget=${widget} name="format" kind="format" value=${widget.format} onChange=${change("format")} />`
          : null}
        <${NameFields} widget=${widget} />
        ${widget.module === "weather"
          ? html`<${Field} label=${t("Forecast")} hint=${t("shown when the module is large enough: wider or taller shows more")}>
              <${Chips} label=${t("Forecast")} value=${widget.forecast} onChange=${change("forecast")}
                options=${[
                  ["auto", t("automatic")],
                  ["days", t("days")],
                  ["hours", t("hours")],
                  ["both", t("days and hours")],
                  ["off", t("off")],
                ].map(([value, text]) => ({ value, label: text }))} />
            <//>`
          : null}
        ${widget.module === "processes"
          ? html`<${Field} label=${t("Sorted by")}>
              <${Segmented} label=${t("Sorted by")} value=${widget.sort} onChange=${change("sort")}
                options=${[
                  { value: "cpu", label: t("processor time") },
                  { value: "memory", label: t("memory") },
                ]} />
            <//>`
          : null}
        ${items}
        ${widget.module === "cores"
          ? html`<${Field} label=${t("Every thread")} hint=${t("off: a bar for each core, its threads together, where the processor says which they are")}>
              <${Toggle} label=${t("Every thread")} checked=${Boolean(widget.threads)} onChange=${change("threads")} />
            <//>`
          : null}
        ${widget.module === "temps"
          ? html`<${Field} label=${t("Fans")} hint=${t("at most so many, below or beside the temperatures where there is room; 0: none")}>
              <${NumberField} label=${t("Fans")} value=${widget.fans ?? 6} min=${0} max=${12} onChange=${change("fans")} />
            <//>
            ${widget.fans !== 0
              ? html`<${Field} label=${t("Which fans")} hint=${t('one per line: a sensor ("gpu.fan"), a pattern ("fan.*") and a name if you like ("fan.lpc.fan_2 = Front"); empty: the GPU fan first, then the others')}>
                  <${TextField} multiline rows=${3} mono label=${t("Which fans")} value=${widget.fan_items} placeholder=${info.fan_items} onChange=${change("fan_items")} />
                <//>`
              : null}`
          : null}
        ${widget.module === "analog"
          ? html`<${Field} label=${t("Time zone")} hint=${t('e.g. "Asia/Tokyo" or "America/New_York"')}>
              <${TextField} label=${t("Time zone")} value=${widget.timezone} placeholder=${t("this computer's")} onChange=${change("timezone")} />
            <//>`
          : null}
        ${widget.module === "countdown"
          ? html`<${Field} label=${t("Counts down to")} hint=${t('"2026-12-24", "2026-12-24 18:00", or "12-24" for every year; the title names it')}>
              <${TextField} label=${t("Counts down to")} value=${widget.target} placeholder="01-01" onChange=${change("target")} />
            <//>`
          : null}
        ${widget.module === "image"
          ? html`<${Field} label=${t("Pictures")} hint=${t('one per line, from the theme folder; "assets/*.jpg" takes them all; several take turns')}>
              <${TextField} multiline rows=${3} mono label=${t("Pictures")} value=${widget.items}
                placeholder=${["assets/photo.jpg", "assets/*.jpg"].join("\n")} onChange=${change("items")} />
              <button type="button" class="btn btn-quiet" onClick=${async () => {
                const path = await uploadAsset(".png,.jpg,.jpeg,.gif,.webp");
                if (path) change("items")([widget.items, path].filter(Boolean).join("\n"));
              }}><${Icon} name="upload" size=${14} /> ${t("Add a picture…")}</button>
            <//>`
          : null}
        ${widget.module === "text"
          ? html`<${Field} label=${t("Text")}><${TextField} multiline rows=${2} label=${t("Text")} value=${widget.text} onChange=${change("text")} /><//>`
          : null}
      <//>
      <${Section} title=${t("Style")}>
        <${FinishTiles} widget=${widget} value=${widget.finish} options=${enumOptions(specOf("module").finish[0])} onChange=${change("finish")} />
        <${Row} label=${t("Card")}>
          <${Segmented} label=${t("Card")} value=${widget.card} onChange=${change("card")}
            options=${["auto", "on", "off"].map((value) => ({ value, label: enumLabel(value) }))} />
        <//>
      <//>
      <${Section} title=${t("Colour")}>
        <${ColorSwatches} label=${t("Colour")} value=${widget.color} optional onChange=${change("color")} />
        <small class="field-hint">${t("empty: the look's colour")}</small>
      <//>
      <${Section} title=${t("Size")}>
        <div class="size-steps">
          <span class="muted">${t("Wide")}</span>
          <${IconButton} icon="minus" label=${t("Narrower")} onClick=${() => resizeModuleBy(widget, -1, 0)} />
          <strong class="mono">${widget.cols}</strong>
          <${IconButton} icon="plus" label=${t("Wider")} onClick=${() => resizeModuleBy(widget, 1, 0)} />
          <span class="muted">${t("High")}</span>
          <${IconButton} icon="minus" label=${t("Lower")} onClick=${() => resizeModuleBy(widget, 0, -1)} />
          <strong class="mono">${widget.rows}</strong>
          <${IconButton} icon="plus" label=${t("Taller")} onClick=${() => resizeModuleBy(widget, 0, 1)} />
        </div>
        <small class="field-hint">${t("Or drag its edges on the panel: a larger module shows more.")}</small>
      <//>
    </div>
    <${Footer}
      summary=${`${t("Fine-tuning")}: ${[t("Title"), t("Advanced"), t("Detach")].join(", ")}`}
      onDetails=${() => setPage("details")}
      onDuplicate=${duplicateSelection}
      onRemove=${deleteSelection}
    />
  `;
}

// -- several widgets --------------------------------------------------------------------------

/** The reading several parts share (a building block: a fan, its name and its speed), changed
 * for all of them at once, and so are their conditions on it. */
function SharedSensor({ chosen }) {
  const reading = chosen.filter((widget) => typeof widget.sensor === "string" && widget.sensor);
  const sensors = [...new Set(reading.map((widget) => widget.sensor))];
  if (reading.length < 2 || sensors.length !== 1) return null;
  const [old] = sensors;
  const ids = new Set(reading.map((widget) => widget.id));
  const change = (value) => {
    if (!value || value === old) return;
    edit("multi:sensor", (current) => {
      for (const widget of current.widgets) {
        if (!ids.has(widget.id)) continue;
        widget.sensor = value;
        if (widget.needs === old) widget.needs = value;
      }
    });
  };
  return html`<${Section} title=${t("Shows")}>
    <${FieldRow} widget=${reading[0]} name="sensor" kind="sensor" value=${old} onChange=${change}
      label=${t("Sensor of all {count} parts", { count: reading.length })} />
  <//>`;
}

function MultiInspector({ chosen }) {
  const count = chosen.length;
  const ids = chosen.map((widget) => widget.id);
  const free = chosen.filter((widget) => !isModule(widget)).length;
  const tools = [
    ["align-left", "left", t("Align left edges")],
    ["align-center", "center", t("Align horizontal centres")],
    ["align-right", "right", t("Align right edges")],
    ["distribute-h", "distribute-h", t("Distribute horizontally")],
    ["align-top", "top", t("Align top edges")],
    ["align-middle", "middle", t("Align vertical centres")],
    ["align-bottom", "bottom", t("Align bottom edges")],
    ["distribute-v", "distribute-v", t("Distribute vertically")],
  ];
  return html`
    <${Header} icon="select-all" title=${t("{count} widgets", { count })} subtitle=${t("selected together")} />
    <div class="inspector-body">
      <${SharedSensor} chosen=${chosen} />
      <${Section} title=${t("Align")}>
        <div class="align-grid">
          ${tools.map(([icon, mode, text]) => html`<${IconButton} key=${mode} icon=${icon} size=${18} label=${text}
            disabled=${free < 2 || (mode.startsWith("distribute") && free < 3)} onClick=${() => align(mode)} />`)}
        </div>
      <//>
      <${Section} title=${t("Selection")}>
        <div class="button-grid">
          <button type="button" class="btn" onClick=${copySelection}><${Icon} name="duplicate" size=${14} /> ${t("Copy")}</button>
          <button type="button" class="btn" onClick=${() => setFlag(ids, "locked", true)}><${Icon} name="lock" size=${14} /> ${t("Lock all")}</button>
          <button type="button" class="btn" onClick=${() => setFlag(ids, "locked", false)}><${Icon} name="unlock" size=${14} /> ${t("Unlock all")}</button>
          <button type="button" class="btn" onClick=${() => setFlag(ids, "visible")}><${Icon} name="eye-off" size=${14} /> ${t("Hide or show")}</button>
        </div>
      <//>
    </div>
    <${Footer} onDuplicate=${duplicateSelection} onRemove=${deleteSelection} removeLabel=${t("Remove {count}", { count })} />
  `;
}

// -- the theme (nothing selected) -------------------------------------------------------------

function themeChange(key, apply) {
  return (value) => edit(`theme:${key}`, (theme) => apply(theme, value));
}

export function PaletteEditor({ theme }) {
  const palette = theme.palette || {};
  const replace = (from, to) => {
    const walk = (node) => {
      if (Array.isArray(node)) return node.map(walk);
      if (node && typeof node === "object") return Object.fromEntries(Object.entries(node).map(([k, v]) => [k, walk(v)]));
      return node === from ? to : node;
    };
    return walk;
  };
  return html`<div class="palette-editor">
    ${Object.entries(palette).map(
      ([name, color]) => html`<div class="palette-row" key=${name}>
        <input type="color" aria-label=${t("Colour of {name}", { name })} value=${/^#[0-9a-f]{6}/i.test(color) ? color.slice(0, 7) : "#000000"}
          onInput=${(event) => edit(`palette:${name}`, (current) => (current.palette[name] = event.currentTarget.value))} />
        <${TextField} label=${t("Name (a-z, 0-9, - _)")} value=${name} live=${false} mono
          onChange=${(next) => {
            next = next.trim();
            if (next === name) return;
            if (!PALETTE_NAME.test(next) || next in palette) {
              toast(t('"{name}" is not a valid or free palette name', { name: next }), { kind: "error" });
              return;
            }
            edit(null, (current) => {
              current.palette = Object.fromEntries(Object.entries(current.palette).map(([k, v]) => [k === name ? next : k, v]));
              const walk = replace(`@${name}`, `@${next}`);
              current.widgets = walk(current.widgets);
              current.background = walk(current.background);
            });
          }} />
        <span class="mono muted">${color}</span>
        <${IconButton} icon="close" size=${14} label=${t("Remove (uses become the plain colour)")}
          onClick=${() => edit(null, (current) => {
            const walk = replace(`@${name}`, current.palette[name]);
            current.widgets = walk(current.widgets);
            current.background = walk(current.background);
            delete current.palette[name];
          })} />
      </div>`,
    )}
    <button type="button" class="btn btn-quiet" onClick=${() => edit(null, (current) => {
      current.palette = current.palette || {};
      let i = 1;
      while (`color${i}` in current.palette) i++;
      current.palette[`color${i}`] = "#22d3ee";
    })}><${Icon} name="plus" size=${14} /> ${t("Add a colour")}</button>
    <small class="field-hint">${t("Widgets use palette colours as @name; change a colour here and the whole theme follows.")}</small>
  </div>`;
}

/** The looks: one click puts a look's colours and module style on the theme. Look has them too,
 * with the rest of the theme's look; the link goes there. */
function LookTiles({ theme }) {
  const active = activeLook(theme);
  return html`<div class="look-grid" role="radiogroup" aria-label=${t("Colour world")}>
      ${Object.entries(looks()).map(
        ([key, item]) => html`<button type="button" role="radio" key=${key} aria-checked=${String(active === key)} class="look-tile"
          title=${t("Use the {name} look", { name: lookName(key) })} onClick=${() => applyLook(key)}>
          <span class="look-dots">
            ${["accent", "mem", "net", "surface"].map((role) => html`<i key=${role} style=${{ background: item.palette[role] || item.palette.accent }}></i>`)}
          </span>
          <span>${lookName(key)}</span>
        </button>`,
      )}
    </div>
    <small class="field-hint">${t("The colours of the whole theme in one click.")}</small>
    <${ToLook} text=${t("Finish, glow, panel light and notices: in Look")} />`;
}

/** The way to Look, where the theme's look is set in full. */
function ToLook({ text }) {
  return html`<button type="button" class="link-btn inspector-link" onClick=${() => navigate("look")}>
    ${text} <${Icon} name="chevron-right" size=${13} />
  </button>`;
}

function PluginOptions({ options, values, onChange }) {
  return Object.entries(options || {}).map(([key, [kind, fallback]]) => html`<${FieldRow} key=${key} name=${key} kind=${kind}
    value=${values?.[key] ?? fallback} onChange=${(value) => onChange(key, value)} />`);
}

/** What a theme has that is changed now and then: its modules' grid and style, its colours by
 * name, its notices, its timing, a plugin's screen and what is said about it. The look itself
 * (colour world, finish, light, grid background, dust) is Look's, a click away. */
function ThemeDetails({ theme, onBack }) {
  const specs = store.get().specs;
  const grid = useStore((s) => s.render.grid);
  const screens = specs.screens || {};
  const toasts = specs.toasts || {};
  const toast = theme.toast || {};
  const style = { ...specs.modules.style, ...(theme.style || {}) };
  const hasModules = Boolean(theme.grid || theme.style || theme.widgets.some(isModule));
  const set = (key, apply) => themeChange(key, apply);
  const setStyle = (key) => (value) => edit(`style:${key}`, (current) => (current.style = { ...style, ...(current.style || {}), [key]: value }));
  const setToast = (key) => (value) => edit(`toast:${key}`, (current) => (current.toast = { ...(current.toast || {}), [key]: value }));
  const setGrid = (key) => (value) =>
    edit(`grid:${key}`, (current) => (current.grid = { ...(current.grid || { columns: 0, rows: 0, gap: 0, margin: 0 }), [key]: Math.max(0, Math.round(value || 0)) }));
  const screenOptions = [{ value: "", label: t("none") }, ...Object.entries(screens).map(([name, info]) => ({ value: name, label: info.label }))];
  if (theme.screen?.name && !screens[theme.screen.name]) screenOptions.push({ value: theme.screen.name, label: t("{name} (not installed)", { name: theme.screen.name }) });
  const toastOptions = [{ value: "", label: t("card (built in)") }, ...Object.entries(toasts).map(([name, info]) => ({ value: name, label: info.label }))];
  if (toast.style && !toasts[toast.style]) toastOptions.push({ value: toast.style, label: t("{name} (not installed)", { name: toast.style }) });
  return html`
    <${DetailsHead} title=${t("Theme")} onBack=${onBack} />
    <div class="inspector-body">
      <div class="inspector-aside">
        <span>${t("The colour world, the finish, the panel light, the grid background and the depth dust are set in Look.")}</span>
        <${ToLook} text=${t("Open Look")} />
      </div>
      ${hasModules
        ? html`<${Section} title=${t("Grid of the modules")}>
            <div class="quad">
              ${[
                ["columns", t("columns")],
                ["rows", t("rows")],
                ["gap", t("gap")],
                ["margin", t("margin")],
              ].map(([key, text]) => html`<${Field} key=${key} label=${cap(text)}>
                <${NumberField} label=${text} value=${theme.grid?.[key] ?? 0} min=${0} max=${key === "columns" || key === "rows" ? 96 : 200}
                  onChange=${setGrid(key)} />
              <//>`)}
            </div>
            <small class="field-hint">${t("The cells the modules are placed on; 0 = as the panel's size suggests. Now {cols} × {rows} cells.", { cols: grid?.columns ?? "?", rows: grid?.rows ?? "?" })}</small>
          <//>
          <${Section} title=${t("Module style")}>
            <${Field} label=${cap(t("cards"))}>
              <${Segmented} label=${t("cards")} value=${style.card} onChange=${setStyle("card")}
                options=${[["glass", t("glass")], ["flat", t("flat")], ["outline", t("outline")], ["none", t("none")]].map(([value, text]) => ({ value, label: text }))} />
            <//>
            <div class="quad">
              <${Field} label=${cap(t("corners"))}><${NumberField} label=${t("corners")} value=${style.radius} min=${0} max=${100} unit="px" onChange=${setStyle("radius")} /><//>
              <${Field} label=${label("glow")}><${NumberField} label=${label("glow")} value=${style.glow} min=${0} max=${1} step=${0.05} digits=${2} onChange=${setStyle("glow")} /><//>
            </div>
            <${Field} label=${cap(t("backdrop"))}>
              <${Segmented} label=${t("backdrop")} value=${style.backdrop} onChange=${setStyle("backdrop")}
                options=${[{ value: "gradient", label: t("gradient") }, { value: "flat", label: t("plain colour") }]} />
            <//>
            <${Field} label=${cap(t("numbers font"))}><${FontControl} value=${style.display_font} onChange=${setStyle("display_font")} /><//>
            <${Field} label=${cap(t("labels font"))}><${FontControl} value=${style.text_font} onChange=${setStyle("text_font")} /><//>
          <//>`
        : null}
      <${Section} title=${t("Colours of this theme")}><${PaletteEditor} theme=${theme} /><//>
      <${Section} title=${t("Notices")}>
        <${Field} label=${cap(t("position"))} hint=${t("where notices appear on the panel")}>
          <${Select} label=${t("position")} value=${toast.anchor || "top-right"} onChange=${setToast("anchor")}
            options=${(specs.toast_anchors || ["top-right"]).map((value) => ({ value, label: enumLabel(value) }))} />
        <//>
        <${Field} label=${cap(t("seconds shown"))}>
          <${NumberField} label=${t("seconds shown")} value=${toast.seconds ?? 4} min=${0.5} max=${60} step=${0.5} digits=${1} onChange=${setToast("seconds")} />
        <//>
        <${Row} class="row-box" label=${cap(t("one after another"))} hint=${t("off: a new notice of the same or a higher rank takes over at once, a lower one is dropped")}>
          <${Toggle} label=${t("one after another")} checked=${toast.queue ?? true} onChange=${setToast("queue")} />
        <//>
        <${Field} label=${cap(t("hidden kinds"))} hint=${t("kinds of notices this theme does not show, e.g. music")}>
          <${TextField} label=${t("hidden kinds")} value=${(toast.off || []).join(", ")}
            onChange=${(text) => setToast("off")(text.split(",").map((kind) => kind.trim()).filter(Boolean))} />
        <//>
        <${Field} label=${cap(t("style"))} hint=${t("how notices look; plugins can bring more")}>
          <${Select} label=${t("style")} value=${toast.style || ""} options=${toastOptions}
            onChange=${(name) => edit("toast:style", (th) => {
              th.toast = { ...(th.toast || {}) };
              if (name) {
                th.toast.style = name;
                th.toast.options = Object.fromEntries(Object.entries(toasts[name]?.options || {}).map(([k, [, d]]) => [k, d]));
              } else {
                delete th.toast.style;
                delete th.toast.options;
              }
            })} />
        <//>
        ${toast.style
          ? html`<${PluginOptions} options=${toasts[toast.style]?.options} values=${toast.options}
              onChange=${(key, value) => edit(`toast:${key}`, (th) => (th.toast.options = { ...th.toast.options, [key]: value }))} />`
          : null}
      <//>
      <${Section} title=${t("Timing")}>
        <${Field} label=${cap(t("sensor refresh (ms)"))} hint=${t("how often the values are read; 1000 = once a second")}>
          <${NumberField} label=${t("sensor refresh (ms)")} value=${theme.refresh_ms ?? 1000} min=${100} step=${100}
            onChange=${set("refresh", (th, v) => (th.refresh_ms = Math.max(100, v || 1000)))} />
        <//>
        <${Field} label=${cap(t("smoothing (ms)"))} hint=${t("how long bars, rings and gliding numbers take to a new value; 0 = jump")}>
          <${NumberField} label=${t("smoothing (ms)")} value=${theme.animation?.smoothing_ms ?? 400} min=${0} max=${5000} step=${50}
            onChange=${set("smooth", (th, v) => (th.animation = { ...(th.animation || {}), smoothing_ms: v ?? 0 }))} />
        <//>
      <//>
      ${Object.keys(screens).length || theme.screen
        ? html`<${Section} title=${t("screen")}>
            <${Field} hint=${t("drawn by a plugin, under the widgets")}>
              <${Select} label=${t("screen")} value=${theme.screen?.name || ""} options=${screenOptions}
                onChange=${(name) => edit("theme:screen", (th) => {
                  if (!name) delete th.screen;
                  else th.screen = { name, options: Object.fromEntries(Object.entries(screens[name]?.options || {}).map(([k, [, d]]) => [k, d])) };
                })} />
            <//>
            ${theme.screen
              ? html`<${PluginOptions} options=${screens[theme.screen.name]?.options} values=${theme.screen.options}
                  onChange=${(key, value) => edit(`screen:${key}`, (th) => (th.screen.options = { ...th.screen.options, [key]: value }))} />`
              : null}
          <//>`
        : null}
      <${Section} title=${t("About")}>
        <${Field} label=${cap(t("author"))}><${TextField} label=${t("author")} value=${theme.author} onChange=${set("author", (th, v) => (th.author = v))} /><//>
        <${Field} label=${cap(t("license"))} hint=${t("e.g. CC0-1.0, CC-BY-4.0")}>
          <${TextField} label=${t("license")} value=${theme.license} onChange=${set("license", (th, v) => (th.license = v))} />
        <//>
        <${Field} label=${cap(t("description"))}>
          <${TextField} multiline rows=${3} label=${t("description")} value=${theme.description} onChange=${set("description", (th, v) => (th.description = v))} />
        <//>
      <//>
    </div>
  `;
}

/** The theme's background: a picture over a colour. The picture fills the whole panel, so a
 * colour changed under it showed nothing (Lumen, Encore, Almanac, Nimbus): with a picture, the
 * picture comes first, the colour says it lies under it, and the picture goes in one click. */
export function Background({ theme }) {
  const background = theme.background || {};
  const image = background.image;
  const setColor = themeChange("bg", (th, v) => (th.background = { ...(th.background || {}), color: v }));
  const setImage = themeChange("bgimg", (th, v) => (th.background = { ...(th.background || {}), image: v || null }));
  const picture = html`<${Field} key="picture" label=${t("Picture")}>
    <${AssetControl} value=${image} onChange=${setImage} />
  <//>`;
  const colour = html`<${Field} key="colour" label=${image ? t("Colour under the picture") : t("Colour")}>
    <${ColorSwatches} label=${t("Background")} value=${background.color} onChange=${setColor} />
  <//>`;
  if (!image) return html`<${Section} title=${t("Background")}>${colour}${picture}<//>`;
  return html`<${Section} title=${t("Background")}>
    ${picture}
    ${colour}
    <div class="inspector-aside background-under">
      <span>${t("You see it only where the picture is see-through.")}</span>
      <button type="button" class="link-btn" onClick=${() => setImage(null)}>${t("Remove the picture")}</button>
    </div>
  <//>`;
}

/** Nothing selected: the whole theme. What is changed most (its name, colour world, background
 * and font) is here; the rest under Fine-tuning. */
function ThemeInspector({ theme }) {
  const [page, setPage] = usePage();
  const builtin = useStore((s) => s.doc?.builtin); // why saving it asks for a name
  // a theme's version for a panel on end or of a size of its own (Lumen Vertical): the one it
  // goes with
  const versionOf = useStore((s) => ((s.versions || []).some((item) => item.id === s.doc?.id) ? versionBase(s.doc.id) : null));
  const specs = store.get().specs;
  const hasModules = Boolean(theme.grid || theme.widgets.some(isModule));
  if (page === "details") return html`<${ThemeDetails} theme=${theme} onBack=${() => setPage("main")} />`;
  const size = `${theme.display?.width ?? "?"} × ${theme.display?.height ?? "?"}`;
  const more = [hasModules ? t("Grid") : null, t("Colours"), t("Notices"), t("About")].filter(Boolean);
  return html`
    <${Header} icon="frame" title=${theme.name || t("Untitled")} subtitle=${`${size} · ${panelName(currentModel())}`} />
    <div class="inspector-body">
      <p class="inspector-note">
        ${t("The whole theme. Click something on the panel to change just that.")}
        ${versionOf
          ? html`<span class="inspector-version">
              <${Icon} name="portrait" size=${14} />
              <span>${t("The version of {name} for a panel mounted vertically. Mounted horizontally, the panel shows {name}, a design of its own: what you change here stays in this version.", { name: themeName(versionOf) })}</span>
            </span>`
          : null}
        ${builtin ? html`<span class="inspector-builtin">${t("Built in: your changes are saved as a theme of your own.")}</span>` : null}
      </p>
      <${Section} title=${t("Name")}>
        <${TextField} label=${t("Name")} value=${theme.name} onChange=${themeChange("name", (th, v) => (th.name = v))} />
      <//>
      ${Object.keys(looks()).length ? html`<${Section} title=${t("Colour world")}><${LookTiles} theme=${theme} /><//>` : null}
      <${Background} theme=${theme} />
      <${Section} title=${t("Font")}>
        <${FontControl} value=${theme.font === specs.default_font ? "" : theme.font} empty=${t("Default (Barlow Medium)")}
          onChange=${themeChange("font", (th, v) => (th.font = v || specs.default_font))} />
        <small class="field-hint">${t("used by every text widget without its own font")}</small>
      <//>
    </div>
    <${Footer} summary=${`${t("Fine-tuning")}: ${more.join(", ")}`} onDetails=${() => setPage("details")} />
  `;
}

// -- the inspector ------------------------------------------------------------------------------

export function Inspector() {
  const { selection, rev, hasDoc } = useStore((s) => ({ selection: s.selection, rev: s.rev, hasDoc: Boolean(s.doc) }));
  if (!hasDoc) return html`<aside class="inspector" aria-label=${t("Properties")}></aside>`;
  const theme = currentTheme();
  const chosen = selectedWidgets();
  let body;
  if (chosen.length > 1) body = html`<${MultiInspector} chosen=${chosen} key="multi" />`;
  else if (chosen.length === 1 && isModule(chosen[0])) body = html`<${ModuleInspector} widget=${chosen[0]} key=${`m:${chosen[0].id}`} />`;
  else if (chosen.length === 1) body = html`<${WidgetInspector} widget=${chosen[0]} key=${`w:${chosen[0].id}`} />`;
  else body = html`<${ThemeInspector} theme=${theme} key="theme" />`;
  return html`<aside class="inspector" aria-label=${t("Properties")} data-rev=${rev}>${body}</aside>`;
}

export { widgetById, setSelection };
