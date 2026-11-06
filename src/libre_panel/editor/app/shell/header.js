// The header every view shares: the brand, the view's own parts (lead, center, actions), the
// tabs (each says what it is for), the search field, always right after the tabs, and the way to
// the settings, by name, which leads back to where the person was when pressed in Settings.

import { applyToPanel, confirmDiscard, openChosenPanelTheme, panelState } from "../core/actions.js";
import { chooseTarget, useTarget } from "../core/target.js";
import { redo, undo } from "../core/doc.js";
import { t } from "../core/i18n.js";
import { firstStart, store, useStore } from "../core/state.js";
import { bindKey, keyLabel } from "../core/shortcuts.js";
import { html, useLayoutEffect, useRef } from "../lib/preact.js";
import { IconButton, Segmented } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { dialogOpen } from "../ui/dialog.js";
import { openPalette, paletteOpen } from "../ui/palette.js";
import { allViews, leaveSettings, navigate, settingsReturn, showsTheme, viewHint } from "./views.js";

const titleOf = (view) => (typeof view.title === "function" ? view.title() : view.title);

// What gives way where the header has too little room for all it shows, in this order, each after
// the one before (app.css: data-fit): the search field's hint of its keys, the tabs closer
// together, the search field to its glass, the unsaved mark to its dot, the panel chip to its
// name, the brand to its icon, "Panel 1 | Panel 2" to the numbers, the way to the settings to its
// gear. The window's width alone does not say when (a second panel adds its switch, German its
// longer words, the theme its name), so each step is measured: the first with which nothing
// reaches beyond the header, the theme's name is whole and the search field shows some of its
// words (or none: its glass) is kept. The name gives way only after all of them.
const GIVE_WAY = ["keys", "tabs", "search", "unsaved", "chip", "brand", "panels", "settings"];
const SEARCH_WORDS = 60; // px of the search field's words, at least ("Search or…"): not a letter

/** How much of its text an element's box hides, in pixels (0 where it shows all of it). */
function hidden(element) {
  if (!element) return 0;
  const range = document.createRange();
  range.selectNodeContents(element);
  return range.getBoundingClientRect().width - element.getBoundingClientRect().width;
}

function fits(bar) {
  const end = bar.getBoundingClientRect().right - parseFloat(getComputedStyle(bar).paddingRight);
  const last = bar.lastElementChild;
  const words = bar.querySelector(".search-trigger.wide .search-trigger-text");
  const wordsWidth = words?.getClientRects().length ? words.getBoundingClientRect().width : Infinity; // (none shown: its glass)
  return (
    (!last || last.getBoundingClientRect().right <= end + 0.5) &&
    hidden(bar.querySelector(".theme-name span")) <= 0.1 &&
    wordsWidth >= SEARCH_WORDS
  );
}

/** The header's parts give way as far as they must: measured again whenever its width or what it
 * shows changes (or a font arrives), before that is painted. From the steps it took last, so a
 * window drawn wider or narrower is measured once or twice a frame, not once for every step. */
function useGiveWay(ref) {
  useLayoutEffect(() => {
    const bar = ref.current;
    if (!bar) return undefined;
    let steps = 0;
    const take = (n) => {
      bar.dataset.fit = GIVE_WAY.slice(0, n).join(" ");
    };
    const fit = () => {
      // fewer steps while all of it fits with them, then more while it does not
      while (steps > 0) {
        take(steps - 1);
        if (!fits(bar)) break;
        steps -= 1;
      }
      take(steps);
      while (steps < GIVE_WAY.length && !fits(bar)) take(++steps);
    };
    fit();
    const resized = new ResizeObserver(fit);
    resized.observe(bar);
    const changed = new MutationObserver(fit); // (its own data-fit is an attribute: not watched)
    changed.observe(bar, { childList: true, subtree: true, characterData: true });
    document.fonts?.addEventListener("loadingdone", fit);
    return () => {
      resized.disconnect();
      changed.disconnect();
      document.fonts?.removeEventListener("loadingdone", fit);
    };
  }, []);
}

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

/** "Panel 1 | Panel 2", while more panels are set up: the panel Start and Status show and
 * Design and Look edit (target.js). The theme the chosen panel shows opens: in Design and Look
 * after asking about unsaved changes, elsewhere where there are none. */
function PanelSwitch() {
  const count = useStore((s) => 1 + (s.settings?.config?.panels?.length || 0));
  const number = useTarget();
  if (count < 2) return null;
  const choose = async (n) => {
    if (n === number) return;
    const editing = showsTheme(); // (Design, Look)
    if (editing && !(await confirmDiscard())) return;
    await chooseTarget(n, () => openChosenPanelTheme({ force: editing }));
  };
  return html`<${Segmented}
    class="panel-switch"
    label=${t("Which panel Start and Status show and Design edits")}
    value=${number}
    options=${Array.from({ length: count }, (_, i) => ({ value: i + 1, label: t("Panel {number}", { number: i + 1 }), short: String(i + 1) }))}
    onChange=${choose}
  />`;
}

export function Header({ view }) {
  const route = useStore((s) => s.route);
  // the question "How should it look?" is asked on its own: the other views have nothing to show yet
  const asking = useStore((s) => firstStart(s)) && route.view === "home" && !route.rest.startsWith("themes");
  const parts = view?.header || {};
  const tabs = allViews().filter((item) => item.tab);
  const bar = useRef(null);
  useGiveWay(bar);
  return html`<header ref=${bar} class=${`topbar${parts.lead ? " with-lead" : ""}`}>
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
    <${PanelSwitch} />
    ${parts.center ? html`<${parts.center} />` : html`<${SearchTrigger} wide />`}
    <div class="topbar-flex"></div>
    ${parts.actions ? html`<${parts.actions} />` : null}
    <${SettingsButton} active=${route.view === "settings"} />
  </header>`;
}
