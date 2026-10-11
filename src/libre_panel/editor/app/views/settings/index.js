// Settings (#/settings, #/settings/<section>): the panel, sensors, services,
// weather and place, start and system, about. Built on the shell: see
// ../../README.md.

import { t } from "../../core/i18n.js";
import { html } from "../../lib/preact.js";
import { registerView } from "../../shell/views.js";
import { EmptySlot } from "../slot.js";

function SettingsView() {
  return html`<${EmptySlot}
    icon="gear"
    title=${t("Settings")}
    text=${t("The panel, sensors, services, weather and place, how Libre Panel starts, and about it.")}
  />`;
}

const SettingsTitle = () => html`<span class="topbar-title">${t("Settings")}</span>`;

registerView({
  id: "settings",
  title: () => t("Settings"),
  tab: false,
  order: 60,
  icon: "gear",
  component: SettingsView,
  header: { actions: SettingsTitle },
});
