// Settings → Panel: the panel that was found, the video mode and its frame rate,
// brightness and which way up the panel is mounted; under Advanced the driver, the file
// the picture goes to when there is no panel, the serial port and ffmpeg.

import { t } from "../../core/i18n.js";
import { loadPanelInfo, loadSettings, saveSettings, setting, useCatalog, useConfig } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { videoState } from "../../core/video.js";
import { html, useRef, useState } from "../../lib/preact.js";
import { panelName, stateText } from "../../shell/panel-popover.js";
import { Segmented, Select, Slider, Toggle } from "../../ui/controls.js";
import { Popover } from "../../ui/layer.js";
import { MountedChoice } from "../home/panel.js";
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
              <span class="model-proto">${model.protocol_name.startsWith("USB bulk") ? "USB" : t("serial")}</span>
            </button>`,
          )}
        </div>`,
      )}
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
      ${why ? html`<div class="panel-found-why"><${Status} tone="warn">${stateText(running.state)}: ${running.detail}<//></div>` : null}
    </div>
    <button ref=${anchor} type="button" class="btn" aria-haspopup="dialog" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      ${t("Another panel")}
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} placement="bottom-end" label=${t("Panel")} width=${380}>
      <${PanelPicker} configured=${configured} onPick=${pick} />
    <//>
  <//>`;
}

// -- the video mode ----------------------------------------------------------------------------

/** What the video mode is doing, in a mark and words. */
function VideoStatus({ video, mode, fps }) {
  const state = videoState(video, mode, fps);
  return html`<${Status} tone=${state.tone}>${state.text}<//>`;
}

function VideoCard() {
  const config = useConfig();
  const info = useStore((s) => s.panelInfo);
  if (!config || !info) return null;
  const { mode, fps, local_clip: clip } = config.video;
  const video = info.video;
  const rates = [25, 30, 50, ...(fps && ![25, 30, 50].includes(fps) ? [fps] : [])].sort((a, b) => a - b);
  return html`<${Card} id="panel-video">
    <${SettingRow}
      title=${t("Video mode")}
      hint=${t("Smooth motion: up to 50 pictures a second instead of single ones.")}
      status=${html`<${VideoStatus} video=${video} mode=${mode} fps=${fps} />`}
    >
      <${Segmented}
        label=${t("Video mode")}
        value=${mode}
        onChange=${(value) => change({ "video.mode": value })}
        options=${[
          { value: "auto", label: t("Automatic") },
          { value: "on", label: t("On") },
          { value: "off", label: t("Off") },
        ]}
      />
    <//>
    <${SettingRow} title=${t("Frames per second")} hint=${t("How many pictures a second the panel gets in video mode. 25 and 30 ask less of the PC.")}>
      <${Segmented}
        label=${t("Frames per second")}
        value=${fps}
        onChange=${(value) => change({ "video.fps": value })}
        options=${rates.map((rate) => ({ value: rate, label: String(rate) }))}
      />
    <//>
    ${mode !== "off" && video.usb
      ? html`<${SettingRow}
          title=${t("Name of the standby clip")}
          hint=${t(
            "The clip the panel plays by itself while the PC is off, as the vendor app names it. It goes to the panel when the video starts, so it must be a name the panel has. Nothing is written until you type it.",
          )}
        >
          <${CommitField}
            label=${t("Name of the standby clip")}
            value=${clip}
            placeholder="usr/data/standby.h264"
            mono
            onCommit=${(text) => change({ "video.local_clip": text, "video.clip_asked": true })}
          />
        <//>`
      : null}
  <//>`;
}

// -- brightness and which way up ------------------------------------------------------------

function DisplayCard() {
  const config = useConfig();
  const [chosen, setChosen] = useState(null); // the slider stays where it is let go until it is written
  if (!config) return null;
  const { brightness, rotate } = config.device;
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
  <//>`;
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
  const [open, setOpen] = useState(false);
  if (!config || !catalog) return null;
  const text = (name, label, placeholder) => html`<${CommitField}
    label=${label}
    value=${setting(name, config)}
    placeholder=${placeholder}
    mono
    onCommit=${(value) => change({ [name]: value })}
  />`;
  return html`<${Disclosure}
    id="panel-advanced"
    title=${t("Advanced")}
    hint=${t("The driver, the file for pictures without a panel, the serial port, ffmpeg")}
    open=${open}
    onToggle=${() => setOpen(!open)}
  >
    <${SettingRow} title=${t("Driver")} hint=${t("Automatic uses a connected panel and otherwise writes a picture to the file below.")}>
      <${Select}
        label=${t("Driver")}
        value=${config.device.driver}
        options=${catalog.drivers.map((name) => ({ value: name, label: driverName(name) }))}
        onChange=${(value) => change({ "device.driver": value })}
      />
    <//>
    <${SettingRow} title=${t("Picture file")} hint=${t("Where the pictures go when no panel is used. A short name is in the settings folder.")}>
      ${text("device.output", t("Picture file"), "libre-panel-frame.png")}
    <//>
    <${SettingRow} title=${t("Serial port")} hint=${t("Serial panels are found by their USB ids. Only if that picks the wrong port, name it.")}>
      ${text("device.port", t("Serial port"), "COM5, /dev/ttyACM0")}
    <//>
    <${SettingRow} title=${t("ffmpeg program")} hint=${t("For the video mode, if Libre Panel does not find ffmpeg by itself.")}>
      ${text("video.ffmpeg", t("ffmpeg program"), "ffmpeg")}
    <//>
  <//>`;
}

export const panelGroups = [
  { id: "found", order: 10, component: PanelFound, load: loadPanelSection },
  { id: "video", order: 20, component: VideoCard },
  { id: "display", order: 30, component: DisplayCard },
  { id: "start-screen", order: 35, component: StartScreenCard },
  { id: "advanced", order: 40, component: AdvancedCard },
];
