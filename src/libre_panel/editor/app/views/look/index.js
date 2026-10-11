// Look (#/look): the theme's look and finish. The colours, how rings, bars and lettering are
// drawn (finish and glow), the panel light, the grid and the dust behind the panel, and how
// notices look; the preview follows every change. Built on the shell: see ../../README.md.

import { registerCommands } from "../../core/commands.js";
import { edit, theme as currentTheme, widgets } from "../../core/doc.js";
import { t } from "../../core/i18n.js";
import { store } from "../../core/state.js";
import { html } from "../../lib/preact.js";
import { ApplyButton, UndoRedo } from "../../shell/header.js";
import { PanelChip } from "../../shell/panel-popover.js";
import { ThemeTitle } from "../../shell/theme-menu.js";
import { registerView } from "../../shell/views.js";
import { FINISH_NAMES, finishesFor } from "../design/fields.js";
import { LookPanel, toggleBlock } from "./sections.js";
import { Stage } from "./stage.js";

function LookView() {
  return html`<div class="look">
    <${Stage} />
    <${LookPanel} />
  </div>`;
}

const LookActions = () => html`<${PanelChip} /><${UndoRedo} /><${ApplyButton} />`;

registerView({
  id: "look",
  title: () => t("Look"),
  order: 30,
  icon: "palette",
  component: LookView,
  header: { lead: ThemeTitle, actions: LookActions },
});

// -- commands (Ctrl+K) ------------------------------------------------------------------------------------

const hasDoc = () => Boolean(store.get().doc);
const group = () => t("Look");
const on = (block) => Boolean(currentTheme()?.look?.[block]);

/** One finish for everything it can draw (one undo step). */
function finishAll(finish) {
  edit("finish:all", () => {
    for (const widget of widgets()) if ("finish" in widget && finishesFor(widget).includes(finish)) widget.finish = finish;
  });
}

registerCommands([
  {
    id: "look.light",
    title: () => (on("light") ? t("Panel light: off") : t("Panel light: on")),
    group,
    icon: "sun",
    when: hasDoc,
    rank: 62,
    run: () => toggleBlock("light", !on("light")),
  },
  {
    id: "look.grid",
    title: () => (on("grid") ? t("Grid background: off") : t("Grid background: on")),
    group,
    icon: "grid",
    when: hasDoc,
    rank: 62,
    run: () => toggleBlock("grid", !on("grid")),
  },
  {
    id: "look.dust",
    title: () => (on("dust") ? t("Depth dust: off") : t("Depth dust: on")),
    group,
    icon: "sensor",
    when: hasDoc,
    rank: 62,
    run: () => toggleBlock("dust", !on("dust")),
  },
  ...["lit", "facet", "acrylic", "flat"].map((finish) => ({
    id: `look.finish.${finish}`,
    title: () => t("Finish for everything: {name}", { name: FINISH_NAMES()[finish] }),
    group,
    icon: "ring",
    when: hasDoc,
    rank: 63,
    run: () => finishAll(finish),
  })),
]);
