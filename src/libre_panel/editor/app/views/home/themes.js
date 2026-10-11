// The themes Start shows: the theme list (/api/themes) in the order that helps most, and the
// cards and rows they come in. Nothing here names a theme: what is installed is what shows.

import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { html } from "../../lib/preact.js";
import { panelName } from "../../shell/panel-popover.js";
import { Icon } from "../../ui/icons.js";

const same = (a, b) => Boolean(a && b) && a[0] === b[0] && a[1] === b[1];

/** Whether a theme is made for a panel: its model, or its size either way round. */
export const fitsPanel = (theme, model) => !model || theme.model === model.id || same(theme.size, model.landscape) || same(theme.size, model.portrait);

const sourceText = (theme) => (theme.source === "plugin" ? t("from a plugin") : theme.builtin ? t("built in") : t("yours"));

/**
 * The themes with what Start says about each: whether it is on the panel (`shown`), made for
 * the panel (`fits`) or kept for another page (`special`: the round report), and a short
 * `note`. Order: the one on the panel, then those that fit (yours, built in, kept for a
 * page), then the rest.
 */
export function startThemes({ themes, models, pages, model }) {
  // on a first start the theme config.toml names is the default, not a choice
  const normal = pages?.chosen === false ? null : (pages?.pages?.find((page) => page.id === "normal")?.theme ?? null);
  const special = new Set((pages?.pages || []).filter((page) => page.id !== "normal" && page.theme).map((page) => page.theme));
  const items = themes.map((theme) => {
    const made = models.find((item) => item.id === theme.model);
    const size = theme.size ? `${theme.size[0]} × ${theme.size[1]}` : null;
    const where = [made ? panelName(made) : null, size].filter(Boolean).join(" · ");
    const shown = theme.id === normal;
    return {
      ...theme,
      shown,
      fits: fitsPanel(theme, model),
      special: special.has(theme.id) && !shown,
      note: where || sourceText(theme),
      own: !theme.builtin && theme.source !== "plugin",
    };
  });
  const order = (item) => (item.shown ? 0 : (item.fits ? 0 : 10) + (item.special ? 3 : item.own ? 1 : 2));
  return items.sort((a, b) => order(a) - order(b) || (a.name || a.id).localeCompare(b.name || b.id));
}

/** A theme's picture, drawn by the server (it keeps the picture until the theme changes). */
function Picture({ item, width }) {
  const [w, h] = item.size || [480, 320];
  return html`<img src=${endpoints.previewUrl(item.id, width)} alt="" loading="lazy" decoding="async" width=${w} height=${h} />`;
}

/**
 * A theme as a card with its picture: a click chooses it (`onChoose`), and `onEdit` (if given)
 * opens it for editing from the corner.
 */
export function ThemeCard({ item, big = false, label, onChoose, onEdit }) {
  return html`<article class=${`theme-card${item.shown ? " current" : ""}${big ? " big" : ""}`} data-theme=${item.id}>
    <button type="button" class="theme-card-main" aria-label=${label} aria-current=${item.shown ? "true" : undefined} onClick=${() => onChoose(item)}>
      <span class="theme-card-picture"><${Picture} item=${item} width=${big ? 960 : 640} /></span>
      <span class="theme-card-text">
        <span class="theme-card-title">
          <span class="theme-card-name">${item.name || item.id}</span>
          ${item.shown ? html`<span class="theme-card-badge"><${Icon} name="check" size=${12} strokeWidth=${3} /> ${t("On the panel")}</span>` : null}
          ${item.own || item.source === "plugin" ? html`<span class="theme-card-tag">${sourceText(item)}</span>` : null}
        </span>
        <span class="theme-card-note">${item.note}</span>
      </span>
    </button>
    ${onEdit
      ? html`<button type="button" class="icon-btn theme-card-edit" aria-label=${t("Edit “{name}”", { name: item.name || item.id })}
          title=${t("Edit “{name}”", { name: item.name || item.id })} onClick=${() => onEdit(item)}>
          <${Icon} name="pencil" size=${15} />
        </button>`
      : null}
  </article>`;
}

/** A theme in the launcher's list: a small picture, its name, and what it is. */
export function ThemeRow({ item, onChoose }) {
  return html`<button type="button" class=${`quick-theme${item.shown ? " current" : ""}`} data-theme=${item.id}
    aria-current=${item.shown ? "true" : undefined} onClick=${() => onChoose(item)}>
    <span class="quick-theme-picture"><${Picture} item=${item} width=${640} /></span>
    <span class="quick-theme-text">
      <span class="quick-theme-name">${item.name || item.id}</span>
      <span class="quick-theme-note">${item.shown ? t("shows now") : item.note}</span>
    </span>
  </button>`;
}

/** The cards in a grid. */
export function Gallery({ items, big = false, onChoose, onEdit, label }) {
  return html`<div class=${`theme-grid${big ? " big" : ""}`} role="list" aria-label=${t("Themes")}>
    ${items.map(
      (item) => html`<div role="listitem" key=${item.id} class="theme-grid-item">
        <${ThemeCard} item=${item} big=${big} label=${label(item)} onChoose=${onChoose} onEdit=${onEdit} />
      </div>`,
    )}
  </div>`;
}
