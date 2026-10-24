// History (#/live/history, #/live/history/week, #/live/history/month), the other half of Live:
// what the memory kept on this computer.
// The temperatures, the load and the power of the CPU and the GPU over a day, a week or a month
// (the time in games behind them), what that came to, the games played most and the last
// rounds. The server reads the memory's database (/api/history, editor/history.py); without
// the memory service there is nothing kept, and the view says so with the switch that keeps it.
// Built on the shell: see ../../README.md.

import { get } from "../../core/api.js";
import { usePolled } from "../../core/console.js";
import { t } from "../../core/i18n.js";
import { enableService } from "../../core/settings.js";
import { html, useEffect, useLayoutEffect, useRef, useState } from "../../lib/preact.js";
import { navigate } from "../../shell/views.js";
import { Segmented } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";

const SPANS = () => [
  { value: "day", label: t("24 hours") },
  { value: "week", label: t("7 days") },
  { value: "month", label: t("30 days") },
];

// the CPU and the GPU, the same in every chart (checked for colour blindness on this surface)
const SERIES = () => [
  { id: "cpu", name: t("CPU"), color: "#12a598" },
  { id: "gpu", name: t("GPU"), color: "#9a78ee" },
];

const CHARTS = () => [
  { id: "temp", title: t("Temperature"), unit: "°C", min: 30 },
  { id: "load", title: t("Load"), unit: "%", min: 0, max: 100 },
  { id: "power", title: t("Power"), unit: "W", min: 0 },
];

const HEIGHT = 168;
const PAD = { top: 10, right: 44, bottom: 24, left: 40 };

// -- numbers and times -------------------------------------------------------------------------------

const whole = (value) => (value === null || value === undefined ? "–" : String(Math.round(value)));

/** A length of time as people say it: "3 h 20 min", "45 min". */
export function duration(seconds) {
  const minutes = Math.round((seconds || 0) / 60);
  if (minutes < 60) return t("{n} min", { n: minutes });
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? t("{h} h {m} min", { h: hours, m: rest }) : t("{h} h", { h: hours });
}

const format = (options) => new Intl.DateTimeFormat(document.documentElement.lang || undefined, options);
const timeOfDay = (s) => format({ hour: "2-digit", minute: "2-digit" }).format(new Date(s * 1000));
const dayOf = (s) => format({ weekday: "short", day: "numeric", month: "numeric" }).format(new Date(s * 1000));
const moment = (s) => format({ weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(s * 1000));

/** Where the x axis is labelled: whole hours (a day) or days at midnight. */
function timeTicks(from, to, span) {
  const ticks = [];
  const start = new Date(from * 1000);
  if (span === "day") {
    start.setMinutes(0, 0, 0);
    for (let at = start.getTime() / 1000 + 3600; at <= to; at += 3600) {
      if (new Date(at * 1000).getHours() % 4 === 0) ticks.push({ at, label: timeOfDay(at) });
    }
    return ticks;
  }
  start.setHours(0, 0, 0, 0);
  const every = span === "week" ? 1 : 5;
  for (let day = 1; ; day++) {
    const date = new Date(start);
    date.setDate(start.getDate() + day);
    const at = date.getTime() / 1000;
    if (at > to) break;
    if ((day - 1) % every === 0) ticks.push({ at, label: dayOf(at) });
  }
  return ticks;
}

/** Three or four round values from `low` to `high` for the y axis. */
function valueTicks(low, high) {
  const span = Math.max(high - low, 1);
  const rough = span / 3;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * power).find((s) => s >= rough) || rough;
  const ticks = [];
  for (let v = Math.floor(low / step) * step; v <= high + 1e-9; v += step) ticks.push(Math.round(v * 100) / 100);
  return { ticks, low: Math.min(low, ticks[0] ?? low), high: Math.max(high, ticks.at(-1) ?? high) };
}

// -- a chart ------------------------------------------------------------------------------------------

function useWidth() {
  const box = useRef(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (!box.current) return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);
  return [box, width];
}

/** The path of a series, broken where a step has no row (the PC was off). */
function linePath(times, values, x, y, step) {
  let path = "";
  let open = false;
  times.forEach((at, i) => {
    const value = values[i];
    if (value === null || value === undefined) {
      open = false;
      return;
    }
    const joined = open && at - times[i - 1] <= step * 1.5;
    path += `${joined ? "L" : "M"}${x(at).toFixed(1)},${y(value).toFixed(1)}`;
    open = true;
  });
  return path;
}

/** The names at the ends of the lines, apart enough to be read where the lines end close. */
function ends(series, times, x, y) {
  const found = [];
  for (const item of series) {
    let i = item.values.length - 1;
    while (i >= 0 && (item.values[i] === null || item.values[i] === undefined)) i--;
    if (i >= 0) found.push({ id: item.id, name: item.name, x: x(times[i]) + 6, y: y(item.values[i]) + 4 });
  }
  found.sort((a, b) => a.y - b.y);
  for (let i = 1; i < found.length; i++) found[i].y = Math.max(found[i].y, found[i - 1].y + 12);
  return found;
}

function Chart({ chart, data, span }) {
  const [box, width] = useWidth();
  const [hover, setHover] = useState(null); // the index of the step under the pointer
  const course = data.course;
  const times = course.t;
  const series = SERIES().map((item) => ({
    ...item,
    values: course[`${item.id}_${chart.id}`] || [],
    peaks: course[`${item.id}_${chart.id}_max`] || null,
  }));
  const seen = series.flatMap((item) => item.values).filter((v) => v !== null && v !== undefined);
  const inner = Math.max(width - PAD.left - PAD.right, 10);
  const from = data.since;
  const to = data.since + { day: 86400, week: 7 * 86400, month: 30 * 86400 }[span];
  const top = chart.max ?? Math.max(chart.min + 10, ...seen) * 1.08;
  const bottom = chart.max !== undefined ? chart.min : Math.min(chart.min, ...seen);
  const axis = valueTicks(bottom, top);
  const x = (at) => PAD.left + ((at - from) / (to - from)) * inner;
  const y = (value) => PAD.top + (1 - (value - axis.low) / (axis.high - axis.low || 1)) * (HEIGHT - PAD.top - PAD.bottom);
  const pick = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const at = from + ((event.clientX - rect.left - PAD.left) / inner) * (to - from);
    let best = -1;
    times.forEach((time, i) => {
      if (best < 0 || Math.abs(time - at) < Math.abs(times[best] - at)) best = i;
    });
    setHover(best >= 0 && Math.abs(times[best] - at) <= data.step * 2 ? best : null);
  };
  const label = `${chart.title}: ${series
    .map((item) => {
      const values = item.values.filter((v) => v !== null && v !== undefined);
      return values.length ? t("{name} {low} to {high} {unit}", { name: item.name, low: whole(Math.min(...values)), high: whole(Math.max(...values)), unit: chart.unit }) : item.name;
    })
    .join(", ")}`;
  return html`<section class="history-chart" data-chart=${chart.id}>
    <div class="history-chart-head">
      <h3>${chart.title} <span class="muted">${chart.unit}</span></h3>
      <ul class="history-legend" aria-label=${t("Legend")}>
        ${series.map((item) => html`<li key=${item.id}><span class="history-swatch" style=${`background:${item.color}`}></span>${item.name}</li>`)}
        <li><span class="history-swatch game"></span>${t("In a game")}</li>
      </ul>
    </div>
    <div class="history-plot" ref=${box}>
      ${width
        ? html`<svg width=${width} height=${HEIGHT} role="img" aria-label=${label}
            onPointerMove=${pick} onPointerLeave=${() => setHover(null)}>
            ${times.map((at, i) =>
              (course.game[i] || 0) >= 0.5
                ? html`<rect key=${`g${at}`} class="history-game" x=${x(at)} y=${PAD.top}
                    width=${Math.max(x(at + data.step) - x(at), 1)} height=${HEIGHT - PAD.top - PAD.bottom} />`
                : null,
            )}
            ${axis.ticks.map(
              (v) => html`<g key=${`y${v}`} class="history-grid">
                <line x1=${PAD.left} x2=${PAD.left + inner} y1=${y(v)} y2=${y(v)} />
                <text x=${PAD.left - 8} y=${y(v) + 4} text-anchor="end">${v}</text>
              </g>`,
            )}
            ${timeTicks(from, to, span).map(
              (tick) => html`<text key=${`x${tick.at}`} class="history-time" x=${x(tick.at)} y=${HEIGHT - 6} text-anchor="middle">${tick.label}</text>`,
            )}
            ${series.map(
              (item) => html`<path key=${item.id} class="history-line" d=${linePath(times, item.values, x, y, data.step)} stroke=${item.color} />`,
            )}
            ${ends(series, times, x, y).map(
              (end) => html`<text key=${`l${end.id}`} class="history-end" x=${end.x} y=${end.y}>${end.name}</text>`,
            )}
            ${hover !== null
              ? html`<g class="history-cross">
                  <line x1=${x(times[hover])} x2=${x(times[hover])} y1=${PAD.top} y2=${HEIGHT - PAD.bottom} />
                  ${series.map((item) =>
                    item.values[hover] === null || item.values[hover] === undefined
                      ? null
                      : html`<circle key=${item.id} cx=${x(times[hover])} cy=${y(item.values[hover])} r="4" fill=${item.color} />`,
                  )}
                </g>`
              : null}
          </svg>`
        : null}
      ${hover !== null && width
        ? html`<div class="history-tip" role="status"
            style=${`left:${Math.min(Math.max(x(times[hover]) - 90, 0), width - 180)}px`}>
            <strong>${span === "day" ? timeOfDay(times[hover]) : moment(times[hover])}</strong>
            ${series.map(
              (item) => html`<span key=${item.id}><span class="history-swatch" style=${`background:${item.color}`}></span>
                ${item.name} ${whole(item.values[hover])} ${chart.unit}
                ${item.peaks && item.peaks[hover] !== null && item.peaks[hover] !== undefined
                  ? html`<span class="muted"> · ${t("peak {value}", { value: whole(item.peaks[hover]) })}</span>`
                  : null}</span>`,
            )}
            ${(course.game[hover] || 0) >= 0.5 ? html`<span class="muted">${t("In a game")}</span>` : null}
          </div>`
        : null}
    </div>
  </section>`;
}

// -- the rest --------------------------------------------------------------------------------------------

function Summary({ summary, power }) {
  const tiles = [
    { id: "cpu", label: t("CPU temperature"), value: `${whole(summary.cpu_temp)} °C`, note: t("peak {value} °C", { value: whole(summary.cpu_temp_max) }) },
    { id: "gpu", label: t("GPU temperature"), value: `${whole(summary.gpu_temp)} °C`, note: t("peak {value} °C", { value: whole(summary.gpu_temp_max) }) },
    { id: "kept", label: t("Kept"), value: duration(summary.minutes * 60), note: t("of which {time} in games", { time: duration(summary.game_minutes * 60) }) },
  ];
  if (power) {
    // the power service prices the electricity: what the time kept added up to, as it counts
    tiles.push(
      { id: "energy", label: t("Energy"), value: `${power.kwh.toFixed(2)} kWh`, note: t("CPU and GPU, and {rest} W for the rest of the PC", { rest: Math.round(power.rest_w) }) },
      { id: "cost", label: t("Cost"), value: `${power.cost.toFixed(2)} ${power.currency}`, note: t("at {price} {currency} per kWh", { price: power.price, currency: power.currency }) },
    );
  }
  return html`<div class="history-tiles" role="list">
    ${tiles.map(
      (tile) => html`<div key=${tile.id} class="history-tile" role="listitem" data-tile=${tile.id}>
        <span class="history-tile-label">${tile.label}</span>
        <strong>${tile.value}</strong>
        <span class="muted">${tile.note}</span>
      </div>`,
    )}
    <p class="history-tiles-note muted">${t("Averages over the time Libre Panel ran.")}</p>
  </div>`;
}

function Games({ games }) {
  if (!games.length) return null;
  return html`<section class="history-games">
    <h3 class="section-title">${t("Games played most")}</h3>
    <table class="history-table">
      <thead>
        <tr>
          <th scope="col">${t("Game")}</th>
          <th scope="col">${t("Rounds")}</th>
          <th scope="col">${t("Played")}</th>
          <th scope="col">${t("Frame rate")}</th>
          <th scope="col">${t("1% low")}</th>
          <th scope="col">${t("CPU peak")}</th>
          <th scope="col">${t("GPU peak")}</th>
        </tr>
      </thead>
      <tbody>
        ${games.map(
          (game) => html`<tr key=${game.game}>
            <th scope="row">${game.game}</th>
            <td>${game.rounds}</td>
            <td>${duration(game.seconds)}</td>
            <td>${game.fps === null ? "–" : `${whole(game.fps)} fps`}</td>
            <td>${game.fps_low === null ? "–" : `${whole(game.fps_low)} fps`}</td>
            <td>${game.cpu_temp_max === null ? "–" : `${whole(game.cpu_temp_max)} °C`}</td>
            <td>${game.gpu_temp_max === null ? "–" : `${whole(game.gpu_temp_max)} °C`}</td>
          </tr>`,
        )}
      </tbody>
    </table>
  </section>`;
}

function Rounds({ rounds }) {
  if (!rounds.length) return null;
  return html`<section class="history-rounds">
    <h3 class="section-title">${t("Last rounds")}</h3>
    <ul class="history-round-list">
      ${rounds.map(
        (round) => html`<li key=${`${round.game}${round.started}`}>
          <span class="history-round-when muted">${moment(round.started)}</span>
          <strong>${round.game}</strong>
          <span>${duration(round.seconds)}</span>
          <span class="muted">${round.fps === null ? "" : t("{fps} fps, 1% low {low}", { fps: whole(round.fps), low: whole(round.fps_low) })}</span>
        </li>`,
      )}
    </ul>
  </section>`;
}

function Off({ onOn }) {
  const [busy, setBusy] = useState(false);
  const on = async () => {
    setBusy(true);
    if (await enableService("memory", true)) onOn();
    setBusy(false);
  };
  return html`<div class="history-empty" data-history="off">
    <${Icon} name="graph" size=${28} />
    <h3>${t("No history is kept yet")}</h3>
    <p>${t("Switched on, Libre Panel keeps the readings of the CPU and the GPU a minute at a time, and each round of a game with its frame rate: on this computer only. It also makes the round report after a game.")}</p>
    <button type="button" class="btn btn-primary" disabled=${busy} onClick=${on}>${t("Keep a history")}</button>
  </div>`;
}

/** History, under Live's switch (`switcher`); `rest` is the span: "", "week" or "month". */
export function HistoryView({ rest, switcher }) {
  const span = SPANS().some((item) => item.value === rest) ? rest : "day";
  const found = usePolled(() => get(`/api/history?span=${span}`), 60_000, { deps: [span] });
  const data = found.data?.span === span ? found.data : null;
  useEffect(() => {
    document.querySelector(".history")?.scrollTo?.(0, 0);
  }, [span]);
  const kept = data && data.course.t.length > 0;
  return html`<div class="history">
    ${switcher}
    <div class="history-head">
      <div>
        <h2>${t("History")}</h2>
        <p class="muted">${t("What your PC went through, kept on this computer.")}</p>
      </div>
      <${Segmented} label=${t("Period")} value=${span} options=${SPANS()}
        onChange=${(value) => navigate("live", value === "day" ? "history" : `history/${value}`)} />
    </div>
    ${found.error && !data ? html`<p class="history-note">${found.error}</p>` : null}
    ${data && !data.memory && !kept ? html`<${Off} onOn=${found.reload} />` : null}
    ${data && data.memory && !kept
      ? html`<div class="history-empty" data-history="empty">
          <${Icon} name="clock" size=${28} />
          <h3>${t("Nothing kept in this time yet")}</h3>
          <p>${t("The first minutes come in while Libre Panel runs in the background.")}</p>
        </div>`
      : null}
    ${kept
      ? html`<${Summary} summary=${data.summary} power=${data.power} />
          ${CHARTS().map((chart) => html`<${Chart} key=${chart.id} chart=${chart} data=${data} span=${span} />`)}
          <div class="history-lists">
            <${Games} games=${data.games} />
            <${Rounds} rounds=${data.rounds} />
          </div>
          ${!data.memory ? html`<p class="history-note muted">${t("The memory is switched off: nothing new is kept.")}</p>` : null}`
      : null}
  </div>`;
}
