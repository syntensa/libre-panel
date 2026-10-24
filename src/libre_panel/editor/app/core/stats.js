// How the panel is doing: the numbers the main loop measures once a second (RunStatus.stats,
// docs/BACKGROUND.md, "How the panel is doing") as the tiles of Live and the lines of the
// panel card. `null` is "nothing counts this": the data rate of a PNG file, the video link
// outside video mode.

import { formatNumber, t } from "./i18n.js";

const DASH = "–";

/** A number with `digits` decimals, or a dash where nothing counts. */
const number = (value, digits = 0) => (typeof value === "number" ? formatNumber(value, digits, { fixed: true }) : DASH);

/**
 * The tiles: [{id, label, value, note, level, title}]. `level` is "" (as it should be), "ok"
 * (a count of faults that is zero), "warn" or "bad"; `stats` is the panel's `stats` (empty
 * while it does not run: every tile then shows a dash).
 */
export function statTiles(stats) {
  const s = stats && stats.fps !== undefined ? stats : {};
  const target = s.target_fps;
  const budget = target ? 1000 / target : null; // the milliseconds one frame has
  const slow = s.streaming && target && typeof s.fps === "number" && s.fps < 0.9 * target;
  const faults = (s.usb_errors ?? 0) + (s.panel_restarts ?? 0) + (s.ffmpeg_restarts ?? 0);
  const known = s.fps !== undefined;
  const queueTrouble = s.drops > 0 || s.max_depth >= 4;
  const planned = target ? t("target {fps}", { fps: target }) : "";
  return [
    {
      id: "fps",
      label: t("Frames per second"),
      value: number(s.fps, 1),
      note: planned,
      level: slow ? (s.fps < 0.5 * target ? "bad" : "warn") : "",
      title: t("Frames drawn per second, and the rate Libre Panel aims for. In video mode every frame goes out, so a lower rate means the PC or the panel cannot keep up; without it a frame is drawn only when something moves."),
    },
    {
      id: "data",
      label: t("Data rate"),
      value: number(s.kbps, 0),
      note: s.kbps == null ? (known ? t("not counted") : "") : s.bit_rate ? t("KB/s · up to {rate} Mbit/s", { rate: number(s.bit_rate, 1) }) : "KB/s",
      level: "",
      title: t("What goes to the panel, in KB/s (1 KB = 1000 bytes); not counted for the PNG file. In video mode also the bit rate's cap: with maxrate \"auto\" it steps down while the panel takes less than it is sent, and up again after a calm minute."),
    },
    {
      id: "draw",
      label: t("Drawing"),
      value: number(s.render_ms, 1),
      note: s.render_ms == null ? "" : t("ms · peak {n}", { n: number(s.render_peak_ms, 0) }),
      level: s.streaming && budget && s.render_ms > budget ? "warn" : "",
      title: t("The time to draw a frame (the median of the last frames) and the slowest of them. It has to stay below the time one frame has."),
    },
    {
      id: "queue",
      label: t("Queue"),
      value: s.max_depth == null ? DASH : number(s.max_depth, 0),
      note: s.max_depth == null ? (known ? t("video mode only") : "") : t("waited {waits}× · dropped {drops}", { waits: s.waits, drops: s.drops }),
      level: queueTrouble ? "warn" : "",
      title: t("Video mode: the deepest queue the panel reported (it holds five pictures and the driver waits above two), how often the driver waited for room, and how many pictures it threw away unsent."),
    },
    {
      id: "faults",
      label: t("Faults"),
      value: known ? number(faults, 0) : DASH,
      note: known ? t("USB {usb} · panel {panel} · ffmpeg {ffmpeg}", { usb: s.usb_errors, panel: s.panel_restarts, ffmpeg: s.ffmpeg_restarts }) : "",
      level: known ? (faults ? "warn" : "ok") : "",
      title: t("USB errors {usb}, panel restarts {panel}, ffmpeg restarts {ffmpeg}", {
        usb: s.usb_errors ?? 0,
        panel: s.panel_restarts ?? 0,
        ffmpeg: s.ffmpeg_restarts ?? 0,
      }),
    },
  ];
}

/** "3 h", "12 min", "40 s": how long the panel loop has run. */
export function runtime(seconds) {
  if (typeof seconds !== "number") return "";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours) return minutes ? t("{h} h {m} min", { h: hours, m: minutes }) : t("{h} h", { h: hours });
  if (minutes) return seconds % 60 ? t("{m} min {s} s", { m: minutes, s: seconds % 60 }) : t("{m} min", { m: minutes });
  return t("{s} s", { s: seconds });
}
