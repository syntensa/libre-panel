// The header every view shares: the brand, the view's own parts (lead, center, actions), the
// tabs (each says what it is for), the search field, always right after the tabs, and the way to
// the settings, by name, which leads back to where the person was when pressed in Settings.

import { applyToPanel } from "../core/actions.js";
import { redo, undo } from "../core/doc.js";
import { t } from "../core/i18n.js";
import { firstStart, store, useStore } from "../core/state.js";
import { bindKey, keyLabel } from "../core/shortcuts.js";
import { html } from "../lib/preact.js";
import { IconButton } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { dialogOpen } from "../ui/dialog.js";
import { openPalette, paletteOpen } from "../ui/palette.js";
import { allViews, leaveSettings, navigate, settingsReturn, viewHint } from "./views.js";

const titleOf = (view) => (typeof view.title === "function" ? view.title() : view.title);

// Escape leaves Settings for where the person was (a popover or a dialog in it takes Escape first)
bindKey("escape", () => leaveSettings(), {
  when: () => store.get().route.view === "settings" && !dialogOpen() && !paletteOpen(),
});

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

/** The way to the settings, by name; pressed again in Settings, the way back. */
function SettingsButton({ active }) {
  const back = active ? settingsReturn() : null;
  const title = back && back.id !== "settings" ? t("Back to {view}", { view: titleOf(back) }) : t("Settings");
  return html`<button type="button" class=${`btn settings-btn${active ? " active" : ""}`}
    aria-pressed=${String(active)} aria-label=${title} title=${active ? `${title} (Esc)` : title}
    onClick=${() => (active ? leaveSettings() : navigate("settings"))}>
    <${Icon} name="gear" size=${17} />
    <span class="settings-btn-text">${t("Settings")}</span>
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
              title=${viewHint(item)}
              aria-current=${route.view === item.id ? "page" : undefined}
            >${titleOf(item)}</a>`,
          )}
        </nav>`}
    ${parts.center ? html`<${parts.center} />` : html`<${SearchTrigger} wide />`}
    <div class="topbar-flex"></div>
    ${parts.actions ? html`<${parts.actions} />` : null}
    <${SettingsButton} active=${route.view === "settings"} />
  </header>`;
}
