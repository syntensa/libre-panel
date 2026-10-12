// The theme's name in the header: unsaved marker, and the theme menu (open
// another theme, new, save, save as, import, export).

import { blankTheme, confirmDiscard, exportTheme, importTheme, openTheme, save, saveAs, startTheme } from "../core/actions.js";
import { endpoints } from "../core/api.js";
import { clone, setSelection } from "../core/doc.js";
import { t } from "../core/i18n.js";
import { versionFor } from "../core/orientation.js";
import { store, useStore } from "../core/state.js";
import { html, useEffect, useMemo, useRef, useState } from "../lib/preact.js";
import { openDialog } from "../ui/dialog.js";
import { Icon } from "../ui/icons.js";
import { Popover, arrowNavigation } from "../ui/layer.js";
import { Kbd } from "../ui/controls.js";
import { currentModel, panelName } from "./panel-popover.js";
import { navigate } from "./views.js";

const sourceText = (theme) =>
  theme.source === "plugin" ? t("from a plugin") : theme.builtin ? t("built in") : t("yours");

function ThemeList({ onClose }) {
  const { themes, current, active } = useStore((s) => ({ themes: s.themes, current: s.doc?.id, active: s.pages?.pages?.[0]?.theme }));
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = themes.filter((theme) => !q || theme.id.toLowerCase().includes(q) || (theme.name || "").toLowerCase().includes(q));
    return list.sort((a, b) => Number(a.builtin) - Number(b.builtin) || (a.name || a.id).localeCompare(b.name || b.id));
  }, [themes, query]);
  return html`
    <label class="search-field">
      <${Icon} name="search" size=${15} />
      <input type="text" value=${query} placeholder=${t("Find a theme")} aria-label=${t("Find a theme")} autofocus
        onInput=${(event) => setQuery(event.currentTarget.value)} />
    </label>
    <div class="theme-list" role="menu" aria-label=${t("Themes")} onKeyDown=${(event) => arrowNavigation(event)}>
      ${shown.map(
        (theme) => html`<button
          type="button"
          role="menuitem"
          key=${theme.id}
          class=${`theme-row${versionFor(theme.id) === current ? " current" : ""}`}
          onClick=${() => {
            onClose();
            if (versionFor(theme.id) !== current) openTheme(theme.id);
          }}
        >
          <img src=${endpoints.previewUrl(versionFor(theme.id), 240)} alt="" loading="lazy" />
          <span class="theme-row-text">
            <span class="theme-row-name">${theme.name || theme.id}</span>
            <span class="theme-row-note">${theme.id === active ? t("shown on the panel") : sourceText(theme)} · <span class="mono">${theme.id}</span></span>
          </span>
          ${theme.id === current ? html`<${Icon} name="check" size=${15} />` : null}
        </button>`,
      )}
      ${shown.length ? null : html`<p class="muted pad">${t("No theme of that name")}</p>`}
    </div>
  `;
}

/** The theme's name, whether it is saved, and its menu. */
export function ThemeTitle() {
  const { name, dirty, id, builtin } = useStore((s) => ({
    name: s.doc?.theme?.name,
    id: s.doc?.id,
    builtin: s.doc?.builtin,
    dirty: s.dirty,
    rev: s.rev,
  }));
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  if (name === undefined) return null;
  const close = () => setOpen(false);
  const actions = [
    { icon: "plus", label: t("New theme…"), run: () => newThemeDialog() },
    { icon: "save", label: t("Save"), keys: "mod+s", run: save },
    { icon: "file", label: t("Save as…"), keys: "mod+shift+s", run: saveAs },
    { icon: "upload", label: t("Import…"), run: () => importTheme() },
    { icon: "download", label: t("Export"), run: exportTheme },
    {
      icon: "sliders",
      label: t("Theme settings"),
      run: () => {
        setSelection([]);
        navigate("design");
      },
    },
  ];
  return html`
    <div class="theme-title">
      <button
        ref=${anchor}
        type="button"
        class="theme-name"
        aria-haspopup="dialog"
        aria-expanded=${String(open)}
        title=${id ? (builtin ? t("{id} (built-in)", { id }) : id) : t("(unsaved)")}
        onClick=${() => setOpen(!open)}
      >
        <span>${name || t("Untitled")}</span>
        <${Icon} name="chevron-down" size=${14} />
      </button>
      ${dirty
        ? html`<button type="button" class="unsaved" title=${t("Save (Ctrl+S)")} aria-label=${t("Not saved")} onClick=${save}>
            <span class="dot"></span><span class="unsaved-text">${t("Not saved")}</span>
          </button>`
        : null}
    </div>
    <${Popover} anchor=${anchor} open=${open} onClose=${close} label=${t("Themes")} width=${340} class="theme-menu">
      <${ThemeList} onClose=${close} />
      <div class="menu" role="menu" onKeyDown=${(event) => arrowNavigation(event)}>
        <div class="menu-sep"></div>
        ${actions.map(
          (item) => html`<button type="button" role="menuitem" class="menu-item" key=${item.label}
            onClick=${() => (close(), item.run())}>
            <span class="menu-icon"><${Icon} name=${item.icon} /></span>
            <span class="menu-label">${item.label}</span>
            <${Kbd} keys=${item.keys} />
          </button>`,
        )}
      </div>
    <//>
  `;
}

// -- a new theme: a starting layout and a look ---------------------------------

function NewTheme({ close }) {
  const specs = store.get().specs;
  const model = currentModel() || specs.models[0];
  const display = store.get().doc?.theme?.display || {};
  const orientation = display.orientation || "landscape";
  const width = display.width || model?.[orientation]?.[0] || 480;
  const height = display.height || model?.[orientation]?.[1] || 320;
  const looks = specs.modules.looks;
  const [look, setLook] = useState("arctic" in looks ? "arctic" : Object.keys(looks)[0]);
  const [templates, setTemplates] = useState(null);
  const [choice, setChoice] = useState(null);
  const [pictures, setPictures] = useState({});

  const build = (template, lookKey) => {
    const parts = looks[lookKey];
    return {
      ...blankTheme(model, orientation, [width, height]),
      display: { model: display.model || model?.id || "custom", orientation, width, height },
      palette: clone(parts.palette),
      style: clone(parts.style),
      grid: { columns: 0, rows: 0, gap: 0, margin: 0 },
      widgets: (template?.modules || []).map((m, i) => ({ type: "module", id: `${m.module}-${i + 1}`, ...m })),
    };
  };

  useEffect(() => {
    endpoints
      .templates(width, height, display.model || model?.id || "custom")
      .then((data) => {
        const list = [...data.templates, { id: "blank", name: t("Empty grid"), modules: [] }];
        setTemplates(list);
        setChoice(list[0].id);
      })
      .catch((error) => setTemplates({ error: error.message }));
  }, []);
  useEffect(() => {
    if (!Array.isArray(templates)) return undefined;
    let alive = true;
    for (const template of templates) {
      endpoints
        .render(build(template, look))
        .then((data) => alive && setPictures((all) => ({ ...all, [`${look}:${template.id}`]: data.png })))
        .catch(() => {});
    }
    return () => {
      alive = false;
    };
  }, [templates, look]);

  const create = () => {
    const template = templates.find((item) => item.id === choice);
    close(true);
    startTheme(build(template?.id === "blank" ? null : template, look), { ask: false });
  };
  return html`
    <h2 class="dialog-title">${t("New theme")}</h2>
    <p class="dialog-text">${t("A starting layout and a look for {panel}. Everything can be changed later.", { panel: panelName(model) })}</p>
    <h3 class="section-title">${t("Layout")}</h3>
    ${templates?.error
      ? html`<p class="field-hint error">${templates.error}</p>`
      : html`<div class="template-grid" role="radiogroup" aria-label=${t("Layout")}>
          ${(templates || []).map(
            (template) => html`<button type="button" role="radio" key=${template.id} aria-checked=${String(choice === template.id)}
              class="template-card" onClick=${() => setChoice(template.id)} onDblClick=${create}>
              <span class="template-picture">
                ${pictures[`${look}:${template.id}`] ? html`<img alt="" src=${`data:image/png;base64,${pictures[`${look}:${template.id}`]}`} />` : null}
              </span>
              <span>${template.name}</span>
            </button>`,
          )}
        </div>`}
    <h3 class="section-title">${t("Look")}</h3>
    <div class="look-grid" role="radiogroup" aria-label=${t("Look")}>
      ${Object.entries(looks).map(
        ([key, item]) => html`<button type="button" role="radio" key=${key} aria-checked=${String(look === key)} class="look-tile"
          onClick=${() => setLook(key)}>
          <span class="look-dots">
            ${["accent", "mem", "net", "surface"].map((role) => html`<i key=${role} style=${{ background: item.palette[role] || item.palette.accent }}></i>`)}
          </span>
          <span>${item.name}</span>
        </button>`,
      )}
    </div>
    <div class="dialog-actions">
      <button type="button" class="btn" onClick=${() => (close(true), startTheme(blankTheme(model, orientation, [width, height]), { ask: false }))}>
        ${t("Free layout without modules")}
      </button>
      <span class="grow"></span>
      <button type="button" class="btn" onClick=${() => close(false)}>${t("Cancel")}</button>
      <button type="button" class="btn btn-primary" disabled=${!choice} onClick=${create}>${t("Create")}</button>
    </div>
  `;
}

/** Ask about unsaved changes, then let the user pick a layout and a look. */
export async function newThemeDialog() {
  if (!(await confirmDiscard())) return;
  const made = await openDialog((close) => html`<${NewTheme} close=${close} />`, { label: t("New theme"), wide: true });
  if (made) navigate("design");
}
