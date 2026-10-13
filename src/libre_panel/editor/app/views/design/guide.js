// Help in Design that is there when it is wanted and gone when it is not: the first visit's
// three steps, on top of the blocks until "Got it".

import { t } from "../../core/i18n.js";
import { keyLabel } from "../../core/shortcuts.js";
import { setPref, useStore } from "../../core/state.js";
import { html } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";

/** Keys as a sentence writes them: "Ctrl+Z" ("Strg+Z"). */
const keys = (combo) => keyLabel(combo).split(" ").join("+");

/** Whether the first steps are on show: until "Got it", remembered in this browser (or window). */
export const useFirstSteps = () => useStore((s) => !s.prefs.stepsDone);

/** The first visit's three steps: add a block, change what is on the panel, keep it. */
export function FirstSteps() {
  const steps = [
    ["plus", t("Click a block below to add it, or drag it to where it should go.")],
    ["pencil", t("Click something on the panel to change it: its settings are on the right.")],
    ["to-panel", t("To the panel saves your theme and shows it on the panel. {keys} takes back any step.", { keys: keys("mod+z") })],
  ];
  return html`<section class="design-steps" aria-labelledby="design-steps-title">
    <h2 id="design-steps-title" class="design-steps-title">${t("Design in three steps")}</h2>
    <ol class="design-steps-list">
      ${steps.map(
        ([icon, text]) => html`<li key=${icon}>
          <span class="design-steps-icon"><${Icon} name=${icon} size=${14} strokeWidth=${2.4} /></span>
          <span>${text}</span>
        </li>`,
      )}
    </ol>
    <button type="button" class="btn btn-primary design-steps-done" onClick=${() => setPref("stepsDone", true)}>${t("Got it")}</button>
  </section>`;
}
