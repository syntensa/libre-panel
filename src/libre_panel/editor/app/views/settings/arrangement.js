// Where the panels stand: each in its real size, beside a monitor to place them by, moved by
// dragging or with the arrow keys. Positions are millimetres (config.toml `at = [x, y]`: the
// top left corner of the panel's picture as it is mounted); the first panel's is [device]'s.

import { endpoints } from "../../core/api.js";
import { formatNumber, t } from "../../core/i18n.js";
import { setPref, useStore } from "../../core/state.js";
import { html, useEffect, useRef, useState } from "../../lib/preact.js";
import { Select } from "../../ui/controls.js";

const MM_PER_INCH = 25.4;
const GAP_MM = 30; // between two panels not placed yet
const SNAP_MM = 5;
const STICK_MM = 8; // an edge this near another's lines up with it
const BELOW_MM = 40; // panels not placed yet stand this far under the monitor
const MARGIN_MM = 40;
const LIMIT_MM = 5000; // config.ARRANGEMENT_MM
const UNKNOWN_MM = [120, 70]; // a panel whose model is not known yet
const COMMIT_MS = 500; // arrow keys: written once they rest
const LABEL_PX = 12; // the labels' size on the screen, whatever the view's scale

// a monitor to place the panels by (the person's choice, kept in this browser)
const MONITORS = { none: null, 24: [16, 9], 27: [16, 9], 32: [16, 9], 34: [21, 9] };

function monitorSize(id) {
  const ratio = MONITORS[id];
  if (!ratio) return null;
  const diagonal = Number(id) * MM_PER_INCH;
  const unit = diagonal / Math.hypot(...ratio);
  return [Math.round(ratio[0] * unit), Math.round(ratio[1] * unit)];
}

/** A panel's picture in millimetres, as it is mounted (`size`: its frames, in pixels). */
export function panelMillimetres(model, size) {
  if (!model?.size_inch || !model.native_width) return null;
  const per = (model.size_inch * MM_PER_INCH) / Math.hypot(model.native_width, model.native_height);
  const [width, height] = size || model.landscape;
  return [Math.round(width * per), Math.round(height * per)];
}

/**
 * A panel as the arrangement shows it: its number, its model's name, its size in millimetres
 * as mounted (from the size of its frames where the panel says it, else from `orientation`),
 * its place (`at`, null: not placed yet) and the theme it shows. `model`: the catalogue's
 * (specs), or null.
 */
export function arrangementItem({ number, model, size, orientation, at, theme = "" }) {
  const turned = size || (model ? model[orientation === "portrait" ? "portrait" : "landscape"] : null);
  const mm = panelMillimetres(model, turned);
  const unknown = orientation === "portrait" ? [...UNKNOWN_MM].reverse() : UNKNOWN_MM;
  return {
    number,
    label: model ? model.label.replace(/"/g, "″") : t("Panel {number}", { number }),
    mm: mm || unknown,
    known: Boolean(mm),
    round: model?.shape === "round",
    at: Array.isArray(at) && at.length === 2 ? at : null,
    theme: theme || "",
  };
}

/**
 * Where each panel stands: its own place, or, not placed yet, in a row with the others not
 * placed (under the monitor, or beside the panels that are placed).
 */
export function arranged(items) {
  const placed = items.filter((item) => item.at);
  const loose = items.filter((item) => !item.at);
  const row = loose.reduce((sum, item) => sum + item.mm[0], 0) + GAP_MM * Math.max(0, loose.length - 1);
  let x = placed.length ? Math.max(...placed.map((item) => item.at[0] + item.mm[0])) + GAP_MM : -Math.round(row / 2);
  const y = placed.length ? Math.min(...placed.map((item) => item.at[1])) : 0;
  return items.map((item) => {
    if (item.at) return item;
    const at = [x, y];
    x += item.mm[0] + GAP_MM;
    return { ...item, at };
  });
}

const clamp = (value) => Math.max(-LIMIT_MM, Math.min(LIMIT_MM, value));

/** ``at`` snapped to whole steps, and an edge or the middle near another's lined up with it. */
function snapped(at, mm, others) {
  const fit = (value, size, axis) => {
    let best = Math.round(value / SNAP_MM) * SNAP_MM;
    let gap = STICK_MM;
    for (const other of others) {
      const lines = [other.at[axis], other.at[axis] + other.mm[axis], other.at[axis] + other.mm[axis] / 2];
      for (const line of lines) {
        for (const [edge, shift] of [[value, 0], [value + size, size], [value + size / 2, size / 2]]) {
          if (Math.abs(edge - line) < gap) {
            gap = Math.abs(edge - line);
            best = Math.round(line - shift);
          }
        }
      }
    }
    return clamp(best);
  };
  return [fit(at[0], mm[0], 0), fit(at[1], mm[1], 1)];
}

const centimetres = ([width, height]) =>
  t("{width} × {height} cm", { width: formatNumber(width / 10, 1), height: formatNumber(height / 10, 1) });

/**
 * The panels where they stand, in their real sizes. `items`: [{number, label, mm, round, known,
 * at}] (`at` null: not placed yet). `onPlace` gets the place of every panel, in their order,
 * once one has moved: those not placed yet keep the place they are shown at. `picture`: the
 * address of the picture across the panels (null: none), shown over those that are placed as
 * the panels show it; `onPicture` gets a file chosen for it, `onNoPicture` takes it away.
 */
export function Arrangement({ items, onPlace, onReset, picture = null, onPicture, onNoPicture }) {
  const chosen = useStore((s) => s.prefs.monitor || "27");
  const monitor = monitorSize(chosen);
  const [moved, setMoved] = useState(null); // the places while one moves: [at, ...]
  const drag = useRef(null);
  const timer = useRef(null);
  const svg = useRef(null);
  const picker = useRef(null);
  const [area, setArea] = useState([600, 260]); // the view's size on the screen, in pixels
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    const element = svg.current;
    if (!element || typeof ResizeObserver === "undefined") return undefined;
    const seen = new ResizeObserver(() => {
      const { width, height } = element.getBoundingClientRect();
      if (width && height) setArea([width, height]);
    });
    seen.observe(element);
    return () => seen.disconnect();
  }, []);
  const shown = arranged(items).map((item, i) => (moved ? { ...item, at: moved[i] } : item));
  // what the view takes in: the panels and the monitor, with a margin; kept while one is dragged
  const screen = monitor ? { at: [-Math.round(monitor[0] / 2), -BELOW_MM - monitor[1]], mm: monitor } : null;
  const boxes = [...shown, ...(screen ? [screen] : [])];
  const fresh = [
    Math.min(...boxes.map((box) => box.at[0])) - MARGIN_MM,
    Math.min(...boxes.map((box) => box.at[1])) - MARGIN_MM,
    Math.max(...boxes.map((box) => box.at[0] + box.mm[0])) + MARGIN_MM,
    Math.max(...boxes.map((box) => box.at[1] + box.mm[1])) + MARGIN_MM,
  ];
  // and room for the line under the lowest panel, at the scale the view comes to
  const scale = Math.max((fresh[2] - fresh[0]) / area[0], (fresh[3] - fresh[1]) / area[1]);
  fresh[3] += LABEL_PX * 2.2 * scale;
  const view = drag.current?.view || fresh;
  const [left, top, right, bottom] = view;
  const width = right - left;
  const perPixel = Math.max(width / area[0], (bottom - top) / area[1]); // (the view meets the area)
  const text = LABEL_PX * perPixel; // the labels' size, in millimetres of the view
  const places = () => shown.map((item) => [...item.at]);
  const others = (index) => [...shown.filter((_, i) => i !== index), ...(screen ? [screen] : [])];
  // the picture across them covers the panels that are placed, its middle at theirs (arrangement.py)
  const covered = picture ? shown.filter((_, i) => items[i].at) : [];
  const cover = covered.length
    ? (() => {
        const x0 = Math.min(...covered.map((item) => item.at[0]));
        const y0 = Math.min(...covered.map((item) => item.at[1]));
        const x1 = Math.max(...covered.map((item) => item.at[0] + item.mm[0]));
        const y1 = Math.max(...covered.map((item) => item.at[1] + item.mm[1]));
        return [x0, y0, x1 - x0, y1 - y0];
      })()
    : null;
  const pictured = (item) => cover && items[shown.indexOf(item)]?.at;
  // written, then shown as config.toml has it again (the new places, or the old where it failed)
  const commit = (next) => Promise.resolve(onPlace(next)).finally(() => setMoved(null));

  const move = (index, at) => {
    const next = places();
    next[index] = at;
    setMoved(next);
    return next;
  };
  const down = (index, event) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { index, x: event.clientX, y: event.clientY, from: [...shown[index].at], per: perPixel, view, places: null };
  };
  const pointer = (event) => {
    const now = drag.current;
    if (!now) return;
    const at = [now.from[0] + (event.clientX - now.x) * now.per, now.from[1] + (event.clientY - now.y) * now.per];
    now.places = move(now.index, snapped(at, shown[now.index].mm, others(now.index)));
  };
  const up = () => {
    const now = drag.current;
    drag.current = null;
    if (now?.places && now.places[now.index].join() !== now.from.join()) commit(now.places);
    else setMoved(null);
  };
  const key = (index, event) => {
    const steps = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (!steps) return;
    event.preventDefault();
    const step = event.shiftKey ? SNAP_MM * 5 : SNAP_MM;
    const at = shown[index].at;
    const next = move(index, [clamp(at[0] + steps[0] * step), clamp(at[1] + steps[1] * step)]);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => commit(next), COMMIT_MS);
  };

  return html`<div class="arrangement">
    <div class="arrangement-head">
      <span class="arrangement-text">${t("Drag each panel to where it stands: in the case, under the monitor, on end. They are shown in their real sizes.")}</span>
      <span class="arrangement-actions">
        <${Select}
          label=${t("Monitor beside them")}
          value=${chosen}
          options=${[
            { value: "none", label: t("No monitor") },
            ...["24", "27", "32"].map((inch) => ({ value: inch, label: t("Monitor {size}", { size: `${inch}″` }) })),
            { value: "34", label: t("Monitor {size}, ultrawide", { size: "34″" }) },
          ]}
          onChange=${(value) => setPref("monitor", value)}
        />
        ${onReset && items.some((item) => item.at)
          ? html`<button type="button" class="btn" onClick=${onReset}>${t("Line them up again")}</button>`
          : null}
        ${onPicture
          ? html`<button type="button" class="btn" title=${t("One picture under the themes of all panels, each showing the part where it stands")}
              onClick=${() => picker.current?.click()}>${picture ? t("Another picture…") : t("A picture across them…")}</button>
              <input ref=${picker} type="file" accept="image/*" hidden data-across-file
                onChange=${(event) => {
                  const file = event.currentTarget.files?.[0];
                  event.currentTarget.value = "";
                  if (file) onPicture(file);
                }} />`
          : null}
        ${picture && onNoPicture ? html`<button type="button" class="btn" onClick=${onNoPicture}>${t("No picture")}</button>` : null}
      </span>
    </div>
    <svg
      ref=${svg}
      class="arrangement-view"
      viewBox=${`${left} ${top} ${width} ${bottom - top}`}
      preserveAspectRatio="xMidYMid meet"
      onPointerMove=${pointer}
      onPointerUp=${up}
      onPointerCancel=${up}
    >
      <defs>
        ${cover
          ? html`<clipPath id="arranged-covered">
              ${shown.filter(pictured).map((item) =>
                item.round
                  ? html`<ellipse key=${item.number} cx=${item.at[0] + item.mm[0] / 2} cy=${item.at[1] + item.mm[1] / 2} rx=${item.mm[0] / 2} ry=${item.mm[1] / 2} />`
                  : html`<rect key=${item.number} x=${item.at[0]} y=${item.at[1]} width=${item.mm[0]} height=${item.mm[1]} rx=${Math.min(item.mm[0], item.mm[1]) / 12} />`,
              )}
            </clipPath>`
          : null}
        ${shown.map((item) =>
          html`<clipPath id=${`arranged-${item.number}`} key=${item.number}>
            ${item.round
              ? html`<ellipse cx=${item.at[0] + item.mm[0] / 2} cy=${item.at[1] + item.mm[1] / 2} rx=${item.mm[0] / 2} ry=${item.mm[1] / 2} />`
              : html`<rect x=${item.at[0]} y=${item.at[1]} width=${item.mm[0]} height=${item.mm[1]} rx=${Math.min(item.mm[0], item.mm[1]) / 12} />`}
          </clipPath>`,
        )}
      </defs>
      ${screen
        ? html`<g class="arrangement-monitor" aria-hidden="true">
            <rect x=${screen.at[0]} y=${screen.at[1]} width=${monitor[0]} height=${monitor[1]} rx=${text / 2} />
            <text x=${screen.at[0] + monitor[0] / 2} y=${screen.at[1] + monitor[1] / 2} font-size=${text * 1.2}>${t("Monitor")}</text>
          </g>`
        : null}
      ${cover
        ? html`<image class="arrangement-across" href=${picture} x=${cover[0]} y=${cover[1]} width=${cover[2]} height=${cover[3]}
            preserveAspectRatio="xMidYMid slice" clip-path="url(#arranged-covered)" />`
        : null}
      ${shown.map(
        (item, i) => html`<g
          key=${item.number}
          class=${`arrangement-panel${item.known ? "" : " unknown"}${pictured(item) ? " pictured" : ""}${drag.current?.index === i ? " moving" : ""}`}
          data-arranged=${item.number}
          data-at=${item.at.join(",")}
          tabindex="0"
          role="button"
          aria-label=${t("Panel {number}, {label}: drag it, or move it with the arrow keys", { number: item.number, label: item.label })}
          onPointerDown=${(event) => down(i, event)}
          onKeyDown=${(event) => key(i, event)}
        >
          ${item.theme && !pictured(item)
            ? html`<image href=${endpoints.previewUrl(item.theme, 320)} x=${item.at[0]} y=${item.at[1]} width=${item.mm[0]} height=${item.mm[1]}
                preserveAspectRatio="xMidYMid meet" clip-path=${`url(#arranged-${item.number})`} opacity="0.85" />`
            : null}
          ${item.round
            ? html`<ellipse cx=${item.at[0] + item.mm[0] / 2} cy=${item.at[1] + item.mm[1] / 2} rx=${item.mm[0] / 2} ry=${item.mm[1] / 2} />`
            : html`<rect x=${item.at[0]} y=${item.at[1]} width=${item.mm[0]} height=${item.mm[1]} rx=${Math.min(item.mm[0], item.mm[1]) / 12} />`}
          ${(item.theme || pictured(item)) // over what it shows: in its corner
            ? html`<text class="arrangement-number" x=${item.at[0] + text * 1.1} y=${item.at[1] + text * 1.1} font-size=${text * 1.3}>${item.number}</text>`
            : html`<text class="arrangement-number" x=${item.at[0] + item.mm[0] / 2} y=${item.at[1] + item.mm[1] / 2} font-size=${Math.min(text * 2, item.mm[1] * 0.7)}>${item.number}</text>`}
          <text class="arrangement-size" x=${item.at[0] + item.mm[0] / 2} y=${item.at[1] + item.mm[1] + text * 1.3} font-size=${text}>
            ${item.known ? `${item.label} · ${centimetres(item.mm)}` : t("{label}: size not known yet", { label: item.label })}
          </text>
        </g>`,
      )}
    </svg>
  </div>`;
}
