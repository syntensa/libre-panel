// The Control page: the panel's picture, how it runs, and what can be done about it.
import { describeStats } from "/static/stats.js";
import { $, api, dot, duration, el, every, note, pageUrl, say, section, send, t } from "./common.js";

let state = null; // the last answer of `status`
let themesShown = ""; // what the theme selects were built from: rebuilt only when it changes
let liveTimer = 0;
let logOpen = false;

const panelStates = () => ({
  starting: t("Starting"),
  showing: t("Showing"),
  waiting: t("Waiting for the panel"),
  paused: t("Paused"),
  error: t("Needs attention"),
  stopped: t("Stopped"),
});

// The service manager's states: the dot and the words.
const serviceStates = () => ({
  running: ["showing", t("running")],
  stopped: ["waiting", t("stopped")],
  failed: ["error", t("failed")],
  "not-installed": ["error", t("not installed")],
  off: ["gone", t("off")],
});

export async function start(root) {
  document.title = t("Control");
  root.replaceChildren(
    el("h1", { text: t("Control") }),
    el("div", { id: "background-note" }),
    el("div", { id: "panel-part" },
      section(
        t("Live picture"),
        el("div", { class: "screen" }, el("img", { id: "live", alt: t("The picture on the panel"), hidden: true }), el("p", { class: "empty", id: "live-note" }, t("Loading…"))),
        el("p", { class: "muted", id: "live-caption" }),
      ),
      section(t("How the panel is doing"), el("div", { class: "tiles", id: "tiles" })),
      section(t("Sensor sources"), el("div", { id: "sources" })),
      section(t("Services"), el("div", { id: "services" })),
      section(
        t("Test messages"),
        el("p", { class: "muted" }, t("A test message is shown like a real one: the theme and the screen on the panel decide whether it shows.")),
        el("div", { class: "row", id: "probes" }),
        el("p", { id: "probe-result", "aria-live": "polite" }),
      ),
    ),
    section(t("Theme per mode"), el("div", { id: "themes" })),
    section(t("Log"), el("details", { id: "log-box" }, el("summary", { text: t("The last lines of the log") }), el("p", { class: "muted", id: "log-where" }), el("pre", { class: "log", id: "log-text" }))),
  );
  $("log-box").addEventListener("toggle", () => {
    logOpen = $("log-box").open;
    if (logOpen) refreshLog();
  });
  $("live").addEventListener("load", liveShown);
  $("live").addEventListener("error", liveFailed);
  await refresh();
  every(2000, refresh);
  every(3000, () => (logOpen ? refreshLog() : null));
  liveLoop();
}

async function refresh() {
  state = await api("status");
  const background = state.background;
  $("background-note").replaceChildren(
    ...(background ? [] : [note("warn", t("This editor does not run the panel: it was started with “libre-panel editor”. Start Libre Panel with “libre-panel tray” to see the live picture and the numbers, to restart services and to send test messages."))]),
    ...(state.config_problem ? [note("error", state.config_problem)] : []),
  );
  $("panel-part").hidden = !background;
  if (background) {
    drawLiveCaption();
    drawTiles();
    drawSources();
    drawServices();
    drawProbes();
  }
  drawThemes();
}

// -- the picture --------------------------------------------------------------------------

// One request after the other: the next picture is asked for when the last one has come
// (or failed), and the old one stays on show meanwhile.
function liveLoop() {
  clearTimeout(liveTimer);
  if (!state?.background || document.hidden) {
    liveTimer = setTimeout(liveLoop, 500);
    return;
  }
  $("live").src = pageUrl(`live?w=960&t=${Date.now()}`);
}

function liveShown() {
  $("live").hidden = false;
  $("live-note").hidden = true;
  liveTimer = setTimeout(liveLoop, 200);
}

function liveFailed() {
  $("live").hidden = true;
  $("live-note").hidden = false;
  $("live-note").textContent = noPicture();
  liveTimer = setTimeout(liveLoop, 1500);
}

function noPicture() {
  const panel = state?.panel;
  if (panel?.state === "paused") return t("The panel is paused: there is no picture.");
  if (panel?.state === "error") return t("The panel needs attention: there is no picture.");
  return t("There is no picture yet.");
}

function drawLiveCaption() {
  const panel = state.panel;
  if (!panel) return;
  const parts = [panel.theme, panel.mode ? t("{mode} mode", { mode: panel.mode }) : "", panel.target];
  $("live-caption").textContent = parts.filter(Boolean).join(" · ");
}

// -- the numbers --------------------------------------------------------------------------

function tile(row) {
  return el("div", { class: `tile ${row.level}`.trim() }, el("span", { class: "label", text: row.label }), el("b", { text: row.value }), el("small", { text: row.note || "" }));
}

function drawTiles() {
  const panel = state.panel;
  if (!panel) return;
  const label = panelStates()[panel.state] || panel.state;
  const level = panel.state === "showing" ? "" : panel.state === "error" ? "bad" : "warn";
  const rows = [{ label: t("Panel"), value: label, note: panel.detail || panel.target || "", level }];
  rows.push(...describeStats(panel.stats, t));
  if (panel.stats?.uptime_s !== undefined) {
    rows.push({ label: t("Running for"), value: duration(panel.stats.uptime_s), note: t("the numbers count from the start of the panel loop"), level: "" });
  }
  $("tiles").replaceChildren(...rows.map(tile));
}

// -- sensor sources -----------------------------------------------------------------------

function drawSources() {
  const withDetails = state.sources.some((source) => source.details.length);
  const rows = state.sources.flatMap((source) => [
    el(
      "tr",
      {},
      el("td", {}, dot(source.problem ? "waiting" : "showing")),
      el("td", {}, el("code", { text: source.name })),
      el("td", { class: "number", text: String(source.readings) }),
      withDetails ? el("td", { text: source.details.map(([name, value]) => `${name}: ${value}`).join(" · ") }) : null,
    ),
    source.hint ? el("tr", {}, el("td"), el("td", { colspan: 3, class: "hint-row", text: source.hint })) : null,
  ]);
  $("sources").replaceChildren(
    rows.length
      ? el(
          "table",
          { class: "list" },
          el("tr", {}, el("th"), el("th", { text: t("Source") }), el("th", { class: "number", text: t("Readings") }), withDetails ? el("th", { text: t("Details") }) : null),
          ...rows,
        )
      : note("", t("No sensor source is running.")),
  );
}

// -- services -----------------------------------------------------------------------------

function drawServices() {
  const states = serviceStates();
  const busy = new Set([...document.querySelectorAll("#services button:disabled")].map((b) => b.dataset.service));
  const withReasons = state.services.some((service) => service.detail);
  const rows = state.services.map((service) => {
    const [dotState, words] = states[service.state] || ["gone", service.state];
    const button = el("button", { type: "button", class: "small", "data-service": service.name, text: t("Restart"), onclick: () => restart(service.name, button) });
    button.disabled = busy.has(service.name);
    return el(
      "tr",
      {},
      el("td", {}, dot(dotState)),
      el("td", {}, el("code", { text: service.name })),
      el("td", { text: words }),
      withReasons ? el("td", { class: "detail", text: service.detail }) : null,
      el("td", { class: "action" }, service.restartable ? button : ""),
    );
  });
  $("services").replaceChildren(
    el("table", { class: "list" }, el("tr", {}, el("th"), el("th", { text: t("Service") }), el("th", { text: t("State") }), withReasons ? el("th", { text: t("Reason") }) : null, el("th")), ...rows),
    state.services.some((s) => s.restartable) ? "" : note("", t("No service is switched on. Add them to “enabled” under [services] in config.toml.")),
  );
}

async function restart(name, button) {
  button.disabled = true;
  const answer = await send("restart", { service: name });
  if (answer) {
    const [, words] = serviceStates()[answer.state] || ["gone", answer.state];
    say(t("{name}: {state}", { name, state: words }), answer.state === "running" ? "" : "warn");
  }
  await refresh();
}

// -- test messages ------------------------------------------------------------------------

function drawProbes() {
  if ($("probes").childElementCount === state.probes.length) return;
  $("probes").replaceChildren(
    ...state.probes.map((probe) =>
      el("button", {
        type: "button",
        text: probe.label,
        onclick: async () => {
          const answer = await send("probe", { kind: probe.kind });
          if (!answer) return;
          const result = $("probe-result");
          result.textContent = `${answer.name}: ${answer.message}`;
          result.className = answer.shown ? "" : "warn-text";
        },
      }),
    ),
  );
}

// -- theme per mode -----------------------------------------------------------------------

function drawThemes() {
  const themes = state.themes;
  const signature = JSON.stringify([themes.available, themes.normal, themes.game, themes.shown, themes.mode]);
  if (signature === themesShown || $("themes").contains(document.activeElement)) return;
  themesShown = signature;
  const choose = (mode, current, first) => {
    const ids = themes.available.includes(current) || !current ? themes.available : [...themes.available, current];
    const select = el("select", { "aria-label": mode === "normal" ? t("Theme in normal mode") : t("Theme in game mode") });
    if (first) select.append(el("option", { value: "", text: first }));
    for (const id of ids) select.append(el("option", { value: id, text: themes.available.includes(id) ? id : t("{id} (not installed)", { id }) }));
    select.value = current;
    select.addEventListener("change", async () => {
      const answer = await send("theme", { mode, theme: select.value });
      if (answer) say(t("Saved to config.toml."));
      themesShown = "";
      await refresh();
    });
    return select;
  };
  $("themes").replaceChildren(
    el(
      "div",
      { class: "fields" },
      el("label", { class: "field" }, el("span", { text: t("Normal mode") }), choose("normal", themes.normal, "")),
      el("label", { class: "field" }, el("span", { text: t("Game mode") }), choose("game", themes.game, t("same as the normal mode"))),
    ),
    el("p", { class: "muted" }, themes.shown ? t("Showing now: {theme}. A service such as the autopilot can show another theme for a while.", { theme: themes.shown }) : t("The theme is written to config.toml; the panel follows at once.")),
  );
}

// -- log ----------------------------------------------------------------------------------

async function refreshLog() {
  const log = await api("log?lines=80");
  $("log-where").textContent = log.exists
    ? log.path
    : t("There is no log file yet. The tray app writes it to {path}; “libre-panel start” logs to its terminal.", { path: log.path });
  const box = $("log-text");
  const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
  box.textContent = log.lines.join("\n");
  if (atEnd) box.scrollTop = box.scrollHeight;
}
