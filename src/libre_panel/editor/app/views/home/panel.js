// The panel on Start: which model it is (the one the background app sends frames to, or the
// one the user picked to see themes for), the strip that says so on a first start, and the
// card of the launcher with the picture on the panel, its state and its controls.

import { normalTheme, openTheme, themeName } from "../../core/actions.js";
import { appAction } from "../../core/app.js";
import { endpoints } from "../../core/api.js";
import { useLivePicture } from "../../core/console.js";
import { formatNumber, t } from "../../core/i18n.js";
import { VERTICAL, themeEntry, useMounted, versionFor } from "../../core/orientation.js";
import { saveSettings } from "../../core/settings.js";
import { setPref, store, useStore } from "../../core/state.js";
import { runtime } from "../../core/stats.js";
import { designPages, target, targetStatus, useTarget } from "../../core/target.js";
import { html, useRef, useState } from "../../lib/preact.js";
import { modeName, panelName, stateText } from "../../shell/panel-popover.js";
import { navigate } from "../../shell/views.js";
import { Segmented, Slider, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { Popover } from "../../ui/layer.js";

/** The model the app sends frames to: what it says it shows on starts with the model's label. */
export function connectedModel(app, models) {
  const target = app?.available ? app.panel?.target : "";
  return (target && models.find((model) => target.startsWith(model.label))) || null;
}

/** The panel Start shows themes for: the one picked, else the one connected, else none. */
export function panelModelNow() {
  const { specs, app, prefs } = store.get();
  const models = specs?.models || [];
  const picked = prefs.startModel ? models.find((model) => model.id === prefs.startModel) : null;
  return { model: picked || connectedModel(app, models), picked, connected: connectedModel(app, models) };
}

export function usePanelModel() {
  useStore((s) => [s.specs, s.app.panel?.target, s.prefs.startModel]);
  return panelModelNow();
}

const protocolName = (model) => (model.protocol_name.startsWith("USB bulk") ? "USB" : t("serial"));

/** How the model's panel plugs in, unless another model has the same name (two Turing 8.8″). */
const connection = (model, models) => (models.filter((other) => other.label === model.label).length > 1 ? null : protocolName(model));

/** The state of the panel as the card says it: a title, the dot's state and the line below.
 * ``status``: the panel's, where it is not the first (target.js). */
export function panelSummary(app, model, shown, status = app.panel) {
  const panel = status || {};
  const stats = panel.stats || {};
  const name = model ? panelName(model) : null;
  const theme = themeName(panel.theme || shown);
  const state = app.quit ? "stopped" : panel.state || "starting";
  const titles = {
    showing: name ? t("{panel} shows “{theme}”", { panel: name, theme }) : t("Showing “{theme}”", { theme }),
    paused: name ? t("{panel} is paused", { panel: name }) : t("Paused"),
    waiting: t("Waiting for the panel"),
    starting: t("Starting"),
    error: t("Needs attention"),
    stopped: t("Stopped"),
  };
  // in plain words: how smooth, for how long (the data rate and the rest are on Live)
  const parts = [];
  if (stats.streaming) parts.push(t("Smooth motion"));
  if (typeof stats.fps === "number") parts.push(t("{n} frames per second", { n: formatNumber(stats.fps, 0) }));
  if (typeof stats.uptime_s === "number") parts.push(t("running for {time}", { time: runtime(stats.uptime_s) }));
  if (!parts.length && panel.target) parts.push(panel.target);
  if (panel.mode) parts.push(modeName(panel.mode));
  if (app.quit) parts.splice(0, parts.length, t("Libre Panel has quit."));
  // the theme config.toml names is missing: the panel shows its own until another is chosen
  const detail = panel.detail || panel.notice || "";
  return { state, title: titles[state] || stateText(state), line: parts.join(" · "), detail };
}

// -- the strip of a first start and of the gallery -------------------------------------------

/** Which panel the themes are for: a model list, grouped by maker, to pick another one. */
function PanelPicker({ models, current, connected, onPick, onClose }) {
  const groups = [];
  for (const model of models) {
    let group = groups.find((item) => item.vendor === model.vendor);
    if (!group) groups.push((group = { vendor: model.vendor, models: [] }));
    group.models.push(model);
  }
  return html`<div class="pop-section">
      <div class="pop-title">${t("Show themes for")}</div>
      <div class="model-list" role="listbox" aria-label=${t("Panel model")}>
        ${groups.map(
          (group) => html`<div class="model-group" key=${group.vendor}>
            <div class="model-vendor">${group.vendor}</div>
            ${group.models.map(
              (model) => html`<button type="button" role="option" key=${model.id} aria-selected=${String(model.id === current?.id)}
                class="model-row" title=${model.limits || null} onClick=${() => onPick(model.id)}>
                <span class=${`model-shape${model.shape === "round" ? " round" : ""}`}></span>
                <span class="model-name">${panelName(model)}</span>
                <span class="model-size mono">${model.landscape[0]} × ${model.landscape[1]}</span>
                <span class="model-proto">${protocolName(model)}</span>
              </button>`,
            )}
          </div>`,
        )}
      </div>
    </div>
    <div class="pop-section">
      <div class="pop-actions">
        ${connected && current?.id !== connected.id
          ? html`<button type="button" class="btn" onClick=${() => onPick(null)}>${t("Use the connected panel")}</button>`
          : null}
        <button type="button" class="btn btn-quiet" onClick=${() => (onClose(), navigate("settings", "panel"))}>
          <${Icon} name="gear" size=${14} /> ${t("Panel settings")}
        </button>
      </div>
    </div>`;
}

/** The panel at the top of a first start: whether it is there, and a way to name another.
 * `compact`: a line, for the gallery. */
export function PanelStrip({ compact = false }) {
  const { model, picked, connected } = usePanelModel();
  const models = useStore((s) => s.specs?.models || []);
  const app = useStore((s) => s.app);
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  const stats = app.panel?.stats || {};
  let title = t("No panel found yet");
  let line = t("Plug it in with its USB cable, or say which panel you have: the themes are shown for it.");
  if (connected && !picked) title = t("Your panel is here: {panel}", { panel: panelName(connected) });
  else if (model) title = t("Themes for {panel}", { panel: panelName(model) });
  if (model) {
    const parts = [`${model.landscape[0]} × ${model.landscape[1]}`, connection(model, models)].filter(Boolean);
    if (connected && !picked && stats.streaming) parts.push(t("smooth motion with {n} frames per second is on", { n: stats.target_fps }));
    else if (model.video) parts.push(t("smooth motion is possible"));
    line = parts.join(" · ");
  }
  const round = model?.shape === "round";
  const ratio = model ? model.landscape[0] / model.landscape[1] : 2;
  const shape = round ? { width: "48px", height: "48px" } : { width: `${Math.round(Math.max(48, Math.min(120, 40 * ratio)))}px`, height: "40px" };
  return html`<section class=${`panel-strip${model ? "" : " none"}${compact ? " compact" : ""}`} aria-label=${t("Panel")}>
    <span class=${`panel-strip-shape${round ? " round" : ""}`} style=${shape} aria-hidden="true"></span>
    <div class="panel-strip-text">
      <strong>${title}</strong>
      <span class="muted">${line}</span>
    </div>
    <button ref=${anchor} type="button" class="btn" aria-haspopup="dialog" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      ${model ? t("Another panel") : t("Choose a panel")}
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} placement="bottom-end" label=${t("Panel")} width=${380}>
      <${PanelPicker} models=${models} current=${model} connected=${connected}
        onPick=${(id) => (setPref("startModel", id), setOpen(false))} onClose=${() => setOpen(false)} />
    <//>
  </section>`;
}

// -- the card of the launcher ------------------------------------------------------------------

/** The theme on the panel the app is about: the one the panel says, else the one its pages
 * name (a panel beside the first), else config.toml's. */
const shownTheme = (app, status = app.panel) =>
  (app.available && status?.theme) || (target() > 1 ? designPages()?.pages?.[0]?.theme : null) || normalTheme();

/** Open the theme on the panel in Design (asks about unsaved changes first). */
export async function editShown() {
  const state = store.get();
  const id = shownTheme(state.app, targetStatus(state));
  if (id && state.doc?.id !== id && !(await openTheme(id, { page: "normal" }))) return;
  navigate("design");
}

function Picture({ app, status, number, theme, size }) {
  const running = app.available && !app.quit;
  const state = status?.state;
  const live = useLivePicture({ width: 960, every: 1000, on: running && state === "showing", panel: number });
  const ratio = size ? `${size[0]} / ${size[1]}` : "4 / 1";
  let note = null;
  if (running && live.src === null) {
    note =
      state === "paused"
        ? t("The panel is paused: there is no picture.")
        : state === "error"
          ? t("The panel needs attention: there is no picture.")
          : state === "waiting"
            ? t("No panel yet: this is the theme it will show.")
            : t("There is no picture yet.");
  }
  // what the panel shows; until there is a picture of it (and without the app) the theme's own,
  // which the server draws, dimmed under the note that says why
  const own = theme ? endpoints.previewUrl(versionFor(theme), 960) : null;
  const src = (running && live.src) || own;
  const upright = size && size[1] > size[0];
  return html`<div class=${`panel-picture${upright ? " upright" : ""}${note ? " still" : ""}`} style=${{ aspectRatio: ratio }}>
    ${src ? html`<img src=${src} alt=${running && live.src ? t("What the panel shows now") : t("The theme on the panel")} />` : null}
    ${note ? html`<p class="panel-picture-note">${note}</p>` : null}
  </div>`;
}

const sameSize = (a, b) => Boolean(a && b) && a[0] === b[0] && a[1] === b[1];

/**
 * Whether how the panel is mounted changes anything for it: a theme made for it has a version
 * for a panel on end (Lumen Vertical for a bar). A panel not known yet may be a bar; a round
 * one, a 3.5″ or a 12.3″ has no such version and is not asked.
 */
export function asksMounting(model) {
  const { themes, versions } = store.get();
  return (versions || []).some((version) => {
    const base = themes.find((item) => version.id === `${item.id}${VERTICAL}`);
    return Boolean(base) && (!model || base.model === model.id || sameSize(base.size, model.landscape));
  });
}

/** How the panel is mounted: on its side or on end (config.toml's device.orientation). The
 * screens show in their version for it, Lumen Vertical for Lumen on a bar on end. */
export function MountedChoice({ big = false }) {
  const way = useMounted(); // "" until chosen: then neither is marked, and either click says so
  return html`<${Segmented}
    class=${big ? "mounted-choice big" : "mounted-choice"}
    label=${t("How the panel is mounted")}
    value=${way}
    onChange=${(value) => value !== way && saveSettings({ "device.orientation": value })}
    options=${[
      { value: "landscape", icon: "landscape", label: t("Horizontal") },
      { value: "portrait", icon: "portrait", label: t("Vertical") },
    ]}
  />`;
}

/** The size of the picture on the panel: the connected panel's, the way it is mounted (a theme
 * made for another size is scaled to it), else the theme's own. */
function pictureSize(model, theme, way) {
  if (model) return way === "portrait" ? model.portrait || [...model.landscape].reverse() : model.landscape;
  return themeEntry(versionFor(theme))?.size;
}

/**
 * The panel at a glance, at the top of Start: its state, what it shows with the way to edit
 * that, its picture, and pause, brightness and upside down. How the panel is mounted is a
 * choice made once: the first start, the first steps (or the news) and Settings → Panel ask it.
 */
export function PanelCard() {
  const { app } = useStore((s) => ({ app: s.app, themes: s.themes, versions: s.versions }));
  const way = useMounted();
  const models = useStore((s) => s.specs?.models || []);
  const number = useTarget(); // the panel the header chose: Start shows that one
  const status = useStore((s) => targetStatus(s));
  const first = number === 1;
  const { connected } = usePanelModel();
  const model = first ? connected : connectedModel({ available: app.available, panel: status }, models);
  const running = app.available && !app.quit;
  const shown = shownTheme(app, status);
  const summary = app.available ? panelSummary(app, model, shown, status) : null;
  const paused = status?.state === "paused";
  return html`<section class="panel-card" aria-label=${t("Panel")}>
    <div class="panel-card-head">
      <span class=${`state-dot ${summary ? summary.state : "none"}`} aria-hidden="true"></span>
      <div class="panel-card-title">
        <h2>${summary ? summary.title : themeName(shown)}</h2>
        <p class="muted">
          ${summary
            ? summary.line
            : t("This window is not connected to the running panel. Open Libre Panel from your apps (or its icon in the tray) to see the panel live and control it here.")}
        </p>
        ${summary?.detail ? html`<p class="panel-card-detail">${summary.detail}</p>` : null}
      </div>
      <button type="button" class="btn btn-primary" onClick=${editShown}>
        <${Icon} name="pencil" size=${16} strokeWidth=${2.2} /> ${t("Edit theme")}
      </button>
    </div>
    <${Picture} app=${app} status=${status} number=${number} theme=${shown}
      size=${first ? pictureSize(running ? connected : null, shown, way) : status?.size || pictureSize(running ? model : null, shown, "landscape")} />
    ${running && !first
      ? html`<div class="panel-card-controls">
          <button type="button" class="btn" onClick=${() => appAction(paused ? "resume" : "pause")}>
            <${Icon} name=${paused ? "play" : "pause"} size=${14} /> ${paused ? t("Resume panels") : t("Pause panels")}
          </button>
          <span class="muted">${t("Brightness and mounting of this panel: Settings → Panel.")}</span>
          <span class="grow"></span>
          <button type="button" class="link-btn" onClick=${() => navigate("settings", "panel")}>${t("Panel settings")}</button>
        </div>`
      : null}
    ${running && first
      ? html`<div class="panel-card-controls">
          <button type="button" class="btn" onClick=${() => appAction(paused ? "resume" : "pause")}>
            <${Icon} name=${paused ? "play" : "pause"} size=${14} /> ${paused ? t("Resume panel") : t("Pause panel")}
          </button>
          <label class="panel-card-brightness">
            <${Icon} name="sun" size=${16} />
            <span>${t("Brightness")}</span>
            <${Slider} label=${t("Brightness")} value=${app.brightness ?? 0} step=${5} format=${(v) => `${v} %`}
              onCommit=${(value) => appAction("brightness", value)} />
          </label>
          <span class="grow"></span>
          <label class="panel-card-flip">
            <span>${t("Upside down")}</span>
            <${Toggle} label=${t("Upside down")} checked=${app.upside_down} disabled=${app.upside_down === null}
              onChange=${(on) => appAction("upside_down", on)} />
          </label>
        </div>`
      : null}
  </section>`;
}
