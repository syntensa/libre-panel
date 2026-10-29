// Screen (#/screen): the panel as a monitor (/api/mirror, mirror.py): a screen of this computer
// on a panel instead of its theme, as it changes. What it shows: the panel's own monitor (the computer extends its
// desktop to it: extend.py; on Windows the Virtual Display Driver, set up here once), one of the
// monitors, or a window; how it fills the panel, the mouse pointer with it or not, on which
// panels. On Wayland the desktop's dialog may ask which monitor or window, once.
// (index.js: the view)

import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { setPref, store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Segmented, Select, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toastError } from "../../ui/toast.js";

const POLL_MS = 3000;
const ASK = "ask"; // (wayland.py: the desktop's dialog asks which)

async function load(windows = false) {
  try {
    const data = await endpoints.mirror(windows);
    const before = store.get().mirror;
    // the windows are listed when asked for; kept until asked again
    store.set({ mirror: { ...data, windows: data.windows ?? before?.windows ?? null } });
  } catch (error) {
    if (!error.quiet) store.set({ mirror: { error: error.message } });
  }
}

async function act(body) {
  try {
    const data = await endpoints.mirrorAction(body);
    store.set({ mirror: { ...data, windows: store.get().mirror?.windows ?? null } });
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

/** The choice kept in this window: {kind, id, name}, fit, cursor, panels. */
const chosen = (prefs) => ({
  source: prefs.mirrorSource || { kind: "virtual", id: "", name: "" },
  fit: prefs.mirrorFit || "contain",
  cursor: prefs.mirrorCursor !== false,
  panels: Array.isArray(prefs.mirrorPanels) && prefs.mirrorPanels.length ? prefs.mirrorPanels : [1],
});

const sizeOf = (m) => `${m.width} × ${m.height}`;

/** What the panel's own monitor needs here, said for the person; null: nothing. */
function ownState(data) {
  const extend = data.extend || {};
  if (extend.running) return { tone: "info", text: t("Setting up the panel's own monitor… Windows may ask for permission.") };
  if (extend.error) return { tone: "warn", text: t("Not set up: {error}", { error: extend.error }) };
  if (extend.how === "vdd" && extend.missing === "driver")
    return { tone: "info", text: t("Windows needs a display driver for it: the Virtual Display Driver (open source, signed). Set it up installs it and gives it the panel's size; Windows asks for permission once."), setup: true };
  if (extend.how === "vdd" && extend.missing === "resolution")
    return { tone: "info", text: t("The Virtual Display Driver does not know the panel's size yet: Set it up gives it ({size}); Windows asks for permission once.", { size: data.size ? data.size.join(" × ") : "?" }), setup: true };
  if (extend.missing === "gstreamer") return { tone: "warn", text: t("On Wayland the screen comes through GStreamer: install gst-launch-1.0 and its PipeWire plugin.") };
  if (extend.missing === "jeepney") return { tone: "warn", text: t("On Wayland the screen comes through D-Bus: this Libre Panel lacks the jeepney library.") };
  if (extend.missing === "xrandr") return { tone: "warn", text: t("On X11 the panel's own monitor needs xrandr.") };
  if (extend.missing === "portal") return { tone: "warn", text: t("This desktop shares no screen (no screen-sharing portal).") };
  if (!extend.how) return { tone: "warn", text: t("Not on this system.") };
  return null;
}

function Hint({ tone = "info", children }) {
  return html`<p class=${`screen-note mirror-hint ${tone}`}>
    <${Icon} name=${tone === "info" ? "info" : "warning"} size=${15} /> <span>${children}</span>
  </p>`;
}

export function MonitorSection() {
  const data = useStore((s) => s.mirror);
  const prefs = useStore((s) => s.prefs);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const next = async () => {
      if (!alive) return;
      if (!document.hidden) await load();
      if (alive) timer = setTimeout(next, POLL_MS);
    };
    next();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);
  if (!data) return null;
  if (data.error && !data.platform) return html`<section class="screen-card mirror"><${Hint} tone="warn">${data.error}<//></section>`;
  const choice = chosen(prefs);
  const showing = data.showing;
  const wayland = data.platform === "wayland";
  const own = ownState(data);
  const choose = (source) => setPref("mirrorSource", source);
  const source = choice.source;
  const isOn = (kind, id = "") => source.kind === kind && (kind === "virtual" || source.id === id);
  const monitors = data.monitors || [];
  const windows = data.windows || [];
  const panels = data.panels || [];
  const start = async () => {
    setBusy(true);
    await act({ action: "show", source: { ...source, fit: choice.fit, cursor: choice.cursor }, panels: choice.panels });
    setBusy(false);
  };
  const setUp = async () => {
    setBusy(true);
    await act({ action: "setup" });
    setBusy(false);
  };
  const shownName = showing
    ? showing.source.kind === "virtual"
      ? t("the panel's own monitor")
      : showing.source.name || showing.source.id
    : "";
  return html`<section class="screen-card mirror" data-mirror>
    <header class="mirror-head">
      <h2><${Icon} name="monitor" size=${18} /> ${t("As a monitor")}</h2>
      <span class="muted">${t("A screen of this computer on the panel instead of its theme, as it changes.")}</span>
    </header>
    ${!data.available ? html`<${Hint}>${t("A panel shows the screen in the running app. Start Libre Panel and open this page from it.")}<//>` : null}
    <div class="screen-row mirror-sources" role="group" aria-label=${t("What the panel shows")}>
      <button type="button" class=${`chip${isOn("virtual") ? " is-on" : ""}`} aria-pressed=${String(isOn("virtual"))}
        data-mirror-source="virtual" onClick=${() => choose({ kind: "virtual", id: "", name: "" })}>
        <${Icon} name="monitor" size=${14} /> ${t("Its own monitor")}
      </button>
      ${monitors.filter((m) => !m.virtual).map((m) => html`<button type="button" key=${m.id}
          class=${`chip${isOn("monitor", m.id) ? " is-on" : ""}`} aria-pressed=${String(isOn("monitor", m.id))}
          data-mirror-source=${m.id} onClick=${() => choose({ kind: "monitor", id: m.id, name: m.name })}>
          ${m.name} · ${sizeOf(m)}${m.primary ? ` · ${t("main")}` : ""}
        </button>`)}
      ${wayland
        ? html`<button type="button" class=${`chip${isOn("monitor", ASK) ? " is-on" : ""}`} aria-pressed=${String(isOn("monitor", ASK))}
              onClick=${() => choose({ kind: "monitor", id: ASK, name: "" })}>${t("A monitor (the desktop asks)")}</button>
            <button type="button" class=${`chip${isOn("window", ASK) ? " is-on" : ""}`} aria-pressed=${String(isOn("window", ASK))}
              onClick=${() => choose({ kind: "window", id: ASK, name: "" })}><${Icon} name="window" size=${14} /> ${t("A window (the desktop asks)")}</button>`
        : html`<span class="mirror-window">
            <${Select} label=${t("A window")} value=${source.kind === "window" ? source.id : ""}
              options=${[
                { value: "", label: data.windows ? t("A window…") : t("A window… (list them)") },
                ...windows.map((w) => ({ value: w.id, label: w.program ? `${w.title} — ${w.program}` : w.title })),
              ]}
              onChange=${(value) => {
                const found = windows.find((w) => w.id === value);
                if (found) choose({ kind: "window", id: found.id, name: found.title });
                else load(true);
              }} />
            <button type="button" class="btn btn-quiet" onClick=${() => load(true)}>${t("List the windows")}</button>
          </span>`}
    </div>
    ${source.kind === "virtual" && own ? html`<${Hint} tone=${own.tone}>${own.text}<//>` : null}
    ${source.kind === "virtual" && !own
      ? html`<${Hint}>${wayland
          ? t("The desktop makes the panel's monitor as it starts and takes it away as it stops. Arrange it in the system's display settings; on KDE the dialog offers it as a new virtual output.")
          : t("It goes on the desktop as it starts, right of the others, and off again as it stops. Windows go there as to any monitor.")}<//>`
      : null}
    <div class="screen-row">
      <span class="screen-label">${t("Fills the panel")}</span>
      <${Segmented} label=${t("How it fills the panel")} value=${choice.fit} onChange=${(value) => setPref("mirrorFit", value)}
        options=${[
          { value: "contain", label: t("Whole") },
          { value: "cover", label: t("To the edges") },
          { value: "stretch", label: t("Stretched") },
        ]} />
    </div>
    <div class="screen-row">
      <span class="screen-label">${t("The mouse pointer")}</span>
      <${Toggle} label=${t("The mouse pointer")} checked=${choice.cursor} onChange=${(on) => setPref("mirrorCursor", on)} />
    </div>
    ${panels.length > 1
      ? html`<div class="screen-row">
          <span class="screen-label">${t("On")}</span>
          <div class="screen-panels" role="group" aria-label=${t("The panels it shows on")}>
            ${panels.map((panel) => {
              const on = choice.panels.includes(panel.number);
              return html`<button type="button" class=${`chip${on ? " is-on" : ""}`} aria-pressed=${String(on)}
                onClick=${() => {
                  const next = on ? choice.panels.filter((n) => n !== panel.number) : [...choice.panels, panel.number];
                  setPref("mirrorPanels", next.length ? next.sort((a, b) => a - b) : [panel.number]);
                }}>${panel.number} · ${panel.name || t("Panel")}</button>`;
            })}
          </div>
        </div>`
      : null}
    <div class="mirror-actions">
      ${showing
        ? html`<span class="mirror-showing" data-mirror-showing>
              <span class="dot"></span> ${t("The panel shows {name}", { name: shownName })}
            </span>
            <button type="button" class="btn" onClick=${() => act({ action: "stop" })}><${Icon} name="stop" size=${14} /> ${t("The theme again")}</button>`
        : null}
      ${own?.setup && source.kind === "virtual"
        ? html`<button type="button" class="btn btn-primary" disabled=${busy || data.extend?.running} onClick=${setUp}>${t("Set it up")}</button>`
        : html`<button type="button" class="btn btn-primary" data-mirror-start
            disabled=${busy || !data.available || (source.kind === "virtual" && own && own.tone === "warn")}
            onClick=${start}><${Icon} name="play" size=${14} /> ${showing ? t("Show this instead") : t("Show it on the panel")}</button>`}
    </div>
    <p class="muted mirror-small">${t("A picture is about a tenth of a second old on a TURZX panel, a little more on others: for windows, chats and music, not for games. A panel takes no touch.")}</p>
  </section>`;
}
