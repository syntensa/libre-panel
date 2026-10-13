// Start (#/home, #/home/themes): the launcher. What the panel shows now and when, with its
// controls; the themes to switch between; and, for a while, the first steps (a first start of
// this version) or what is new (an update). On a first start (no theme in config.toml yet) it
// asks "How should it look?" with the themes made for the panel; the choice goes on the panel
// and Start goes on to the launcher: the panel's picture, then the question about smooth motion
// (clip.js) and the first steps. Built on the shell: see ../../README.md.

import { blankTheme, importTheme, openTheme, showOnPanel, startTheme } from "../../core/actions.js";
import { registerCommands, registerProvider } from "../../core/commands.js";
import { t } from "../../core/i18n.js";
import { useMounted } from "../../core/orientation.js";
import { loadUnlock, runUnlock } from "../../core/settings.js";
import { firstStart, store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { panelName } from "../../shell/panel-popover.js";
import { navigate, registerView } from "../../shell/views.js";
import { Segmented } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toast } from "../../ui/toast.js";
import { HealthCard } from "../settings/health.js";
import { ClipQuestion, useClipQuestion } from "./clip.js";
import { WhatsNew, showsFirstSteps, showsNews, useNews } from "./news.js";
import { FirstSteps, PagesCard, StartScreenTile, VolumeTile } from "./overview.js";
import { MountedChoice, PanelCard, PanelStrip, asksMounting, panelModelNow, usePanelModel } from "./panel.js";
import { Gallery, ThemeRow, collectionsOf, fitsPanel, startThemes } from "./themes.js";
import { SequenceCard, SequencePage } from "./sequence.js";
import { SetupCard } from "./setup.js";
import { UpdateCard } from "./update.js";

// -- what a click does ---------------------------------------------------------------------------

/** Open a theme for editing in Design (asks about unsaved changes first). */
async function edit(item) {
  if (store.get().doc?.id !== item.id && !(await openTheme(item.id))) return;
  navigate("design");
}

/**
 * A theme chosen: it goes on the panel at once. A theme for another panel size says so, since
 * the picture is then scaled to the panel. On a first start Start then goes on to the launcher
 * by itself (config.toml names a theme now): the panel's picture, the next steps.
 */
async function choose(item) {
  if (!(await showOnPanel(item.id))) return;
  const { model } = panelModelNow();
  if (model && !fitsPanel(item, model) && item.size) {
    toast(
      t("“{name}” is made for {size}; the picture is scaled to this panel. Adapt the theme in Design for a sharp result.", {
        name: item.name || item.id,
        size: `${item.size[0]} × ${item.size[1]}`,
      }),
      { kind: "warn" },
    );
  }
}

/** A new theme with nothing on it, for the panel at hand. */
export async function startEmpty() {
  const { specs, doc } = store.get();
  const models = specs?.models || [];
  const base = panelModelNow().model || models.find((model) => model.id === doc?.theme?.display?.model) || models[0];
  if (await startTheme(blankTheme(base))) navigate("design");
}

async function openFile() {
  if (await importTheme()) navigate("design");
}

const themeLabel = (item) => t("Show “{name}” on the panel", { name: item.name || item.id });

function StartActions({ children }) {
  return html`<div class="start-actions">
    <button type="button" class="btn btn-wide btn-dashed-wide" onClick=${startEmpty}><${Icon} name="plus" size=${18} /> ${t("Start empty")}</button>
    <button type="button" class="btn btn-wide" onClick=${openFile}><${Icon} name="upload" size=${18} /> ${t("Open a theme file")}</button>
    <span class="grow"></span>
    ${children}
  </div>`;
}

function useStartThemes() {
  useMounted(); // the order and the pictures follow how the panel is mounted
  const { themes, models, pages } = useStore((s) => ({ themes: s.themes, models: s.specs?.models || [], pages: s.pages }));
  const { model } = usePanelModel();
  // what the panel shows until a theme is chosen: the default of the panel the themes are shown
  // for (a bar gets Lumen, any other panel Libre Default), else the one the app works out
  const defaultId = model?.default_theme ?? pages?.default ?? null;
  return { model, items: startThemes({ themes, models, pages, model, defaultId }) };
}

// -- a first start --------------------------------------------------------------------------------

/**
 * What a first start offers: Libre Panel's own themes (and yours), those made for the panel
 * when it is known, and always the panel's default, first. A collection and the theme kept for
 * the round report wait under "See all".
 */
function suggestions(items, model) {
  const own = items.filter((item) => !item.collection && !item.special);
  const fitting = model ? own.filter((item) => item.fits) : own;
  const picks = fitting.length ? fitting : own.length ? own : items;
  const standard = own.find((item) => item.isDefault);
  return standard && !picks.includes(standard) ? [standard, ...picks] : picks;
}

/** A step of a first start: its number (where there is more than one), what it asks, and how. */
function Step({ number, title, text, class: className = "", children }) {
  return html`<div class=${`start-step ${className}`}>
    ${number ? html`<span class="start-step-number" aria-hidden="true">${number}</span>` : null}
    <span class="start-step-text">
      <strong>${title}</strong>
      ${text ? html`<span class="muted">${text}</span>` : null}
    </span>
    ${children}
  </div>`;
}

/**
 * The first start, one thing after the other: the panel (found, or which one it is), how it is
 * mounted, and its theme. Smooth motion and the rest come after the choice, on the launcher;
 * the question about the clip is here only for whom the tray's line brought (`focus`), and the
 * setup of Linux only where the panel may not be used without it.
 */
function FirstStart({ focus }) {
  const { model, items } = useStartThemes();
  const [everything, setEverything] = useState(false);
  const picks = suggestions(items, model);
  const more = items.length - picks.length;
  const shown = everything ? items : picks;
  const mounting = asksMounting(model); // a bar (or a panel not known yet), not a 3.5″
  useClipQuestion(); // known already, for the tray's line and for the launcher that follows
  return html`<div class="start first">
    <${PanelStrip} />
    ${focus ? html`<${ClipQuestion} focus />` : null}
    <${SetupCard} blocking />
    <div class="start-head">
      <h1>${t("How should it look?")}</h1>
      <p class="muted">${t("Everything can be changed freely afterwards.")}</p>
    </div>
    ${mounting
      ? html`<${Step} number="1" class="start-mounted" title=${t("How is your panel mounted?")}
          text=${t("Upright, every screen shows in its version for a bar on end.")}>
          <${MountedChoice} big />
        <//>`
      : null}
    <${Step} number=${mounting ? "2" : ""} title=${t("Choose a theme")} text=${t("A click puts it on the panel; the pictures show it as the panel will.")} />
    <${Gallery} big=${shown.length <= 4} items=${shown} label=${themeLabel} onChoose=${choose} />
    <${StartActions}>
      ${more > 0
        ? html`<span class="muted">${t("More themes: {n}", { n: more })}</span>
            <button type="button" class="link-btn" onClick=${() => setEverything(!everything)}>
              ${everything ? t("Show fewer") : t("See all")}
            </button>`
        : null}
    <//>
  </div>`;
}

// -- all themes -------------------------------------------------------------------------------------

function ThemesPage({ asked }) {
  const { model, items } = useStartThemes();
  const [scope, setScope] = useState("fits");
  const fitting = items.filter((item) => item.fits);
  const filtered = model && scope === "fits" && fitting.length ? fitting : items;
  return html`<div class="start gallery">
    <${PanelStrip} compact />
    <div class="start-head">
      <div>
        <a class="back-link" href="#/home"><${Icon} name="chevron-left" size=${14} /> ${t("Start")}</a>
        <h1>${t("Themes")}</h1>
        <p class="muted">${t("A click on a theme puts it on the panel; Edit opens it in Design.")}</p>
      </div>
      ${model && fitting.length < items.length
        ? html`<${Segmented} label=${t("Which themes")} value=${scope} onChange=${setScope}
            options=${[
              { value: "fits", label: t("For {panel}", { panel: panelName(model) }) },
              { value: "all", label: t("All sizes") },
            ]} />`
        : null}
    </div>
    <${Gallery} items=${filtered} asked=${asked} label=${themeLabel} onChoose=${choose} onEdit=${edit} />
    <${StartActions} />
  </div>`;
}

// -- the launcher -------------------------------------------------------------------------------------

/** Whether this Windows download can set up the sensor service, and it is not there yet. */
const unlockable = (info) => Boolean(info?.platform === "win32" && info.available && !info.service);

/**
 * "Unlock all sensors": the sensor service (and PawnIO, and the group game frame rates need),
 * set up after one question of Windows for administrator rights. Libre Panel itself goes on
 * without them.
 */
function UnlockCard() {
  const info = useStore((s) => s.unlockInfo);
  useEffect(() => {
    loadUnlock();
  }, []);
  if (!unlockable(info)) return null;
  const busy = Boolean(info.job?.running);
  return html`<aside class="hint-card unlock-card" role="status">
    <div class="hint-card-text">
      <strong>${t("Unlock all sensors")}</strong>
      <p>
        ${t(
          "CPU, GPU and mainboard temperatures, power, fan speeds, memory and drives need a small Windows service with the rights to read them. Libre Panel itself keeps running without administrator rights.",
        )}
      </p>
    </div>
    <button type="button" class="btn" disabled=${busy} onClick=${() => runUnlock("unlock")}>
      ${busy ? t("Waiting for Windows…") : t("Unlock all sensors")}
    </button>
  </aside>`;
}

/** What the panel could not read, in one sentence, with the way to the details. */
function Hint() {
  const hint = useStore((s) => (s.app.available && !unlockable(s.unlockInfo) ? s.app.panel?.hint : ""));
  if (!hint) return null;
  return html`<aside class="hint-card" role="status">
    <div class="hint-card-text">
      <strong>${t("Readings are missing")}</strong>
      <p>${hint}</p>
    </div>
    <button type="button" class="btn" onClick=${() => navigate("live")}>${t("Show details")}</button>
  </aside>`;
}

// what Start has a card or a button of its own for: the standby clip, the sensors, Linux'
// rights, readings, the paused panel (the panel card's "Resume panel")
const OWN_CARDS = ["video-clip", "sensor-service", "linux-setup", "readings", "paused"];

/**
 * What keeps the panel from all it can do, each with the way to fix it: in short what
 * Settings → Overview says in full, then the sensors Windows keeps to administrators, the
 * rights Linux gives, readings that are missing. The launcher's first place, above the panel:
 * "is everything all right?" is answered before anything else.
 */
function StartNotices() {
  return html`<${HealthCard} compact skip=${OWN_CARDS} /><${UnlockCard} /><${SetupCard} /><${Hint} />`;
}

/** For a while: what is new (who updated, until it is closed), else the first steps (who began
 * with this version, until they are done or hidden). */
function Welcome() {
  const news = useNews();
  const version = useStore((s) => s.specs?.version);
  if (news === null || news === undefined) return null; // asked: neither until it is known
  if (showsNews(news, version)) return html`<${WhatsNew} />`;
  return showsFirstSteps(news, version) ? html`<${FirstSteps} />` : null;
}

/** The themes beside the panel: what is on it and what is made for it, a click away. */
function QuickThemes() {
  const { model, items } = useStartThemes();
  // the quick switch: what is on the panel and what is made for it, of Libre Panel's own and
  // yours (not a collection's, nor a theme kept for a page of its own, such as the round
  // report); the rest is under "All themes", and a collection has a line of its own under them
  const own = items.filter((item) => !item.collection && !item.special);
  const wanted = items.filter((item) => item.shown || (own.includes(item) && (item.fits || !model)));
  // the panel's own default (Lumen on a bar) right after the one on the panel
  const first = (item) => (item.shown ? 0 : item.id === model?.default_theme ? 1 : 2);
  const quick = (wanted.length ? [...wanted].sort((a, b) => first(a) - first(b)) : own.length ? own : items).slice(0, 5);
  const sets = collectionsOf(items.filter((item) => item.fits || !model));
  return html`<section class="start-themes" aria-labelledby="start-themes-title">
    <div class="start-side-head">
      <h2 id="start-themes-title">${t("Themes")}</h2>
      <a href="#/home/themes">${t("All themes")} <${Icon} name="chevron-right" size=${14} /></a>
    </div>
    <p class="start-side-hint muted">${t("A click puts one on the panel.")}</p>
    <div class="quick-themes">
      ${quick.map((item) => html`<${ThemeRow} key=${item.id} item=${item} label=${themeLabel(item)} onChoose=${choose} />`)}
      ${sets.map(
        ({ name, count }) => html`<a key=${name} class="quick-collection" href=${`#/home/themes/${encodeURIComponent(name)}`}>
          <span class="quick-collection-text">
            <strong>${t("{collection} · Legacy", { collection: name })}</strong>
            <span class="muted">${t("The screens of the project Libre Panel grew out of")}</span>
          </span>
          <span class="theme-collection-count">${count}</span>
          <${Icon} name="chevron-right" size=${16} />
        </a>`,
      )}
    </div>
  </section>`;
}

/**
 * The launcher, in the order of the questions someone has who comes back: is everything all
 * right (what is missing, with its fix), what is on the panel now (its card), what to do next
 * (smooth motion, the first steps or what is new) and what it shows when. The themes and the
 * rest for the panel are beside it.
 */
function Launcher({ focus }) {
  return html`<div class="start launcher">
    <div class="start-main">
      <${StartNotices} />
      <${PanelCard} />
      <${ClipQuestion} focus=${focus} />
      <${UpdateCard} />
      <${Welcome} />
      <${PagesCard} />
    </div>
    <div class="start-side">
      <${QuickThemes} />
      <section class="start-more" aria-labelledby="start-more-title">
        <h2 id="start-more-title">${t("More for your panel")}</h2>
        <${SequenceCard} />
        <${VolumeTile} />
        <${StartScreenTile} />
      </section>
    </div>
  </div>`;
}

/** A part of the address as the text it stands for; what is not valid ("%E0%A4%A") stays as it is. */
function decoded(part) {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

function HomeView({ rest }) {
  const first = useStore((s) => firstStart(s));
  const [page, asked] = rest.split("/"); // #/home/themes, or #/home/themes/<a collection>
  if (page === "themes") return html`<${ThemesPage} asked=${asked ? decoded(asked) : ""} />`;
  if (page === "sequence") return html`<${SequencePage} />`;
  const focus = page === "clip"; // #/home/clip: the tray points to the question about the clip
  return first ? html`<${FirstStart} focus=${focus} />` : html`<${Launcher} focus=${focus} />`;
}

registerView({ id: "home", title: () => t("Start"), order: 10, icon: "home", component: HomeView });

// -- commands (Ctrl+K) ------------------------------------------------------------------------------------

registerCommands([
  { id: "home.empty", title: () => t("Start with an empty theme"), group: () => t("Theme"), icon: "plus", rank: 24, run: startEmpty },
  { id: "home.themes", title: () => t("All themes"), hint: () => t("the gallery on Start"), group: () => t("Go to"), icon: "home", rank: 6, run: () => navigate("home", "themes") },
  { id: "home.sequence", title: () => t("Screens in turn"), hint: () => t("themes one after another"), group: () => t("Go to"), icon: "refresh", rank: 7, run: () => navigate("home", "sequence") },
]);

registerProvider(() =>
  store.get().themes.map((item) => ({
    id: `theme.show.${item.id}`,
    title: () => t("Show {name} on the panel", { name: item.name || item.id }),
    keywords: () => [item.id, item.collection].filter(Boolean).join(" "),
    group: () => t("Theme"),
    icon: "to-panel",
    rank: 31,
    run: () => showOnPanel(item.id),
  })),
);
