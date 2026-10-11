// What the video mode is doing, in words, for Settings and Live. The server works it out with
// the driver's own `video_mode()` (editor/settings.py, /api/panel): `video` here is that answer,
// `{usb, on, gaps}`, and `mode` and `fps` are the settings it was worked out from.

import { t } from "./i18n.js";

// what the video mode still lacks, by the codes of `video_gaps()`
const GAPS = {
  clip: () => t("the name of the standby clip"),
  ffmpeg: () => t("ffmpeg"),
  "ffmpeg-path": () => t("ffmpeg at the path given under Advanced"),
};

/**
 * `{tone, text}`: "ok" while it is on, "warn" while something is missing, else "off". `running`
 * (Live, where the panel is known) says whether the panel is driven in video mode now: where it
 * is not, "on" only means that it will be, once a panel it runs on is there.
 */
export function videoState(video, mode, fps, running) {
  if (!video.usb) return { tone: "off", text: t("This panel gets single pictures: the video mode is for TURZX USB panels.") };
  if (mode === "off") return { tone: "off", text: t("Off. The panel gets single pictures, about nine a second.") };
  if (video.gaps[0] === "model") return { tone: "off", text: t("Off. Not proven on this panel yet; “On” tries it.") };
  if (video.gaps.length) {
    const missing = video.gaps.map((gap) => GAPS[gap]()).join(", ");
    const text = mode === "on" ? t("Cannot start yet. Missing: {missing}.", { missing }) : t("Off for now. Missing: {missing}.", { missing });
    return { tone: "warn", text };
  }
  if (video.on && running !== false) return { tone: "ok", text: t("On, {fps} fps", { fps }) };
  return { tone: "off", text: t("Starts when a panel it runs on is connected.") };
}
