// Screen (#/screen): what of this computer's screen a panel shows instead of its theme
// (monitor.js: the panel as a monitor).

import { t } from "../../core/i18n.js";
import { html } from "../../lib/preact.js";
import { navigate, registerView } from "../../shell/views.js";
import { registerCommands } from "../../core/commands.js";
import { MonitorSection } from "./monitor.js";

function ScreenView() {
  return html`<div class="screen-view">
    <div class="screen-main">
      <${MonitorSection} />
    </div>
  </div>`;
}

registerView({
  id: "screen",
  title: () => t("Screen"),
  order: 46,
  icon: "monitor",
  component: ScreenView,
});

registerCommands([
  {
    id: "screen.open",
    title: () => t("Screen"),
    hint: () => t("the computer's screen on the panel"),
    keywords: () => t("screen monitor mirror extend window desktop"),
    group: () => t("Go to"),
    icon: "monitor",
    run: () => navigate("screen"),
  },
]);
