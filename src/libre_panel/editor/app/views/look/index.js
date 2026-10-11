// Look (#/look): the colours of the theme, how bars and rings are drawn
// (finish), the panel light, depth, transitions and messages. Built on the
// shell: see ../../README.md.

import { t } from "../../core/i18n.js";
import { setSelection } from "../../core/doc.js";
import { html } from "../../lib/preact.js";
import { ApplyButton } from "../../shell/header.js";
import { PanelChip } from "../../shell/panel-popover.js";
import { ThemeTitle } from "../../shell/theme-menu.js";
import { navigate, registerView } from "../../shell/views.js";
import { EmptySlot } from "../slot.js";

function LookView() {
  return html`<${EmptySlot}
    icon="palette"
    title=${t("Look")}
    text=${t("Colours, finishes, the panel light, depth, transitions and messages of this theme.")}
  >
    <button type="button" class="btn" onClick=${() => (setSelection([]), navigate("design"))}>${t("Theme settings")}</button>
  <//>`;
}

const LookActions = () => html`<${PanelChip} /><${ApplyButton} />`;

registerView({
  id: "look",
  title: () => t("Look"),
  order: 30,
  icon: "palette",
  component: LookView,
  header: { lead: ThemeTitle, actions: LookActions },
});
