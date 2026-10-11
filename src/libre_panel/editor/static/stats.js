// How the panel is doing: the numbers the main loop measures (RunStatus.stats, as in
// /api/app), as short rows for the editor's panel menu and as tiles for the Control page.
//
//   import { describeStats, showStats } from "/static/stats.js";
//   describeStats(stats, t)  ->  [{ id, label, value, note, level }]   (level: "", "warn", "bad")
//   showStats(dl, stats, t)  fills a <dl> with them and hides it when there are no numbers
//
// `t` is the caller's translate function: the editor's and the pages' catalogs differ in
// where they live, not in what they say.

const num = (value, digits = 1) => Number(value).toFixed(digits).replace(/\.0+$/, "");

export function describeStats(stats, t) {
  if (!stats || stats.fps === undefined) return [];
  const rows = [];
  const target = stats.target_fps;
  let planned = "";
  if (target) {
    planned = stats.streaming
      ? t("target {fps}", { fps: target })
      : t("target {fps}, drawn when something moves", { fps: target });
  }

  // Frame rate: in video mode every frame goes out, so the rate should be the target.
  const slow = stats.streaming && target && stats.fps < 0.9 * target;
  rows.push({
    id: "fps",
    label: t("Frame rate"),
    value: stats.fps == null ? "–" : t("{n} fps", { n: num(stats.fps) }),
    note: planned,
    level: slow ? (stats.fps < 0.5 * target ? "bad" : "warn") : "",
  });

  // Drawing: the time to make a frame must stay under the time one frame has.
  if (stats.render_ms != null) {
    const budget = target ? 1000 / target : null;
    rows.push({
      id: "render",
      label: t("Drawing"),
      value: t("{n} ms", { n: num(stats.render_ms) }),
      note: t("slowest of the last frames {n} ms", { n: num(stats.render_peak_ms) }),
      level: stats.streaming && budget && stats.render_ms > budget ? "warn" : "",
    });
  }

  if (stats.kbps != null) {
    rows.push({ id: "data", label: t("Data rate"), value: t("{n} KB/s", { n: num(stats.kbps) }), note: "", level: "" });
  }

  // The panel's video queue: it holds five pictures and the driver waits above two.
  if (stats.blocks_per_s != null) {
    rows.push({
      id: "video",
      label: t("Video"),
      value: t("{n} blocks/s", { n: num(stats.blocks_per_s) }),
      note: t("queue up to {depth}, waits {waits}, dropped {drops}", {
        depth: stats.max_depth,
        waits: stats.waits,
        drops: stats.drops,
      }),
      level: stats.drops > 0 || stats.max_depth >= 4 ? "warn" : "",
    });
  }

  const faults = stats.usb_errors + stats.panel_restarts + stats.ffmpeg_restarts;
  rows.push({
    id: "faults",
    label: t("Faults"),
    value: faults ? t("{n} so far", { n: faults }) : t("none"),
    note: t("USB errors {usb}, panel restarts {panel}, ffmpeg restarts {ffmpeg}", {
      usb: stats.usb_errors,
      panel: stats.panel_restarts,
      ffmpeg: stats.ffmpeg_restarts,
    }),
    level: faults ? "warn" : "",
  });
  return rows;
}

export function showStats(list, stats, t) {
  const rows = describeStats(stats, t);
  list.hidden = rows.length === 0;
  const cells = [];
  for (const row of rows) {
    const label = document.createElement("dt");
    label.textContent = row.label;
    const value = document.createElement("dd");
    value.textContent = row.value;
    value.title = row.note;
    value.className = row.level;
    cells.push(label, value);
  }
  list.replaceChildren(...cells);
}
