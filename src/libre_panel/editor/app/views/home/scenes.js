// Start → Scenes: a theme for every panel at once ("Gaming", "Work"), put on with a click; what the
// panels show now is kept as one with "Keep as a scene". A shortcut puts the next one on
// (Settings → Start and system), and the tray has them too. See scenes.py.

import { endpoints } from "../../core/api.js";
import { themeName } from "../../core/actions.js";
import { t } from "../../core/i18n.js";
import { store } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { confirmDialog, promptDialog } from "../../ui/dialog.js";
import { Icon } from "../../ui/icons.js";
import { toastError } from "../../ui/toast.js";

async function reloadPages() {
  store.set({ pages: await endpoints.pages() }); // the theme on the panel is the scene's now
}

export function ScenesCard() {
  const [state, setState] = useState(null);
  useEffect(() => {
    endpoints.scenes().then(setState, () => setState(null));
  }, []);
  if (!state) return null;
  const act = async (action, name) => {
    try {
      setState(await endpoints.setScene(action, name));
      if (action !== "delete") await reloadPages();
    } catch (error) {
      toastError(error);
    }
  };
  const keep = async () => {
    const name = await promptDialog({
      title: t("Keep as a scene"),
      text: t("What every panel shows now, under a name: one click puts it back on."),
      label: t("Name of the scene"),
      placeholder: t("Gaming"),
      confirm: t("Keep"),
      validate: (text) => (text.length > 40 ? t("At most 40 characters") : null),
    });
    if (name) act("save", name);
  };
  const remove = async (name) => {
    const sure = await confirmDialog({ title: t("Remove the scene “{name}”?", { name }), message: t("The themes stay as they are."), confirm: t("Remove"), danger: true });
    if (sure) act("delete", name);
  };
  const { scenes, on } = state;
  return html`<section class=${`scenes-card${on ? " on" : ""}`} aria-label=${t("Scenes")}>
    <div class="scenes-card-head">
      <span class="more-row-icon" aria-hidden="true"><${Icon} name="layers" size=${16} /></span>
      <span class="more-row-text">
        <strong>${t("Scenes")}</strong>
        <span class="muted">${on ? t("On: {name}", { name: on }) : t("A theme for every panel at once, with one click")}</span>
      </span>
      <button type="button" class="btn" onClick=${keep}>${t("Keep as a scene")}</button>
    </div>
    ${scenes.length
      ? html`<ul class="scene-chips">
          ${scenes.map(
            (scene) => html`<li key=${scene.name} class=${scene.name === on ? "current" : ""}>
              <button type="button" class="scene-chip" aria-pressed=${String(scene.name === on)}
                title=${scene.themes.filter(Boolean).map(themeName).join(" · ")}
                onClick=${() => act("on", scene.name === on ? "" : scene.name)}>${scene.name}</button>
              <button type="button" class="scene-remove" aria-label=${t("Remove the scene “{name}”?", { name: scene.name })}
                onClick=${() => remove(scene.name)}><${Icon} name="close" size=${12} /></button>
            </li>`,
          )}
        </ul>`
      : null}
  </section>`;
}
