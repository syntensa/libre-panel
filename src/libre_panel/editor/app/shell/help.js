// Help (? or F1, and "Help" in Ctrl+K): how to find anything, where to read and to ask, how to
// report a problem or a panel, and the keyboard shortcuts of every view.

import { t } from "../core/i18n.js";
import { keyLabel } from "../core/shortcuts.js";
import { loadAbout } from "../core/settings.js";
import { store, useStore } from "../core/state.js";
import { html, useEffect } from "../lib/preact.js";
import { dialogOpen, openDialog } from "../ui/dialog.js";
import { Icon } from "../ui/icons.js";

/** The shortcuts of the whole app and of each view (a view lists its own with `listShortcuts`). */
export const SHORTCUTS = [];

/** A view's keys for the help: `listShortcuts(() => t("Design"), [["mod+a", () => t("Select all")]])`;
 * `first` puts the group before the others (the keys that work everywhere). */
export function listShortcuts(group, items, { first = false } = {}) {
  if (first) SHORTCUTS.unshift({ group, items });
  else SHORTCUTS.push({ group, items });
}

/** The places to read and to ask, from the project's links the server knows. */
const LINKS = (links) => [
  {
    id: "faq",
    href: `${links.project}#faq`,
    icon: "info",
    title: t("Questions and answers"),
    text: t("The guide on the project page, with the questions people ask most."),
  },
  {
    id: "issue",
    href: links.issues,
    icon: "warning",
    title: t("Report a problem or an idea"),
    text: t("Something does not work as it should, or something is missing: tell the project."),
  },
  {
    id: "panel",
    href: links.hardware_report,
    icon: "landscape",
    title: t("Report a panel"),
    text: t("A panel that is not on the list of supported ones, or one that behaves oddly."),
  },
];

function Help({ close, keys }) {
  const about = useStore((s) => s.aboutInfo);
  useEffect(() => {
    if (!store.get().aboutInfo) loadAbout();
    // asked for the keys ("All keyboard shortcuts"): they in view, not the top
    if (keys) document.getElementById("help-keys")?.scrollIntoView({ block: "start" });
  }, []);
  return html`<h2 class="dialog-title">${t("Help")}</h2>
    <p class="dialog-text">
      ${t("The search field at the top ({keys}) finds everything in Libre Panel by the words you would use: “brightness”, “game mode”, “upright” or “start screen”.", { keys: keyLabel("mod+k") })}
    </p>
    ${about?.links
      ? html`<div class="help-links">
          ${LINKS(about.links).map(
            (link) => html`<a key=${link.id} class="help-link" data-help=${link.id} href=${link.href} target="_blank" rel="noopener noreferrer">
              <${Icon} name=${link.icon} size=${18} />
              <span class="help-link-text"><strong>${link.title}</strong><span>${link.text}</span></span>
              <${Icon} name="external" size=${14} />
            </a>`,
          )}
        </div>`
      : null}
    <h3 class="section-title" id="help-keys">${t("Keyboard shortcuts")}</h3>
    <div class="shortcut-groups">
      ${SHORTCUTS.map(
        (group) => html`<section key=${group.group()}>
          <h4 class="shortcut-group-title">${group.group()}</h4>
          <dl class="shortcut-list">
            ${group.items.map(
              ([keys, label]) => html`<div key=${keys}><dt><kbd class="kbd">${keyLabel(keys)}</kbd></dt><dd>${label()}</dd></div>`,
            )}
          </dl>
        </section>`,
      )}
    </div>
    <div class="dialog-actions"><button type="button" class="btn btn-primary" autofocus onClick=${() => close()}>${t("Close")}</button></div>`;
}

/** Show the help (once: a second ? while it is open does nothing); `keys`: at its keys. */
export function openHelp({ keys = false } = {}) {
  if (dialogOpen()) return null;
  return openDialog((close) => html`<${Help} close=${close} keys=${keys} />`, { label: t("Help"), wide: true });
}
