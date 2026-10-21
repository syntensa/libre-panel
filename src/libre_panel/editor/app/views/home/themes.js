// The themes Start shows: the theme list (/api/themes) in the order that helps most, and the
// cards and rows they come in. Nothing here names a theme: what is installed is what shows. A
// theme that names a `collection` (a set that belongs together, such as the screens of the
// project Libre Panel grew out of) is kept apart: Libre Panel's own themes and yours come first,
// each collection waits closed at the end.

import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { html, useEffect, useRef, useState } from "../../lib/preact.js";
import { mounted, themeEntry, useMounted, versionBase, versionFor } from "../../core/orientation.js";
import { store } from "../../core/state.js";
import { panelName } from "../../shell/panel-popover.js";
import { Icon } from "../../ui/icons.js";

const same = (a, b) => Boolean(a && b) && a[0] === b[0] && a[1] === b[1];

const sized = (theme, model) => same(theme.size, model.landscape) || same(theme.size, model.portrait);

/** Whether a theme is made for a panel: its model, or its size either way round, or it has a
 * version of that size ("lumen-1920x720" makes Lumen one for the 12.3″). */
export const fitsPanel = (theme, model) =>
  !model ||
  theme.model === model.id ||
  sized(theme, model) ||
  (store.get().versions || []).some((version) => versionBase(version.id) === theme.id && sized(version, model));

const sourceText = (theme) => (theme.source === "plugin" ? t("from a plugin") : theme.builtin ? t("built in") : t("yours"));

/**
 * The themes with what Start says about each: whether it is on the panel (`shown`), made for
 * the panel (`fits`) or kept for another page or panel (`special`: the round report, a theme
 * to go beside another on a second panel, Lumen Companion), the `collection`
 * it belongs to ("" for Libre Panel's own and yours), and a short `note`. On a first start the
 * theme `defaultId` is the panel's own default (`isDefault`: what it shows until another is
 * chosen). Order: the one on the panel or the default, then Libre Panel's own and yours (those
 * that fit first: yours, built in, kept for a page), then the collections' the same way.
 */
// a theme made to go beside another on a second panel (its versions too): kept for that
const COMPANION = /-companion(-vertical|-\d+x\d+)?$/;

export function startThemes({ themes, models, pages, model, defaultId = null }) {
  // on a first start the theme config.toml names is the default, not a choice
  const first = pages?.chosen === false;
  const normal = first ? null : (pages?.pages?.find((page) => page.id === "normal")?.theme ?? null);
  // a page of its own that is off (music's, the clock's) keeps nothing: its screen is one like any other
  const kept = (page) => page.id !== "normal" && page.theme && (page.on || !page.own);
  const special = new Set((pages?.pages || []).filter(kept).map((page) => page.theme));
  const upright = mounted() === "portrait";
  const items = themes.map((theme) => {
    const made = models.find((item) => item.id === theme.model);
    // the size the panel shows it at: its version for a bar on end, where it has one
    const [w, h] = (themeEntry(versionFor(theme.id)) || theme).size || [];
    const size = w ? `${w} × ${h}` : null;
    const where = [made ? panelName(made) : null, size].filter(Boolean).join(" · ");
    const shown = theme.id === normal;
    return {
      ...theme,
      shown,
      fits: fitsPanel(theme, model),
      sideways: upright && Boolean(w) && w > h, // on a panel on end, a theme that has no version for it
      collection: typeof theme.collection === "string" ? theme.collection : "",
      special: (special.has(theme.id) || COMPANION.test(theme.id)) && !shown,
      isDefault: first && theme.id === defaultId,
      note: where || sourceText(theme),
      own: !theme.builtin && theme.source !== "plugin",
    };
  });
  const order = (item) =>
    item.shown || item.isDefault ? 0 : (item.collection ? 20 : 0) + (item.fits ? 0 : 10) + (item.sideways ? 5 : 0) + (item.special ? 3 : item.own ? 1 : 2);
  return items.sort((a, b) => order(a) - order(b) || (a.name || a.id).localeCompare(b.name || b.id));
}

/** The collections the themes name, A to Z, with how many themes each has: [{name, count}]. */
export function collectionsOf(items) {
  const found = new Map();
  for (const item of items) if (item.collection) found.set(item.collection, (found.get(item.collection) || 0) + 1);
  return [...found].sort(([a], [b]) => a.localeCompare(b)).map(([name, count]) => ({ name, count }));
}

/** A theme's picture, drawn by the server (it keeps the picture until the theme changes). */
function Picture({ item, width }) {
  useMounted(); // as the panel would show it: its version for a panel on end
  const shown = themeEntry(versionFor(item.id)) || item;
  const [w, h] = shown.size || [480, 320];
  return html`<img src=${endpoints.previewUrl(shown.id, width)} alt="" loading="lazy" decoding="async" width=${w} height=${h} />`;
}

/**
 * A theme as a card with its picture: a click chooses it (`onChoose`: it goes on the panel), and
 * `onEdit` (if given) adds a button that says so and opens it for editing.
 */
export function ThemeCard({ item, big = false, label, onChoose, onEdit }) {
  const name = item.name || item.id;
  return html`<article class=${`theme-card${item.shown ? " current" : ""}${big ? " big" : ""}${onEdit ? " editable" : ""}`} data-theme=${item.id}>
    <button type="button" class="theme-card-main" aria-label=${label} aria-current=${item.shown ? "true" : undefined} onClick=${() => onChoose(item)}>
      <span class="theme-card-picture">
        <${Picture} item=${item} width=${big ? 960 : 640} />
        ${item.shown ? null : html`<span class="theme-card-hint" aria-hidden="true"><${Icon} name="to-panel" size=${15} /> ${t("Show on the panel")}</span>`}
      </span>
      <span class="theme-card-text">
        <span class="theme-card-title">
          <span class="theme-card-name">${name}</span>
          ${item.shown ? html`<span class="theme-card-badge"><${Icon} name="check" size=${12} strokeWidth=${3} /> ${t("On the panel")}</span>` : null}
          ${item.isDefault
            ? html`<span class="theme-card-tag default" title=${t("Shown on the panel until you choose another")}>${t("Default")}</span>`
            : null}
          ${item.own || item.source === "plugin" ? html`<span class="theme-card-tag">${sourceText(item)}</span>` : null}
        </span>
        <span class="theme-card-note">${item.note}</span>
      </span>
    </button>
    ${onEdit
      ? html`<button type="button" class="btn theme-card-edit" aria-label=${t("Edit “{name}”", { name })} onClick=${() => onEdit(item)}>
          <${Icon} name="pencil" size=${14} /> ${t("Edit")}
        </button>`
      : null}
  </article>`;
}

/** A theme in the launcher's list: a small picture, its name, and what it is; a click puts it on
 * the panel (`label` says so). */
export function ThemeRow({ item, label, onChoose }) {
  return html`<button type="button" class=${`quick-theme${item.shown ? " current" : ""}`} data-theme=${item.id}
    aria-label=${label} aria-current=${item.shown ? "true" : undefined} onClick=${() => onChoose(item)}>
    <span class="quick-theme-picture"><${Picture} item=${item} width=${640} /></span>
    <span class="quick-theme-text">
      <span class="quick-theme-name">${item.name || item.id}</span>
      ${item.shown
        ? html`<span class="quick-theme-note on"><${Icon} name="check" size=${12} strokeWidth=${3} /> ${t("On the panel")}</span>`
        : html`<span class="quick-theme-note">${item.note}</span>`}
    </span>
  </button>`;
}

function Grid({ items, big, onChoose, onEdit, label, name }) {
  const upright = useMounted() === "portrait"; // tall pictures: narrow cards
  return html`<div class=${`theme-grid${big ? " big" : ""}${upright ? " upright" : ""}`} role="list" aria-label=${name || t("Themes")}>
    ${items.map(
      (item) => html`<div role="listitem" key=${item.id} class="theme-grid-item">
        <${ThemeCard} item=${item} big=${big} label=${label(item)} onChoose=${onChoose} onEdit=${onEdit} />
      </div>`,
    )}
  </div>`;
}

/**
 * A collection of themes, apart from the rest and closed until it is opened: its pictures are
 * not asked for before. It opens by itself where there is nothing else to show, or when a link
 * leads to it (`asked`), and then comes into view.
 */
function Collection({ name, items, alone, asked, ...more }) {
  const [open, setOpen] = useState(alone || asked);
  const section = useRef(null);
  useEffect(() => {
    if (asked) section.current?.scrollIntoView({ block: "start" });
  }, []);
  const body = `collection-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  const title = t("{collection} · Legacy", { collection: name });
  return html`<section class="theme-collection" ref=${section}>
    <button type="button" class="theme-collection-toggle" aria-expanded=${String(open)} aria-controls=${open ? body : undefined} onClick=${() => setOpen(!open)}>
      <${Icon} name=${open ? "chevron-down" : "chevron-right"} size=${16} />
      <span class="theme-collection-text">
        <span class="theme-collection-title">
          <strong>${title}</strong>
          ${items.some((item) => item.shown)
            ? html`<span class="theme-card-badge"><${Icon} name="check" size=${12} strokeWidth=${3} /> ${t("On the panel")}</span>`
            : null}
        </span>
        <span class="theme-collection-note">${t("The screens of the project Libre Panel grew out of")}</span>
      </span>
      <span class="theme-collection-count">${items.length}</span>
    </button>
    ${open ? html`<div id=${body} class="theme-collection-body"><${Grid} items=${items} name=${title} ...${more} /></div>` : null}
  </section>`;
}

/**
 * The cards: Libre Panel's own themes and yours in a grid, then one closed group for each
 * collection the themes name (A to Z; `asked` opens one). `big` makes the cards of the grid large.
 */
export function Gallery({ items, big = false, asked = "", onChoose, onEdit, label }) {
  const own = items.filter((item) => !item.collection);
  const names = collectionsOf(items).map((set) => set.name);
  return html`<div class="theme-gallery">
    ${own.length ? html`<${Grid} items=${own} big=${big} onChoose=${onChoose} onEdit=${onEdit} label=${label} />` : null}
    ${names.map(
      (name) => html`<${Collection} key=${name} name=${name} items=${items.filter((item) => item.collection === name)}
        alone=${!own.length && names.length === 1} asked=${name === asked} onChoose=${onChoose} onEdit=${onEdit} label=${label} />`,
    )}
  </div>`;
}
