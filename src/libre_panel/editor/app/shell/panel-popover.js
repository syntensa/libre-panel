// The panel chip in the header and its popover: which panel the theme is for
// (model, orientation, custom size) and, when the background app runs, how the
// panel is doing (state, brightness, upside down, start with system, pause, quit).

import { adaptPanel } from "../core/actions.js";
import { appAction, quitApp } from "../core/app.js";
import { t } from "../core/i18n.js";
import { setPref, store, useStore } from "../core/state.js";
import { html, useRef, useState } from "../lib/preact.js";
import { NumberField, Row, Segmented, Slider, Toggle } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { Popover } from "../ui/layer.js";

export const panelName = (model) => (model ? model.label.replace(/"/g, "″") : t("Custom size"));

export const modelOf = (theme, models) => models.find((model) => model.id === theme?.display?.model) || null;

export const stateText = (state) =>
  ({
    starting: t("Starting"),
    showing: t("Showing"),
    waiting: t("Waiting for the panel"),
    paused: t("Paused"),
    error: t("Needs attention"),
    stopped: t("Stopped"),
  })[state] || state;

/** The name of a mode for people: "game" → "In game". */
export const modeName = (mode) => (mode === "game" ? t("In game") : mode ? mode.charAt(0).toUpperCase() + mode.slice(1) : t("Normal"));

/** The name of a page of the panel: "normal", "report" (the round report) or a mode. */
export const pageName = (id) => (id === "report" ? t("Round report") : modeName(id === "normal" ? null : id));

/**
 * The pages that have a theme of their own (/api/pages): the normal one, each mode
 * with its own theme, and the round report while the memory service makes one.
 */
export function shownPages(data = store.get().pages) {
  return (data?.pages || []).filter((page) => page.id === "normal" || (page.service ? page.on && page.theme : page.theme));
}

function PanelChoice({ theme, models }) {
  const display = theme.display || {};
  const current = models.some((m) => m.id === display.model) ? display.model : "custom";
  const orientation = display.orientation || (display.width >= display.height ? "landscape" : "portrait");
  const adapt = useStore((s) => s.prefs.adapt || "scale");
  const [size, setSize] = useState([display.width, display.height]);
  const change = (patch) =>
    adaptPanel({
      model: current,
      orientation,
      mode: adapt,
      width: size[0],
      height: size[1],
      ...patch,
    });
  const groups = [];
  for (const model of models) {
    let group = groups.find((g) => g.vendor === model.vendor);
    if (!group) groups.push((group = { vendor: model.vendor, models: [] }));
    group.models.push(model);
  }
  return html`
    <div class="pop-section">
      <div class="pop-title">${t("Panel")}</div>
      <div class="model-list" role="listbox" aria-label=${t("Panel model")}>
        ${groups.map(
          (group) => html`<div class="model-group" key=${group.vendor}>
            <div class="model-vendor">${group.vendor}</div>
            ${group.models.map(
              (model) => html`<button
                type="button"
                role="option"
                key=${model.id}
                aria-selected=${String(model.id === current)}
                class="model-row"
                onClick=${() => model.id !== current && change({ model: model.id })}
              >
                <span class="model-shape ${model.shape === "round" ? "round" : ""}"></span>
                <span class="model-name">${panelName(model)}</span>
                <span class="model-size mono">${model.landscape[0]} × ${model.landscape[1]}</span>
                <span class="model-proto">${model.protocol_name.startsWith("USB bulk") ? "USB" : t("serial")}</span>
              </button>`,
            )}
          </div>`,
        )}
        <button
          type="button"
          role="option"
          aria-selected=${String(current === "custom")}
          class="model-row"
          onClick=${() => current !== "custom" && change({ model: "custom" })}
        >
          <span class="model-shape free"></span>
          <span class="model-name">${t("Custom size")}</span>
          <span class="model-size mono"></span>
          <span class="model-proto"></span>
        </button>
      </div>
      ${current === "custom"
        ? html`<div class="custom-size">
            <${NumberField} label=${t("Width")} value=${size[0]} min=${16} max=${4096} unit="px"
              onChange=${(w) => (setSize([w, size[1]]), change({ width: w, height: size[1] }))} />
            <span class="muted">×</span>
            <${NumberField} label=${t("Height")} value=${size[1]} min=${16} max=${4096} unit="px"
              onChange=${(h) => (setSize([size[0], h]), change({ width: size[0], height: h }))} />
          </div>`
        : null}
    </div>
    <div class="pop-section">
      <${Row} label=${t("Orientation")}>
        <${Segmented}
          label=${t("Orientation")}
          value=${orientation}
          onChange=${(value) => value !== orientation && change({ orientation: value })}
          options=${[
            { value: "landscape", icon: "landscape", label: t("Landscape") },
            { value: "portrait", icon: "portrait", label: t("Portrait") },
          ]}
        />
      <//>
      <${Row} class="stacked" label=${t("On size change")}>
        <${Segmented}
          label=${t("On size change")}
          value=${adapt}
          onChange=${(value) => setPref("adapt", value)}
          options=${[
            { value: "scale", label: t("Scale layout") },
            { value: "keep", label: t("Keep positions") },
          ]}
        />
      <//>
    </div>
  `;
}

function Running({ app }) {
  const panel = app.panel || {};
  if (app.quit) return html`<div class="pop-section"><p class="muted">${t("Libre Panel has quit.")}</p></div>`;
  return html`<div class="pop-section">
    <div class="pop-title">${t("On the panel now")}</div>
    <div class=${`run-state ${panel.state}`}>
      <span class="dot"></span>
      <strong>${stateText(panel.state)}</strong>
      <span class="muted">${[panel.target, panel.theme, panel.mode && modeName(panel.mode)].filter(Boolean).join(" · ")}</span>
    </div>
    ${panel.detail ? html`<p class="run-detail">${panel.detail}</p>` : null}
    <${Row} label=${t("Brightness")}>
      <${Slider}
        label=${t("Brightness")}
        value=${app.brightness ?? 0}
        step=${5}
        format=${(v) => `${v} %`}
        onCommit=${(value) => appAction("brightness", value)}
      />
    <//>
    <${Row} label=${t("Upside down")}>
      <${Toggle} label=${t("Upside down")} checked=${app.upside_down} disabled=${app.upside_down === null}
        onChange=${(on) => appAction("upside_down", on)} />
    <//>
    <${Row} label=${t("Start with system")} hint=${app.autostart_location || null}>
      <${Toggle} label=${t("Start with system")} checked=${app.autostart} disabled=${app.autostart === null}
        onChange=${(on) => appAction("autostart", on)} />
    <//>
    <div class="pop-actions">
      <button type="button" class="btn" onClick=${() => appAction(panel.state === "paused" ? "resume" : "pause")}>
        <${Icon} name=${panel.state === "paused" ? "play" : "pause"} size=${14} />
        ${panel.state === "paused" ? t("Resume panel") : t("Pause panel")}
      </button>
      <button type="button" class="btn btn-danger-quiet" onClick=${quitApp}>
        <${Icon} name="power" size=${14} /> ${t("Quit Libre Panel")}
      </button>
    </div>
  </div>`;
}

/** The chip: the panel the theme is for, and how the real one is doing. */
export function PanelChip() {
  const { theme, models, app, size } = useStore((s) => ({
    theme: s.doc?.theme,
    models: s.specs?.models || [],
    app: s.app,
    size: s.render.size,
    rev: s.rev,
  }));
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  if (!theme) return null;
  const model = modelOf(theme, models);
  const panel = app.available ? app.panel || {} : null;
  const state = app.quit ? "gone" : panel ? panel.state : "preview";
  const detail = panel
    ? panel.mode
      ? `${stateText(panel.state)} · ${modeName(panel.mode)}`
      : stateText(panel.state)
    : `${size[0]} × ${size[1]}`;
  return html`
    <button
      ref=${anchor}
      type="button"
      class=${`panel-chip ${state}`}
      aria-haspopup="dialog"
      aria-expanded=${String(open)}
      title=${panel?.detail || t("Panel and orientation")}
      onClick=${() => setOpen(!open)}
    >
      <span class="dot"></span>
      <span class="panel-chip-name">${panelName(model)}</span>
      <span class="panel-chip-detail">${detail}</span>
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} placement="bottom-end" label=${t("Panel")} width=${360}>
      <${PanelChoice} theme=${theme} models=${models} />
      ${app.available ? html`<${Running} app=${app} />` : null}
    <//>
  `;
}

export const currentModel = () => modelOf(store.get().doc?.theme, store.get().specs?.models || []);
