// What is new in a release, on Start, for someone who used an older Libre Panel (a first start
// of this one gets the first steps instead): the big things of the release, each with what does
// it right here, until the card is closed. Whom it is for and whether it was closed is kept by
// the server (/api/news, editor/news.py), not by this browser: the window and a browser are told
// alike, and once.

import { get, post } from "../../core/api.js";
import { themeName } from "../../core/actions.js";
import { t } from "../../core/i18n.js";
import { loadSettings, useConfig } from "../../core/settings.js";
import { store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { toastError } from "../../ui/toast.js";
import { StepList, knownModel, openPages, switchPage, usePages } from "./overview.js";
import { MountedChoice, asksMounting, usePanelModel } from "./panel.js";
import { fitsPanel } from "./themes.js";

/** The release these news are of. */
export const RELEASE = "0.6";

const numbers = (version) => String(version).split(/[^0-9]+/).filter(Boolean).map(Number);

/** Whether a version is older than a release, by their first two numbers ("0.5.6" < "0.6"). */
export function olderThan(version, release) {
  const [a, b] = [numbers(version), numbers(release)];
  for (const i of [0, 1]) if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) < (b[i] || 0);
  return false;
}

/** Whether whoever uses this Libre Panel began with a version older than this release ("older":
 * before the server kept it). */
const beganBefore = (news) => news.first === "older" || olderThan(news.first, RELEASE);

/**
 * Whether Start shows the news: to someone who began with a version older than their release,
 * never to someone who began with the one running now, and not once they were closed.
 */
export function showsNews(news, version) {
  if (!news || news.seen === RELEASE || news.first === version) return false;
  return beganBefore(news);
}

/** Whether Start offers the first steps: to someone who began with this version (or where the
 * server keeps no news). Who updated is told what is new instead, and knows the rest. */
export const showsFirstSteps = (news, version) => !news || news.first === version || !beganBefore(news);

async function loadNews() {
  store.set({ news: null }); // asked
  try {
    store.set({ news: await get("/api/news") });
  } catch {
    store.set({ news: false }); // an older server: no news, the first steps
  }
}

/** The server's word on the news ({first, seen}); null while it is asked, false without one. */
export function useNews() {
  const news = useStore((s) => s.news);
  useEffect(() => {
    if (store.get().news === undefined) loadNews();
  }, []);
  return news;
}

async function close() {
  const before = store.get().news;
  store.set({ news: { ...before, seen: RELEASE } });
  try {
    store.set({ news: await post("/api/news", { seen: RELEASE }) });
  } catch (error) {
    store.set({ news: before });
    toastError(error);
  }
}

/** The part of Start that says what the panel shows when, brought into view and marked. */
function showOverview() {
  const card = document.querySelector(".pages-card");
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.remove("pointed");
  void card.offsetWidth; // the mark plays again on a second click
  card.classList.add("pointed");
}

/** The news of 0.6: bars on end (for a bar), Arena for game mode (where it fits the panel), and
 * Start's new overview. */
export function WhatsNew() {
  const config = useConfig();
  const pages = usePages();
  const themes = useStore((s) => s.themes);
  const { model: shownFor } = usePanelModel();
  const [looked, setLooked] = useState(false);
  useEffect(() => {
    if (!store.get().settings) loadSettings();
  }, []);
  if (!config || !pages?.pages) return null;
  const model = shownFor || knownModel(config);
  const game = pages.pages.find((page) => page.id === "game");
  const arena = themes.find((item) => item.id === "arena");
  const arenaOn = Boolean(game?.on && game.theme === "arena");
  const items = [
    asksMounting(model) && {
      id: "upright",
      title: t("Bars on end"),
      text: t("Mounted upright? Every screen now comes in a version for a bar on end."),
      done: Boolean(config.device?.orientation),
      action: html`<${MountedChoice} />`,
    },
    game &&
      arena &&
      fitsPanel(arena, model) && {
        id: "arena",
        title: t("Arena for game mode"),
        text: arenaOn
          ? t("Game mode is on and shows Arena: the frame rate large, the CPU and the GPU beside it.")
          : t("Libre Panel's own game screen: the frame rate large with its 1% low, the CPU and the GPU beside it."),
        done: arenaOn,
        action: html`<span class="news-actions">
          <button type="button" class="link-btn" onClick=${openPages}>${t("Open the page")}</button>
          ${arenaOn
            ? null
            : html`<button type="button" class="btn" onClick=${() => switchPage("game", true, "arena")}>
                ${t("Use {theme} in games", { theme: themeName("arena") })}
              </button>`}
        </span>`,
      },
    {
      id: "overview",
      title: t("Start at a glance"),
      text: t("What the panel shows when, each page with its switch, right here on Start."),
      done: looked,
      action: html`<button type="button" class="btn" onClick=${() => (showOverview(), setLooked(true))}>${t("Show me")}</button>`,
    },
  ].filter(Boolean);
  return html`<section class="news-card" aria-labelledby="news-card-title">
    <div class="first-steps-head">
      <h2 id="news-card-title">${t("What's new in {version}", { version: RELEASE })}</h2>
      <span class="muted">${t("The big things, each a click away")}</span>
      <span class="grow"></span>
      <button type="button" class="link-btn" onClick=${close}>${t("Got it")}</button>
    </div>
    <${StepList} steps=${items} />
  </section>`;
}
