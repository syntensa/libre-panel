// The header every view shares: the brand, the view's own parts (lead,
// center, actions), the tabs, the command field and the way to the settings, by name.

import { applyToPanel, panelState } from "../core/actions.js";
import { redo, undo } from "../core/doc.js";
import { t } from "../core/i18n.js";
import { firstStart, useStore } from "../core/state.js";
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

/** "Aufs Panel": save the theme and show it on the panel. Once the panel shows the open theme
 * as it is here (saved, and the theme of the page it is open for), the button says so instead
 * of asking again: what is edited and what is on the panel are told apart at a glance. */
export function ApplyButton() {
  const state = useStore((s) => (s.doc ? panelState(s) : null));
  const shown = state === "shown";
  const title = {
    shown: t("The panel shows this theme as you see it here"),
    changed: t("Save the changes and show them on the panel"),
    other: t("Save and show this theme on the panel"),
  }[state];
  return html`<button type="button" class=${`btn btn-apply${shown ? " shown" : " btn-primary"}`} disabled=${!state} title=${title}
    onClick=${applyToPanel}>
    <${Icon} name=${shown ? "check" : "to-panel"} size=${16} strokeWidth=${2.2} />
    ${shown ? t("On the panel") : t("To the panel")}
  </button>`;
}

export function Header({ view }) {
  const route = useStore((s) => s.route);
  // the question "How should it look?" is asked on its own: the other views have nothing to show yet
  const asking = useStore((s) => firstStart(s)) && route.view === "home" && !route.rest.startsWith("themes");
  const parts = view?.header || {};
  const tabs = allViews().filter((item) => item.tab);
  return html`<header class=${`topbar${parts.lead ? " with-lead" : ""}`}>
    <a class="brand" href="#/home" aria-label=${t("Libre Panel: start")}>
      <${Icon} name="logo" size=${22} />
      <span>Libre Panel</span>
    </a>
    ${parts.lead ? html`<div class="topbar-sep"></div><${parts.lead} />` : null}
    ${asking
      ? null
      : html`<nav class="tabs" aria-label=${t("Area")}>
          ${tabs.map(
            (item) => html`<a
              key=${item.id}
              href=${`#/${item.id}`}
              class="tab"
              aria-current=${route.view === item.id ? "page" : undefined}
            >${typeof item.title === "function" ? item.title() : item.title}</a>`,
          )}
        </nav>`}
    ${parts.center ? html`<${parts.center} />` : null}
    <div class="topbar-flex"></div>
    ${parts.actions ? html`<${parts.actions} />` : null}
    ${parts.center ? null : html`<${SearchTrigger} wide />`}
    <button type="button" class=${`btn settings-btn${route.view === "settings" ? " active" : ""}`}
      aria-pressed=${String(route.view === "settings")} title=${t("Settings")} onClick=${() => navigate("settings")}>
      <${Icon} name="gear" size=${17} />
      <span class="settings-btn-text">${t("Settings")}</span>
    </button>
  </header>`;
}
