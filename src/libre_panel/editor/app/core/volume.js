// The volume over every screen: whether turning it shows on the panel (the notices service and
// its "volume" option) and the look it shows in ("volume_look", render/volume_looks.py). They
// are settings, not a theme's, so they hold for every theme; the music page's screen shows the
// volume itself. Start, Look and Ctrl+K switch and choose them through here alike.

import { t } from "./i18n.js";
import { enableService, saveSettings, useConfig } from "./settings.js";

/** The looks, in the order Look offers them; "" draws the volume as the theme's notices. */
export const VOLUME_LOOKS = () => [
  { value: "", label: t("As the notices"), hint: t("in the style of the notices above") },
  { value: "bar", label: t("Bar"), hint: t("a slim bar along the bottom edge") },
  { value: "levels", label: t("Levels"), hint: t("bars that rise as on a mixer") },
  { value: "ring", label: t("Ring"), hint: t("a ring that fills, the number in it") },
  { value: "big", label: t("Large"), hint: t("a large number in the middle") },
];

/** `{on, look}` from a config (`on`: undefined while it is not loaded). */
export function volumeNotice(config) {
  if (!config) return { on: undefined, look: "" };
  const options = config.services?.options?.notices || {};
  const on = (config.services?.enabled || []).includes("notices") && options.volume !== false;
  return { on, look: options.volume_look || "" };
}

export const useVolumeNotice = () => volumeNotice(useConfig());

/** Show the volume on every screen, or not: the notices service is switched on for it. */
export async function showVolume(value) {
  if (value && !(await enableService("notices", true))) return false;
  return saveSettings({ "services.notices.volume": value });
}

export const chooseVolumeLook = (look) => saveSettings({ "services.notices.volume_look": look });

/** Look, at its choice of the volume's look. */
export function openVolumeLook() {
  location.hash = "#/look"; // (the shell's navigate, without core reaching up to the shell)
  let tries = 30; // Look draws its side once the theme is there
  const find = () => {
    const found = document.querySelector("[data-look-volume]");
    if (found) found.scrollIntoView({ block: "center", behavior: "smooth" });
    else if (tries-- > 0) setTimeout(find, 100);
  };
  setTimeout(find, 50);
}
