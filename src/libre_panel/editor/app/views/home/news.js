// What is new in a release, on Start, for someone who used an older Libre Panel (a first start
// of this one gets the first steps instead): the big things of the release, each with what does
// it right here, until the card is closed. Whom it is for and whether it was closed is kept by
// the server (/api/news, editor/news.py), not by this browser: the window and a browser are told
// alike, and once.

import { normalTheme } from "../../core/actions.js";
import { get, post } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { loadSettings, useCatalog, useConfig } from "../../core/settings.js";
import { store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { toastError } from "../../ui/toast.js";
import { sizesDialog } from "../../shell/theme-menu.js";
import { navigate } from "../../shell/views.js";
import { StepList } from "./overview.js";
import { usePanelModel } from "./panel.js";

/** The release these news are of. */
export const RELEASE = "0.8";

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

/** A part of Start (by its class), brought into view and marked. */
function pointAt(selector) {
  const card = document.querySelector(selector);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.remove("pointed");
  void card.offsetWidth; // the mark plays again on a second click
  card.classList.add("pointed");
}

/** A theme's card among all themes (Start → All themes), brought into view and marked once it
 * is drawn: the list comes a moment after the view. */
function showTheme(id) {
  navigate("home", "themes");
  const until = Date.now() + 3000;
  const look = () => {
    const card = document.querySelector(`.theme-card[data-theme="${id}"]`);
    if (card) pointAt(`.theme-card[data-theme="${id}"]`);
    else if (Date.now() < until) setTimeout(look, 50);
  };
  look();
}

/** The news of 0.8: Libre Panel's own engine (where the panel has the video mode and the
 * download carries it, or it was chosen), every panel size, and Terra. */
export function WhatsNew() {
  const config = useConfig();
  const catalog = useCatalog();
  const { model } = usePanelModel();
  const [looked, setLooked] = useState({});
  useEffect(() => {
    if (!store.get().settings) loadSettings();
  }, []);
  if (!config) return null;
  const look = (id) => setLooked((now) => ({ ...now, [id]: true }));
  const encoder = config.video?.encoder;
  const engine = encoder === "native" || (encoder === "auto" && Boolean(catalog?.engine));
  const items = [
    model?.video && config.video?.mode !== "off" && (engine || catalog?.engine) && {
      id: "engine",
      title: t("Libre Panel's own engine"),
      text: t("It now encodes the panel's video and sends each picture on its time, lighter on the PC, unless you chose another encoder."),
      done: engine,
      action: html`<button type="button" class="btn" onClick=${() => navigate("settings", "panel/panel-video-experiment")}>${t("Show me")}</button>`,
    },
    {
      id: "sizes",
      title: t("Every panel size"),
      text: t("Libre Panel's screens are laid out for every panel size, and your own themes can have a version for each."),
      done: Boolean(looked.sizes),
      action: html`<button type="button" class="btn" onClick=${() => (look("sizes"), normalTheme() ? sizesDialog(normalTheme()) : navigate("design"))}>${t("Show me")}</button>`,
    },
    {
      id: "terra",
      title: t("Terra: the earth as it is now"),
      text: t("A new screen: the globe lit where the sun stands this minute, the cities' lights at night, the clouds and your place."),
      done: Boolean(looked.terra),
      action: html`<button type="button" class="btn" onClick=${() => (look("terra"), showTheme("terra"))}>${t("Show me")}</button>`,
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
