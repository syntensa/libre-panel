// Live (#/live): what the panel shows now, how fast and how well; the
// sources and services, and messages to try. Built on the shell: see
// ../../README.md.

import { t } from "../../core/i18n.js";
import { html } from "../../lib/preact.js";
import { registerView } from "../../shell/views.js";
import { EmptySlot } from "../slot.js";

function LiveView() {
  return html`<${EmptySlot}
    icon="pulse"
    title=${t("Live")}
    text=${t("What the panel shows now, its frame rate, the sources and services, and messages to try.")}
  />`;
}

registerView({ id: "live", title: () => t("Live"), order: 40, icon: "pulse", component: LiveView });
