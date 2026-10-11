// The hardware's names (core/hardware.js): a group in Settings → Sensors, and the same
// field in Design's inspector beside a widget that shows one, so a name is changed
// where it is seen. Where the server has no names, nothing shows.

import { hardwareParts, loadHardware, setHardwareName } from "../../core/hardware.js";
import { t } from "../../core/i18n.js";
import { useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { toastError } from "../../ui/toast.js";

const useHardware = () => {
  const hardware = useStore((s) => s.hardware);
  useEffect(() => {
    loadHardware();
  }, []);
  return hardware;
};

/**
 * One part's name: the detected one as the placeholder, a typed text in its place
 * (Enter or leaving the field keeps it), "Automatic" for the detected one again.
 * `row`: as a settings row (the name and what was detected on the left).
 */
export function HardwareNameField({ part, label, hint, row = false }) {
  const hardware = useHardware();
  const info = hardware ? hardware[part] : null;
  const [draft, setDraft] = useState(null);
  useEffect(() => setDraft(null), [info?.custom]);
  if (!info) return null;
  const custom = info.custom || "";
  const detected = info.detected || "";
  const id = `hardware-${part}${row ? "-row" : ""}`;
  const keep = (value) => setHardwareName(part, value).catch(toastError);
  const automatic = custom
    ? html`<button type="button" class="link-btn" title=${detected ? t("Back to “{name}”", { name: detected }) : null}
        onClick=${() => {
          setDraft(null);
          keep("");
        }}>${t("Automatic")}</button>`
    : null;
  const input = html`<input id=${id} type="text" class="input" value=${draft ?? custom} maxlength="80" spellcheck="false"
    placeholder=${detected || t("not detected")}
    onInput=${(event) => setDraft(event.currentTarget.value)}
    onChange=${(event) => keep(event.currentTarget.value)}
    onKeyDown=${(event) => {
      if (event.key === "Enter") event.currentTarget.blur();
      else if (event.key === "Escape") {
        event.stopPropagation();
        setDraft(null);
      }
    }} />`;
  if (row) {
    return html`<div class="settings-row hardware-name">
      <label class="settings-row-text" for=${id}>
        <span class="settings-row-title">${label}</span>
        <span class="settings-row-hint nowrap">${detected ? t("detected: {name}", { name: detected }) : t("not detected")}</span>
      </label>
      <div class="settings-row-control">${input}<span class="row-action">${automatic}</span></div>
    </div>`;
  }
  return html`<div class="field hardware-name">
    <div class="field-head"><label class="field-label" for=${id}>${label}</label>${automatic}</div>
    ${input}
    ${hint ? html`<small class="field-hint">${hint}</small>` : null}
  </div>`;
}

/** Settings → Sensors: the three names. */
export function HardwareNames() {
  const hardware = useHardware();
  if (!hardware) return null;
  return html`<section class="settings-card" aria-labelledby="hardware-names">
    <div class="settings-card-head">
      <h2 id="hardware-names">${t("Hardware names")}</h2>
      <p>${t("How the panel names the processor, the graphics card and the memory. Empty: the name the sensors detect. Every theme shows it.")}</p>
    </div>
    ${hardwareParts().map((part) => html`<${HardwareNameField} key=${part.id} part=${part.id} label=${part.label} row />`)}
  </section>`;
}
