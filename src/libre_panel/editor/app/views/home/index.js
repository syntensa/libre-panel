// Start (#/home, #/home/themes): the launcher. The panel at a glance with its picture and
// controls, the themes to switch between, and the way into the other views. On a first start
// (no theme in config.toml yet) it asks "How should it look?" with the themes made for the
// panel. Where the video mode only lacks the name of the panel's standby clip, a card at the top
// asks for it once (clip.js). Built on the shell: see ../../README.md.

import { blankTheme, importTheme, openTheme, showOnPanel, startTheme, themeName } from "../../core/actions.js";
import { registerCommands, registerProvider } from "../../core/commands.js";
import { control, usePolled } from "../../core/console.js";
import { t } from "../../core/i18n.js";
import { loadUnlock, runUnlock } from "../../core/settings.js";
import { firstStart, store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { panelName } from "../../shell/panel-popover.js";
import { navigate, registerView } from "../../shell/views.js";
import { Segmented } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toast } from "../../ui/toast.js";
import { ClipQuestion } from "./clip.js";
import { PanelCard, PanelStrip, panelModelNow, usePanelModel } from "./panel.js";
import { Gallery, ThemeRow, collectionsOf, fitsPanel, startThemes } from "./themes.js";
import { SetupCard } from "./setup.js";

// -- what a click does ---------------------------------------------------------------------------

// A first start stays on show while a theme is chosen and taken on to Design (`choosing`),
// instead of giving way to the launcher the moment config.toml names a theme.

/** Open a theme for editing in Design (asks about unsaved changes first). */
async function edit(item) {
  if (store.get().doc?.id !== item.id && !(await openTheme(item.id))) return;
  navigate("design");
}

/**
 * A theme chosen: it goes on the panel at once. A theme for another panel size says so, since
 * the picture is then scaled to the panel; `open` takes it on to Design (a first start).
 */
async function choose(item, { open = false } = {}) {
  if (open) store.set({ choosing: true });
  try {
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
    if (open) await edit(item);
  } finally {
    store.set({ choosing: false });
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

function FirstStart({ focus }) {
  const { model, items } = useStartThemes();
  const [everything, setEverything] = useState(false);
  const picks = suggestions(items, model);
  const more = items.length - picks.length;
  const shown = everything ? items : picks;
  return html`<div class="start first">
    <${PanelStrip} />
    <${ClipQuestion} focus=${focus} />
    <div class="start-head">
      <h1>${t("How should it look?")}</h1>
      <span class="muted">${t("Everything can be changed freely afterwards.")}</span>
    </div>
    <${Gallery} big=${shown.length <= 4} items=${shown} label=${themeLabel} onChoose=${(item) => choose(item, { open: true })} />
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
      </div>
      ${model && fitting.length < items.length
        ? html`<${Segmented} label=${t("Which themes")} value=${scope} onChange=${setScope}
            options=${[
              { value: "fits", label: t("For {panel}", { panel: panelName(model) }) },
              { value: "all", label: t("All sizes") },
            ]} />`
        : null}
    </div>
    <${Gallery} items=${filtered} asked=${asked} label=${themeLabel} onChoose=${(item) => choose(item)} onEdit=${edit} />
    <${StartActions} />
  </div>`;
}

// -- the launcher -------------------------------------------------------------------------------------

const TILES = () => [
  { view: "design", icon: "blocks", title: t("Design"), text: t("Blocks, layout, pages") },
  { view: "look", icon: "palette", title: t("Look"), text: t("Colours, finishes, light") },
  { view: "live", icon: "pulse", title: t("Live"), text: t("Values, services, test notices") },
  { view: "settings", icon: "sliders", title: t("Settings"), text: t("Panel, sensors, services, start") },
];

/** Where the panel's other pages stand: game mode's theme, and whether the autopilot is on. */
function Routing() {
  const pages = useStore((s) => s.pages);
  const services = usePolled(control.services, 5000);
  const game = pages?.pages?.find((page) => page.id === "game");
  const autopilot = services.data?.services?.find((service) => service.name === "autopilot");
  if (!game && !autopilot) return null;
  return html`<dl class="routing">
    ${game
      ? html`<div><dt>${t("In game")}</dt><dd>${game.theme ? themeName(game.theme) : t("shows the normal theme")}</dd></div>`
      : null}
    ${autopilot ? html`<div><dt>${t("Autopilot")}</dt><dd>${autopilot.enabled ? t("on") : t("off")}</dd></div>` : null}
  </dl>`;
}

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
    <strong>${t("Unlock all sensors")}</strong>
    <p>
      ${t(
        "CPU, GPU and mainboard temperatures, power, fan speeds, memory and drives need a small Windows service with the rights to read them. Libre Panel itself keeps running without administrator rights.",
      )}
    </p>
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
    <strong>${t("Readings are missing")}</strong>
    <p>${hint}</p>
    <button type="button" class="btn" onClick=${() => navigate("live")}>${t("Show details")}</button>
  </aside>`;
}

function Launcher({ focus }) {
  const { model, items } = useStartThemes();
  // the quick switch: what is on the panel and what is made for it, of Libre Panel's own and
  // yours (not a collection's, nor a theme kept for a page of its own, such as the round
  // report); the rest is under "All", and a collection has a line of its own under them
  const own = items.filter((item) => !item.collection && !item.special);
  const wanted = items.filter((item) => item.shown || (own.includes(item) && (item.fits || !model)));
  const quick = (wanted.length ? wanted : own.length ? own : items).slice(0, 5);
  const sets = collectionsOf(items.filter((item) => item.fits || !model));
  return html`<div class="start launcher">
    <div class="start-main">
      <${ClipQuestion} focus=${focus} />
      <${PanelCard} />
      <nav class="start-tiles" aria-label=${t("Area")}>
        ${TILES().map(
          (tile) => html`<a key=${tile.view} class="start-tile" href=${`#/${tile.view}`}>
            <${Icon} name=${tile.icon} size=${22} />
            <strong>${tile.title}</strong>
            <span class="muted">${tile.text}</span>
          </a>`,
        )}
      </nav>
    </div>
    <aside class="start-side" aria-label=${t("Themes")}>
      <div class="start-side-head">
        <h2>${t("Themes")}</h2>
        <a href="#/home/themes">${t("All")}</a>
      </div>
      <div class="quick-themes">
        ${quick.map((item) => html`<${ThemeRow} key=${item.id} item=${item} onChoose=${(chosen) => choose(chosen)} />`)}
        ${sets.map(
          ({ name, count }) => html`<a key=${name} class="quick-collection" href=${`#/home/themes/${encodeURIComponent(name)}`}
            title=${t("The screens of the project Libre Panel grew out of")}>
            <strong>${t("{collection} · Legacy", { collection: name })}</strong>
            <span class="theme-collection-count">${count}</span>
            <${Icon} name="chevron-right" size=${16} />
          </a>`,
        )}
      </div>
      <div class="start-side-actions">
        <button type="button" class="btn" onClick=${startEmpty}><${Icon} name="plus" size=${15} /> ${t("Start empty")}</button>
        <button type="button" class="btn" onClick=${openFile}><${Icon} name="upload" size=${15} /> ${t("Open a theme file")}</button>
      </div>
      <${Routing} />
      <span class="grow"></span>
      <${UnlockCard} />
      <${SetupCard} />
      <${Hint} />
    </aside>
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
  const focus = page === "clip"; // #/home/clip: the tray points to the question about the clip
  return first ? html`<${FirstStart} focus=${focus} />` : html`<${Launcher} focus=${focus} />`;
}

registerView({ id: "home", title: () => t("Start"), order: 10, icon: "home", component: HomeView });

// -- commands (Ctrl+K) ------------------------------------------------------------------------------------

registerCommands([
  { id: "home.empty", title: () => t("Start with an empty theme"), group: () => t("Theme"), icon: "plus", rank: 24, run: startEmpty },
  { id: "home.themes", title: () => t("All themes"), hint: () => t("the gallery on Start"), group: () => t("Go to"), icon: "home", rank: 6, run: () => navigate("home", "themes") },
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
