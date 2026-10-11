// Settings → Sensors: which sources the readings come from and in which order they are
// asked, how LibreHardwareMonitor is read and what it says, how often the sensors are
// read. (The hardware's names are in hardware.js.) The sources are made when the panel
// starts, so a change here takes effect after a restart; the reading interval at once.

import { formatNumber, t } from "../../core/i18n.js";
import { loadSettings, loadSourceStates, saveSettings, useCatalog, useConfig } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html } from "../../lib/preact.js";
import { IconButton, NumberField, Segmented, Toggle } from "../../ui/controls.js";
import { Card, CommitField, Note, RestartNote, SettingRow, Status, usePolling } from "./parts.js";

export const loadSensorsSection = () => Promise.all([loadSettings(), loadSourceStates()]);

// What the people who use Libre Panel are told about each source (the others: their name).
const SOURCES = {
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
  demo: {
    title: () => t("Demo values"),
    hint: () => t("Invented values, to try a theme without real sensors."),
  },
  media: { title: () => t("What is playing"), hint: () => t("The title, the cover and the player of the music that plays.") },
  sky: { title: () => t("Sun and moon"), hint: () => t("Sunrise, sunset and the phase of the moon.") },
};
const ORDER = ["load", "audio", "librehardwaremonitor", "psutil", "presentmon", "calendar", "homeassistant", "mqtt", "demo"];

/** Whether a source can work on this system (`platform` is Python's sys.platform). */
function worksHere(name, platform) {
  if (name === "librehardwaremonitor" || name === "presentmon") return platform === "win32";
  if (name === "audio") return platform === "win32" || platform.startsWith("linux");
  return true;
}

/** The sources in use, in the order they are asked, then those that are not, as rows. */
function sourceRows(config, catalog) {
  const listed = config.sensors.providers;
  const known = new Map(catalog.providers.map((source) => [source.name, source]));
  const used = listed.map((name) => ({ name, on: true, known: known.has(name), source: known.get(name) }));
  const rank = (name) => (ORDER.includes(name) ? ORDER.indexOf(name) : ORDER.length);
  const unused = catalog.providers
    .filter((source) => !listed.includes(source.name) && !catalog.implicit_providers.includes(source.name))
    .sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name))
    .map((source) => ({ name: source.name, on: false, known: true, source }));
  return { used, unused };
}

/** A source switched on goes before psutil, the one that fills in the rest. */
function withSource(listed, name) {
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
      title=${t("Sources")}
      text=${t("Libre Panel asks the sources from the top. For each reading the first source that has one wins, so a quicker or better source goes first.")}
      action=${usual
        ? null
        : html`<button type="button" class="link-btn" onClick=${() => save(() => catalog.default_providers)}>${t("Back to the usual")}</button>`}
    >
      ${used.map(row)}
      ${unused.length ? html`<div class="settings-subhead">${t("Not in use")}</div>` : null}
      ${unused.map((item) => row(item, -1))}
    <//>`;
}

// -- LibreHardwareMonitor ----------------------------------------------------------------------

function LibreHardwareMonitor() {
  const config = useConfig();
  const catalog = useCatalog();
  const states = useStore((s) => s.sourceStates);
  if (!config || !catalog) return null;
  const used = config.sensors.providers.includes("librehardwaremonitor");
  if (catalog.platform !== "win32" && !used) return null;
  const lhm = config.sensors.librehardwaremonitor;
  const state = (states?.sources || []).find((source) => source.name === "librehardwaremonitor");
  let report;
  if (!states?.background) report = t("Its state shows while Libre Panel runs in the background.");
  else if (!used) report = t("Not in use: switch it on under Sources.");
  else if (!state) report = t("Not running yet.");
  return html`<${Card} id="sensor-lhm" title="LibreHardwareMonitor" text=${t("Where the temperatures, the power and the fans of Windows come from.")}>
    <${SettingRow}
      title=${t("How it is read")}
      hint=${t("Automatic reads it inside Libre Panel where that works, and otherwise asks the web server of a LibreHardwareMonitor that you run yourself.")}
    >
      <${Segmented}
        label=${t("How it is read")}
        value=${lhm.mode}
        onChange=${(value) => saveSettings({ "sensors.librehardwaremonitor.mode": value })}
        options=${[
          { value: "auto", label: t("Automatic") },
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
    ${state?.hint ? html`<div class="settings-row"><${Note} tone="warn">${state.hint}<//></div>` : null}
  <//>`;
}

// -- how often ---------------------------------------------------------------------------------

function Interval() {
  const config = useConfig();
  if (!config) return null;
  const ms = config.refresh_ms;
  return html`<${Card} id="sensor-interval">
    <${SettingRow}
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
    <//>
  <//>`;
}

export const sensorGroups = [
  { id: "sources", order: 10, component: Sources, load: loadSensorsSection },
  { id: "lhm", order: 20, component: LibreHardwareMonitor },
  { id: "interval", order: 30, component: Interval },
];
