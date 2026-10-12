// The hardware's names (core/hardware.js): a group in Settings → Sensors, and the same
// field in Design's inspector beside a widget that shows one, so a name is changed
// where it is seen. Where the server has no names, nothing shows.

import { endpoints } from "../../core/api.js";
import { hardwareParts, loadHardware, setFanLook, setFanName, setHardwareName } from "../../core/hardware.js";
import { formatNumber, t } from "../../core/i18n.js";
import { useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Segmented, Select } from "../../ui/controls.js";
import { toastError } from "../../ui/toast.js";
import { SettingRow } from "./parts.js";

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
  if (!info) return null;
  return html`<${NameField} id=${`hardware-${part}${row ? "-row" : ""}`} label=${label} hint=${hint} row=${row}
    info=${info} keep=${(value) => setHardwareName(part, value).catch(toastError)} />`;
}

/** A name field (see HardwareNameField) for `info` ({detected, custom}); `keep(text)` saves. */
function NameField({ id, label, hint, row, info, keep, rowHint }) {
  const [draft, setDraft] = useState(null);
  useEffect(() => setDraft(null), [info?.custom]);
  const custom = info.custom || "";
  const detected = info.detected || "";
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
        <span class="settings-row-hint nowrap">${rowHint ?? (detected ? t("detected: {name}", { name: detected }) : t("not detected"))}</span>
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

/**
 * Settings → Sensors: how the fans turn on the panel, where a theme leaves it to the user: the
 * look of their rotor (tiles with a picture of each) and colours for all of them, and a look of
 * its own for each fan.
 */
function FanLooks({ hardware }) {
  const info = hardware.fan_look;
  if (!info || !Array.isArray(info.looks) || !info.looks.length) return null;
  const fans = Array.isArray(hardware.fans) ? hardware.fans : [];
  const looks = info.looks;
  const current = info.look || looks[0].id;
  const paint = info.paint || "theme";
  const save = (change) => setFanLook(change).catch(toastError);
  const own = [{ value: "", label: t("as all fans") }, ...looks.map((look) => ({ value: look.id, label: look.label }))];
  return html`<section class="settings-card" aria-labelledby="fan-looks">
    <div class="settings-card-head">
      <h2 id="fan-looks">${t("How the fans turn")}</h2>
      <p>${t("The look of the fans' rotors on the panel, where a theme leaves it to you: for all fans, or for one of its own. A theme may give a fan a look of its own.")}</p>
    </div>
    <div class="fan-look-tiles" role="radiogroup" aria-label=${t("Look of all fans")}>
      ${looks.map(
        (look) => html`<button type="button" role="radio" key=${look.id} class="fan-look-tile" data-look=${look.id}
          aria-checked=${String(look.id === current)} onClick=${() => look.id !== current && save({ fan_look: look.id })}>
          <img src=${endpoints.fanLookUrl(look.id, paint)} alt="" width="64" height="64" decoding="async" />
          <span>${look.label}</span>
        </button>`,
      )}
    </div>
    <${SettingRow} title=${t("Colours")} hint=${t("the theme's colour, or the look's own (Noctua's brown and beige)")}>
      <${Segmented} label=${t("Colours")} value=${paint} onChange=${(value) => value !== paint && save({ fan_paint: value })}
        options=${[
          { value: "theme", label: t("the theme's") },
          { value: "own", label: t("the look's own") },
        ]} />
    <//>
    ${fans.map(
      (fan) => html`<${SettingRow} key=${fan.key} title=${fan.custom || fan.detected || fan.key} hint=${fan.key} data-fan=${fan.key}>
        <${Select} label=${t("Look of {fan}", { fan: fan.custom || fan.detected || fan.key })} value=${fan.look || ""} options=${own}
          onChange=${(value) => value !== (fan.look || "") && save({ fan_looks: { [fan.key]: value } })} />
      <//>`,
    )}
  </section>`;
}

/** Settings → Sensors: the three names, and the fans'. */
export function HardwareNames() {
  const hardware = useHardware();
  if (!hardware) return null;
  const fans = Array.isArray(hardware.fans) ? hardware.fans : [];
  return html`<section class="settings-card" aria-labelledby="hardware-names">
    <div class="settings-card-head">
      <h2 id="hardware-names">${t("Hardware names")}</h2>
      <p>${t("How the panel names the processor, the graphics card and the memory. Empty: the name the sensors detect. Every theme shows it.")}</p>
    </div>
    ${hardwareParts().map((part) => html`<${HardwareNameField} key=${part.id} part=${part.id} label=${part.label} row />`)}
  </section>
  ${fans.length
    ? html`<section class="settings-card" aria-labelledby="fan-names">
        <div class="settings-card-head">
          <h2 id="fan-names">${t("Fan names")}</h2>
          <p>${t("How the panel names each fan: a mainboard wires its fan headers its own way. Empty: the name FanControl gives the fan, else the sensor's. The speed now tells one from the other.")}</p>
        </div>
        ${fans.map(
          (fan) => html`<${NameField} key=${fan.key} id=${`fan-name-${fan.key}`} label=${fan.detected || fan.key} row info=${fan}
            rowHint=${`${fan.key} · ${typeof fan.value === "number" ? t("{rpm} RPM now", { rpm: formatNumber(fan.value, 0) }) : t("not read now")}`}
            keep=${(value) => setFanName(fan.key, value).catch(toastError)} />`,
        )}
      </section>`
    : null}
  <${FanLooks} hardware=${hardware} />`;
}
