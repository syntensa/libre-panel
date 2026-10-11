// A plugin's editor page (docs/PLUGINS.md, "Editor pages") inside the app:
// #/plugin/<id> shows /plugins/<id>/ in a frame, with the way back.

import { t } from "../core/i18n.js";
import { useStore } from "../core/state.js";
import { html } from "../lib/preact.js";
import { Icon } from "../ui/icons.js";
import { registerView } from "./views.js";

function PluginView({ rest }) {
  const pages = useStore((s) => s.plugins);
  const page = pages.find((item) => item.id === rest);
  return html`<div class="plugin-view">
    <div class="plugin-bar">
      <button type="button" class="btn btn-quiet" onClick=${() => history.back()}>
        <${Icon} name="chevron-left" size=${15} /> ${t("Back")}
      </button>
      <strong>${page?.title || rest}</strong>
    </div>
    ${page
      ? html`<iframe class="plugin-frame" title=${page.title} src=${`/plugins/${encodeURIComponent(page.id)}/`}></iframe>`
      : html`<div class="view-message">${t("This plugin page is not installed.")}</div>`}
  </div>`;
}

registerView({ id: "plugin", title: () => t("Plugins"), tab: false, order: 90, icon: "plugin", component: PluginView });
