// What needs attention, in plain words, each with the click that mends it or the next step: the
// panel not found or paused, smooth motion that cannot start, readings that are locked, games
// without a frame rate, a weather without a place, a service that stopped, a change that waits
// for a restart, Libre Panel not running in the background. Settings → Overview shows it in full
// with the state of each part at a glance; `<HealthCard compact />` is the short form for Start,
// only what needs attention, and nothing when all is well.
//
// It is worked out from what the app knows already (healthOf, a function of the store): the
// background app (/api/app), the panel found and the video mode (/api/panel, with the theme that
// shows the weather), the setup of the system (/api/unlock: the sensor service and PresentMon on
// Windows, the rights on Linux), config.toml (/api/settings), the standby clip's question
// (/api/clip) and the console's sources and services. useHealth() asks for them while it is shown.

import { themeName } from "../../core/actions.js";
import { appAction } from "../../core/app.js";
import { t } from "../../core/i18n.js";
import {
  answerClipQuestion,
  enableService,
  loadClipQuestion,
  loadPanelInfo,
  loadServiceStates,
  loadSettings,
  loadSourceStates,
  loadSystem,
  loadUnlock,
  restartPanel,
  restartService,
  runUnlock,
  saveSettings,
} from "../../core/settings.js";
import { linuxSetup, setupNeeds } from "../../core/setup.js";
import { store, useStore } from "../../core/state.js";
import { videoState } from "../../core/video.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { panelName } from "../../shell/panel-popover.js";
import { navigate } from "../../shell/views.js";
import { Icon } from "../../ui/icons.js";
import { SOURCES, withSource } from "./sensors.js";
import { SERVICES } from "./services.js";

// -- asking for what it needs ------------------------------------------------------------------------

// One timer for each thing asked for, however many cards show (Settings and Start): it runs
// while one of them is shown and the page is visible.
const polls = new Map();

function useShared(load, ms) {
  useEffect(() => {
    let entry = polls.get(load);
    if (!entry) {
      load();
      entry = { users: 0, timer: ms ? setInterval(() => !document.hidden && load(), ms) : 0 };
      polls.set(load, entry);
    }
    entry.users += 1;
    return () => {
      entry.users -= 1;
      if (entry.users) return;
      clearInterval(entry.timer);
      polls.delete(load);
    };
  }, []);
}

const loadOnce = () => {
  if (!store.get().settings) loadSettings();
};

/** What the health is worked out from, asked for while a card shows it; healthOf(the store). */
export function useHealth() {
  useShared(loadPanelInfo, 6000); // a panel plugged in, smooth motion that starts
  useShared(loadSourceStates, 5000);
  useShared(loadServiceStates, 5000);
  useShared(loadUnlock, 0); // (a run of the setup follows itself)
  useShared(loadOnce, 0);
  // the question about the standby clip, where smooth motion lacks its name: it is for the panel
  // at hand, and asked again when that changes
  const panel = useStore((s) => [
    s.app.panel?.target,
    s.panelInfo?.model?.id,
    s.panelInfo?.video?.mode,
    Boolean(s.panelInfo?.video?.gaps?.includes("clip")),
  ]);
  useEffect(() => {
    if (panel[3]) loadClipQuestion();
  }, panel);
  const facts = useStore((s) => ({
    lang: s.lang,
    app: s.app,
    themes: s.themes,
    panelInfo: s.panelInfo,
    unlockInfo: s.unlockInfo,
    settings: s.settings,
    settingsError: s.settingsDown ? null : s.settingsError,
    sourceStates: s.sourceStates,
    serviceStates: s.serviceStates,
    clipQuestion: s.clipQuestion,
  }));
  return healthOf(facts);
}

// -- what needs attention ---------------------------------------------------------------------------

const RANK = { error: 0, warn: 1, info: 2 };

const go = (label, view, rest = "") => ({ label, go: () => navigate(view, rest) });

/** A service's name for people, as Settings → Game mode and more calls it. */
const serviceTitle = (name) => SERVICES[name]?.title() ?? name;

/** A sensor source's name for people, as Settings → Sensors calls it. */
const sourceTitle = (name) => SOURCES[name]?.title() ?? name;

/**
 * Switch the source of games' frame rates on (before the one that fills in the rest), and start
 * the panel again where it runs, which makes the sources anew.
 */
async function readFrameRates(running) {
  const written = await saveSettings((now) =>
    now.sensors.providers.includes("presentmon") ? null : { "sensors.providers": withSource(now.sensors.providers, "presentmon") },
  );
  if (written && running) await restartPanel();
}

/**
 * `{ready, items, good}` for the facts in the store: `items` are what needs attention, each
 * `{id, tone: "error" | "warn" | "info", title, text, action}`, the worst first; `action` is
 * `{label, run}` (a click that mends it) or `{label, go}` (the way to where it is done), or none
 * when the next step is in the text. `ready` is false until the panel's facts are known.
 */
export function healthOf(facts) {
  const { app, panelInfo: info, unlockInfo: unlock, settings, sourceStates, serviceStates, clipQuestion } = facts;
  const config = settings?.config;
  const catalog = settings?.catalog;
  const items = [];
  const add = (item) => items.push(item);
  const ready = Boolean(info) || Boolean(facts.settingsError);
  const panel = app.available ? app.panel || {} : {};
  const running = Boolean(app.available && !app.quit && !app.gone); // its controls answer
  const windows = (catalog?.platform || unlock?.platform) === "win32";

  if (facts.settingsError) {
    add({
      id: "config",
      tone: "error",
      title: t("The settings file has a mistake"),
      text: facts.settingsError,
      action: go(t("Show"), "settings", "overview"),
    });
  }

  // -- Libre Panel and the panel
  if (info && !info.running && !app.available && !app.gone) {
    add({
      id: "background",
      tone: "warn",
      title: t("Libre Panel is not running in the background"),
      text: t("This window edits the settings and the themes, but the panel gets nothing from it. Start Libre Panel from the Start menu or your list of apps: it then runs in the background, drives the panel and uses what you set here."),
    });
  }
  if (running && panel.state === "paused") {
    add({
      id: "paused",
      tone: "warn",
      title: t("The panel is paused"),
      text: t("It shows nothing new until it goes on."),
      action: { label: t("Resume panel"), run: () => appAction("resume") },
    });
  }
  if (running && panel.state === "error") {
    add({
      id: "error",
      tone: "error",
      title: t("The panel needs attention"),
      text: panel.detail || t("Libre Panel stopped drawing for the panel."),
      action: go(t("Show the log"), "live"),
    });
  }
  const file = config?.device?.driver === "virtual"; // the pictures go to a file, as chosen
  const setup = linuxSetup(unlock);
  const needs = setupNeeds(setup);
  if (info && !file) {
    const why = info.running && ["waiting", "error"].includes(info.running.state) ? info.running.detail : "";
    const plugIn = t("Plug the panel in with its USB cable: Libre Panel looks for it every few seconds.");
    if (!info.found) {
      add({
        id: "no-panel",
        tone: "warn",
        title: info.model ? t("{panel} is not connected", { panel: panelName(info.model) }) : t("No panel found"),
        text: [plugIn, why ? t("Libre Panel says: {reason}", { reason: why }) : "", t("Plugged in and still not found? Then it may be a panel Libre Panel does not know yet.")]
          .filter(Boolean)
          .join(" "),
        action: go(t("Report a panel"), "settings", "about/about-report"),
      });
    } else if (info.model && info.found.id !== info.model.id) {
      add({
        id: "other-panel",
        tone: "warn",
        title: t("Another panel is connected"),
        text: t("The settings name the {chosen}, but the {found} is plugged in.", {
          chosen: panelName(info.model),
          found: panelName(info.found),
        }),
        action: {
          label: t("Use the {panel}", { panel: panelName(info.found) }),
          run: async () => (await saveSettings({ "device.model": "auto" })) && loadPanelInfo(),
        },
      });
    } else if (why) {
      add({
        id: "panel-busy",
        tone: "warn",
        title: t("The panel is found but cannot be used"),
        text: needs.includes("usb")
          ? `${why} ${t("Your user may not use the USB panel yet: the one-time setup gives the permission.")}`
          : `${why} ${t("Another program may be using it (the panel's own app?): close it, and Libre Panel takes the panel within a few seconds.")}`,
        action: needs.includes("usb") ? go(t("Set up"), "settings", "sensors/sensor-setup") : null,
      });
    }
  }

  // -- smooth motion: what keeps it from starting, where it would run
  const video = info?.video;
  if (video?.usb && video.mode !== "off" && video.gaps.length && video.gaps[0] !== "model" && !file) {
    const on = video.mode === "on";
    const title = on ? t("Smooth motion cannot start") : t("Smooth motion is off");
    if (video.gaps.includes("ffmpeg")) {
      add({
        id: "video-ffmpeg",
        tone: "warn",
        title,
        text: t("It needs the program ffmpeg, which turns the pictures into video and is not on this computer. The downloads of Libre Panel bring it (on Linux, your software centre has it too); then start the panel again."),
        action: go(t("Advanced"), "settings", "panel/panel-advanced"),
      });
    } else if (video.gaps.includes("ffmpeg-path")) {
      add({
        id: "video-ffmpeg",
        tone: "warn",
        title,
        text: t("The video program named under Advanced is not there."),
        action: go(t("Advanced"), "settings", "panel/panel-advanced"),
      });
    } else if (video.gaps.includes("clip") && (on || clipQuestion?.ask)) {
      const name = clipQuestion?.name;
      add({
        id: "video-clip",
        tone: "warn",
        title,
        text: name
          ? t("It needs the name of the clip your panel plays by itself when the PC starts. The panel's own app calls it {name}: Turn on uses that name. Nothing on the panel is changed.", { name })
          : t("It needs the name of the clip your panel plays by itself when the PC starts."),
        action: name
          ? { label: t("Turn on"), run: async () => (await answerClipQuestion(name)) && loadPanelInfo() }
          : go(t("Panel settings"), "settings", "panel/panel-video"),
      });
    }
  }

  // -- readings
  const service = unlock?.platform === "win32" ? unlock.service : undefined;
  const busy = Boolean(unlock?.job?.running);
  const unlockAction = (label) => ({ label: busy ? t("Waiting for Windows…") : label, run: () => runUnlock("unlock"), busy });
  const locked = Boolean(unlock?.platform === "win32" && unlock.available && !service);
  const gameOn = Boolean(config?.services?.enabled?.includes("game"));
  const ratesLocked = windows && gameOn && unlock?.frame_rates === "no";
  if (locked) {
    add({
      id: "sensor-service",
      tone: "warn",
      title: t("Some sensors are locked"),
      text: `${t("Temperatures, power and fans of the processor and the mainboard need a small Windows service with the rights to read them. Windows asks you once; Libre Panel itself keeps running without administrator rights.")}${ratesLocked ? ` ${t("It also lets the frame rates of games be recorded.")}` : ""}`,
      action: unlockAction(t("Unlock all sensors")),
    });
  } else if (service && (service.disabled || service.state !== "running")) {
    add({
      id: "sensor-service",
      tone: "warn",
      title: t("The sensor service is not running"),
      text: t("Temperatures, power and fans of the processor and the mainboard stay empty until it runs again."),
      action: unlock.available ? unlockAction(t("Set up again")) : go(t("Sensor settings"), "settings", "sensors/sensor-service"),
    });
  }
  if (running && panel.hint && !items.some((item) => item.id === "sensor-service")) {
    add({ id: "readings", tone: "warn", title: t("Readings are missing"), text: panel.hint, action: go(t("Sensor settings"), "settings", "sensors") });
  }
  const missing = [
    needs.includes("usb") && !items.some((item) => item.id === "panel-busy") ? t("Your user may not use the USB panel yet.") : "",
    needs.includes("fans") ? t("Without it there are no fan readings.") : "",
  ].filter(Boolean);
  if (missing.length) {
    add({
      id: "linux-setup",
      tone: "warn",
      title: t("A one-time setup is missing"),
      text: `${missing.join(" ")} ${t("One click sets it up; your system asks for the password.")}`,
      action: go(t("Set up"), "settings", "sensors/sensor-setup"),
    });
  }
  for (const source of sourceStates?.background ? sourceStates.sources : []) {
    const error = source.status?.error;
    if (!error || source.name === "librehardwaremonitor") continue; // (its hint is the readings')
    add({
      id: `source-${source.name}`,
      tone: "warn",
      title: t("{source}: a problem", { source: sourceTitle(source.name) }),
      text: String(error),
      action: go(t("Show details"), "live"),
    });
  }

  // -- the frame rate of games (Windows: PresentMon)
  if (windows && gameOn && config) {
    const title = t("Games show no frame rate");
    if (!config.sensors.providers.includes("presentmon")) {
      add({
        id: "game-fps",
        tone: "warn",
        title,
        text: t("Game mode is on, but the frame rate of games is not read: its source is switched off."),
        action: { label: t("Switch it on"), run: () => readFrameRates(running) },
      });
    } else if (unlock?.presentmon === false) {
      add({
        id: "game-fps",
        tone: "warn",
        title,
        text: t("The frame rate of games comes from the program PresentMon, which is not on this computer. The Windows setup of Libre Panel brings it."),
      });
    } else if (ratesLocked && !locked) {
      add({
        id: "game-fps",
        tone: "warn",
        title,
        text: t("Recording them needs a Windows permission (the group Performance Log Users) that Unlock all sensors gives you."),
        action: unlock.available ? unlockAction(t("Unlock all sensors")) : null,
      });
    } else if (unlock?.frame_rates === "sign-in") {
      add({
        id: "game-fps",
        tone: "info",
        title: t("Games show their frame rate from your next sign-in"),
        text: t("Windows gives the permission to record them when you sign in again."),
      });
    }
  }

  // -- the weather
  const weather = config?.weather;
  if (weather && !weather.enabled && info?.weather_theme) {
    const name = themeName(info.weather_theme);
    const placed = weather.latitude !== null && weather.longitude !== null;
    add({
      id: "weather",
      tone: "info",
      title: t("{theme} can show the weather", { theme: name }),
      text: placed
        ? weather.place
          ? t("The weather is off. Turned on, only the coordinates of {place} go to Open-Meteo.", { place: weather.place })
          : t("The weather is off. Turned on, only the coordinates of your place go to Open-Meteo.")
        : t("It has no place yet, so it shows no weather. Choose your town: only its coordinates go to Open-Meteo."),
      action: placed
        ? { label: t("Turn on the weather"), run: () => saveSettings({ "weather.enabled": true }) }
        : go(t("Choose a place"), "settings", "weather"),
    });
  }
  const meteo = sourceStates?.background ? sourceStates.sources.find((source) => source.name === "open-meteo") : null;
  if (weather?.enabled && meteo && !meteo.readings && (panel.stats?.uptime_s ?? 0) >= 120) {
    add({
      id: "weather",
      tone: "warn",
      title: t("No weather has arrived"),
      text: t("Libre Panel could not reach Open-Meteo. It tries again every two minutes; is this computer online?"),
      action: go(t("Show the log"), "live"),
    });
  }

  // -- what runs by itself
  for (const row of serviceStates?.background ? serviceStates.services : []) {
    if (!row.enabled) continue;
    const name = serviceTitle(row.name);
    if (row.state === "failed") {
      add({
        id: `service-${row.name}`,
        tone: "error",
        title: t("{name}: stopped with a fault", { name }),
        text: row.detail || t("A restart often helps; the log says why."),
        action: { label: t("Restart"), run: () => restartService(row.name) },
      });
    } else if (row.state === "not-installed") {
      add({
        id: `service-${row.name}`,
        tone: "warn",
        title: t("{name}: not installed", { name }),
        text: t("Switched on, but nothing installed here brings it."),
        action: { label: t("Switch off"), run: () => enableService(row.name, false) },
      });
    }
  }
  if (panel.notice) {
    add({ id: "theme", tone: "warn", title: t("A theme is missing"), text: panel.notice, action: go(t("Choose a theme"), "home", "themes") });
  }
  if (running && settings?.restart?.length) {
    add({
      id: "restart",
      tone: "info",
      title: t("A change waits for a restart of the panel"),
      text: t("Sensor sources and the weather are set up when the panel starts."),
      action: { label: t("Restart the panel"), run: restartPanel },
    });
  }
  items.sort((a, b) => RANK[a.tone] - RANK[b.tone]);
  return { ready, items, good: !items.some((item) => item.tone !== "info") };
}

// -- the card ------------------------------------------------------------------------------------------

function ActionButton({ action, quiet }) {
  const [busy, setBusy] = useState(false);
  if (!action) return null;
  if (action.go) {
    return html`<button type="button" class="btn health-go" onClick=${action.go}>
      ${action.label}<${Icon} name="chevron-right" size=${14} />
    </button>`;
  }
  const run = async () => {
    setBusy(true);
    try {
      await action.run();
    } finally {
      setBusy(false);
    }
  };
  return html`<button type="button" class=${`btn${quiet ? "" : " btn-primary"}`} disabled=${busy || action.busy} onClick=${run}>${action.label}</button>`;
}

const MARKS = { error: "warning", warn: "warning", info: "info" };

function Item({ item, compact }) {
  return html`<li class=${`health-item ${item.tone}`} data-health=${item.id}>
    <span class="health-mark" aria-hidden="true"><${Icon} name=${MARKS[item.tone]} size=${16} /></span>
    <div class="health-text">
      <strong>${item.title}</strong>
      ${item.text ? html`<span class=${compact ? "health-short" : ""}>${item.text}</span>` : null}
    </div>
    <div class="health-action"><${ActionButton} action=${item.action} quiet=${item.tone === "info"} /></div>
  </li>`;
}

/**
 * What needs attention, each with its action. In full (Settings → Overview): a calm "All good"
 * when nothing does, and the suggestions under it. `compact` (Start): only what needs attention,
 * three at most and the way to the rest, and nothing at all when all is well.
 */
export function HealthCard({ compact = false }) {
  const { ready, items, good } = useHealth();
  const broken = useStore((s) => Boolean(s.settingsError)); // the note at the top of Settings says it
  if (!ready || (broken && !compact)) return null;
  const attention = items.filter((item) => item.tone !== "info");
  if (compact) {
    if (!attention.length) return null;
    const shown = attention.slice(0, 3);
    return html`<section class="health compact" aria-labelledby="health-compact-title">
      <div class="health-head">
        <h2 id="health-compact-title">${t("What needs attention")}</h2>
        <button type="button" class="link-btn" onClick=${() => navigate("settings", "overview")}>
          ${attention.length > shown.length ? t("All {n} in Settings", { n: attention.length }) : t("Settings")}
        </button>
      </div>
      <ul class="health-list">${shown.map((item) => html`<${Item} key=${item.id} item=${item} compact />`)}</ul>
    </section>`;
  }
  return html`<section id="health" class=${`settings-card health${good ? " good" : ""}`} aria-labelledby="health-title">
    <div class="health-head">
      <span class="health-badge" aria-hidden="true"><${Icon} name=${good ? "check" : "warning"} size=${18} strokeWidth=${2.4} /></span>
      <div>
        <h2 id="health-title">${good ? t("All good") : t("What needs attention")}</h2>
        <p>${good ? t("Libre Panel found nothing that needs you.") : t("Each with the click that mends it, or the next step.")}</p>
      </div>
    </div>
    ${items.length
      ? html`<ul class="health-list">${items.map((item) => html`<${Item} key=${item.id} item=${item} />`)}</ul>`
      : null}
  </section>`;
}

// -- each part at a glance ------------------------------------------------------------------------------

/** One part: its name, how it is now, and the way to its section. */
function Glance({ id, title, value, tone = "", section }) {
  return html`<button type="button" class="glance-row" data-glance=${id} onClick=${() => navigate("settings", section)}>
    <span class="glance-title">${title}</span>
    <span class=${`glance-value ${tone}`}>${value}</span>
    <${Icon} name="chevron-right" size=${16} />
  </button>`;
}

/** Settings → Overview: the panel, smooth motion, the sensors, what runs by itself, the weather and
 * the start with the system, as they are now; a click goes to the section that changes it. */
export function AtAGlance() {
  const { info, config, sources, services, system } = useStore((s) => ({
    info: s.panelInfo,
    config: s.settings?.config,
    sources: s.sourceStates,
    services: s.settings?.catalog?.services,
    system: s.systemInfo,
    lang: s.lang,
  }));
  useShared(loadSystem, 0);
  if (!info || !config) return null;
  const panel = info.found
    ? t("{panel}, connected", { panel: panelName(info.found) })
    : info.model
      ? t("{panel}, not connected", { panel: panelName(info.model) })
      : t("No panel found");
  const video = videoState(info.video, info.video.mode, info.video.fps);
  const readings = sources?.background ? sources.sources.reduce((sum, source) => sum + source.readings, 0) : null;
  const used = config.sensors.providers.length;
  const on = config.services.enabled.filter((name) => services?.some((service) => service.name === name));
  const { weather } = config;
  const placed = weather.latitude !== null && weather.longitude !== null;
  const rows = [
    { id: "panel", title: t("Panel"), value: panel, tone: info.found ? "ok" : "warn", section: "panel" },
    { id: "video", title: t("Smooth motion"), value: video.text, tone: video.tone === "warn" ? "warn" : "", section: "panel/panel-video" },
    {
      id: "sensors",
      title: t("Sensors"),
      value: readings === null ? t("{n} sources in use", { n: used }) : t("{n} sources in use, {readings} readings now", { n: used, readings }),
      section: "sensors",
    },
    {
      id: "services",
      title: t("Game mode and more"),
      value: on.length ? on.map(serviceTitle).join(", ") : t("All off"),
      section: "services",
    },
    {
      id: "weather",
      title: t("Weather and place"),
      value: weather.enabled
        ? weather.place
          ? t("On, for {place}", { place: weather.place })
          : t("On")
        : placed
          ? t("Off; the place is {place}", { place: weather.place || t("chosen") })
          : t("Off, no place yet"),
      section: "weather",
    },
    system && system.kind !== "unsupported"
      ? {
          id: "system",
          title: system.kind === "windows" ? t("Start with Windows") : t("Start when you log in"),
          value: system.enabled ? t("On") : t("Off"),
          section: "system",
        }
      : null,
  ].filter(Boolean);
  return html`<section id="glance" class="settings-card glance" aria-labelledby="glance-title">
    <div class="settings-card-head">
      <h2 id="glance-title">${t("At a glance")}</h2>
      <p>${t("How each part is set now. A click leads to where it is changed.")}</p>
    </div>
    ${rows.map((row) => html`<${Glance} key=${row.id} ...${row} />`)}
  </section>`;
}
