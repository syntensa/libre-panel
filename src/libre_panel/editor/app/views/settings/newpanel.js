// A panel added beside the first, step by step: which one, how it is mounted, and what it shows.
// Each step comes with a choice made already (the panel plugged in, a bar on its side, the theme
// that goes beside panel 1's), so "Next" all the way adds the panel as it is suggested.

import { t } from "../../core/i18n.js";
import { html, useState } from "../../lib/preact.js";
import { panelName } from "../../shell/panel-popover.js";
import { Segmented, Select, Toggle } from "../../ui/controls.js";

const STEPS = ["panel", "mounted", "shows"];

/**
 * The steps in a dialog; `close` gets the new panel's table ([[panels]]: model, port,
 * orientation, rotate, theme, same_look, follow_brightness, game_theme) or null.
 * `start`: what is known already ({model, port} of a panel plugged in), `models` the
 * catalogue's, `spare` the panels plugged in that nothing shows on, `themes` the options of
 * the theme select (the companion of panel 1's theme first), `suggested` the table a panel
 * would get without the steps (its companion and the game's).
 */
export function NewPanel({ close, start = {}, models, spare = [], themes, suggested }) {
  const [step, setStep] = useState(start.model ? 1 : 0);
  const [panel, setPanel] = useState({ model: "auto", orientation: "", rotate: 0, follow_brightness: true, ...suggested, ...start });
  const change = (changes) => setPanel((now) => ({ ...now, ...changes }));
  const found = spare.map((model) => ({ value: `${model.id}|${model.port || ""}`, label: t("{name}, plugged in", { name: panelName(model) }), group: t("Found") }));
  const chosen = `${panel.model}|${panel.port || ""}`;
  const model = models.find((item) => item.id === panel.model) || null;
  const last = step === STEPS.length - 1;
  const body = {
    panel: html`<p class="dialog-text">${t("Which panel is it? One that is plugged in and shows nothing yet is offered first.")}</p>
      <${Select}
        label=${t("Panel model")}
        value=${found.some((option) => option.value === chosen) ? chosen : panel.model}
        options=${[
          ...found,
          { value: "auto", label: t("Detect automatically"), group: found.length ? t("Others") : "" },
          ...models.map((item) => ({ value: item.id, label: panelName(item), group: item.vendor })),
        ]}
        onChange=${(value) => {
          const [id, port] = value.split("|");
          change({ model: id, port: port || undefined });
        }}
      />`,
    mounted: html`<p class="dialog-text">${t("How is it mounted? A bar can lie on its side or stand on end, under the monitor or in the case.")}</p>
      <${Segmented}
        label=${t("How the panel is mounted")}
        value=${panel.orientation || "landscape"}
        onChange=${(value) => change({ orientation: value })}
        options=${[
          { value: "landscape", icon: "landscape", label: t("Horizontal") },
          { value: "portrait", icon: "portrait", label: t("Vertical") },
        ]}
      />
      <${Toggle} label=${t("Upside down")} checked=${panel.rotate === 180} onChange=${(on) => change({ rotate: on ? 180 : 0 })} />`,
    shows: html`<p class="dialog-text">${t("What should it show? The theme made to go beside panel 1's comes first.")}</p>
      <${Select} label=${t("Theme")} value=${panel.theme || ""} options=${themes} onChange=${(value) => change({ theme: value })} />
      <${Toggle} label=${t("Look as panel 1's")} checked=${panel.same_look === true} onChange=${(on) => change({ same_look: on })} />
      <${Toggle} label=${t("Brightness as panel 1's")} checked=${panel.follow_brightness !== false} onChange=${(on) => change({ follow_brightness: on })} />`,
  }[STEPS[step]];
  const done = () => {
    const { orientation, rotate, port, ...rest } = panel;
    close({
      ...rest,
      ...(port ? { port } : {}),
      ...(orientation && orientation !== "landscape" ? { orientation } : {}),
      ...(rotate === 180 ? { rotate } : {}),
    });
  };
  return html`<div class="new-panel" data-new-panel=${STEPS[step]}>
    <h2 class="dialog-title">${t("Add a panel")}</h2>
    <p class="new-panel-step muted">${t("Step {n} of {count}", { n: step + 1, count: STEPS.length })}${model ? ` · ${panelName(model)}` : ""}</p>
    <div class="new-panel-body">${body}</div>
    <div class="dialog-actions">
      <button type="button" class="btn" onClick=${() => (step ? setStep(step - 1) : close(null))}>${step ? t("Back") : t("Cancel")}</button>
      <button type="button" class="btn btn-primary" autofocus onClick=${() => (last ? done() : setStep(step + 1))}>
        ${last ? t("Add the panel") : t("Next")}
      </button>
    </div>
  </div>`;
}
