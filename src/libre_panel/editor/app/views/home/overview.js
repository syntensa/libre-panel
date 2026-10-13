// Start's overview of what the panel shows when (its pages, switched on and off right here),
// the first steps after a first start (each says what it does and does it, until all are done
// or the person hides them), and the way to the panel's own start screen. Design → Pages has
// the pages in full.

import { refreshPages, themeName } from "../../core/actions.js";
import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { enableService, loadSettings, useConfig } from "../../core/settings.js";
import { useMounted } from "../../core/orientation.js";
import { setPref, store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { pageName } from "../../shell/panel-popover.js";
import { navigate } from "../../shell/views.js";
import { Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toastError } from "../../ui/toast.js";
import { MountedChoice, panelModelNow } from "./panel.js";

/** Design with its Pages open. */
export function openPages() {
  setPref("panel", "pages");
  navigate("design");
}

/** The panel's model as far as it is known: the one Start shows themes for, else config.toml's. */
export function knownModel(config) {
  const models = store.get().specs?.models || [];
  return panelModelNow().model || models.find((model) => model.id === config?.device?.model) || null;
}

async function switchPage(id, on) {
  try {
    store.set({ pages: await endpoints.setPage(id, { on }) });
  } catch (error) {
    toastError(error);
  }
}

const WHEN = () => ({
  normal: t("The rest of the time"),
  game: t("While a game runs"),
  media: t("While music plays"),
  idle: t("When nobody has touched the PC for a while"),
  report: t("After a game"),
});

function usePages() {
  const pages = useStore((s) => s.pages);
  useEffect(() => {
    if (!store.get().pages) refreshPages();
  }, []);
  return pages;
}

/** What the panel shows when: the normal theme, and each page with its switch. */
export function PagesCard() {
  const pages = usePages();
  if (!pages?.pages?.length) return null;
  const when = WHEN();
  const order = ["normal", "game", "media", "idle", "report"];
  const rows = pages.pages
    .filter((page) => order.includes(page.id) && (page.id !== "report" || page.theme))
    .sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
  return html`<section class="pages-card" aria-labelledby="pages-card-title">
    <div class="pages-card-head">
      <h2 id="pages-card-title">${t("What the panel shows when")}</h2>
      <button type="button" class="link-btn" onClick=${openPages}>${t("Change the pages")}</button>
    </div>
    <ul class="pages-card-rows">
      ${rows.map((page) => {
        const switchable = page.id !== "normal";
        const off = switchable && !page.on;
        return html`<li key=${page.id} class=${`pages-card-row${off ? " off" : ""}`} data-page=${page.id}>
          <span class="pages-card-text">
            <strong>${pageName(page.id)}</strong>
            <span class="muted">${when[page.id]}${page.id === "idle" && page.minutes ? ` · ${t("after {n} min", { n: page.minutes })}` : ""}</span>
          </span>
          <span class="pages-card-theme">${page.theme ? themeName(page.theme) : t("The normal theme")}</span>
          ${switchable
            ? html`<${Toggle} label=${t("Show the page {page}", { page: pageName(page.id) })} checked=${Boolean(page.on)}
                onChange=${(on) => switchPage(page.id, on)} />`
            : html`<span class="pages-card-always">${t("always")}</span>`}
        </li>`;
      })}
    </ul>
  </section>`;
}

/**
 * The first steps: how the panel is mounted, the weather's place, game mode, music's page and
 * the notices. Done ones are ticked; the card goes when all are done, or with "Hide".
 */
export function FirstSteps() {
  const config = useConfig();
  const pages = usePages();
  const hidden = useStore((s) => s.prefs.stepsHidden);
  useEffect(() => {
    if (!store.get().settings) loadSettings();
  }, []);
  if (hidden || !config || !pages?.pages) return null;
  const page = (id) => pages.pages.find((item) => item.id === id);
  const enabled = new Set(config.services?.enabled || []);
  const on = (id) => html`<${Toggle} label=${pageName(id)} checked=${Boolean(page(id)?.on)} onChange=${(value) => switchPage(id, value)} />`;
  const steps = [
    {
      id: "mounted",
      title: t("How is your panel mounted?"),
      text: t("Upright, every screen shows in its version for a bar on end."),
      done: Boolean(config.device?.orientation),
      action: html`<${MountedChoice} />`,
    },
    page("game") && {
      id: "game",
      title: t("Game mode"),
      text: t("When a game starts, the panel opens its game screen with the frame rate."),
      done: Boolean(page("game").on),
      action: on("game"),
    },
    page("media") && {
      id: "media",
      title: t("Music page"),
      text: t("While music plays, Encore takes over by itself."),
      done: Boolean(page("media").on),
      action: on("media"),
    },
    {
      id: "notices",
      title: t("Notices"),
      text: t("The volume, a new track or a warning, briefly over the screen."),
      done: enabled.has("notices"),
      action: html`<${Toggle} label=${t("Notices")} checked=${enabled.has("notices")} onChange=${(value) => enableService("notices", value)} />`,
    },
    {
      id: "weather",
      title: t("Weather for your place"),
      text: t("For the weather cards and Nimbus; nothing else leaves your PC."),
      done: Boolean(config.weather?.enabled),
      action: html`<button type="button" class="btn" onClick=${() => navigate("settings", "weather")}>${t("Set the place")}</button>`,
    },
  ].filter(Boolean);
  const left = steps.filter((step) => !step.done).length;
  if (!left) return null;
  return html`<section class="first-steps" aria-labelledby="first-steps-title">
    <div class="first-steps-head">
      <h2 id="first-steps-title">${t("First steps")}</h2>
      <span class="muted">${t("{done} of {all} done", { done: steps.length - left, all: steps.length })}</span>
      <span class="grow"></span>
      <button type="button" class="link-btn" onClick=${() => setPref("stepsHidden", true)}>${t("Hide")}</button>
    </div>
    <ol class="first-steps-list">
      ${steps.map(
        (step) => html`<li key=${step.id} class=${`first-step${step.done ? " done" : ""}`} data-step=${step.id}>
          <span class="first-step-mark" aria-hidden="true">${step.done ? html`<${Icon} name="check" size=${14} strokeWidth=${3} />` : null}</span>
          <span class="first-step-text">
            <strong>${step.title}</strong>
            <span class="muted">${step.text}</span>
          </span>
          <span class="first-step-action">${step.action}</span>
        </li>`,
      )}
    </ol>
  </section>`;
}

/** The panel's own boot logo and standby clip, for a TURZX USB panel at hand: Settings has them. */
export function StartScreenTile() {
  const [model, setModel] = useState(null);
  const way = useMounted() === "portrait" ? "portrait" : "landscape";
  useEffect(() => {
    endpoints
      .startScreen()
      .then((data) => setModel(data.model || null))
      .catch(() => setModel(null));
  }, []);
  if (!model) return null;
  return html`<section class="sequence-card start-screen-tile" aria-label=${t("Start screen")}>
    <div class="sequence-card-head">
      <${Icon} name="power" size=${16} />
      <strong>${t("Start screen")}</strong>
    </div>
    <img class=${`start-screen-thumb${way === "portrait" ? " upright" : ""}`} alt="" src=${endpoints.startScreenPicture(model, "logo", 480, way)} />
    <p class="muted">${t("The panel's own boot logo and the clip it plays while the PC starts, in Libre Panel's look.")}</p>
    <div class="sequence-card-foot">
      <span class="grow"></span>
      <button type="button" class="btn" onClick=${() => navigate("settings", "panel/start-screen")}>${t("Set up")}</button>
    </div>
  </section>`;
}
