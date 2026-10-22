// Settings → Sensors: where the readings come from (the sources, in the order they are asked),
// the sensor service (Windows), and under Advanced how often the sensors are read and how
// LibreHardwareMonitor is reached and what it says. (The hardware's names are in hardware.js,
// Linux' setup in setup.js.) The sources are made when the panel starts, so a change here takes
// effect after a restart; the reading interval at once.

import { formatNumber, t } from "../../core/i18n.js";
import {
  loadSettings,
  loadSourceStates,
  loadUnlock,
  runUnlock,
  saveSettings,
  useCatalog,
  useConfig,
} from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html, useState } from "../../lib/preact.js";
import { IconButton, NumberField, Segmented, Toggle } from "../../ui/controls.js";
import { confirmDialog } from "../../ui/dialog.js";
import { Icon } from "../../ui/icons.js";
import { Card, CommitField, Disclosure, RestartNote, SettingRow, Status, usePolling } from "./parts.js";

export const loadSensorsSection = () => Promise.all([loadSettings(), loadSourceStates()]);

// What the people who use Libre Panel are told about each source (the others: their name).
export const SOURCES = {
  load: {
    title: () => t("Fast load"),
    hint: () => t("The load of the processor and the graphics card, ten times a second, so bars and graphs follow it as it happens."),
  },
  audio: {
    title: () => t("Volume"),
    hint: () => t("The volume and the mute of the default output (Windows, Linux)."),
  },
  librehardwaremonitor: {
    title: () => "LibreHardwareMonitor",
    hint: () => t("Temperatures, power, clocks and fans of the processor, the graphics card and the mainboard (Windows)."),
  },
  linux: {
    title: () => t("Linux sensors"),
    hint: () => t("Temperatures, power and fans of the processor, the graphics card, the mainboard and the drives, read from the kernel and from NVIDIA's driver (Linux)."),
  },
  psutil: {
    title: () => t("System"),
    hint: () => t("Processor, memory, drives, network, battery and the busiest programs; temperatures and fans where the system has them."),
  },
  presentmon: {
    title: () => t("Game frame rate"),
    hint: () => t("The frames per second of the game you play, through PresentMon (Windows; the download carries it)."),
  },
  calendar: {
    title: () => t("Calendars"),
    hint: () => t("Appointments from the address of an iCal calendar. Set up in config.toml."),
  },
  homeassistant: {
    title: () => "Home Assistant",
    hint: () => t("The entities of your Home Assistant. Set up in config.toml."),
  },
  mqtt: { title: () => "MQTT", hint: () => t("What your devices publish. Set up in config.toml.") },
  json: {
    title: () => t("Values from JSON"),
    hint: () => t("What the address of a NAS, a router or another PC answers, or a file. Set up in config.toml."),
  },
  nut: {
    title: () => t("UPS"),
    hint: () => t("The battery, the runtime and the load of a UPS, through Network UPS Tools (NUT) on this PC, a NAS or a Raspberry Pi. Set up in config.toml."),
  },
  demo: {
    title: () => t("Demo values"),
    hint: () => t("Invented values, to try a theme without real sensors."),
  },
  media: { title: () => t("What is playing"), hint: () => t("The title, the cover and the player of the music that plays.") },
  sky: { title: () => t("Sun and moon"), hint: () => t("Sunrise, sunset and the phase of the moon.") },
};
const ORDER = ["load", "audio", "librehardwaremonitor", "linux", "psutil", "presentmon", "calendar", "homeassistant", "mqtt", "json", "nut", "demo"];

/** Whether a source can work on this system (`platform` is Python's sys.platform). */
function worksHere(name, platform) {
  if (name === "librehardwaremonitor" || name === "presentmon") return platform === "win32";
  if (name === "linux") return platform.startsWith("linux");
  if (name === "audio") return platform === "win32" || platform.startsWith("linux");
  return true;
}

/** The sources in use, in the order they are asked, then those that are not (the ones that work
 * on this system first), as rows. */
function sourceRows(config, catalog) {
  const listed = config.sensors.providers;
  const known = new Map(catalog.providers.map((source) => [source.name, source]));
  const used = listed.map((name) => ({ name, on: true, known: known.has(name), source: known.get(name) }));
  const rank = (name) => (ORDER.includes(name) ? ORDER.indexOf(name) : ORDER.length);
  const unused = catalog.providers
    .filter((source) => !listed.includes(source.name) && !catalog.implicit_providers.includes(source.name))
    .sort((a, b) => {
      const works = worksHere(b.name, catalog.platform) - worksHere(a.name, catalog.platform);
      return works || rank(a.name) - rank(b.name) || a.name.localeCompare(b.name);
    })
    .map((source) => ({ name: source.name, on: false, known: true, source }));
  return { used, unused };
}

/** A source switched on goes before psutil, the one that fills in the rest. */
export function withSource(listed, name) {
  const at = listed.indexOf("psutil");
  return at < 0 || name === "psutil" ? [...listed, name] : [...listed.slice(0, at), name, ...listed.slice(at)];
}

function SourceRow({ row, index, count, used, state, platform, move, toggle }) {
  const info = SOURCES[row.name];
  const title = info ? info.title() : row.name;
  let hint = info ? info.hint() : "";
  if (!info && row.source) hint = row.source.builtin ? "" : t("From {source}", { source: row.source.source });
  if (!row.known) hint = t("Not installed here any more. Switching it off takes it out of config.toml.");
  const works = worksHere(row.name, platform);
  const status = [];
  if (!works) status.push(html`<${Status}>${t("Not on this system")}<//>`);
  else if (row.on && state) {
    status.push(
      html`<${Status} tone=${state.problem ? "warn" : "ok"}>${t("{count} readings", { count: state.readings })}<//>`,
    );
  }
  const only = row.on && (count === 1 || (count === 2 && row.name !== "psutil" && used.includes("psutil")));
  return html`<div class=${`settings-row source-row${row.on ? "" : " unused"}`} data-source=${row.name}>
    <div class="source-rank" aria-hidden="true">${row.on ? index + 1 : ""}</div>
    <div class="settings-row-text">
      <span class="settings-row-title">${title}</span>
      ${hint ? html`<span class="settings-row-hint">${hint}</span>` : null}
      ${row.on && state?.hint ? html`<span class="settings-row-status"><${Status} tone="warn">${state.hint}<//></span>` : null}
    </div>
    <div class="settings-row-control">
      ${status}
      ${row.on
        ? html`<span class="source-moves">
            <${IconButton} icon="arrow-up" label=${t("Ask {name} earlier", { name: title })} disabled=${index === 0} onClick=${() => move(row.name, -1)} />
            <${IconButton} icon="arrow-down" label=${t("Ask {name} later", { name: title })} disabled=${index === count - 1} onClick=${() => move(row.name, 1)} />
          </span>`
        : null}
      <${Toggle}
        label=${title}
        checked=${row.on}
        disabled=${(!works && !row.on) || only}
        onChange=${(on) => toggle(row.name, on)}
      />
    </div>
  </div>`;
}

function Sources() {
  const config = useConfig();
  const catalog = useCatalog();
  const states = useStore((s) => s.sourceStates);
  const [more, setMore] = useState(false); // the sources not in use, folded away
  usePolling(loadSourceStates, 3000);
  if (!config || !catalog) return null;
  const listed = config.sensors.providers;
  const { used, unused } = sourceRows(config, catalog);
  const running = new Map((states?.sources || []).map((source) => [source.name, source]));
  // the list is worked out from what config.toml has when the write comes: two quick clicks
  // follow each other
  const save = (change) => saveSettings((now) => ({ "sensors.providers": change([...now.sensors.providers]) }));
  const move = (name, step) =>
    save((list) => {
      const from = list.indexOf(name);
      const to = from + step;
      if (from < 0 || to < 0 || to >= list.length) return list;
      [list[from], list[to]] = [list[to], list[from]];
      return list;
    });
  const toggle = (name, on) =>
    save((list) => (on ? (list.includes(name) ? list : withSource(list, name)) : list.filter((item) => item !== name)));
  const usual = JSON.stringify(listed) === JSON.stringify(catalog.default_providers);
  const row = (item, index) => html`<${SourceRow}
    key=${item.name}
    row=${item}
    index=${index}
    count=${used.length}
    used=${listed}
    state=${running.get(item.name)}
    platform=${catalog.platform}
    move=${move}
    toggle=${toggle}
  />`;
  return html`<${RestartNote} prefix="sensors." />
    <${Card}
      id="sensor-sources"
      title=${t("Where the readings come from")}
      text=${t("Each one brings a kind of reading to the panel; switched off, those stay empty. Where two bring the same reading, the one higher up is used.")}
      action=${usual
        ? null
        : html`<button type="button" class="link-btn" onClick=${() => save(() => catalog.default_providers)}>${t("Back to the usual")}</button>`}
    >
      ${used.map(row)}
      ${unused.length
        ? html`<button type="button" class="settings-disclosure small sources-more" aria-expanded=${String(more)} onClick=${() => setMore(!more)}>
            <span>${t("More sources")} · ${unused.length}</span>
            <${Icon} name=${more ? "chevron-up" : "chevron-down"} size=${14} />
          </button>`
        : null}
      ${more ? unused.map((item) => row(item, -1)) : null}
    <//>`;
}

// -- values from JSON ([sensors.json]) ----------------------------------------------------------

/** "name = value" lines as a table, and back. */
const fromLines = (text) =>
  Object.fromEntries(
    text
      .split("\n")
      .map((line) => line.split("="))
      .filter((parts) => parts.length >= 2 && parts[0].trim())
      .map(([key, ...rest]) => [key.trim(), rest.join("=").trim()]),
  );
const toLines = (table) => Object.entries(table || {}).map(([key, value]) => `${key} = ${value}`).join("\n");

/** A text of several lines, written when it is left. */
function LinesField({ value, onCommit, label, placeholder }) {
  const [draft, setDraft] = useState(null);
  return html`<textarea
    class="input mono"
    rows="3"
    aria-label=${label}
    placeholder=${placeholder}
    spellcheck="false"
    value=${draft ?? value}
    onInput=${(event) => setDraft(event.currentTarget.value)}
    onBlur=${(event) => {
      const text = event.currentTarget.value;
      setDraft(null);
      if (text !== value) onCommit(text);
    }}
  ></textarea>`;
}

function JsonSource({ source, index, change, remove }) {
  const set = (key) => (value) => change(index, key, value);
  return html`<div class="json-source" data-json-source=${source.name || index}>
    <${SettingRow} title=${t("Name")} hint=${t("The readings are json.{name}.<field>.", { name: source.name || "…" })}>
      <${CommitField} label=${t("Name")} value=${source.name} mono placeholder="nas" onCommit=${set("name")} />
    <//>
    <${SettingRow} title=${t("Address or file")} hint=${source.headers ? t("Its headers (a token, a password) stay as config.toml has them.") : t("http:// or https://, or the path of a file.")}>
      <${CommitField} label=${t("Address or file")} value=${source.where} mono placeholder="http://192.168.1.10/api/values" onCommit=${set("where")} />
    <//>
    <${SettingRow} title=${t("Fields")} hint=${t("One per line: a name = its path in the answer. Empty: every value it has.")} stacked>
      <${LinesField} label=${t("Fields")} value=${toLines(source.fields)} placeholder=${"cpu = cpu\ntemp = sensors[0].value"} onCommit=${(text) => set("fields")(fromLines(text))} />
    <//>
    <${SettingRow} title=${t("Units")} hint=${t("One per line: a name = its unit.")} stacked>
      <${LinesField} label=${t("Units")} value=${toLines(source.units)} placeholder=${"cpu = %\ntemp = °C"} onCommit=${(text) => set("units")(fromLines(text))} />
    <//>
    <div class="row-action"><button type="button" class="link-btn" onClick=${() => remove(index)}>${t("Remove this source")}</button></div>
  </div>`;
}

function JsonSources() {
  const config = useConfig();
  if (!config || !config.sensors.providers.includes("json")) return null;
  const json = config.sensors.json || { update_s: 30, sources: [] };
  // each change made to the sources as the write before it left them: the address and then
  // the fields at once keep both
  const write = (edit) =>
    saveSettings((latest) => ({
      "sensors.json.sources": edit(latest?.sensors?.json?.sources || []).map(({ name, where, fields, units }) => ({ name, where, fields, units })),
    }));
  const change = (index, key, value) =>
    write((sources) => sources.map((item, n) => (n === index ? { ...item, [key]: value } : item)));
  const remove = (index) => write((sources) => sources.filter((_, n) => n !== index));
  const add = () =>
    write((sources) => {
      const taken = new Set(sources.map((source) => source.name));
      let n = sources.length + 1;
      while (taken.has(`source${n}`)) n += 1;
      return [...sources, { name: `source${n}`, where: "http://", fields: {}, units: {} }];
    });
  return html`<${Card}
    id="json-sources"
    title=${t("Values from JSON")}
    text=${t("What an address answers, or a file holds, as readings for any module: a NAS, a router, another PC. Nothing to sign up for.")}
    action=${html`<button type="button" class="btn" onClick=${add}>${t("Add a source")}</button>`}
  >
    ${json.sources.length
      ? json.sources.map((source, index) => html`<${JsonSource} key=${index} source=${source} index=${index} change=${change} remove=${remove} />`)
      : html`<p class="muted">${t("No source yet.")}</p>`}
    <${SettingRow} title=${t("Asked every")}>
      <${NumberField} label=${t("Asked every")} value=${json.update_s} min=${2} max=${3600} unit="s" onChange=${(value) => saveSettings({ "sensors.json.update_s": value })} />
    <//>
  <//>`;
}

// -- the sensor service (Windows) ---------------------------------------------------------------

/** The service as Windows has it, as a status mark. */
function serviceStatus(service) {
  if (!service) return html`<${Status}>${t("Not set up")}<//>`;
  if (service.disabled) return html`<${Status} tone="warn">${t("Switched off")}<//>`;
  if (service.state === "running") return html`<${Status} tone="ok">${t("Running")}<//>`;
  return html`<${Status} tone="warn">${t("Not running")}<//>`;
}

async function removeService() {
  const sure = await confirmDialog({
    title: t("Remove the sensor service?"),
    message: t(
      "CPU temperature and power, the mainboard's fans and the temperatures of memory and drives are then read only while Libre Panel runs as administrator. The PawnIO driver stays installed.",
    ),
    confirm: t("Remove"),
    danger: true,
  });
  if (sure) await runUnlock("remove");
}

function SensorService() {
  const info = useStore((s) => s.unlockInfo);
  if (!info || info.platform !== "win32") return null;
  const busy = Boolean(info.job?.running);
  const service = info.service;
  let actions = null;
  if (info.available && !service) {
    actions = html`<button type="button" class="btn btn-primary" disabled=${busy} onClick=${() => runUnlock("unlock")}>
      ${busy ? t("Waiting for Windows…") : t("Unlock all sensors")}
    </button>`;
  } else if (info.available) {
    actions = html`<button type="button" class="btn" disabled=${busy} onClick=${() => runUnlock("unlock")}>${t("Set up again")}</button>
      <button type="button" class="btn" disabled=${busy} onClick=${removeService}>${t("Remove")}</button>`;
  }
  const driver = info.pawnio === null ? html`<${Status} tone="warn">${t("Not installed")}<//>` : html`<${Status} tone="ok">${info.pawnio || t("Installed")}<//>`;
  const rates = {
    yes: html`<${Status} tone="ok">${t("Allowed")}<//>`,
    "sign-in": html`<${Status} tone="warn">${t("From your next sign-in")}<//>`,
    no: html`<${Status}>${t("Not allowed")}<//>`,
  }[info.frame_rates];
  return html`<${Card}
    id="sensor-service"
    title=${t("Sensor service")}
    text=${t("A small Windows service reads the sensors that need administrator rights, so that Libre Panel itself runs without them. It only reads: it changes no hardware setting.")}
  >
    <${SettingRow}
      title=${t("Sensor service")}
      hint=${info.available || service
        ? t("Reads CPU, GPU and mainboard temperatures, power, fan speeds, memory and drives with the rights this needs.")
        : t("The sensor service comes with the Windows downloads (setup or zip).")}
      status=${serviceStatus(service)}
    >
      ${actions}
    <//>
    <${SettingRow} title=${t("PawnIO driver")} hint=${t("Signed and open source: the service reads the CPU and the mainboard through it.")} status=${driver} />
    ${rates
      ? html`<${SettingRow} title=${t("Game frame rates")} hint=${t("Recording them needs membership in Performance Log Users, which unlocking gives you.")} status=${rates} />`
      : null}
  <//>`;
}

// -- advanced: how often, and LibreHardwareMonitor ----------------------------------------------

/** How LibreHardwareMonitor is reached (Windows, or where it is in use) and what it says. */
function LibreHardwareMonitor({ config }) {
  const states = useStore((s) => s.sourceStates);
  const used = config.sensors.providers.includes("librehardwaremonitor");
  const lhm = config.sensors.librehardwaremonitor;
  const state = (states?.sources || []).find((source) => source.name === "librehardwaremonitor");
  let report;
  if (!states?.background) report = t("Its state shows while Libre Panel runs in the background.");
  else if (!used) report = t("Not in use: switch it on above.");
  else if (!state) report = t("Not running yet.");
  return html`<div id="sensor-lhm">
    <div class="settings-subhead">LibreHardwareMonitor</div>
    <${SettingRow}
      title=${t("How it is read")}
      hint=${t("Automatic reads it through the sensor service where that is set up, else inside Libre Panel, and otherwise asks the web server of a LibreHardwareMonitor that you run yourself.")}
    >
      <${Segmented}
        label=${t("How it is read")}
        value=${lhm.mode}
        onChange=${(value) => saveSettings({ "sensors.librehardwaremonitor.mode": value })}
        options=${[
          { value: "auto", label: t("Automatic") },
          { value: "service", label: t("Sensor service") },
          { value: "inprocess", label: t("Inside Libre Panel") },
          { value: "webserver", label: t("Web server") },
        ]}
      />
    <//>
    ${lhm.mode !== "inprocess"
      ? html`<${SettingRow} title=${t("Address of the web server")} hint=${t("Where LibreHardwareMonitor answers, when its Remote Web Server runs.")}>
          <${CommitField}
            label=${t("Address of the web server")}
            value=${lhm.url}
            mono
            onCommit=${(text) => saveSettings({ "sensors.librehardwaremonitor.url": text })}
          />
        <//>`
      : null}
    ${report ? html`<div class="settings-row"><span class="settings-row-hint">${report}</span></div>` : null}
    ${state && state.details.length
      ? html`<div class="settings-row stacked">
          <dl class="state-list">
            ${state.details.map(([name, value]) => html`<div key=${name}><dt>${name}</dt><dd>${value}</dd></div>`)}
          </dl>
        </div>`
      : null}
  </div>`;
}

/** How often the sensors are read: the theme's choice, or a fixed interval. */
function Interval({ ms }) {
  return html`<${SettingRow}
    title=${t("Reading interval")}
    hint=${ms === null ? t("Automatic: each theme says how often it needs the readings.") : t("Every {seconds} s, whatever the theme says.", { seconds: formatNumber(ms / 1000, 2) })}
  >
    <${NumberField}
      label=${t("Reading interval")}
      value=${ms}
      min=${100}
      max=${60000}
      step=${50}
      unit="ms"
      allowEmpty
      placeholder=${t("Automatic")}
      onChange=${(value) => saveSettings({ refresh_ms: value })}
    />
    <span class="row-action">
      ${ms !== null ? html`<button type="button" class="link-btn" onClick=${() => saveSettings({ refresh_ms: null })}>${t("Automatic")}</button>` : null}
    </span>
  <//>`;
}

/** What few people change: how often the sensors are read, and how LibreHardwareMonitor is
 * reached (Windows). Folded away; what it says when readings are missing shows above. */
function Advanced() {
  const config = useConfig();
  const catalog = useCatalog();
  if (!config || !catalog) return null;
  const lhm = catalog.platform === "win32" || config.sensors.providers.includes("librehardwaremonitor");
  return html`<${Disclosure}
    id="sensor-advanced"
    title=${t("Advanced")}
    hint=${lhm ? t("How often the sensors are read, and how LibreHardwareMonitor is reached.") : t("How often the sensors are read.")}
  >
    <${Interval} ms=${config.refresh_ms} />
    ${lhm ? html`<${LibreHardwareMonitor} config=${config} />` : null}
  <//>`;
}

export const sensorGroups = [
  { id: "sources", order: 10, component: Sources, load: loadSensorsSection },
  { id: "service", order: 15, component: SensorService, load: loadUnlock }, // Windows only
  { id: "json", order: 20, component: JsonSources }, // with the source of values from JSON
  { id: "advanced", order: 90, component: Advanced }, // after the hardware's names
];
