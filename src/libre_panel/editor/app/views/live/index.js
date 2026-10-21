// Live (#/live): what the panel shows now and how it runs; its switch leads to History
// (#/live/history, ../history), what the memory kept of the last days, and to Diagnose
// (#/live/diagnose, ../diagnose): the panel test, the log and what mends a problem. A calm line says it in words ("Runs
// smoothly at 50 fps", or what keeps it from running); under it the picture on the panel, the
// numbers as detail (frame rate against the target, data rate, drawing time, queue, faults) and
// the end of the log; at the side smooth motion, the sensors, game mode and the other services
// with their switches (named as Settings names them), and test notices. It uses the console API
// (docs/ARCHITECTURE.md) and polls it while it is shown. Built on the shell: see ../../README.md.

import { appAction } from "../../core/app.js";
import { registerCommands, registerProvider } from "../../core/commands.js";
import { control, useLivePicture, usePolled } from "../../core/console.js";
import { t } from "../../core/i18n.js";
import { loadPanelInfo } from "../../core/settings.js";
import { store, useStore } from "../../core/state.js";
import { statTiles } from "../../core/stats.js";
import { videoState } from "../../core/video.js";
import { html, useEffect, useRef, useState } from "../../lib/preact.js";
import { themeName } from "../../core/actions.js";
import { modeName, panelName, stateText } from "../../shell/panel-popover.js";
import { navigate, registerView } from "../../shell/views.js";
import { Segmented, Slider, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toast, toastError } from "../../ui/toast.js";
import { DiagnoseView } from "../diagnose/index.js";
import { HistoryView } from "../history/index.js";
import { connectedModel } from "../home/panel.js";
import { SOURCES } from "../settings/sensors.js";
import { SERVICES } from "../settings/services.js";

// -- names ------------------------------------------------------------------------------------------

// What the sensor sources and services are for, in a few words (a row's hover); their names are
// the ones Settings gives them (SOURCES, SERVICES), and any other (a plugin's) keeps its own.
const SOURCE_ABOUT = () => ({
  librehardwaremonitor: t("temperatures, power and fans"),
  linux: t("temperatures, fans and power from the kernel"),
  load: t("CPU and GPU load, ten times a second"),
  audio: t("what the default output plays at"),
  psutil: t("processor, memory, disk and network"),
  media: t("what is playing"),
  sky: t("where they stand"),
  "open-meteo": "Open-Meteo",
  calendar: t("your appointments"),
  presentmon: t("frame rates of games"),
  demo: t("made-up readings"),
  services: t("what game mode and the other services publish"),
  hardware: t("the names of the processor, the graphics card and the memory"),
});

// the sources Settings does not list (they start by themselves, or come with a service)
const SOURCE_NAMES = () => ({
  "open-meteo": t("Weather"),
  services: t("Game mode and more"),
  hardware: t("Hardware names"),
});

const sourceName = (name) => SOURCES[name]?.title() ?? SOURCE_NAMES()[name] ?? name;

const SERVICE_ABOUT = () => ({
  notices: t("volume, tracks, warnings"),
  game: t("Steam and a list of games"),
  autopilot: t("rules that choose the theme"),
  devices: t("USB, drives, controllers"),
  memory: t("records a game and reports after it"),
  cooling: t("checks FanControl's curves"),
  downloads: t("Steam and the browsers"),
});

const serviceName = (name) => SERVICES[name]?.title() ?? name;

const SERVICE_STATES = () => ({
  stopped: t("stopped"),
  failed: t("failed"),
  pending: t("changing"),
  "not-installed": t("not installed"),
});

// -- how it runs, in words --------------------------------------------------------------------------

/**
 * How the panel runs, in one calm sentence and a line under it: `{tone, title, text}`, tone "ok",
 * "warn", "error" or "off". The numbers further down are the detail; this says what they mean.
 */
export function runState(background, status) {
  if (!background) {
    return {
      tone: "warn",
      title: t("Libre Panel is not running in the background"),
      text: t("This window edits the settings and the themes, but the panel gets nothing from it. Start Libre Panel from the Start menu or your list of apps: it then runs in the background, drives the panel and uses what you set here."),
    };
  }
  const state = status?.state;
  const detail = status?.detail || "";
  if (state === "waiting") {
    const why = detail ? `${t("Libre Panel says: {reason}", { reason: detail })} ` : "";
    return { tone: "warn", title: t("Waiting for the panel"), text: why + t("Plug the panel in with its USB cable: Libre Panel looks for it every few seconds.") };
  }
  if (state === "paused") return { tone: "off", title: t("Paused"), text: t("The panel keeps its last picture until you resume it.") };
  if (state === "error") return { tone: "error", title: t("The panel needs attention"), text: detail || t("Libre Panel stopped drawing for the panel.") };
  if (state === "stopped") return { tone: "off", title: t("Stopped"), text: "" };
  const s = status?.stats || {};
  if (state !== "showing") return { tone: "off", title: t("Starting …"), text: "" };
  if (typeof s.fps !== "number") return { tone: "ok", title: t("Runs"), text: "" }; // (numbers come after a second)
  const faults = (s.usb_errors ?? 0) + (s.panel_restarts ?? 0) + (s.ffmpeg_restarts ?? 0);
  const hiccups = faults === 1 ? t("One hiccup since it started; it recovered by itself.") : faults ? t("{n} hiccups since it started; it recovered by itself each time.", { n: faults }) : "";
  const fps = Math.round(s.fps);
  if (s.streaming && s.target_fps) {
    if (s.fps < 0.9 * s.target_fps) {
      const why = t("The PC or the panel cannot keep up. Fewer frames per second in Settings → Panel ask less of both.");
      return { tone: "warn", title: t("Runs slower than planned: {fps} of {target} fps", { fps, target: s.target_fps }), text: [why, hiccups].filter(Boolean).join(" ") };
    }
    return { tone: "ok", title: t("Runs smoothly at {fps} fps", { fps }), text: hiccups };
  }
  const single = s.target_fps ? t("A new picture whenever something changes, up to {n} a second.", { n: s.target_fps }) : "";
  return { tone: "ok", title: t("Runs, picture by picture"), text: [single, hiccups].filter(Boolean).join(" ") };
}

function RunState({ background, status }) {
  const state = runState(background, status);
  return html`<div class=${`live-status ${state.tone}`} role="status" data-tone=${state.tone}>
    <span class=${`dot ${state.tone === "off" ? "idle" : state.tone}`} aria-hidden="true"></span>
    <div class="live-status-text">
      <strong>${state.title}</strong>
      ${state.text ? html`<span>${state.text}</span>` : null}
    </div>
  </div>`;
}

// -- the picture and the numbers ------------------------------------------------------------------

function Picture({ state, background, panel = 1 }) {
  const live = useLivePicture({ width: 960, every: 200, on: background && state === "showing", panel });
  let note = null;
  if (live.src === null) {
    note =
      state === "paused"
        ? t("The panel is paused: there is no picture.")
        : state === "error"
          ? t("The panel needs attention: there is no picture.")
          : t("There is no picture yet.");
  }
  return html`<div class="live-picture">
    ${live.src ? html`<img src=${live.src} alt=${t("The picture on the panel")} />` : null}
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
    note = t("No log yet: Libre Panel writes one while it runs in the background, to {path}.", { path: log.data.path });
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

/**
 * Whether the video mode is on, or what it is missing: as Settings says it, from the server's
 * own `video_mode()`, and "on" at the rate the panel is driven at while it is.
 */
function VideoMode({ background, stats }) {
  const info = useStore((s) => s.panelInfo);
  usePolled(loadPanelInfo, 8000, { on: background });
  if (!background || !info) return null;
  const { video } = info;
  const streaming = Boolean(stats?.streaming);
  const state = videoState(video, video.mode, streaming && stats.target_fps ? stats.target_fps : video.fps, streaming);
  return html`<section>
    <h3 class="section-title">${t("Smooth motion")}</h3>
    <p class="live-video" data-video-state=${state.tone}>
      <span class=${`dot ${state.tone === "off" ? "idle" : state.tone}`} aria-hidden="true"></span>
      <span class="live-video-text">${state.text}</span>
      ${state.tone === "warn" ? html`<a class="link-btn" href="#/settings/panel">${t("Panel settings")}</a>` : null}
    </p>
  </section>`;
}

// -- sources, services, notices -----------------------------------------------------------------------

function Source({ source }) {
  const name = sourceName(source.name);
  const about = SOURCE_ABOUT()[source.name] || "";
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
  if (!background) return html`<p class="live-note">${t("They show here while Libre Panel runs in the background.")}</p>`;
  const sources = data?.sources || [];
  if (!sources.length) return html`<p class="live-note">${t("No sensor source is running.")}</p>`;
  return html`<ul class="live-list">${sources.map((source) => html`<${Source} key=${source.name} source=${source} />`)}</ul>`;
}

function Service({ service, busy, onToggle, onRestart }) {
  const name = serviceName(service.name);
  const about = SERVICE_ABOUT()[service.name] || "";
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
    t("{name}: {state}", { name: serviceName(item.name), state: SERVICE_STATES()[answer.state] || t("running") });
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

/** Now, History or Diagnose: the parts of Live. */
function Switch({ part = "" }) {
  const link = (to, label) => html`<a href=${to ? `#/live/${to}` : "#/live"} aria-current=${part === to ? "page" : undefined}>${label}</a>`;
  return html`<nav class="live-switch" aria-label=${t("Live")}>
    ${link("", t("Now"))}${link("history", t("History"))}${link("diagnose", t("Diagnose"))}
  </nav>`;
}

function LiveView({ rest = "" }) {
  const [part, span] = rest.split("/");
  if (part === "history") return html`<${HistoryView} rest=${span || ""} switcher=${html`<${Switch} part="history" />`} />`;
  if (part === "diagnose") return html`<${DiagnoseView} switcher=${html`<${Switch} part="diagnose" />`} />`;
  return html`<${LiveNow} />`;
}

function LiveNow() {
  const app = useStore((s) => s.app);
  const models = useStore((s) => s.specs?.models || []);
  const panel = usePolled(control.panel, 1000);
  const sources = usePolled(control.sources, 2000);
  const services = usePolled(control.services, 2000);
  const background = panel.data ? panel.data.background : app.available;
  const status = panel.data?.panel || (background ? app.panel : null);
  const state = status?.state;
  const model = connectedModel({ available: background, panel: status }, models);
  // with more panels: which one's picture (as Which is which? numbers them), and how it is doing
  const more = status?.more || [];
  const [chosen, choose] = useState(1);
  const beside = more.find((item) => item.index + 2 === chosen) || null;
  const number = beside ? chosen : 1;
  const besideLine = beside
    ? [
        beside.state === "showing" ? t("Shows {theme}", { theme: themeName(beside.theme) }) : stateText(beside.state),
        beside.fps ? t("{fps} pictures a second", { fps: beside.fps }) : null,
        beside.detail || null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "";
  const line = beside
    ? besideLine
    : [model ? panelName(model) : status?.target, status?.mode ? modeName(status.mode) : null].filter(Boolean).join(" · ");
  return html`<div class="live">
    <div class="live-main">
      <${Switch} />
      <div class="live-head">
        <h2>${t("On the panel now")}</h2>
        ${background && line ? html`<span class="live-state">${line}</span>` : null}
      </div>
      ${background && more.length
        ? html`<${Segmented}
            class="live-panels"
            label=${t("Which panel's picture")}
            value=${number}
            options=${[1, ...more.map((item) => item.index + 2)].map((n) => ({ value: n, label: t("Panel {number}", { number: n }) }))}
            onChange=${choose}
          />`
        : null}
      ${beside ? null : html`<${RunState} background=${background} status=${status} />`}
      ${background
        ? html`<${Picture} state=${beside ? beside.state : state} background=${background} panel=${number} />
            ${beside // (the numbers are the first panel's: its loop drives the readings and the video)
              ? null
              : html`<section class="live-numbers" aria-label=${t("In numbers")}>
                  <h3 class="section-title">${t("In numbers")}</h3>
                  <${Tiles} stats=${status?.stats} />
                </section>`}`
        : null}
      <h3 class="section-title live-log-title">${t("Log")}</h3>
      <${LogBox} />
    </div>
    <aside class="live-side" aria-label=${t("Sources and services")}>
      <${VideoMode} background=${background} stats=${status?.stats} />
      <section>
        <h3 class="section-title">${t("Sensors")}</h3>
        <${Sources} data=${sources.data} background=${background} />
      </section>
      <section>
        <h3 class="section-title">${t("Game mode and more")}</h3>
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

registerCommands([
  {
    id: "live.history",
    title: () => t("History"),
    hint: () => t("temperatures, load and games over days"),
    keywords: () => t("history graph chart temperatures past week month games rounds fps statistics"),
    group: () => t("Go to"),
    icon: "graph",
    rank: 8,
    run: () => navigate("live", "history"),
  },
  {
    id: "live.diagnose",
    title: () => t("Diagnose"),
    hint: () => t("test the panel, read the log, mend what is wrong"),
    keywords: () => t("diagnose doctor test check panel log logs error problem fix repair mend restart reconnect"),
    group: () => t("Go to"),
    icon: "pulse",
    rank: 9,
    run: () => navigate("live", "diagnose"),
  },
]);

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
