// Live (#/live): what the panel shows now and how it runs. The picture on the panel, its numbers
// (frame rate against the target, data rate, drawing time, queue, faults), the sensor sources and
// the services with their switches, test notices and the end of the log. It uses the console
// API (docs/ARCHITECTURE.md) and polls it while it is shown. Built on the shell: see
// ../../README.md.

import { appAction } from "../../core/app.js";
import { registerProvider } from "../../core/commands.js";
import { control, useLivePicture, usePolled } from "../../core/console.js";
import { t } from "../../core/i18n.js";
import { store, useStore } from "../../core/state.js";
import { statTiles } from "../../core/stats.js";
import { html, useEffect, useRef, useState } from "../../lib/preact.js";
import { modeName, panelName, stateText } from "../../shell/panel-popover.js";
import { registerView } from "../../shell/views.js";
import { Slider, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toast, toastError } from "../../ui/toast.js";
import { connectedModel } from "../home/panel.js";

// -- names ------------------------------------------------------------------------------------------

// What the sensor sources and services are called for people, and what they are for; any other
// (a plugin's) keeps the name it has. Written as texts the catalog finds, not looked up.
const SOURCE_NAMES = () => ({
  librehardwaremonitor: ["LibreHardwareMonitor", t("temperatures, power and fans")],
  load: [t("Load"), t("CPU and GPU load, ten times a second")],
  audio: [t("Volume"), t("what the default output plays at")],
  psutil: [t("System"), t("processor, memory, disk and network")],
  media: [t("Media"), t("what is playing")],
  sky: [t("Sun and moon"), t("where they stand")],
  "open-meteo": [t("Weather"), "Open-Meteo"],
  calendar: [t("Calendar"), t("your appointments")],
  homeassistant: ["Home Assistant", ""],
  mqtt: ["MQTT", ""],
  presentmon: ["PresentMon", t("frame rates of games")],
  demo: [t("Demo values"), t("made-up readings")],
  services: [t("Services"), t("what the services publish")],
  hardware: [t("Hardware names"), t("the names of the processor, the graphics card and the memory")],
});

const SERVICE_NAMES = () => ({
  notices: [t("Notices"), t("volume, tracks, warnings")],
  game: [t("Game mode"), t("Steam and a list of games")],
  autopilot: [t("Autopilot"), t("rules that choose the theme")],
  devices: [t("Devices"), t("USB, drives, controllers")],
  memory: [t("Round report"), t("records a game and reports after it")],
  cooling: [t("Cooling"), t("checks FanControl's curves")],
  downloads: [t("Downloads"), t("Steam and the browsers")],
});

const SERVICE_STATES = () => ({
  stopped: t("stopped"),
  failed: t("failed"),
  pending: t("changing"),
  "not-installed": t("not installed"),
});

// -- the picture and the numbers ------------------------------------------------------------------

function Picture({ state, background }) {
  const live = useLivePicture({ width: 960, every: 200, on: background && state === "showing" });
  let note = null;
  if (!background) note = t("There is no picture: this editor does not run the panel.");
  else if (live.src === null) {
    note =
      state === "paused"
        ? t("The panel is paused: there is no picture.")
        : state === "error"
          ? t("The panel needs attention: there is no picture.")
          : t("There is no picture yet.");
  }
  return html`<div class="live-picture">
    ${live.src && background ? html`<img src=${live.src} alt=${t("The picture on the panel")} />` : null}
    ${note ? html`<p class="live-picture-note">${note}</p>` : null}
  </div>`;
}

/** A tile's note by its parts ("USB 0 · panel 0 · ffmpeg 0"): a narrow tile wraps between them. */
const noteParts = (note) => note.split(" · ").flatMap((part, i) => [i ? " · " : "", html`<span class="live-tile-part">${part}</span>`]);

function Tiles({ stats }) {
  return html`<div class="live-tiles" role="list" aria-label=${t("How the panel is doing")}>
    ${statTiles(stats).map(
      (tile) => html`<div class=${`live-tile ${tile.level}`} role="listitem" key=${tile.id} data-tile=${tile.id} title=${tile.title}>
        <span class="live-tile-label">${tile.label}</span>
        <b class="mono">${tile.value}</b>
        <small>${noteParts(tile.note)}</small>
      </div>`,
    )}
  </div>`;
}

// The log's lines: "2026-10-11 09:12:04,123 INFO libre_panel.app: text" as "09:12:04  text",
// warnings and errors marked.
const LOG_LINE = /^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d)[.,]\d+ (\w+) ([\w.]+): (.*)$/;

function logLine(text) {
  const found = LOG_LINE.exec(text);
  if (!found) return { text, level: "" };
  const [, time, level, name, message] = found;
  return { text: `${time}  ${message}`, level: level === "WARNING" ? "warn" : level === "ERROR" || level === "CRITICAL" ? "bad" : "", title: name };
}

function LogBox() {
  const log = usePolled(() => control.log(100), 3000);
  const box = useRef(null);
  const atEnd = useRef(true);
  const lines = log.data?.lines || [];
  useEffect(() => {
    if (box.current && atEnd.current) box.current.scrollTop = box.current.scrollHeight;
  }, [log.data]);
  let note = null;
  if (log.data && !log.data.exists) {
    note = t("There is no log file yet. The tray app writes it to {path}; “libre-panel start” logs to its terminal.", { path: log.data.path });
  }
  return html`<div class="live-log" role="log" aria-label=${t("The last lines of the log")} tabindex="0" ref=${box}
    onScroll=${(event) => {
      const node = event.currentTarget;
      atEnd.current = node.scrollTop + node.clientHeight >= node.scrollHeight - 6;
    }}>
    ${note ? html`<p class="muted">${note}</p>` : null}
    ${lines.map((raw, index) => {
      const line = logLine(raw);
      return html`<div key=${index} class=${line.level} title=${line.title}>${line.text}</div>`;
    })}
  </div>`;
}

// -- sources, services, notices -----------------------------------------------------------------------

function Source({ source }) {
  const [name, about] = SOURCE_NAMES()[source.name] || [source.name, ""];
  const status = source.status || {};
  const dot = source.problem ? "warn" : source.readings > 0 ? "ok" : "idle";
  let note = source.readings === 1 ? t("1 reading") : t("{n} readings", { n: source.readings });
  if (source.name === "librehardwaremonitor" && status.mode) {
    note = [status.mode === "inprocess" ? t("inside Libre Panel") : t("web server"), status.elevated ? t("Administrator") : null].filter(Boolean).join(" · ");
  }
  const head = html`<span class=${`dot ${dot}`} aria-hidden="true"></span>
    <span class="live-row-name" title=${about}>${name}</span>
    <span class="live-row-note">${note}</span>`;
  return html`<li class="live-source" data-source=${source.name}>
    ${source.details.length
      ? html`<details>
          <summary class="live-row">${head}<${Icon} name="chevron-down" size=${13} /></summary>
          <dl class="live-details">
            ${source.details.map(([label, value]) => html`<div key=${label}><dt>${label}</dt><dd>${value}</dd></div>`)}
          </dl>
        </details>`
      : html`<div class="live-row">${head}</div>`}
    ${source.hint ? html`<p class="live-hint">${source.hint}</p>` : null}
  </li>`;
}

function Sources({ data, background }) {
  if (!background) {
    return html`<p class="live-note">${t("This editor does not run the panel, so there are no sources to show. Start Libre Panel with “libre-panel tray”.")}</p>`;
  }
  const sources = data?.sources || [];
  if (!sources.length) return html`<p class="live-note">${t("No sensor source is running.")}</p>`;
  return html`<ul class="live-list">${sources.map((source) => html`<${Source} key=${source.name} source=${source} />`)}</ul>`;
}

function Service({ service, busy, onToggle, onRestart }) {
  const [name, about] = SERVICE_NAMES()[service.name] || [service.name, ""];
  const word = SERVICE_STATES()[service.state];
  const speaks = word && (service.enabled || service.state === "pending");
  const trouble = service.state === "failed" || service.state === "not-installed";
  const note = speaks ? `${word}${service.detail ? `: ${service.detail}` : ""}` : about;
  return html`<li class="live-service" data-service=${service.name}>
    <div class="live-row">
      <span class="live-row-name">${name}</span>
      <span class=${`live-row-note${speaks ? (trouble ? " bad" : " warn") : ""}`} title=${note}>${note}</span>
      ${service.restartable && service.enabled
        ? html`<button type="button" class="icon-btn" disabled=${busy} aria-label=${t("Restart {name}", { name })} title=${t("Restart {name}", { name })}
            onClick=${() => onRestart(service)}><${Icon} name="refresh" size=${14} /></button>`
        : null}
      <${Toggle} label=${name} checked=${service.enabled} disabled=${busy || (!service.installed && !service.enabled)}
        onChange=${(on) => onToggle(service, on)} />
    </div>
  </li>`;
}

function Services({ data, onChanged }) {
  const [busy, setBusy] = useState(new Set());
  const services = data?.services || [];
  const run = async (service, action, message) => {
    setBusy((now) => new Set(now).add(service.name));
    try {
      const answer = await action();
      if (message) toast(message(answer), { kind: answer.state === "failed" ? "error" : "info" });
    } catch (error) {
      toastError(error);
    }
    setBusy((now) => {
      const next = new Set(now);
      next.delete(service.name);
      return next;
    });
    onChanged();
  };
  const restarted = (item) => (answer) =>
    t("{name}: {state}", { name: SERVICE_NAMES()[item.name]?.[0] || item.name, state: SERVICE_STATES()[answer.state] || t("running") });
  if (!services.length) return null;
  return html`<ul class="live-list">
      ${services.map(
        (service) => html`<${Service} key=${service.name} service=${service} busy=${busy.has(service.name)}
          onToggle=${(item, on) => run(item, () => control.enableService(item.name, on))}
          onRestart=${(item) => run(item, () => control.restartService(item.name), restarted(item))} />`,
      )}
    </ul>
    ${data?.config_problem ? html`<p class="live-note bad" role="alert">${data.config_problem}</p>` : null}`;
}

function Probes({ background }) {
  const probes = usePolled(control.probes, 600000);
  const [result, setResult] = useState(null);
  const kinds = probes.data?.probes || [];
  const send = async (kind) => {
    try {
      setResult(await control.probe(kind));
    } catch (error) {
      toastError(error);
    }
  };
  return html`<div class="live-chips" role="group" aria-label=${t("Test a notice")}>
      ${kinds.map(
        (probe) => html`<button type="button" key=${probe.kind} class="live-chip" disabled=${!background} data-kind=${probe.kind}
          onClick=${() => send(probe.kind)}>${probe.label}</button>`,
      )}
    </div>
    <p class=${`live-result${result ? (result.shown ? " ok" : " warn") : ""}`} aria-live="polite">
      ${result
        ? `${result.name}: ${result.message}`
        : background
          ? t("A test notice is shown like a real one: the theme and the screen on the panel decide whether it shows.")
          : t("Test notices need the running panel.")}
    </p>`;
}

// -- the header: pause and brightness ---------------------------------------------------------------------

function LiveActions() {
  const app = useStore((s) => s.app);
  if (!app.available || app.quit) return null;
  const paused = app.panel?.state === "paused";
  return html`<button type="button" class="btn live-pause" onClick=${() => appAction(paused ? "resume" : "pause")}>
      <${Icon} name=${paused ? "play" : "pause"} size=${14} /> ${paused ? t("Resume panel") : t("Pause panel")}
    </button>
    <label class="live-brightness">
      <span>${t("Brightness")}</span>
      <${Slider} label=${t("Brightness")} value=${app.brightness ?? 0} step=${5} format=${(v) => `${v} %`}
        onCommit=${(value) => appAction("brightness", value)} />
    </label>`;
}

// -- the view -------------------------------------------------------------------------------------------------

function LiveView() {
  const app = useStore((s) => s.app);
  const models = useStore((s) => s.specs?.models || []);
  const panel = usePolled(control.panel, 1000);
  const sources = usePolled(control.sources, 2000);
  const services = usePolled(control.services, 2000);
  const background = panel.data ? panel.data.background : app.available;
  const status = panel.data?.panel || (background ? app.panel : null);
  const state = status?.state;
  const model = connectedModel({ available: background, panel: status }, models);
  const line = [
    model ? panelName(model) : status?.target,
    status?.stats?.streaming ? t("Video mode") : null,
    status?.mode ? modeName(status.mode) : null,
    state && state !== "showing" ? stateText(state) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return html`<div class="live">
    <div class="live-main">
      <div class="live-head">
        <h2>${t("On the panel now")}</h2>
        ${background ? html`<span class="live-state"><span class=${`dot ${state || "none"}`} aria-hidden="true"></span>${line}</span>` : null}
      </div>
      <${Picture} state=${state} background=${background} />
      <${Tiles} stats=${background ? status?.stats : null} />
      <${LogBox} />
    </div>
    <aside class="live-side" aria-label=${t("Sources and services")}>
      <section>
        <h3 class="section-title">${t("Sources")}</h3>
        <${Sources} data=${sources.data} background=${background} />
      </section>
      <section>
        <h3 class="section-title">${t("Services")}</h3>
        <${Services} data=${services.data} onChanged=${services.reload} />
      </section>
      <section>
        <h3 class="section-title">${t("Test a notice")}</h3>
        <${Probes} background=${background} />
      </section>
    </aside>
  </div>`;
}

registerView({
  id: "live",
  title: () => t("Live"),
  order: 40,
  icon: "pulse",
  component: LiveView,
  header: { actions: LiveActions },
});

// -- commands (Ctrl+K): a test notice of each kind --------------------------------------------------------

let kinds = null; // the kinds of test notice, asked of the panel's console once it runs

async function loadKinds() {
  if (kinds !== null) return;
  kinds = [];
  try {
    kinds = (await control.probes()).probes;
  } catch {
    kinds = null; // asked again at the next search
  }
}

registerProvider(() => {
  if (!store.get().app.available) return [];
  loadKinds();
  return (kinds || []).map((probe) => ({
    id: `live.probe.${probe.kind}`,
    title: () => t("Test notice: {name}", { name: probe.label }),
    hint: () => t("shown like a real one"),
    group: () => t("Panel"),
    icon: "info",
    rank: 66,
    run: async () => {
      try {
        const answer = await control.probe(probe.kind);
        toast(`${answer.name}: ${answer.message}`, { kind: answer.shown ? "success" : "warn" });
      } catch (error) {
        toastError(error);
      }
    },
  }));
});
