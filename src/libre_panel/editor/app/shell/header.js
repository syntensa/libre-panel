// The header every view shares: the brand, the view's own parts (lead,
// center, actions), the tabs, the command trigger and the settings gear.

import { applyToPanel } from "../core/actions.js";
import { redo, undo } from "../core/doc.js";
import { t } from "../core/i18n.js";
import { useStore } from "../core/state.js";
import { keyLabel } from "../core/shortcuts.js";
import { html } from "../lib/preact.js";
import { IconButton } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { openPalette } from "../ui/palette.js";
import { allViews, navigate } from "./views.js";

/** The search and command field (Ctrl+K); `wide` as in the design view, else a small one. */
export function SearchTrigger({ wide = false }) {
  return html`<button
    type="button"
    class=${`search-trigger${wide ? " wide" : ""}`}
    aria-label=${t("Search or command …")}
    aria-keyshortcuts="Control+K"
    onClick=${() => openPalette()}
  >
    <${Icon} name="search" size=${wide ? 15 : 14} />
    ${wide ? html`<span class="search-trigger-text">${t("Search or command …")}</span>` : null}
    <kbd class="kbd">${keyLabel("mod+k")}</kbd>
  </button>`;
}

export function UndoRedo() {
  const { canUndo, canRedo } = useStore((s) => ({ canUndo: s.canUndo, canRedo: s.canRedo }));
  return html`
    <${IconButton} icon="undo" class="square" label=${`${t("Undo")} (${keyLabel("mod+z")})`} disabled=${!canUndo} onClick=${undo} />
    <${IconButton} icon="redo" class="square" label=${`${t("Redo")} (${keyLabel("mod+y")})`} disabled=${!canRedo} onClick=${redo} />
  `;
}

/** "Aufs Panel": save the theme and show it on the panel. */
export function ApplyButton() {
  const hasDoc = useStore((s) => Boolean(s.doc));
  return html`<button type="button" class="btn btn-primary btn-apply" disabled=${!hasDoc} title=${t("Save and show this theme on the panel")}
    onClick=${applyToPanel}>
    <${Icon} name="to-panel" size=${16} strokeWidth=${2.2} />
    ${t("To the panel")}
  </button>`;
}

export function Header({ view }) {
  const route = useStore((s) => s.route);
  const parts = view?.header || {};
  const tabs = allViews().filter((item) => item.tab);
  return html`<header class="topbar">
    <a class="brand" href="#/home" aria-label=${t("Libre Panel: start")}>
      <${Icon} name="logo" size=${22} />
      <span>Libre Panel</span>
    </a>
    ${parts.lead ? html`<div class="topbar-sep"></div><${parts.lead} />` : null}
    <nav class="tabs" aria-label=${t("Area")}>
      ${tabs.map(
        (item) => html`<a
          key=${item.id}
          href=${`#/${item.id}`}
          class="tab"
          aria-current=${route.view === item.id ? "page" : undefined}
        >${typeof item.title === "function" ? item.title() : item.title}</a>`,
      )}
    </nav>
    ${parts.center ? html`<${parts.center} />` : null}
    <div class="topbar-flex"></div>
    ${parts.actions ? html`<${parts.actions} />` : null}
    ${parts.center ? null : html`<${SearchTrigger} />`}
    <${IconButton}
      icon="gear"
      size=${18}
      class="square gear"
      label=${t("Settings")}
      pressed=${route.view === "settings"}
      onClick=${() => navigate("settings")}
    />
  </header>`;
}
