// The panel's pages: the theme it shows normally (config.toml's theme) and the
// theme of each mode ([modes.<name>]: game mode switches to "game" while you
// play). The tabs over the canvas open a page's theme; "+" puts the open theme
// on a page. The pages panel also lists the pages that plugins bring.

import { openTheme, refreshModes, save } from "../../core/actions.js";
import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { store, useStore } from "../../core/state.js";
import { html, useRef, useState } from "../../lib/preact.js";
import { modeName } from "../../shell/panel-popover.js";
import { navigate } from "../../shell/views.js";
import { NumberField } from "../../ui/controls.js";
import { promptDialog } from "../../ui/dialog.js";
import { Icon } from "../../ui/icons.js";
import { Menu, Popover } from "../../ui/layer.js";
import { toast, toastError } from "../../ui/toast.js";

const MODE_NAME = /^[a-z][a-z0-9_-]{0,31}$/;

/** The pages with a theme: [{id: "normal" | mode, theme, fps}]. */
export function pagesOf(modes) {
  if (!modes) return [];
  const pages = [{ id: "normal", theme: modes.theme, fps: null }];
  for (const [name, mode] of Object.entries(modes.modes || {})) if (mode.theme) pages.push({ id: name, theme: mode.theme, fps: mode.fps });
  return pages;
}

export const pageName = (id) => modeName(id === "normal" ? null : id);

async function openPage(page) {
  const { doc } = store.get();
  if (doc?.id === page.theme) store.set({ page: page.id });
  else await openTheme(page.theme, { page: page.id });
}

/** Show the open theme (saved first) on a page: "normal" or a mode. */
export async function putOnPage(page) {
  const { doc, dirty } = store.get();
  if (!doc) return;
  if ((dirty || !doc.id) && !(await save())) return;
  const id = store.get().doc.id;
  try {
    if (page === "normal") await endpoints.activate(id);
    else await endpoints.setMode(page, { theme: id });
    store.set({ page });
    await refreshModes();
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
    validate: (value) => (MODE_NAME.test(value) ? null : t("a-z, 0-9, - and _, starting with a letter")),
    confirm: t("Add"),
  });
  if (name) putOnPage(name);
}

function addItems() {
  const { modes } = store.get();
  const used = new Set(pagesOf(modes).map((page) => page.id));
  const known = (modes?.known || []).filter((name) => !used.has(name));
  return [
    ...known.map((name) => ({ icon: name === "game" ? "game" : "pages", label: t("Show this theme: {page}", { page: pageName(name) }), run: () => putOnPage(name) })),
    { icon: "plus", label: t("Another mode…"), run: askForMode },
    "-",
    { icon: "to-panel", label: t("As the normal theme"), run: () => putOnPage("normal") },
  ];
}

/** The tabs over the canvas. */
export function PageTabs() {
  const { modes, page, active } = useStore((s) => ({ modes: s.modes, page: s.page, active: s.app.panel?.mode }));
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  if (!modes) return null;
  const pages = pagesOf(modes);
  return html`<div class="page-tabs" role="tablist" aria-label=${t("Pages of the panel")} onPointerDown=${(event) => event.stopPropagation()}>
    ${pages.map(
      (item) => html`<button type="button" role="tab" key=${item.id} class="page-tab" aria-selected=${String(page === item.id)}
        title=${t("Shows {theme}", { theme: item.theme })} onClick=${() => openPage(item)}>
        ${pageName(item.id)}
        ${(active || "normal") === item.id && store.get().app.available ? html`<span class="page-live" title=${t("on the panel now")}></span>` : null}
      </button>`,
    )}
    <button ref=${anchor} type="button" class="page-add" aria-label=${t("Add a page")} title=${t("Add a page")}
      aria-haspopup="menu" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      <${Icon} name="plus" size=${15} />
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${t("Add a page")} width=${280}>
      <${Menu} items=${addItems()} onClose=${() => setOpen(false)} />
    <//>
  </div>`;
}

/** The side panel: every page with its theme and frame rate, and the plugins' pages. */
export function PagesPanel() {
  const { modes, page, plugins, docId } = useStore((s) => ({ modes: s.modes, page: s.page, plugins: s.plugins, docId: s.doc?.id }));
  const setFps = async (name, fps) => {
    try {
      store.set({ modes: await endpoints.setMode(name, { fps }) });
    } catch (error) {
      toastError(error);
    }
  };
  const remove = async (name) => {
    try {
      store.set({ modes: await endpoints.setMode(name, { theme: "" }) });
      if (store.get().page === name) store.set({ page: null });
    } catch (error) {
      toastError(error);
    }
  };
  return html`<div class="side-scroll">
    <h2 class="side-title">${t("Pages of the panel")}</h2>
    ${modes
      ? html`<div class="page-cards">
          ${pagesOf(modes).map(
            (item) => html`<div key=${item.id} class=${`page-card${page === item.id ? " current" : ""}`}>
              <button type="button" class="page-card-main" onClick=${() => openPage(item)} title=${t("Open its theme")}>
                <img alt="" src=${endpoints.previewUrl(item.theme, 240)} loading="lazy" />
                <span class="page-card-text">
                  <strong>${pageName(item.id)}</strong>
                  <span class="muted">${item.theme}${item.theme === docId ? ` · ${t("open")}` : ""}</span>
                </span>
              </button>
              ${item.id !== "normal"
                ? html`<div class="page-card-tools">
                    <label class="inline-field"><span class="muted">${t("Frames per second")}</span>
                      <${NumberField} label=${t("Frames per second")} value=${item.fps} min=${1} max=${60} allowEmpty placeholder=${t("auto")}
                        onChange=${(fps) => fps && setFps(item.id, fps)} /></label>
                    <button type="button" class="icon-btn" aria-label=${t("Remove the page")} title=${t("Remove the page")}
                      onClick=${() => remove(item.id)}><${Icon} name="close" size=${14} /></button>
                  </div>`
                : null}
            </div>`,
          )}
        </div>
        <${AddPage} />
        <p class="side-hint">${t("A page is shown while a service switches its mode on: game mode shows “In game” while you play.")}</p>`
      : html`<p class="side-hint">${t("The pages come from config.toml, which could not be read.")}</p>`}
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
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} label=${t("Add a page")} width=${280}>
      <${Menu} items=${addItems()} onClose=${() => setOpen(false)} />
    <//>`;
}
