// The JONSBO ZC-360's three screens: one picture of 1920x180 (a panel), or each a panel of its own
// (640x180, model jonsbo-zc360-screen; `port` "1" to "3" says which screen, devices/jonsbo_zc360.py).
// Settings → Panels turns the one into the other: the settings change as a whole, nothing to type.

import { t } from "../../core/i18n.js";
import { html, useState } from "../../lib/preact.js";

export const ZC360 = "jonsbo-zc360";
export const ZC360_SCREEN = "jonsbo-zc360-screen";
// what the three show at first: the CPU, the GPU and the memory, each with its history
export const ZC360_THEMES = ["lumen-cpu-640x180", "lumen-gpu-640x180", "lumen-memory-640x180"];
// what a screen's panel keeps of the panel the three were: not where it stands, what it shows,
// its picture file or its video
const OWN = ["model", "port", "theme", "output", "at", "video"];

// a panel's own picture file goes with its place (as Settings → Panels takes one out)
const strip = ({ output, ...panel }, own) => (output === undefined || own.test(output) ? panel : { ...panel, output });

/**
 * Where the ZC-360 is among the panels: `{whole}` the place of the one picture (-1: panel 1, else
 * its place in [[panels]]; null: none), `{screens}` the places of the screens as panels of their own.
 * `first` is panel 1's model as set or found; `seen` the models the panels beside it run with.
 */
export function zc360Places(first, panels, seen = []) {
  const modelOf = (panel, n) => (panel.model && panel.model !== "auto" ? panel.model : seen[n] || "");
  const whole = first === ZC360 ? -1 : panels.findIndex((panel, n) => modelOf(panel, n) === ZC360);
  const screens = [
    ...(first === ZC360_SCREEN ? [-1] : []),
    ...panels.flatMap((panel, n) => (modelOf(panel, n) === ZC360_SCREEN ? [n] : [])),
  ];
  return { whole: whole === -1 && first !== ZC360 ? null : whole, screens };
}

/**
 * The settings that make the one picture three panels: `{changes, theme}`, `theme` panel 1's new
 * theme where the picture was panel 1 (else null). The panels beside it share its brightness;
 * null where there is no room for two more panels (`most` beside panel 1).
 */
export function splitZc360(whole, panels, most, own = /^libre-panel-frame-\d+\.png$/) {
  if (panels.length + 2 > most) return null;
  const from = whole === -1 ? {} : panels[whole];
  const kept = Object.fromEntries(Object.entries(from).filter(([key]) => !OWN.includes(key)));
  const screen = (n) => ({ ...kept, model: ZC360_SCREEN, port: String(n + 1), theme: ZC360_THEMES[n] });
  const more = [screen(1), screen(2)];
  if (whole === -1) {
    return { changes: { "device.model": ZC360_SCREEN, "device.port": "1", panels: [...panels, ...more] }, theme: ZC360_THEMES[0] };
  }
  const placed = panels.map((panel, n) => (n === whole ? { ...panel, model: ZC360_SCREEN, port: "1", theme: ZC360_THEMES[0] } : panel));
  return { changes: { panels: [...placed, ...more].map((panel) => strip(panel, own)) }, theme: null };
}

/** The settings that make the screens one picture again: the first of them takes the three. */
export function joinZc360(screens, panels, own = /^libre-panel-frame-\d+\.png$/) {
  if (!screens.length) return null;
  const first = screens[0];
  const kept = panels.flatMap((panel, n) => {
    if (!screens.includes(n)) return [panel];
    return n === first ? [{ ...panel, model: ZC360, port: "", theme: "" }] : [];
  });
  const changes = { panels: kept.map((panel) => strip(panel, own)) };
  if (first === -1) Object.assign(changes, { "device.model": ZC360, "device.port": "" });
  return { changes, theme: first === -1 ? "lumen" : null };
}

/** The row in Settings → Panels that turns one into the other. */
export function Zc360Row({ places, room, onSplit, onJoin }) {
  const [busy, setBusy] = useState(false);
  const run = async (action) => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };
  if (places.whole !== null) {
    return html`<div class="more-spare" data-zc360="whole">
      <span>${room
        ? t("The ZC-360's three screens show one picture. Each can be a panel of its own instead, with a theme of its own; they keep one brightness.")
        : t("The ZC-360's three screens show one picture. As panels of their own they need room for two more panels: take one out first.")}</span>
      <button type="button" class="btn btn-primary" disabled=${!room || busy} onClick=${() => run(onSplit)}>${t("Each screen its own panel")}</button>
    </div>`;
  }
  if (places.screens.length) {
    return html`<div class="more-spare" data-zc360="screens">
      <span>${t("The ZC-360's screens are panels of their own. “Which is which?” shows their numbers on them.")}</span>
      <button type="button" class="btn" disabled=${busy} onClick=${() => run(onJoin)}>${t("Back to one picture")}</button>
    </div>`;
  }
  return null;
}
