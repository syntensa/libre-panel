// Start (#/home): the launcher. The panel at a glance, the theme on it with a
// quick switch, quick actions; on the first run, finding the panel and choosing
// a theme. Built on the shell: see ../../README.md.

import { t } from "../../core/i18n.js";
import { html } from "../../lib/preact.js";
import { navigate, registerView } from "../../shell/views.js";
import { EmptySlot } from "../slot.js";

function HomeView() {
  return html`<${EmptySlot}
    icon="home"
    title=${t("Start")}
    text=${t("The panel at a glance, the theme on it and a quick switch to another.")}
  >
    <button type="button" class="btn btn-primary" onClick=${() => navigate("design")}>${t("Design")}</button>
    <button type="button" class="btn" onClick=${() => navigate("settings")}>${t("Settings")}</button>
  <//>`;
}

registerView({ id: "home", title: () => t("Start"), order: 10, icon: "home", component: HomeView });
