// Help in Design that is there when it is wanted and gone when it is not: the first visit's
// three steps (on top of the blocks until "Got it"), and the tips on the rail, which name what
// Design can do that is not in sight (several at once, the menu of a right click, locking,
// moving freely, the keys).

import { runCommand } from "../../core/commands.js";
import { t } from "../../core/i18n.js";
import { keyLabel } from "../../core/shortcuts.js";
import { setPref, useStore } from "../../core/state.js";
import { html, useRef, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { Popover } from "../../ui/layer.js";

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

/** The tips: what is not in sight, each in a line, and the way to every key. */
function TipList({ close }) {
  const tips = [
    ["select-all", t("Several at once: Shift+click them, or draw a frame around them on the empty panel. The bar above them then aligns them.")],
    ["more", t("Right-click something for more: copy, lock, hide, bring it forward.")],
    ["lock", t("Locked things stay put on the panel; you pick them in Layers.")],
    ["magnet", t("Alt while dragging: free, without snapping or guides.")],
    ["arrow-up", t("The arrow keys move what is selected; with Shift ten times as far.")],
    ["search", t("{keys} finds every block, sensor and command by its name.", { keys: keys("mod+k") })],
  ];
  return html`<div class="pop-section tips">
    <div class="pop-title">${t("Tips")}</div>
    <ul class="tip-list">
      ${tips.map(
        ([icon, text]) => html`<li key=${icon}>
          <span class="tip-icon"><${Icon} name=${icon} size=${15} /></span>
          <span>${text}</span>
        </li>`,
      )}
    </ul>
    <div class="tip-actions">
      <button type="button" class="btn" onClick=${() => (close(), runCommand("help.shortcuts"))}>
        <${Icon} name="keyboard" size=${15} /> ${t("All keyboard shortcuts")}
      </button>
      <button type="button" class="link-btn" onClick=${() => {
        close();
        setPref("stepsDone", false);
        setPref("panel", "library");
      }}>${t("The three steps again")}</button>
    </div>
  </div>`;
}

/** The rail's "Tips". */
export function Tips() {
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  return html`<button ref=${anchor} type="button" class="rail-item rail-tips" title=${t("Tips and keys")}
      aria-haspopup="dialog" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      <${Icon} name="info" size=${20} />
      <span>${t("Tips")}</span>
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} placement="right-end" label=${t("Tips and keys")} width=${340}>
      <${TipList} close=${() => setOpen(false)} />
    <//>`;
}
