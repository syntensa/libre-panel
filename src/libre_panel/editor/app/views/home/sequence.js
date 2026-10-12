// Screens in turn (#/home/sequence): themes the panel shows one after another, each for its
// seconds, minutes or hours, and from the top again after the last ([sequence] in config.toml,
// libre_panel/sequence.py). The launcher has a card with the steps and a switch; this page
// sets them up: a row a screen, its time in the unit that suits it, up, down, away.

import { endpoints } from "../../core/api.js";
import { themeName } from "../../core/actions.js";
import { t } from "../../core/i18n.js";
import { versionFor } from "../../core/orientation.js";
import { store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { IconButton, NumberField, Segmented, Select, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toast, toastError } from "../../ui/toast.js";
import { PanelStrip } from "./panel.js";

export const UNITS = [
  { value: "s", size: 1 },
  { value: "min", size: 60 },
  { value: "h", size: 3600 },
];
const unitLabel = (unit) => ({ s: t("seconds"), min: t("minutes"), h: t("hours") })[unit];
const unitShort = (unit) => ({ s: t("s"), min: t("min"), h: t("h") })[unit];

/** A step as the page edits it: the theme, and its time in the largest unit that takes it whole. */
export function editable(step) {
  const unit = [...UNITS].reverse().find((item) => step.seconds % item.size === 0) || UNITS[0];
  return { theme: step.theme, amount: step.seconds / unit.size, unit: unit.value };
}

export const secondsOf = (row) => Math.round(Number(row.amount) * (UNITS.find((item) => item.value === row.unit)?.size || 1));

/** A time as a person reads it: "5 min", "1 h 30 min", "45 s". */
export function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = s % 60;
  const parts = [];
  if (h) parts.push(`${h} ${unitShort("h")}`);
  if (m) parts.push(`${m} ${unitShort("min")}`);
  if (rest || !parts.length) parts.push(`${rest} ${unitShort("s")}`);
  return parts.join(" ");
}

/** What is left of a step, as a clock: 4:05, 1:02:03. */
function left(seconds) {
  const s = Math.max(0, Math.ceil(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

/** The sequence as the server has it, asked again every `ms` (where the panel stands moves on). */
function useSequence(ms = 2000) {
  const [state, setState] = useState(null);
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const tick = async () => {
      try {
        const data = await endpoints.sequence();
        if (alive) setState(data);
      } catch {
        if (alive) setState((now) => now || { on: false, steps: [], now: null, failed: true });
      }
      if (alive) timer = setTimeout(tick, ms);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);
  return [state, setState];
}

async function save(on, steps) {
  const answer = await endpoints.setSequence(on, steps);
  store.set({ pages: await endpoints.pages() });
  return answer;
}

/** Where the panel stands in the sequence, in a few words. */
function Now({ sequence }) {
  const now = sequence?.now;
  if (!sequence?.on || !sequence.steps.length) return null;
  if (!now) return html`<span class="sequence-now muted">${t("Shows when the panel runs")}</span>`;
  const next = sequence.steps[(now.index + 1) % sequence.steps.length];
  return html`<span class="sequence-now">
    <${Icon} name="play" size=${12} /> ${themeName(now.theme)}
    <span class="muted">${t("{name} in {time}", { name: themeName(next.theme), time: left(now.left_s) })}</span>
  </span>`;
}

// -- the card on the launcher ------------------------------------------------------------------------

export function SequenceCard() {
  const [sequence, setSequence] = useSequence(3000);
  if (!sequence) return null;
  const steps = sequence.steps || [];
  const toggle = async (on) => {
    try {
      setSequence(await save(on, steps));
    } catch (error) {
      toastError(error);
    }
  };
  return html`<section class=${`sequence-card${sequence.on && steps.length ? " on" : ""}`} aria-label=${t("Screens in turn")}>
    <div class="sequence-card-head">
      <${Icon} name="refresh" size=${16} />
      <strong>${t("Screens in turn")}</strong>
      <span class="grow"></span>
      ${steps.length ? html`<${Toggle} checked=${sequence.on} label=${t("Screens in turn")} onChange=${toggle} />` : null}
    </div>
    ${steps.length
      ? html`<ol class="sequence-chips">
          ${steps.map(
            (step, i) => html`<li key=${i} class=${sequence.now?.index === i ? "current" : ""}>
              <span>${themeName(step.theme)}</span><span class="muted">${duration(step.seconds)}</span>
            </li>`,
          )}
        </ol>`
      : html`<p class="muted">${t("Themes one after another, each for as long as you like, then from the top.")}</p>`}
    <div class="sequence-card-foot">
      <${Now} sequence=${sequence} />
      <span class="grow"></span>
      <a class="btn" href="#/home/sequence">${steps.length ? t("Edit") : t("Set up")}</a>
    </div>
  </section>`;
}

// -- the page --------------------------------------------------------------------------------------

function StepRow({ row, index, count, current, options, onChange, onMove, onRemove, shortest }) {
  const seconds = secondsOf(row);
  const short = !(seconds >= shortest);
  return html`<li class=${`sequence-step${current ? " current" : ""}${short ? " invalid" : ""}`} data-step=${index}>
    <span class="sequence-index">${index + 1}</span>
    <span class="quick-theme-picture"><img src=${endpoints.previewUrl(versionFor(row.theme), 320)} alt="" loading="lazy" decoding="async" /></span>
    <${Select} class="sequence-theme" label=${t("Theme of screen {n}", { n: index + 1 })} value=${row.theme} options=${options}
      onChange=${(theme) => onChange({ ...row, theme })} />
    <span class="sequence-time">
      <${NumberField} label=${t("How long screen {n} shows", { n: index + 1 })} value=${row.amount} min=${1} step=${1}
        onChange=${(amount) => onChange({ ...row, amount })} />
      <${Segmented} label=${t("Unit")} value=${row.unit} onChange=${(unit) => onChange({ ...row, unit })}
        options=${UNITS.map((unit) => ({ value: unit.value, label: unitShort(unit.value), title: unitLabel(unit.value) }))} />
    </span>
    <span class="sequence-tools">
      <${IconButton} icon="arrow-up" label=${t("Earlier")} disabled=${index === 0} onClick=${() => onMove(-1)} />
      <${IconButton} icon="arrow-down" label=${t("Later")} disabled=${index === count - 1} onClick=${() => onMove(1)} />
      <${IconButton} icon="trash" label=${t("Remove")} onClick=${onRemove} />
    </span>
    ${short ? html`<span class="sequence-error">${t("At least {n} seconds", { n: shortest })}</span>` : null}
  </li>`;
}

export function SequencePage() {
  const themes = useStore((s) => s.themes);
  const [sequence, setSequence] = useSequence(2000);
  const [rows, setRows] = useState(null); // null: as the server has them
  if (!sequence) return html`<div class="start sequence-page"><${PanelStrip} compact /></div>`;
  const saved = sequence.steps.map(editable);
  const shown = rows ?? saved;
  const shortest = sequence.shortest_s || 5;
  const dirty = rows !== null && JSON.stringify(rows.map((row) => [row.theme, secondsOf(row)])) !== JSON.stringify(saved.map((row) => [row.theme, secondsOf(row)]));
  const valid = shown.every((row) => secondsOf(row) >= shortest);
  const total = shown.reduce((sum, row) => sum + (secondsOf(row) || 0), 0);
  // Libre Panel's own and yours first, then each collection under its name
  const options = [...themes]
    .sort((a, b) => Boolean(a.collection) - Boolean(b.collection))
    .map((item) => ({ value: item.id, label: item.name || item.id, group: item.collection || "" }));
  const edit = (next) => setRows(next);
  const add = () => {
    const used = new Set(shown.map((row) => row.theme));
    const next = themes.find((item) => !item.collection && !used.has(item.id)) || themes[0];
    if (next) edit([...shown, { theme: next.id, amount: 5, unit: "min" }]);
  };
  const apply = async (on) => {
    try {
      const steps = shown.map((row) => ({ theme: row.theme, seconds: secondsOf(row) }));
      setSequence(await save(on, steps));
      setRows(null);
      toast(on && steps.length ? t("The screens take turns on the panel") : t("The panel shows one theme again"), { kind: "success" });
    } catch (error) {
      toastError(error);
    }
  };
  const running = sequence.on && sequence.steps.length > 0;
  return html`<div class="start sequence-page">
    <${PanelStrip} compact />
    <div class="start-head">
      <div>
        <a class="back-link" href="#/home"><${Icon} name="chevron-left" size=${14} /> ${t("Start")}</a>
        <h1>${t("Screens in turn")}</h1>
      </div>
      <${Now} sequence=${sequence} />
    </div>
    <p class="muted sequence-lead">
      ${t("Each screen shows for as long as you set, then the next; after the last it begins again. A game's theme and the autopilot go first.")}
    </p>
    ${shown.length
      ? html`<ol class="sequence-steps">
          ${shown.map(
            (row, i) => html`<${StepRow} key=${i} row=${row} index=${i} count=${shown.length} options=${options} shortest=${shortest}
              current=${!dirty && running && sequence.now?.index === i}
              onChange=${(changed) => edit(shown.map((other, j) => (j === i ? changed : other)))}
              onMove=${(by) => {
                const next = [...shown];
                [next[i], next[i + by]] = [next[i + by], next[i]];
                edit(next);
              }}
              onRemove=${() => edit(shown.filter((_, j) => j !== i))} />`,
          )}
        </ol>`
      : html`<p class="sequence-empty muted">${t("No screens yet: add the first.")}</p>`}
    <button type="button" class="btn btn-wide btn-dashed-wide sequence-add" onClick=${add}><${Icon} name="plus" size=${18} /> ${t("Add a screen")}</button>
    <div class="sequence-foot">
      ${shown.length ? html`<span class="muted">${t("Once round: {time}", { time: duration(total) })}</span>` : null}
      <span class="grow"></span>
      ${running ? html`<button type="button" class="btn" onClick=${() => apply(false)}>${t("Stop")}</button>` : null}
      <button type="button" class="btn btn-primary" disabled=${!valid || !shown.length || (!dirty && running)} onClick=${() => apply(true)}>
        ${running ? t("Save") : t("Save and start")}
      </button>
    </div>
  </div>`;
}
