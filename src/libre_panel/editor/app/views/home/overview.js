// Start's overview of what the panel shows when (its pages, switched on and off right here), the
// first steps after a first start (each says what it does and does it, until all are done or the
// person hides them), and the way to the panel's own start screen. Design → Pages has the pages
// in full. The rows of the first steps are the news' too (news.js).

import { refreshPages, themeName } from "../../core/actions.js";
import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { enableService, loadSettings, useConfig } from "../../core/settings.js";
import { useMounted } from "../../core/orientation.js";
import { VOLUME_LOOKS, openVolumeLook, showVolume, useVolumeNotice } from "../../core/volume.js";
import { setPref, store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { pageName } from "../../shell/panel-popover.js";
import { navigate } from "../../shell/views.js";
import { Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toastError } from "../../ui/toast.js";
import { MountedChoice, asksMounting, panelModelNow, usePanelModel } from "./panel.js";
import { fitsPanel } from "./themes.js";

/** Design with its Pages open. */
export function openPages() {
  setPref("panel", "pages");
  navigate("design");
}

/** Switch a page on or off (and give it a theme, where `theme` is given). */
export async function switchPage(id, on, theme = null) {
  try {
    store.set({ pages: await endpoints.setPage(id, theme ? { on, theme } : { on }) });
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

/** The panel's model as far as it is known: the one Start shows themes for, else config.toml's. */
export function knownModel(config) {
  const models = store.get().specs?.models || [];
  return panelModelNow().model || models.find((model) => model.id === config?.device?.model) || null;
}

/**
 * The screen game mode shows once it is switched on from Start: its own where it has one, else
 * the first made for it (Arena) that is installed and made for the panel; null where none is
 * (or the panel is not known): then it shows the normal theme.
 */
export function gameScreen(page, model) {
  if (page?.theme) return page.theme;
  if (!model) return null;
  const themes = store.get().themes;
  const fits = (id) => themes.some((item) => item.id === id && fitsPanel(item, model));
  return (page?.suggested || []).find(fits) || null;
}

/** Game mode switched on (or off) from Start, with its screen (gameScreen). */
export const switchGameOn = (page, model, on) => switchPage("game", on, on ? gameScreen(page, model) : null);

const WHEN = () => ({
  normal: t("The rest of the time"),
  game: t("While a game runs"),
  media: t("While music plays"),
  idle: t("When nobody has touched the PC for a while"),
  report: t("After a game"),
});

export function usePages() {
  const pages = useStore((s) => s.pages);
  useEffect(() => {
    if (!store.get().pages) refreshPages();
  }, []);
  return pages;
}

/**
 * What the panel shows when: the normal theme, and each page with its switch. Game mode switched
 * on here gets its game screen as in the first steps; while it is off, the row names that screen.
 */
export function PagesCard() {
  const pages = usePages();
  const config = useConfig();
  const { model: shownFor } = usePanelModel();
  if (!pages?.pages?.length) return null;
  const model = shownFor || knownModel(config);
  const when = WHEN();
  const order = ["normal", "game", "media", "idle", "report"];
  const rows = pages.pages
    .filter((page) => order.includes(page.id) && (page.id !== "report" || page.theme))
    .sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
  const shows = (page) => page.theme || (page.id === "game" && !page.on ? gameScreen(page, model) : null);
  const change = (page, on) => (page.id === "game" ? switchGameOn(page, model, on) : switchPage(page.id, on));
  return html`<section class="pages-card" aria-labelledby="pages-card-title">
    <div class="pages-card-head">
      <h2 id="pages-card-title">${t("What the panel shows when")}</h2>
      <button type="button" class="link-btn" onClick=${openPages}>${t("Change the pages")}</button>
    </div>
    <ul class="pages-card-rows">
      ${rows.map((page) => {
        const switchable = page.id !== "normal";
        const off = switchable && !page.on;
        const theme = shows(page);
        return html`<li key=${page.id} class=${`pages-card-row${off ? " off" : ""}`} data-page=${page.id}>
          <span class="pages-card-text">
            <strong>${pageName(page.id)}</strong>
            <span class="muted">${when[page.id]}${page.id === "idle" && page.minutes ? ` · ${t("after {n} min", { n: page.minutes })}` : ""}</span>
          </span>
          <span class="pages-card-theme">${theme ? themeName(theme) : t("The normal theme")}</span>
          ${switchable
            ? html`<${Toggle} label=${t("Show the page {page}", { page: pageName(page.id) })} checked=${Boolean(page.on)}
                onChange=${(on) => change(page, on)} />`
            : html`<span class="pages-card-always">${t("always")}</span>`}
        </li>`;
      })}
    </ul>
  </section>`;
}

/** Rows of things to do, each with its mark (ticked when done), what it is and what does it:
 * the first steps and the news. */
export function StepList({ steps }) {
  return html`<ol class="first-steps-list">
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
  </ol>`;
}

/**
 * The first steps: how the panel is mounted, game mode, music's page, the notices and the
 * weather's place. Done ones are ticked; the card goes when all are done, or with "Hide".
 */
export function FirstSteps() {
  const config = useConfig();
  const pages = usePages();
  const hidden = useStore((s) => s.prefs.stepsHidden);
  const { model: shownFor } = usePanelModel();
  useEffect(() => {
    if (!store.get().settings) loadSettings();
  }, []);
  if (hidden || !config || !pages?.pages) return null;
  const model = shownFor || knownModel(config);
  const page = (id) => pages.pages.find((item) => item.id === id);
  const enabled = new Set(config.services?.enabled || []);
  const on = (id, change = (value) => switchPage(id, value)) =>
    html`<${Toggle} label=${pageName(id)} checked=${Boolean(page(id)?.on)} onChange=${change} />`;
  const game = page("game");
  const screen = game ? gameScreen(game, model) : null;
  const music = page("media");
  const steps = [
    asksMounting(model) && {
      id: "mounted",
      title: t("How is your panel mounted?"),
      text: t("Upright, every screen shows in its version for a bar on end."),
      done: Boolean(config.device?.orientation),
      action: html`<${MountedChoice} />`,
    },
    game && {
      id: "game",
      title: t("Game mode"),
      text: screen
        ? t("When a game starts, the panel shows {theme} with the frame rate.", { theme: themeName(screen) })
        : t("When a game starts, the panel opens its game screen with the frame rate."),
      done: Boolean(game.on),
      action: on("game", (value) => switchGameOn(game, model, value)),
    },
    music && {
      id: "media",
      title: t("Music page"),
      text: music.theme
        ? t("While music plays, {theme} takes over by itself.", { theme: themeName(music.theme) })
        : t("While music plays, the panel shows what plays by itself."),
      done: Boolean(music.on),
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
    <${StepList} steps=${steps} />
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
  return html`<section class="more-row start-screen-tile" aria-label=${t("Start screen")}>
    <img class=${`start-screen-thumb${way === "portrait" ? " upright" : ""}`} alt="" src=${endpoints.startScreenPicture(model, "logo", 320, way)} />
    <span class="more-row-text">
      <strong>${t("Start screen")}</strong>
      <span class="muted">${t("The panel's own boot logo and standby clip")}</span>
    </span>
    <button type="button" class="btn" onClick=${() => navigate("settings", "panel/start-screen")}>${t("Set up")}</button>
  </section>`;
}

/** The volume over every screen, from Start: its switch, the look it has, and the way to Look
 * to choose another (core/volume.js). */
export function VolumeTile() {
  const { on, look } = useVolumeNotice();
  if (on === undefined) return null;
  const name = VOLUME_LOOKS().find((option) => option.value === look)?.label;
  return html`<section class=${`more-row volume-tile${on ? " on" : ""}`} aria-label=${t("Show the volume")} data-volume-tile>
    <span class="more-row-icon" aria-hidden="true"><${Icon} name="volume" size=${16} /></span>
    <span class="more-row-text">
      <strong>${t("Show the volume")}</strong>
      <span class="muted">${on ? t("On every screen: {look}", { look: name }) : t("When you turn it, on every screen")}</span>
    </span>
    <${Toggle} checked=${on} label=${t("Show the volume")} onChange=${showVolume} />
    <button type="button" class="btn" onClick=${openVolumeLook}>${t("Style")}</button>
  </section>`;
}
