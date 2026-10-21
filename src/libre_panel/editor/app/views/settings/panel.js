// Settings → Panel: the panel that was found, smooth motion (the video mode) and its frame
// rate, the name of the standby clip while smooth motion lacks it, brightness and which way up
// the panel is mounted, the start screen; under Advanced where the pictures go, the file they go
// to without a panel, the standby clip's name once it is set, the serial port and ffmpeg; and the
// panels beside this one, each with its model, theme, brightness and port.

import { normalTheme, themeName } from "../../core/actions.js";
import { endpoints } from "../../core/api.js";
import { versionBase } from "../../core/orientation.js";
import { t } from "../../core/i18n.js";
import {
  loadClipQuestion,
  loadPanelInfo,
  loadSettings,
  saveSettings,
  setting,
  useCatalog,
  useConfig,
} from "../../core/settings.js";
import { store, useStore } from "../../core/state.js";
import { videoState } from "../../core/video.js";
import { html, useEffect, useRef, useState } from "../../lib/preact.js";
import { panelName, stateText } from "../../shell/panel-popover.js";
import { Segmented, Select, Slider, Toggle } from "../../ui/controls.js";
import { Popover } from "../../ui/layer.js";
import { MountedChoice } from "../home/panel.js";
import { fitsPanel } from "../home/themes.js";
import { Arrangement, arrangementItem } from "./arrangement.js";
import { Card, CommitField, Disclosure, SettingRow, Status, usePolling } from "./parts.js";
import { StartScreenCard } from "./startscreen.js";

/** Write settings, and ask about the panel again: what they change shows there. */
async function change(changes) {
  if (await saveSettings(changes)) loadPanelInfo();
}

export const loadPanelSection = () => Promise.all([loadSettings(), loadPanelInfo()]);

const connectionName = (model) =>
  model.connection === "usb" ? "USB" : model.port ? t("USB serial ({port})", { port: model.port }) : t("USB serial");

// -- the panel that was found ----------------------------------------------------------------

/** The panel's shape, as mounted, in the card; round ones are round. */
function Outline({ model, tone }) {
  const [width, height] = model ? model.size : [16, 5];
  const scale = Math.min(150 / width, 56 / height);
  return html`<span
    class=${`panel-outline ${tone}${model?.shape === "round" ? " round" : ""}${model ? "" : " none"}`}
    style=${{ width: `${Math.max(28, Math.round(width * scale))}px`, height: `${Math.max(28, Math.round(height * scale))}px` }}
  ></span>`;
}

function PanelPicker({ configured, onPick }) {
  const models = useStore((s) => s.specs?.models || []);
  const groups = [];
  for (const model of models) {
    let group = groups.find((item) => item.vendor === model.vendor);
    if (!group) groups.push((group = { vendor: model.vendor, models: [] }));
    group.models.push(model);
  }
  return html`<div class="pop-section">
    <div class="pop-title">${t("Which panel is connected")}</div>
    <div class="model-list" role="listbox" aria-label=${t("Panel model")}>
      <button type="button" role="option" class="model-row" aria-selected=${String(configured === "auto")} onClick=${() => onPick("auto")}>
        <span class="model-shape free"></span>
        <span class="model-name">${t("Detect automatically")}</span>
        <span class="model-size"></span>
        <span class="model-proto"></span>
      </button>
      ${groups.map(
        (group) => html`<div key=${group.vendor}>
          <div class="model-vendor">${group.vendor}</div>
          ${group.models.map(
            (model) => html`<button
              type="button"
              role="option"
              key=${model.id}
              class="model-row"
              aria-selected=${String(model.id === configured)}
              onClick=${() => onPick(model.id)}
            >
              <span class=${`model-shape${model.shape === "round" ? " round" : ""}`}></span>
              <span class="model-name">${panelName(model)}</span>
              <span class="model-size mono">${model.landscape[0]} × ${model.landscape[1]}</span>
              <span class="model-proto" title=${model.protocol_name.startsWith("USB bulk") ? null : t("USB serial: slower, without smooth motion")}>${model.protocol_name.startsWith("USB bulk") ? "USB" : t("serial")}</span>
            </button>`,
          )}
        </div>`,
      )}
    </div>
  </div>`;
}

/**
 * A port with the USB ids of an awake Turing panel (rev. C), which other USB devices have too:
 * Libre Panel talks to it once the person says which panel it is (device.model).
 */
function MaybePanel({ maybe, onPick }) {
  return html`<div class="panel-maybe" data-maybe-port=${maybe.port}>
    <div class="settings-row-hint">${t("{port} may be a Turing panel (rev. C). Other USB devices have the same USB ids, so Libre Panel talks to it only once you say which panel it is:", { port: maybe.port })}</div>
    <div class="panel-maybe-choice">
      ${maybe.models.map((model) => html`<button type="button" class="btn" key=${model.id} onClick=${() => onPick(model.id)}>${panelName(model)}</button>`)}
    </div>
  </div>`;
}

function PanelFound() {
  const info = useStore((s) => s.panelInfo);
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  usePolling(loadPanelInfo, 6000); // a panel plugged in while this is open
  if (!info) return null;
  const { found, model, configured, running } = info;
  // the running app says why it waits for the panel (no permission, a missing library …)
  const why = running && ["waiting", "error"].includes(running.state) && running.detail;
  const same = Boolean(found && model && found.id === model.id);
  const shown = same ? found : model;
  let status = null; // without a panel there is nothing to say but how to get one
  if (same) status = html`<${Status} tone="ok">${t("Connected")}<//>`;
  else if (found && model)
    status = html`<${Status} tone="warn">${t("Another panel is connected: {name}", { name: panelName(found) })}<//>`;
  else if (model) status = html`<${Status} tone="warn">${t("Not connected")}<//>`;
  const details = shown ? [connectionName(shown), `${shown.size[0]} × ${shown.size[1]}`] : [];
  const pick = (id) => {
    setOpen(false);
    if (id !== configured) change({ "device.model": id });
  };
  return html`<${Card} id="panel-found" class="panel-found">
    <${Outline} model=${shown} tone=${same ? "on" : "off"} />
    <div class="panel-found-text">
      <div class="panel-found-name">${model ? panelName(model) : t("No panel found")}</div>
      <div class="panel-found-detail">
        ${details.length
          ? html`${details.join(" · ")} · ${status}`
          : html`${t("Plug it in: Libre Panel looks for it every few seconds. Until then the picture goes to a file.")}`}
      </div>
      ${configured !== "auto" ? html`<div class="settings-row-hint">${t("Chosen by hand. “Detect automatically” goes back to finding it.")}</div>` : null}
      ${shown && shown.connection !== "usb"
        ? html`<div class="settings-row-hint" data-serial-note>${t("An older USB serial panel: Libre Panel sends it only what changes, so numbers, bars and the clock keep up. It has no smooth motion, parts that move change less often than on a USB panel, and a new theme builds up for a moment.")}</div>`
        : null}
      ${why ? html`<div class="panel-found-why"><${Status} tone="warn">${stateText(running.state)}: ${running.detail}<//></div>` : null}
      ${!found && configured === "auto" ? (info.maybe || []).map((maybe) => html`<${MaybePanel} key=${maybe.port} maybe=${maybe} onPick=${pick} />`) : null}
    </div>
    <button ref=${anchor} type="button" class="btn" aria-haspopup="dialog" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      ${t("Another panel")}
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} placement="bottom-end" label=${t("Panel")} width=${380}>
      <${PanelPicker} configured=${configured} onPick=${pick} />
    <//>
  <//>`;
}

// -- smooth motion (the video mode) --------------------------------------------------------------

/** What smooth motion is doing, in a mark and words. */
function VideoStatus({ video, mode, fps }) {
  const state = videoState(video, mode, fps);
  return html`<${Status} tone=${state.tone}>${state.text}<//>`;
}

/** Whether the rate and the standby clip matter: smooth motion is not off, on a panel it is for. */
const videoApplies = (config, info) => Boolean(info.video.usb) && config.video.mode !== "off";

/** The name of the standby clip, written when it is typed (and so answers Start's question). */
function ClipField({ value }) {
  return html`<${CommitField}
    label=${t("Name of the standby clip")}
    value=${value}
    placeholder="usr/data/standby.h264"
    mono
    onCommit=${(text) => change({ "video.local_clip": text, "video.clip_asked": true })}
  />`;
}

/**
 * The name of the standby clip while smooth motion lacks it: the panel's own app gives it a name,
 * which a click takes; another can be typed. Nothing is written before either.
 */
function ClipRow({ value }) {
  const name = useStore((s) => s.clipQuestion?.name);
  useEffect(() => {
    if (!name) loadClipQuestion();
  }, []);
  const use = () => change({ "video.local_clip": name, "video.clip_asked": true });
  return html`<${SettingRow}
    stacked
    class="clip-row"
    title=${t("Name of the standby clip")}
    hint=${name
      ? t("Smooth motion needs the name of the clip your panel plays by itself when the PC starts. Its own app calls it {name}. Nothing is written until you choose it.", { name })
      : t("Smooth motion needs the name of the clip your panel plays by itself when the PC starts, as its own app calls it. Nothing is written until you type it.")}
  >
    <div class="clip-choice">
      <${ClipField} value=${value} />
      ${name ? html`<button type="button" class="btn btn-primary" onClick=${use}>${t("Use this name")}</button>` : null}
    </div>
  <//>`;
}

function VideoCard() {
  const config = useConfig();
  const info = useStore((s) => s.panelInfo);
  if (!config || !info) return null;
  const { mode, fps, local_clip: clip } = config.video;
  const video = info.video;
  const rates = [25, 30, 50, ...(fps && ![25, 30, 50].includes(fps) ? [fps] : [])].sort((a, b) => a - b);
  const applies = videoApplies(config, info);
  return html`<${Card} id="panel-video">
    <${SettingRow}
      title=${t("Smooth motion")}
      hint=${t("Up to 50 pictures a second, so clocks, graphs and moving parts glide instead of jumping. For TURZX USB panels.")}
      status=${html`<${VideoStatus} video=${video} mode=${mode} fps=${fps} />`}
    >
      <${Segmented}
        label=${t("Smooth motion")}
        value=${mode}
        onChange=${(value) => change({ "video.mode": value })}
        options=${[
          { value: "auto", label: t("Automatic") },
          { value: "on", label: t("On") },
          { value: "off", label: t("Off") },
        ]}
      />
    <//>
    ${applies
      ? html`<${SettingRow} title=${t("Frames per second")} hint=${t("50 is the smoothest; 25 and 30 ask less of the PC.")}>
          <${Segmented}
            label=${t("Frames per second")}
            value=${fps}
            onChange=${(value) => change({ "video.fps": value })}
            options=${rates.map((rate) => ({ value: rate, label: String(rate) }))}
          />
        <//>`
      : null}
    ${applies && video.gaps.includes("clip") ? html`<${ClipRow} value=${clip} />` : null}
    <${MotionRow} motion=${config.device.motion} serial=${info.model?.connection === "serial"} />
  <//>`;
}

/**
 * How much moves on the panel (device.motion): calm is a picture a second without gliding,
 * moving symbols or fades, which the older USB serial panels keep up with; automatic is calm on
 * them and full on the others.
 */
function MotionRow({ motion, serial }) {
  return html`<${SettingRow}
    class="motion-row"
    title=${t("Motion on the panel")}
    hint=${t("Calm: one picture a second, without gliding, moving symbols or fades, so a slow panel keeps up. Automatic is calm on the older USB serial panels and full on all others.")}
    status=${motion === "auto" && serial ? html`<${Status}>${t("Calm, as it is a USB serial panel")}<//>` : null}
  >
    <${Segmented}
      label=${t("Motion on the panel")}
      value=${motion}
      onChange=${(value) => value !== motion && change({ "device.motion": value })}
      options=${[
        { value: "auto", label: t("Automatic") },
        { value: "full", label: t("Full motion") },
        { value: "calm", label: t("Calm motion") },
      ]}
    />
  <//>`;
}

// -- brightness and which way up ------------------------------------------------------------

function DisplayCard() {
  const config = useConfig();
  const catalog = useCatalog();
  const [chosen, setChosen] = useState(null); // the slider stays where it is let go until it is written
  if (!config) return null;
  const { brightness, rotate, dark_on_quit: dark, dark_on_sleep: sleep, night, dark_when_away: away } = config.device;
  // sleep and locking are heard from Windows, and from logind on Linux
  const desktop = ["win32", "linux"].includes(catalog?.platform);
  const choose = (value) => {
    if (value === brightness) return;
    setChosen(value);
    saveSettings({ "device.brightness": value }).finally(() => setChosen(null));
  };
  return html`<${Card} id="panel-display">
    <${SettingRow} title=${t("Brightness")}>
      <${Slider}
        label=${t("Brightness")}
        value=${chosen ?? brightness}
        min=${0}
        max=${100}
        step=${5}
        format=${(value) => `${value} %`}
        onCommit=${choose}
      />
    <//>
    <${AccentRow} accent=${config.accent || ""} windows=${catalog?.platform === "win32"} />
    <${SettingRow}
      title=${t("How the panel is mounted")}
      hint=${t("Libre Panel's screens show in their version for it: Lumen Vertical for Lumen on a bar that stands on end.")}
    >
      <${MountedChoice} />
    <//>
    <${SettingRow}
      title=${t("Upside down")}
      hint=${t("For a panel that is mounted the other way round. The preview stays the right way up.")}
    >
      <${Toggle} label=${t("Upside down")} checked=${rotate === 180} onChange=${(on) => saveSettings({ "device.rotate": on ? 180 : 0 })} />
    <//>
    <${SettingRow}
      class="dark-row"
      title=${t("Dark when Libre Panel quits")}
      hint=${t("When Libre Panel quits, Windows shutting down too, the panel shows black and its light goes off; it lights up again when Libre Panel starts. Off: it keeps the last picture.")}
    >
      <${Toggle} label=${t("Dark when Libre Panel quits")} checked=${Boolean(dark)} onChange=${(on) => saveSettings({ "device.dark_on_quit": on })} />
    <//>
    <${SettingRow}
      class="night-row"
      title=${t("At night")}
      hint=${t("From one time to the other the panel has a brightness of its own, over midnight too; 0 is dark. The brightness above holds the rest of the day.")}
    >
      <${Toggle} label=${t("At night")} checked=${Boolean(night)} onChange=${(on) => saveSettings({ "device.night": on })} />
    <//>
    ${night ? html`<${NightFields} device=${config.device} />` : null}
    ${desktop
      ? html`<${SettingRow}
          class="away-row"
          title=${t("Dimmed while you are away")}
          hint=${catalog?.platform === "win32"
            ? t("While the computer is locked or its monitor is off, the panel takes the brightness below; 0 is dark. Back at the computer, it lights up again.")
            : t("While the session is locked, the panel takes the brightness below; 0 is dark. Unlocked, it lights up again.")}
        >
          <${Toggle} label=${t("Dimmed while you are away")} checked=${away !== false} onChange=${(on) => saveSettings({ "device.dark_when_away": on })} />
        <//>
        ${away !== false ? html`<${AwayBrightness} level=${config.device.away_brightness} />` : null}`
      : null}
    ${desktop
      ? html`<${SettingRow}
          class="sleep-row"
          title=${t("Dark while the computer sleeps")}
          hint=${t("The panel goes dark when the computer goes to sleep and lights up again when it wakes. Off: it keeps the last picture, all night where the USB port stays powered.")}
        >
          <${Toggle} label=${t("Dark while the computer sleeps")} checked=${sleep !== false} onChange=${(on) => saveSettings({ "device.dark_on_sleep": on })} />
        <//>`
      : null}
  <//>`;
}

/**
 * An accent colour for every theme instead of its own (config.toml accent): Windows' or one of
 * the person's; what is in the theme's accent colour takes it.
 */
function AccentRow({ accent, windows }) {
  const own = accent.startsWith("#");
  const mode = own ? "own" : accent || "theme";
  const choose = (value) => {
    if (value === mode) return;
    saveSettings({ accent: value === "theme" ? "" : value === "own" ? "#E0457B" : value });
  };
  return html`<${SettingRow}
    class="accent-row"
    title=${t("Accent colour")}
    hint=${t("Every screen in one colour of your choice: what is in its accent colour takes it. In Design the themes keep their own.")}
  >
    <div class="accent-choice">
      <${Segmented}
        label=${t("Accent colour")}
        value=${mode}
        onChange=${choose}
        options=${[
          { value: "theme", label: t("The theme's") },
          ...(windows || accent === "system" ? [{ value: "system", label: t("Windows'") }] : []),
          { value: "own", label: t("My own") },
        ]}
      />
      ${own
        ? html`<input type="color" class="accent-colour" aria-label=${t("My accent colour")} value=${accent.toLowerCase()}
            onChange=${(event) => saveSettings({ accent: event.currentTarget.value.toUpperCase() })} />`
        : null}
    </div>
  <//>`;
}

/** The panel's brightness while the person is away (0: dark). */
function AwayBrightness({ level }) {
  const [chosen, setChosen] = useState(null); // as the brightness slider above
  const choose = (value) => {
    if (value === level) return;
    setChosen(value);
    saveSettings({ "device.away_brightness": value }).finally(() => setChosen(null));
  };
  return html`<${SettingRow} class="away-brightness" title=${t("Brightness while you are away")}>
    <${Slider} label=${t("Brightness while you are away")} value=${chosen ?? level} min=${0} max=${100} step=${5}
      format=${(value) => (value ? `${value} %` : t("dark"))} onCommit=${choose} />
  <//>`;
}

/** When the night begins and ends (at times, or with the sun), and the panel's brightness then. */
function NightFields({ device }) {
  const config = useConfig();
  const [chosen, setChosen] = useState(null); // as the brightness slider above
  const level = device.night_brightness;
  const sun = device.night_from === "sunset" && device.night_to === "sunrise";
  const placed = config?.weather?.latitude != null && config?.weather?.longitude != null;
  const choose = (value) => {
    if (value === level) return;
    setChosen(value);
    saveSettings({ "device.night_brightness": value }).finally(() => setChosen(null));
  };
  const follow = (on) =>
    saveSettings(on ? { "device.night_from": "sunset", "device.night_to": "sunrise" } : { "device.night_from": "22:00", "device.night_to": "07:00" });
  return html`<${SettingRow} class="night-times" title=${t("Night from … to")} stacked>
    <div class="night-sun">
      <${Toggle} label=${t("From sunset to sunrise")} checked=${sun} onChange=${follow} />
      <span>${t("From sunset to sunrise")}</span>
    </div>
    ${sun
      ? html`<div class="settings-row-hint" data-night-sun>${placed ? t("At the place of the weather, day by day.") : t("Choose a place under Weather: until then the night goes from 19:00 to 7:00.")}</div>`
      : html`<div class="night-fields">
          <${CommitField} class="time" label=${t("Night begins")} value=${device.night_from} placeholder="22:00"
            onCommit=${(text) => saveSettings({ "device.night_from": text })} />
          <span aria-hidden="true">–</span>
          <${CommitField} class="time" label=${t("Night ends")} value=${device.night_to} placeholder="07:00"
            onCommit=${(text) => saveSettings({ "device.night_to": text })} />
        </div>`}
    <${Slider} label=${t("Brightness at night")} value=${chosen ?? level} min=${0} max=${100} step=${5}
      format=${(value) => (value ? `${value} %` : t("dark"))} onCommit=${choose} />
  <//>`;
}

// -- more panels at once ------------------------------------------------------------------------

const MORE_PANELS = 3; // beside this one (config.MORE_PANELS)
const OWN_FILE = /^libre-panel-frame-\d+\.png$/; // a panel's own picture file, by its place

/**
 * The panels beside this one ([[panels]]): each shows a theme of its own, on the same readings.
 * They are written back whole; one taken out moves those behind it up, and their picture file
 * by default with them.
 */
function MorePanelsCard() {
  const config = useConfig();
  const info = useStore((s) => s.panelInfo);
  const models = useStore((s) => s.specs?.models || []);
  const themes = useStore((s) => s.themes || []);
  const shownTheme = useStore((s) => s.app?.panel?.theme); // what panel 1 shows now
  if (!config) return null;
  const panels = config.panels || [];
  const running = info?.running?.more || [];
  const write = (next) => change({ panels: next });
  const edit = (n, changes) => write(panels.map((panel, i) => (i === n ? { ...panel, ...changes } : panel)));
  const remove = (n) =>
    write(panels.filter((_, i) => i !== n).map(({ output, ...panel }) => (OWN_FILE.test(output) ? panel : { ...panel, output })));
  const add = panels.length < MORE_PANELS
    ? html`<button type="button" class="btn" onClick=${() => added({})}>${t("Add a panel")}</button>`
    : null;
  // which is which: each panel shows its number a few seconds (identify.py)
  const identify = panels.length && info?.running
    ? html`<button type="button" class="btn" onClick=${() => endpoints.appAction("identify").catch(() => {})}>${t("Which is which?")}</button>`
    : null;
  // the themes of this panel and another change places (theirs shown, where it has none)
  const swap = async (n) => {
    const first = normalTheme();
    const other = panels[n].theme || running.find((item) => item.index === n)?.theme;
    if (!first || !other) return;
    await write(panels.map((panel, i) => (i === n ? { ...panel, theme: first } : panel)));
    store.set({ pages: await endpoints.setPage("normal", { theme: other }) });
  };
  const spare = panels.length < MORE_PANELS ? info?.spare || [] : [];
  // the theme that goes beside panel 1's: its companion, where it has one (Lumen Companion)
  const normal = normalTheme();
  const companion = normal ? themes.find((theme) => theme.id === `${versionBase(normal) || normal}-companion`) : null;
  // and while a game runs, Arena's companion beside Arena (the game's page)
  const gaming = themes.some((theme) => theme.id === "arena-companion") ? { game_theme: "arena-companion" } : {};
  const added = (more) => write([...panels, { ...(companion ? { theme: companion.id, same_look: true } : {}), ...gaming, ...more }]);
  // where they stand, in their real sizes: the first panel's model as set or found
  const modelOf = (id) => models.find((model) => model.id === id) || null;
  const device = config.device || {};
  const arrangement = [
    arrangementItem({
      number: 1,
      model: modelOf(device.model !== "auto" ? device.model : info?.model?.id),
      size: info?.running?.size,
      orientation: device.orientation,
      at: device.at,
      theme: shownTheme || normalTheme(),
    }),
    ...panels.map((panel, n) => {
      const seen = running.find((item) => item.index === n);
      return arrangementItem({
        number: n + 2,
        model: modelOf(panel.model && panel.model !== "auto" ? panel.model : seen?.model),
        size: seen?.size,
        orientation: panel.orientation,
        at: panel.at,
        theme: seen?.theme || panel.theme,
      });
    }),
  ];
  const place = (places) =>
    change({ "device.at": places[0], panels: panels.map((panel, n) => ({ ...panel, at: places[n + 1] })) });
  const lineUp = () => change({ "device.at": null, panels: panels.map((panel) => ({ ...panel, at: null })) });
  return html`<${Card}
    id="panel-more"
    title=${t("More panels")}
    text=${t("A second or third panel beside this one, each with a screen of its own. They show the same readings in single pictures; smooth motion, the game screen and the screens in turn are this panel's.")}
    action=${html`<span class="more-actions">${identify}${add}</span>`}
  >
    ${spare.map(
      (model) => html`<div class="more-spare" key=${`${model.id}${model.port || ""}`} data-spare=${model.id}>
        <span>${t("Another panel is plugged in: {name}. Show something on it too?", { name: panelName(model) })}</span>
        <button type="button" class="btn btn-primary"
          onClick=${() => added({ model: model.id, ...(model.port ? { port: model.port } : {}) })}>${t("Set it up")}</button>
      </div>`,
    )}
    ${panels.length ? html`<${Arrangement} items=${arrangement} onPlace=${place} onReset=${lineUp} />` : null}
    ${panels.map(
      (panel, n) => html`<${MorePanel}
        key=${n}
        n=${n}
        panel=${panel}
        running=${running.find((item) => item.index === n)}
        models=${models}
        themes=${themes}
        companion=${companion}
        onChange=${(changes) => edit(n, changes)}
        onRemove=${() => remove(n)}
        onSwap=${() => swap(n).catch(() => {})}
      />`,
    )}
  <//>`;
}

function MoreStatus({ panel, running }) {
  if (!panel.on) return html`<${Status}>${t("Off")}<//>`;
  if (!running) return null;
  if (running.state === "showing")
    return html`<${Status} tone="ok">${t("Shows {theme}", { theme: themeName(running.theme) })}<//>`;
  return html`<${Status} tone="warn">${stateText(running.state)}${running.detail ? `: ${running.detail}` : ""}<//>`;
}

function MorePanel({ n, panel, running, models, themes, companion, onChange, onRemove, onSwap }) {
  const [chosen, setChosen] = useState(null); // as the brightness slider above
  const model = models.find((item) => item.id === panel.model);
  const name = t("Panel {number}", { number: n + 2 });
  const brightness = (value) => {
    if (value === panel.brightness) return;
    setChosen(value);
    Promise.resolve(onChange({ brightness: value })).finally(() => setChosen(null));
  };
  const fits = (theme) => fitsPanel(theme, model);
  const themeOptions = [
    { value: "", label: t("The panel's own") },
    ...(companion ? [{ value: companion.id, label: companion.name || companion.id, group: t("Beside panel 1's theme") }] : []),
    ...[...themes]
      .filter((theme) => theme !== companion)
      .sort((a, b) => Number(!fits(a)) - Number(!fits(b)))
      .map((theme) => ({ value: theme.id, label: theme.name || theme.id, group: fits(theme) ? t("Themes") : t("For other panels") })),
  ];
  return html`<div class="more-panel" data-more-panel=${n}>
    <${SettingRow} class="more-panel-head" title=${model ? `${name}: ${panelName(model)}` : name} status=${html`<${MoreStatus} panel=${panel} running=${running} />`}>
      <${Toggle} label=${t("{panel} on", { panel: name })} checked=${panel.on !== false} onChange=${(on) => onChange({ on })} />
      <button type="button" class="btn" title=${t("Its theme and this panel's change places")} onClick=${onSwap}>${t("Swap with panel 1")}</button>
      <button type="button" class="btn" onClick=${onRemove}>${t("Remove")}</button>
    <//>
    ${panel.on === false ? null : html`<${MoreSettings} n=${n} panel=${panel} models=${models} themes=${themeOptions} chosen=${chosen} onBrightness=${brightness} onChange=${onChange} />`}
  </div>`;
}

/** What a panel that runs has to set: its model, theme, brightness, way up and port. */
function MoreSettings({ n, panel, models, themes, chosen, onBrightness, onChange }) {
  // (said once, at the first: it holds for all)
  const hint = n === 0 ? t("Detect automatically takes a panel the others do not use. Two serial panels of the same kind: name the port of each below.") : null;
  return html`
    <${SettingRow} title=${t("Panel model")} hint=${hint}>
      <${Select}
        label=${t("Panel model")}
        value=${panel.model || "auto"}
        options=${[
          { value: "auto", label: t("Detect automatically") },
          ...models.map((item) => ({ value: item.id, label: panelName(item), group: item.vendor })),
        ]}
        onChange=${(value) => onChange({ model: value })}
      />
    <//>
    <${SettingRow} title=${t("Theme")}>
      <${Select} label=${t("Theme")} value=${panel.theme || ""} options=${themes} onChange=${(value) => onChange({ theme: value })} />
    <//>
    <${SettingRow} title=${t("Look as panel 1's")} hint=${t("Its theme takes the colours, fonts and module frames of the theme panel 1 shows, and follows a change in Design.")}>
      <${Toggle} label=${t("Look as panel 1's")} checked=${panel.same_look === true} onChange=${(on) => onChange({ same_look: on })} />
    <//>
    <${SettingRow} title=${t("While a game runs")} hint=${n === 0 ? t("While panel 1 shows the game's page or music's, this panel can show a theme of its own: the temperatures and fans during a game, say.") : null}>
      <${Select} label=${t("While a game runs")} value=${panel.game_theme || ""} options=${[{ value: "", label: t("As ever") }, ...themes.slice(1)]}
        onChange=${(value) => onChange({ game_theme: value })} />
    <//>
    <${SettingRow} title=${t("While music plays")}>
      <${Select} label=${t("While music plays")} value=${panel.media_theme || ""} options=${[{ value: "", label: t("As ever") }, ...themes.slice(1)]}
        onChange=${(value) => onChange({ media_theme: value })} />
    <//>
    <${SettingRow} title=${t("Brightness as panel 1's")} hint=${t("At night and while you are away too. Some panels look brighter than others at the same setting: even them out with the difference below.")}>
      <${Toggle} label=${t("Brightness as panel 1's")} checked=${panel.follow_brightness !== false} onChange=${(on) => onChange({ follow_brightness: on })} />
    <//>
    ${panel.follow_brightness !== false
      ? html`<${SettingRow} title=${t("Difference")}>
          <${Slider} label=${t("Difference")} value=${panel.brightness_offset || 0} min=${-30} max=${30} step=${5}
            format=${(value) => `${value > 0 ? "+" : ""}${value} %`} onCommit=${(value) => onChange({ brightness_offset: value })} />
        <//>`
      : html`<${SettingRow} title=${t("Brightness")}>
          <${Slider} label=${t("Brightness")} value=${chosen ?? panel.brightness} min=${0} max=${100} step=${5}
            format=${(value) => `${value} %`} onCommit=${onBrightness} />
        <//>`}
    <${SettingRow} title=${t("Upside down")}>
      <${Toggle} label=${t("Upside down")} checked=${panel.rotate === 180} onChange=${(on) => onChange({ rotate: on ? 180 : 0 })} />
    <//>
    <${SettingRow} title=${t("Port of a serial panel")}>
      <${CommitField} label=${t("Port of a serial panel")} value=${panel.port || ""} placeholder="COM6, /dev/ttyACM1" mono
        onCommit=${(port) => onChange({ port })} />
    <//>
  `;
}

// -- advanced --------------------------------------------------------------------------------

const driverName = (name) =>
  ({
    auto: t("Automatic"),
    turzx: t("Always the panel"),
    virtual: t("Never the panel (a file only)"),
  })[name] || name;

function AdvancedCard() {
  const config = useConfig();
  const catalog = useCatalog();
  const info = useStore((s) => s.panelInfo);
  if (!config || !catalog) return null;
  const text = (name, label, placeholder) => html`<${CommitField}
    label=${label}
    value=${setting(name, config)}
    placeholder=${placeholder}
    mono
    onCommit=${(value) => change({ [name]: value })}
  />`;
  // the clip's name is asked for above while it is missing; once it is there it is kept here
  const clip = info && videoApplies(config, info) && !info.video.gaps.includes("clip");
  return html`<${Disclosure}
    id="panel-advanced"
    title=${t("Advanced")}
    hint=${t("For special cases: where the pictures go, the standby clip's name, the port of a serial panel, the video program.")}
  >
    <${SettingRow} title=${t("Where the pictures go")} hint=${t("Automatic: to the panel when one is connected, else to the picture file below.")}>
      <${Select}
        label=${t("Where the pictures go")}
        value=${config.device.driver}
        options=${catalog.drivers.map((name) => ({ value: name, label: driverName(name) }))}
        onChange=${(value) => change({ "device.driver": value })}
      />
    <//>
    <${SettingRow} title=${t("Picture file")} hint=${t("Where the pictures go when no panel is used. A short name is in the settings folder.")}>
      ${text("device.output", t("Picture file"), "libre-panel-frame.png")}
    <//>
    ${clip
      ? html`<${SettingRow} title=${t("Name of the standby clip")} hint=${t("The clip your panel plays by itself when the PC starts, as its own app names it. Smooth motion needs it.")}>
          <${ClipField} value=${config.video.local_clip} />
        <//>`
      : null}
    <${SettingRow}
      title=${t("Port of a serial panel")}
      hint=${t("Panels that connect as a serial port (the 3.5″ and the like) are found by themselves. Only if the wrong port is taken, name it.")}
    >
      ${text("device.port", t("Port of a serial panel"), "COM5, /dev/ttyACM0")}
    <//>
    <${SettingRow}
      title=${t("Video program (ffmpeg)")}
      hint=${t("Smooth motion turns the pictures into video with ffmpeg, which the downloads bring. Only if Libre Panel does not find it, name it.")}
    >
      ${text("video.ffmpeg", t("Video program (ffmpeg)"), "ffmpeg")}
    <//>
  <//>`;
}

export const panelGroups = [
  { id: "found", order: 10, component: PanelFound, load: loadPanelSection },
  { id: "video", order: 20, component: VideoCard },
  { id: "display", order: 30, component: DisplayCard },
  { id: "start-screen", order: 35, component: StartScreenCard },
  { id: "more", order: 38, component: MorePanelsCard },
  { id: "advanced", order: 40, component: AdvancedCard },
];
