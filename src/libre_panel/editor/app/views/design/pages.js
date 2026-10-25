// The panel's pages: the theme it shows normally (config.toml's theme), the
// theme of each mode ([modes.<name>]: game mode switches to "game" while you
// play) and the round report's (the memory service shows it after a game). The
// tabs over the canvas open a page's theme; "+ Page" puts the open theme on a page.
// The pages panel says what the panel shows when, as Start does, each page with its
// switch, its theme and the way to edit it; it also lists the pages plugins bring.

import { openTheme, refreshPages, refreshThemes, save, themeName } from "../../core/actions.js";
import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { store, useStore } from "../../core/state.js";
import { html, useEffect, useRef, useState } from "../../lib/preact.js";
import { pageName, shownPages } from "../../shell/panel-popover.js";
import { navigate } from "../../shell/views.js";
import { NumberField, Select, Toggle } from "../../ui/controls.js";
import { useMounted, versionFor } from "../../core/orientation.js";
import { panelModelNow } from "../home/panel.js";
import { designPages, setDesignPage, target, targetStatus, targetTable } from "../../core/target.js";
import { fitsPanel } from "../home/themes.js";
import { promptDialog } from "../../ui/dialog.js";
import { Icon } from "../../ui/icons.js";
import { Menu, Popover } from "../../ui/layer.js";
import { toast, toastError } from "../../ui/toast.js";

const MODE_NAME = /^[a-z][a-z0-9_-]{0,31}$/;

/** The page on show now, if the panel runs: a mode, the round report, or the normal one. */
function pageNow(data) {
  if (!data || !store.get().app.available) return null;
  if (data.mode) return data.mode;
  const report = data.pages.find((page) => page.id === "report");
  if (report?.on && data.shown && data.shown === report.theme) return "report";
  return "normal";
}

async function openPage(page) {
  const { doc } = store.get();
  if (doc?.id === page.theme) store.set({ page: page.id });
  else await openTheme(page.theme, { page: page.id });
}

/** Show the open theme (saved first) on a page: "normal", "report" or a mode. */
export async function putOnPage(page) {
  const { doc, dirty } = store.get();
  if (!doc) return;
  if ((dirty || !doc.id) && !(await save())) return;
  const id = store.get().doc.id;
  try {
    await setDesignPage(page, { theme: id }); // (of the panel Design is about)
    store.set({ page });
    toast(t('"{id}" is on the page "{page}"', { id, page: pageName(page) }), { kind: "success" });
  } catch (error) {
    toastError(error);
  }
}

async function askForMode() {
  const name = await promptDialog({
    title: t("A plugin's page"),
    label: t("Name of the mode"),
    hint: t("The mode a plugin switches the panel to, as the plugin names it: a-z, 0-9, - and _"),
    validate: (value) => (MODE_NAME.test(value) && !["normal", "report"].includes(value) ? null : t("a-z, 0-9, - and _, starting with a letter")),
    confirm: t("Add"),
  });
  if (name) putOnPage(name);
}

/** Where the open theme can be shown too: normally, or on a page, each said with when it is on
 * show; a plugin's own mode last, as it is the rare one. */
function addItems() {
  const data = designPages();
  const shown = new Set(shownPages(data).map((page) => page.id));
  const more = (data?.pages || []).filter((page) => !shown.has(page.id) && page.id !== "normal");
  const beside = target() > 1; // (a panel beside the first has no plugin's pages)
  return [
    { icon: "to-panel", label: t("As the normal theme"), run: () => putOnPage("normal") },
    ...more.map((page) => ({
      icon: page.id === "game" ? "game" : page.id === "report" ? "graph" : page.id === "media" ? "music" : "pages",
      label: `${pageName(page.id)} · ${pageWhen(page)}`,
      run: () => putOnPage(page.id),
    })),
    ...(beside ? [] : ["-", { icon: "plugin", label: t("A plugin's page…"), run: askForMode }]),
  ];
}

/** The tabs over the canvas: the pages with a theme of their own, the one being edited chosen;
 * each says when it is on show and what it shows. "+ Page" shows the open theme on one more. */
export function PageTabs() {
  const { data, page } = useStore((s) => ({ data: designPages(s), page: s.page, app: s.app.available }));
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  if (!data) return null;
  const now = pageNow(data);
  return html`<div class="page-tabs" role="tablist" aria-label=${t("Pages of the panel")} onPointerDown=${(event) => event.stopPropagation()}>
    ${shownPages(data).map(
      (item) => html`<button type="button" role="tab" key=${item.id} class="page-tab" aria-selected=${String(page === item.id)}
        title=${`${pageName(item.id)}: ${pageWhen(item)} · ${t("Shows {theme}", { theme: themeName(item.theme) })}`} onClick=${() => openPage(item)}>
        ${pageName(item.id)}
        ${now === item.id ? html`<span class="page-live" title=${t("on the panel now")}></span>` : null}
      </button>`,
    )}
    <button ref=${anchor} type="button" class="page-add" aria-label=${t("Show this theme on another page")} title=${t("Show this theme on another page")}
      aria-haspopup="menu" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      <${Icon} name="plus" size=${14} /><span>${t("Page")}</span>
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${t("Show this theme on another page")} width=${320}>
      <${Menu} items=${addItems()} onClose=${() => setOpen(false)} />
    <//>
  </div>`;
}

// The round report's screens are for the report alone; the others for any page.
const REPORTS = ["round-report", "spur-round-report"];

/** The panel the pages are for: the one connected, else the one the normal theme is made for. */
function pagesModel(themes) {
  const { specs } = store.get();
  const pages = designPages();
  const models = specs?.models || [];
  if (target() > 1) { // a panel beside the first: its model, as set or as it said
    const id = targetTable()?.model !== "auto" ? targetTable()?.model : targetStatus()?.model;
    const own = models.find((model) => model.id === id);
    if (own) return own;
  }
  const { connected } = target() > 1 ? {} : panelModelNow();
  if (connected) return connected;
  const normal = pages?.pages?.find((page) => page.id === "normal")?.theme;
  const made = themes.find((item) => item.id === normal)?.model;
  return models.find((model) => model.id === made) || null;
}

/** Where a theme stands in a page's list, after the screens made for the page: Libre Panel's
 * own, yours, each collection, a plugin's, and last those made for another panel. */
function themeGroup(item, model) {
  if (model && !fitsPanel(item, model)) return [4, t("For other panels")];
  if (item.collection) return [2, item.collection];
  if (item.source === "plugin") return [3, t("From plugins")];
  return item.builtin ? [0, "Libre Panel"] : [1, t("Your themes")];
}

/** Which theme a page shows, from the installed ones: the screens made for it first
 * (``suggested``), then the others in groups, each A to Z. Two themes of one name (a copy
 * of yours, a plugin's) are told apart by their ids. A mode's page may show the normal theme
 * (""); music's and the idle clock's always show one of their own. Here and on game mode's
 * card in Settings → Services. */
export function PageThemeSelect({ page, theme, label, suggested = [], normal = true, empty = null, onChoose = null }) {
  const themes = useStore((s) => s.themes);
  useEffect(() => {
    if (!store.get().themes.length) refreshThemes();
  }, []);
  const choose = async (id) => {
    try {
      if (onChoose) await onChoose(id);
      else store.set({ pages: await endpoints.setPage(page, { theme: id }) }); // (the first panel's)
    } catch (error) {
      toastError(error);
    }
  };
  const report = page === "report";
  const offered = themes.filter((item) => report || !REPORTS.includes(item.id));
  const names = new Map();
  for (const item of offered) names.set(item.name, (names.get(item.name) || 0) + 1);
  const shownName = (item) => (names.get(item.name) > 1 ? `${item.name} (${item.id})` : item.name);
  const first = suggested.map((id) => offered.find((item) => item.id === id)).filter(Boolean);
  const model = pagesModel(themes);
  const rest = offered
    .filter((item) => !first.includes(item))
    .map((item) => ({ item, group: themeGroup(item, model) }))
    .sort((a, b) => a.group[0] - b.group[0] || a.group[1].localeCompare(b.group[1]) || a.item.name.localeCompare(b.item.name));
  const options = [
    ...(empty ? [{ value: "", label: empty }] : normal ? [{ value: "", label: t("The normal theme") }] : []),
    ...first.map((item) => ({ value: item.id, label: shownName(item), group: t("Made for this page") })),
    ...rest.map(({ item, group }) => ({ value: item.id, label: shownName(item), group: group[1] })),
  ];
  if (theme && !offered.some((item) => item.id === theme)) options.push({ value: theme, label: theme });
  return html`<${Select} label=${label} value=${theme || ""} options=${options} onChange=${choose} />`;
}

/** Why a page is on show: music while it plays, the clock when the PC is idle, ... (as Start's
 * "What the panel shows when" says it) */
function pageWhen(item) {
  if (item.beside && item.id === "game") return t("While panel 1 shows a game");
  if (item.beside && item.id === "alarm") return t("While a sensor is at its critical limit");
  if (item.id === "normal") return t("The rest of the time");
  if (item.id === "game") return t("While a game runs");
  if (item.id === "media") return t("While music plays");
  if (item.id === "idle") return t("When nobody has touched the PC for a while");
  if (item.id === "report") return t("After a game");
  return t("While a service switches to it");
}

function PageCard({ item, current, open }) {
  useMounted(); // its picture as the panel shows it
  const change = async (what) => {
    try {
      await setDesignPage(item.id, what);
    } catch (error) {
      toastError(error);
    }
  };
  const setFps = (fps) => change({ fps });
  const remove = async () => {
    try {
      await setDesignPage(item.id, { theme: "" });
      if (store.get().page === item.id) store.set({ page: null });
    } catch (error) {
      toastError(error);
    }
  };
  const switchable = item.id !== "normal" && !item.beside; // (a panel beside the first: always)
  // a page of a panel beside the first without a theme: the companion of panel 1's, or its own
  const empty = !item.beside || item.id === "normal" ? null : item.follows || designPages()?.pages?.[0]?.companion
    ? t("The companion of panel 1's theme")
    : t("Its own theme");
  const name = pageName(item.id);
  return html`<div class=${`page-card${current ? " current" : ""}${item.theme ? "" : " empty"}${switchable && !item.on ? " off" : ""}`}
      data-page=${item.id}>
    <div class="page-card-head">
      <span class="page-card-name">
        <strong>${name}</strong>
        <span class="muted">${pageWhen(item)}${item.id === "idle" && item.minutes ? ` · ${t("after {n} min", { n: item.minutes })}` : ""}</span>
      </span>
      ${switchable
        ? html`<${Toggle} label=${t("Show the page {page}", { page: name })} checked=${item.on} onChange=${(on) => change({ on })} />`
        : html`<span class="page-card-always">${t("always")}</span>`}
    </div>
    ${item.theme
      ? html`<button type="button" class="page-card-main" onClick=${() => open(item)} title=${t("Edit its theme")}>
          <img alt="" src=${endpoints.previewUrl(versionFor(item.theme), 240)} loading="lazy" fetchpriority="low" />
          ${current ? html`<span class="page-card-badge">${t("Being edited")}</span>` : null}
        </button>`
      : null /* the normal theme shows then, as the choice under it says: no empty picture */}
    <div class="page-card-theme">
      <${PageThemeSelect} page=${item.id} theme=${item.theme} suggested=${item.suggested || []}
        normal=${Boolean(item.mode) && !item.own} empty=${empty}
        onChoose=${(id) => setDesignPage(item.id, { theme: id })}
        label=${t("Theme of the page {page}", { page: name })} />
      ${item.theme && !current
        ? html`<button type="button" class="link-btn page-card-edit" onClick=${() => open(item)}>${t("Edit")}</button>`
        : null}
    </div>
    ${item.id === "idle"
      ? html`<div class="page-card-tools">
          <label class="inline-field"><span class="muted">${t("Minutes without input")}</span>
            <${NumberField} label=${t("Minutes without input")} value=${item.minutes} min=${0.5} max=${1440} step=${0.5}
              onChange=${(minutes) => minutes && change({ minutes })} /></label>
        </div>`
      : null}
    ${item.mode
      ? html`<div class="page-card-tools">
          <label class="inline-field"><span class="muted">${t("Frames per second")}</span>
            <${NumberField} label=${t("Frames per second")} value=${item.fps} min=${1} max=${60} allowEmpty placeholder=${t("auto")}
              onChange=${(fps) => fps && setFps(fps)} /></label>
          ${item.theme && !item.own
            ? html`<button type="button" class="icon-btn" aria-label=${t("Show the normal theme here")} title=${t("Show the normal theme here")}
                onClick=${remove}><${Icon} name="close" size=${14} /></button>`
            : null}
        </div>`
      : null}
  </div>`;
}

// The pages in the order Start lists them; a plugin's own modes after them.
const ORDER = ["normal", "game", "media", "idle", "report"];
const rank = (id) => (ORDER.includes(id) ? ORDER.indexOf(id) : ORDER.length);

/** The side panel: what the panel shows when, every page with its theme, and the plugins'
 * pages. */
export function PagesPanel() {
  const { data, page, plugins, number } = useStore((s) => ({ data: designPages(s), page: s.page, plugins: s.plugins, number: target(s) }));
  const pages = [...(data?.pages || [])].sort((a, b) => rank(a.id) - rank(b.id));
  const hint = number > 1
    ? t("Panel {number} goes with panel 1: while panel 1 shows a game, music or the clock at rest, a companion shows the companion of panel 1's theme there (Arena Companion beside Arena), unless a page here has a theme of its own.", { number })
    : t("Each page is a theme for a moment: while you play, while music plays, when the PC is idle. A page that is off leaves the normal theme on show.");
  return html`<div class="side-scroll">
    <h2 class="side-title">${number > 1 ? t("What panel {number} shows when", { number }) : t("What the panel shows when")}</h2>
    ${data
      ? html`<p class="side-hint">${hint}</p>
        <div class="page-cards">
          ${pages.map((item) => html`<${PageCard} key=${item.id} item=${item} current=${page === item.id} open=${openPage} />`)}
        </div>
        <${AddPage} />`
      : html`<p class="side-hint">${t("The pages come from config.toml, which could not be read.")}</p>
          <button type="button" class="btn" onClick=${refreshPages}>${t("Try again")}</button>`}
    ${plugins.length && number === 1
      ? html`<h2 class="side-title">${t("From plugins")}</h2>
          <div class="plugin-pages">
            ${plugins.map(
              (item) => html`<button type="button" key=${item.id} class="list-row" onClick=${() => navigate("plugin", item.id)}>
                <${Icon} name="plugin" size=${16} /><span>${item.title}</span><${Icon} name="chevron-right" size=${14} />
              </button>`,
            )}
          </div>`
      : null}
  </div>`;
}

function AddPage() {
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  return html`<button ref=${anchor} type="button" class="btn btn-dashed" aria-haspopup="menu" aria-expanded=${String(open)}
      onClick=${() => setOpen(!open)}><${Icon} name="plus" size=${15} /> ${t("Show this theme on another page")}</button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${t("Show this theme on another page")} width=${320}>
      <${Menu} items=${addItems()} onClose=${() => setOpen(false)} />
    <//>`;
}
