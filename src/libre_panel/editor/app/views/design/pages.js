// The panel's pages: the theme it shows normally (config.toml's theme), the
// theme of each mode ([modes.<name>]: game mode switches to "game" while you
// play) and the round report's (the memory service shows it after a game). The
// tabs over the canvas open a page's theme; "+" puts the open theme on a page.
// The pages panel also lists the pages that plugins bring to the editor.

import { openTheme, refreshPages, save } from "../../core/actions.js";
import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { store, useStore } from "../../core/state.js";
import { html, useRef, useState } from "../../lib/preact.js";
import { pageName, shownPages } from "../../shell/panel-popover.js";
import { navigate } from "../../shell/views.js";
import { NumberField } from "../../ui/controls.js";
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
    store.set({ pages: await endpoints.setPage(page, { theme: id }), page });
    toast(t('"{id}" is on the page "{page}"', { id, page: pageName(page) }), { kind: "success" });
  } catch (error) {
    toastError(error);
  }
}

async function askForMode() {
  const name = await promptDialog({
    title: t("Another page"),
    label: t("Name of the mode"),
    hint: t("A mode a service switches to, as a plugin names it: a-z, 0-9, - and _"),
    validate: (value) => (MODE_NAME.test(value) && !["normal", "report"].includes(value) ? null : t("a-z, 0-9, - and _, starting with a letter")),
    confirm: t("Add"),
  });
  if (name) putOnPage(name);
}

function addItems() {
  const data = store.get().pages;
  const shown = new Set(shownPages(data).map((page) => page.id));
  const more = (data?.pages || []).filter((page) => !shown.has(page.id) && page.id !== "normal");
  return [
    ...more.map((page) => ({
      icon: page.id === "game" ? "game" : page.id === "report" ? "graph" : "pages",
      label: t("Show this theme: {page}", { page: pageName(page.id) }),
      run: () => putOnPage(page.id),
    })),
    { icon: "plus", label: t("Another mode…"), run: askForMode },
    "-",
    { icon: "to-panel", label: t("As the normal theme"), run: () => putOnPage("normal") },
  ];
}

/** The tabs over the canvas. */
export function PageTabs() {
  const { data, page } = useStore((s) => ({ data: s.pages, page: s.page, app: s.app.available }));
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  if (!data) return null;
  const now = pageNow(data);
  return html`<div class="page-tabs" role="tablist" aria-label=${t("Pages of the panel")} onPointerDown=${(event) => event.stopPropagation()}>
    ${shownPages(data).map(
      (item) => html`<button type="button" role="tab" key=${item.id} class="page-tab" aria-selected=${String(page === item.id)}
        title=${t("Shows {theme}", { theme: item.theme })} onClick=${() => openPage(item)}>
        ${pageName(item.id)}
        ${now === item.id ? html`<span class="page-live" title=${t("on the panel now")}></span>` : null}
      </button>`,
    )}
    <button ref=${anchor} type="button" class="page-add" aria-label=${t("Add a page")} title=${t("Add a page")}
      aria-haspopup="menu" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      <${Icon} name="plus" size=${15} />
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${t("Add a page")} width=${290}>
      <${Menu} items=${addItems()} onClose=${() => setOpen(false)} />
    <//>
  </div>`;
}

function PageCard({ item, current, open }) {
  const setFps = async (fps) => {
    try {
      store.set({ pages: await endpoints.setPage(item.id, { fps }) });
    } catch (error) {
      toastError(error);
    }
  };
  const remove = async () => {
    try {
      store.set({ pages: await endpoints.setPage(item.id, { theme: "" }) });
      if (store.get().page === item.id) store.set({ page: null });
    } catch (error) {
      toastError(error);
    }
  };
  const note = item.service
    ? item.on
      ? t("after a game")
      : t("the memory service is off")
    : item.mode && !item.theme
      ? t("shows the normal theme")
      : item.theme;
  return html`<div class=${`page-card${current ? " current" : ""}${item.theme ? "" : " empty"}`}>
    <button type="button" class="page-card-main" disabled=${!item.theme} onClick=${() => item.theme && open(item)}
      title=${item.theme ? t("Open its theme") : null}>
      ${item.theme ? html`<img alt="" src=${endpoints.previewUrl(item.theme, 240)} loading="lazy" />` : html`<span class="page-card-blank"></span>`}
      <span class="page-card-text">
        <strong>${pageName(item.id)}</strong>
        <span class="muted">${note}</span>
      </span>
    </button>
    ${item.mode
      ? html`<div class="page-card-tools">
          <label class="inline-field"><span class="muted">${t("Frames per second")}</span>
            <${NumberField} label=${t("Frames per second")} value=${item.fps} min=${1} max=${60} allowEmpty placeholder=${t("auto")}
              onChange=${(fps) => fps && setFps(fps)} /></label>
          ${item.theme
            ? html`<button type="button" class="icon-btn" aria-label=${t("Show the normal theme here")} title=${t("Show the normal theme here")}
                onClick=${remove}><${Icon} name="close" size=${14} /></button>`
            : null}
        </div>`
      : null}
  </div>`;
}

/** The side panel: every page with its theme, and the plugins' pages. */
export function PagesPanel() {
  const { data, page, plugins } = useStore((s) => ({ data: s.pages, page: s.page, plugins: s.plugins }));
  return html`<div class="side-scroll">
    <h2 class="side-title">${t("Pages of the panel")}</h2>
    ${data
      ? html`<div class="page-cards">
          ${data.pages.map((item) => html`<${PageCard} key=${item.id} item=${item} current=${page === item.id} open=${openPage} />`)}
        </div>
        <${AddPage} />
        <p class="side-hint">${t("A page is shown while a service switches its mode on: game mode shows “In game” while you play.")}</p>`
      : html`<p class="side-hint">${t("The pages come from config.toml, which could not be read.")}</p>
          <button type="button" class="btn" onClick=${refreshPages}>${t("Try again")}</button>`}
    ${plugins.length
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
      onClick=${() => setOpen(!open)}><${Icon} name="plus" size=${15} /> ${t("Add a page")}</button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${t("Add a page")} width=${290}>
      <${Menu} items=${addItems()} onClose=${() => setOpen(false)} />
    <//>`;
}
