// Sensors in the design view: friendly names, values as people read them,
// the live readings (GET /api/readings, every two seconds while shown), and
// the picker that finds a sensor by name or key.

import { endpoints } from "../../core/api.js";
import { formatNumber, t } from "../../core/i18n.js";
import { createStore, useSelector } from "../../core/store.js";
import { useStore } from "../../core/state.js";
import { html, useEffect, useMemo, useRef, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { Popover, arrowNavigation } from "../../ui/layer.js";

/** A name people know for the common sensors; others keep their own label. */
export function sensorName(key, label = "") {
  const names = {
    "cpu.load": t("CPU load"),
    "gpu.load": t("GPU load"),
    "mem.load": t("Memory"),
    "swap.load": t("Swap"),
    "disk.load": t("Disk"),
    "cpu.temp": t("CPU temperature"),
    "gpu.temp": t("GPU temperature"),
    "cpu.freq": t("CPU clock"),
    "gpu.freq": t("GPU clock"),
    "cpu.power": t("CPU power"),
    "gpu.power": t("GPU power"),
    "gpu.fan": t("GPU fan"),
    "net.down": t("Download"),
    "net.up": t("Upload"),
    "disk.read": t("Disk read"),
    "disk.write": t("Disk write"),
    "game.fps": t("Game frame rate"),
    "moon.phase": t("Moon phase"),
  };
  return names[key] || label || key;
}

/** A shorter name still, for chips. */
export const shortSensorName = (key) => ({ "mem.load": t("RAM"), "net.down": t("Download") })[key] || sensorName(key);

const BYTES = ["B", "KB", "MB", "GB", "TB"];

/** A reading as people read it: "29 %", "3,4 MB/s", "62 °C". */
export function formatValue(value, unit = "") {
  if (value === null || value === undefined) return "–";
  if (typeof value !== "number") return String(value).slice(0, 40);
  if (unit === "B/s" || unit === "B") {
    let size = Math.abs(value);
    let step = 0;
    while (size >= 1000 && step < BYTES.length - 1) {
      size /= 1000;
      step++;
    }
    const text = formatNumber(Math.sign(value) * size, size < 10 && step ? 1 : 0);
    return `${text} ${BYTES[step]}${unit === "B/s" ? "/s" : ""}`;
  }
  const digits = Math.abs(value) < 10 && !Number.isInteger(value) ? 1 : 0;
  const number = formatNumber(value, digits);
  return unit ? `${number} ${unit}` : number;
}

// -- live readings ---------------------------------------------------------------------

const readings = createStore({ values: {}, history: {}, live: false });
const wanted = new Map(); // key -> how many views want it
const wantedHistory = new Map();
let everything = 0; // views that want every reading (the picker)
let timer = null;

async function poll() {
  const keys = everything ? [] : [...wanted.keys()];
  const history = [...wantedHistory.keys()];
  if (!everything && !keys.length && !history.length) return;
  try {
    const data = await endpoints.readings(keys, history);
    readings.set((state) => ({
      values: { ...state.values, ...data.readings },
      history: { ...state.history, ...data.history },
      live: data.live,
    }));
  } catch {
    // no readings now: the last ones stay
  }
}

function want(map, keys, delta) {
  for (const key of keys) {
    const count = (map.get(key) || 0) + delta;
    if (count > 0) map.set(key, count);
    else map.delete(key);
  }
}

function schedule() {
  const busy = everything || wanted.size || wantedHistory.size;
  if (busy && timer === null) {
    poll();
    timer = setInterval(() => !document.hidden && poll(), 2000);
  } else if (!busy && timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

/** The current values of some sensors (and the recent history of others), kept fresh. */
export function useReadings(keys, historyKeys = [], all = false) {
  const signature = `${all}|${keys.join(",")}|${historyKeys.join(",")}`;
  useEffect(() => {
    want(wanted, keys, 1);
    want(wantedHistory, historyKeys, 1);
    if (all) everything++;
    schedule();
    if (keys.length || all) poll();
    return () => {
      want(wanted, keys, -1);
      want(wantedHistory, historyKeys, -1);
      if (all) everything--;
      schedule();
    };
  }, [signature]);
  return useSelector(readings, (state) => state);
}

/** A small line of a sensor's recent values. */
export function Sparkline({ values, width = 64, height = 24 }) {
  const points = (values || []).filter((v) => typeof v === "number");
  if (points.length < 2) return html`<svg class="sparkline" width=${width} height=${height} aria-hidden="true"></svg>`;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = (width - 2) / (points.length - 1);
  const d = points.map((v, i) => `${i ? "L" : "M"}${(1 + i * step).toFixed(1)} ${(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)}`).join(" ");
  return html`<svg class="sparkline" width=${width} height=${height} viewBox=${`0 0 ${width} ${height}`} aria-hidden="true">
    <path d=${d} fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" />
  </svg>`;
}

// -- the picker --------------------------------------------------------------------------

const GROUPS = () => ({
  cpu: "CPU",
  gpu: "GPU",
  mem: t("Memory"),
  swap: t("Memory"),
  disk: t("Drives"),
  net: t("Network"),
  temp: t("Temperatures"),
  fan: t("Fans"),
  weather: t("Weather"),
  media: t("Music"),
  audio: t("Music"),
  battery: t("Battery"),
  game: t("Game"),
});

/** Common sensors to offer next to the one chosen. */
export const QUICK_SENSORS = ["cpu.load", "gpu.load", "mem.load", "cpu.temp", "gpu.temp", "net.down"];

/** A source's raw reading ("lhm:/gpu-powermonitor/com3/power/0", "hwinfo:..."): listed under
 * the device it is read from, not under its source. */
const isRaw = (key) => /^[a-z]+:/i.test(key);

/**
 * The sensors in groups: the common ones by what they are (CPU, GPU, temperatures, ...), then a
 * source's raw readings by the device they are read from ("WireView Pro II", "NVIDIA GeForce
 * RTX 4090"), each with how many it holds (`device`: collapsed until opened or searched).
 * `query` finds a sensor by its name, its key, its label or its device.
 */
export function sensorGroups(sensors, query = "") {
  const q = query.trim().toLowerCase();
  const names = GROUPS();
  const found = new Map();
  for (const sensor of sensors) {
    const name = sensorName(sensor.key, sensor.label);
    const device = isRaw(sensor.key) && sensor.device ? sensor.device : "";
    if (q && !`${sensor.key} ${name} ${sensor.label} ${sensor.device || ""}`.toLowerCase().includes(q)) continue;
    const prefix = sensor.key.split(/[.:]/)[0];
    const group = device || names[prefix] || prefix.charAt(0).toUpperCase() + prefix.slice(1);
    if (!found.has(group)) found.set(group, { name: group, device: Boolean(device), items: [] });
    found.get(group).items.push({ ...sensor, name });
  }
  return [...found.values()].sort((a, b) => Number(a.device) - Number(b.device));
}

function SensorList({ value, onPick, close }) {
  const sensors = useStore((s) => s.sensors);
  const live = useReadings([], [], true);
  const [query, setQuery] = useState("");
  const [opened, setOpened] = useState(() => new Set());
  const list = useRef(null);
  const groups = useMemo(() => sensorGroups(sensors, query), [sensors, query]);
  // a device's readings show while it is opened, searched for, or holds the sensor chosen
  const shows = (group) => !group.device || query.trim() || opened.has(group.name) || group.items.some((s) => s.key === value);
  const toggle = (name) => setOpened((now) => {
    const next = new Set(now);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    return next;
  });
  const typed = query.trim();
  const custom = typed && /^[!a-z0-9_.*:-]+$/i.test(typed) && !sensors.some((s) => s.key === typed);
  const pick = (key) => {
    onPick(key);
    close();
  };
  return html`
    <label class="search-field">
      <${Icon} name="search" size=${15} />
      <input type="text" autofocus value=${query} placeholder=${t("Find a sensor")} aria-label=${t("Find a sensor")}
        onInput=${(event) => setQuery(event.currentTarget.value)}
        onKeyDown=${(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            list.current?.querySelector('[role="option"]')?.focus();
          } else if (event.key === "Enter" && custom) pick(typed);
        }} />
    </label>
    <div class="sensor-list" role="listbox" ref=${list} aria-label=${t("Sensors")}
      onKeyDown=${(event) => {
        arrowNavigation(event, '[role="option"]');
        if (event.key === "Enter" && event.target.dataset.key) pick(event.target.dataset.key);
      }}>
      ${custom
        ? html`<button type="button" role="option" class="sensor-row" data-key=${typed} onClick=${() => pick(typed)}>
            <span class="sensor-name">${t("Use “{key}”", { key: typed })}</span>
            <span class="sensor-key mono">${t("a key or pattern")}</span>
          </button>`
        : null}
      ${groups.map(
        (group) => html`<div class=${`sensor-group${group.device ? " device" : ""}`} key=${group.name} data-group=${group.name}>
          ${group.device
            ? html`<button type="button" class="sensor-group-title sensor-device" aria-expanded=${String(shows(group))}
                onClick=${() => toggle(group.name)}>
                <${Icon} name=${shows(group) ? "chevron-down" : "chevron-right"} size=${12} />
                <span>${group.name}</span><span class="muted">${group.items.length}</span>
              </button>`
            : html`<div class="sensor-group-title">${group.name}</div>`}
          ${(shows(group) ? group.items : []).map((sensor) => {
            const reading = live.values[sensor.key];
            return html`<button type="button" role="option" key=${sensor.key} data-key=${sensor.key}
              aria-selected=${String(sensor.key === value)} class="sensor-row" onClick=${() => pick(sensor.key)}>
              <span class="sensor-name">${sensor.name}</span>
              <span class="sensor-key mono">${sensor.key}</span>
              <span class=${`sensor-value mono${reading?.sample && live.live ? " sample" : ""}`}
                title=${reading?.sample && live.live ? t("No value on this computer: a sample") : null}>${reading ? formatValue(reading.value, reading.unit) : ""}</span>
            </button>`;
          })}
        </div>`,
      )}
      ${!groups.length && !custom ? html`<p class="muted pad">${t("No sensor of that name")}</p>` : null}
    </div>
    <div class="sensor-foot muted">${live.live ? t("Values from this computer") : t("Sample values")}</div>
  `;
}

/** The picker as a popover at `anchor`. */
export function SensorPicker({ anchor, open, onClose, value, onPick }) {
  return html`<${Popover} anchor=${anchor} open=${open} onClose=${onClose} label=${t("Choose a sensor")} width=${400} class="sensor-picker">
    ${open ? html`<${SensorList} value=${value} onPick=${onPick} close=${onClose} />` : null}
  <//>`;
}

/** The chosen sensor as a button (dot, name, key) that opens the picker. */
export function SensorButton({ value, onPick, compact = false }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  const sensors = useStore((s) => s.sensors);
  const known = sensors.find((sensor) => sensor.key === value);
  const name = value ? sensorName(value, known?.label) : t("No sensor");
  return html`
    <button ref=${anchor} type="button" class=${compact ? "tool-sensor" : "sensor-button"} aria-haspopup="dialog"
      aria-expanded=${String(open)} title=${t("Choose a sensor")} onClick=${() => setOpen(!open)}>
      <span class="dot"></span>
      <span class="sensor-button-name">${name}</span>
      ${compact ? html`<${Icon} name="chevron-down" size=${12} strokeWidth=${2.4} />` : html`<span class="sensor-button-key mono">${value}</span>`}
    </button>
    <${SensorPicker} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} value=${value} onPick=${onPick} />
  `;
}
