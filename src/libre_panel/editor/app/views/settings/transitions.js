// Settings → Panel → Transitions: how the panel goes from one screen to the next ([transitions] in
// config.toml): the default, how fast, and each occasion's own (a game, music, the PC at rest, the
// screens in turn, the autopilot, the round report, a theme chosen), "random" picking one of a set.
// "Show on the panel" plays one there, out to a card with its name and back (transition_show.py).

import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { saveSettings, useCatalog, useConfig } from "../../core/settings.js";
import { html, useState } from "../../lib/preact.js";
import { Segmented, Select } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toastError } from "../../ui/toast.js";
import { Card, SettingRow, Status } from "./parts.js";

export const RANDOM = "random";

// the built-in transitions by name, in words; a plugin's goes by its name
const NAMES = {
  cut: () => t("Hard cut"),
  fade: () => t("Fade"),
  slide: () => t("Slide to the left"),
  "slide-right": () => t("Slide to the right"),
  "slide-up": () => t("Slide up"),
  "slide-down": () => t("Slide down"),
  wipe: () => t("Wipe"),
  zoom: () => t("Zoom"),
  iris: () => t("Iris"),
  blinds: () => t("Blinds"),
  dissolve: () => t("Dissolve"),
  tube: () => t("Tube"),
  entrance: () => t("Entrance"),
  light: () => t("Line of light"),
  glitch: () => t("Glitch"),
  tiles: () => t("Tiles"),
  shatter: () => t("Shatter"),
  [RANDOM]: () => t("Random"),
};

export const transitionName = (id) => (NAMES[id] ? NAMES[id]() : id);

const GROUPS = { plain: () => t("Plain"), elaborate: () => t("Elaborate"), plugin: () => t("From plugins") };

// each occasion: its words, and what "" leaves it to (a service's own, else the default)
const OCCASIONS = [
  { id: "game", title: () => t("A game starts or ends"), own: "entrance", hint: () => t("Its own is the entrance with the game's name and logo.") },
  { id: "media", title: () => t("Music starts or stops"), hint: () => t("The entrance shows the cover and the track.") },
  { id: "idle", title: () => t("The PC is at rest, or in use again") },
  { id: "sequence", title: () => t("Screens in turn") },
  { id: "autopilot", title: () => t("The autopilot changes the screen"), own: "tube", hint: () => t("Its own is the one set for the autopilot.") },
  { id: "report", title: () => t("The round report"), own: "tube", hint: () => t("Its own is the one set for the report.") },
  { id: "manual", title: () => t("A theme or scene you choose") },
];

/** The choices for a select: the transitions by group, "random" last; `first` before them. */
function options(list, first = null) {
  const out = first ? [first] : [];
  for (const item of list) out.push({ value: item.id, label: transitionName(item.id), group: (GROUPS[item.group] || GROUPS.plugin)() });
  out.push({ value: RANDOM, label: transitionName(RANDOM), group: t("Each time another") });
  return out;
}

/** Play `name` on the panels, out to a card with its name and back. */
async function showOnPanel(name) {
  try {
    await endpoints.appAction("show_transition", { name, label: transitionName(name) });
  } catch (error) {
    toastError(error);
  }
}

function ShowButton({ name }) {
  return html`<button type="button" class="btn transition-show" title=${t("Show on the panel")} aria-label=${t("Show “{name}” on the panel", { name: transitionName(name) })}
    onClick=${() => showOnPanel(name)}><${Icon} name="play" size=${14} /></button>`;
}

export function TransitionsCard() {
  const config = useConfig();
  const catalog = useCatalog();
  const [busy, setBusy] = useState(false);
  if (!config || !catalog) return null;
  const chosen = config.transitions || {};
  const list = catalog.transitions || [];
  const save = async (changes) => {
    setBusy(true);
    try {
      await saveSettings(changes);
    } finally {
      setBusy(false);
    }
  };
  const used = [chosen.default, ...OCCASIONS.map((o) => chosen[o.id])].includes(RANDOM);
  const picks = chosen.random || [];
  const togglePick = (id) => {
    const next = picks.includes(id) ? picks.filter((item) => item !== id) : [...picks, id];
    if (next.length) save({ "transitions.random": next });
  };
  const calm = config.device?.motion === "calm";
  return html`<${Card}
    id="panel-transitions"
    title=${t("Transitions")}
    text=${t("How the panel goes from one screen to the next: a default, and its own for each occasion if you like.")}
  >
    ${calm ? html`<div class="settings-row"><${Status}>${t("Calm motion: this panel switches at once, without a transition.")}<//></div>` : null}
    <${SettingRow} title=${t("Default")} hint=${t("For every switch that has none of its own below.")}>
      <span class="transition-choice">
        <${Select} label=${t("Default")} value=${chosen.default || "fade"} options=${options(list)} onChange=${(value) => save({ "transitions.default": value })} />
        <${ShowButton} name=${chosen.default || "fade"} />
      </span>
    <//>
    <${SettingRow} title=${t("Pace")} hint=${t("Slower gives the elaborate ones room; faster keeps the panel brisk.")}>
      <${Segmented}
        label=${t("Pace")}
        value=${chosen.speed || "normal"}
        onChange=${(value) => save({ "transitions.speed": value })}
        options=${[
          { value: "fast", label: t("Fast") },
          { value: "normal", label: t("Normal") },
          { value: "slow", label: t("Leisurely") },
        ]}
      />
    <//>
    ${OCCASIONS.map((occasion) => {
      const value = chosen[occasion.id] ?? "";
      const plays = value || occasion.own || chosen.default || "fade";
      const first = { value: "", label: occasion.own ? t("Its own ({name})", { name: transitionName(occasion.own) }) : t("As the default") };
      return html`<${SettingRow} key=${occasion.id} class="transition-occasion" data-occasion=${occasion.id} title=${occasion.title()} hint=${occasion.hint ? occasion.hint() : null}>
        <span class="transition-choice">
          <${Select} label=${occasion.title()} value=${value} options=${options(list, first)} onChange=${(next) => save({ [`transitions.${occasion.id}`]: next })} />
          <${ShowButton} name=${plays} />
        </span>
      <//>`;
    })}
    ${used
      ? html`<${SettingRow} title=${t("Random picks from")} hint=${t("Each switch set to Random plays one of these.")} stacked>
          <div class="transition-picks" role="group" aria-label=${t("Random picks from")}>
            ${list.map(
              (item) => html`<label key=${item.id} class=${`transition-pick${picks.includes(item.id) ? " on" : ""}`}>
                <input type="checkbox" checked=${picks.includes(item.id)} disabled=${busy || (picks.length === 1 && picks.includes(item.id))}
                  onChange=${() => togglePick(item.id)} />
                ${transitionName(item.id)}
              </label>`,
            )}
          </div>
        <//>`
      : null}
  <//>`;
}
