// What is new in a release, on Start, for someone who used an older Libre Panel (a first start
// of this one gets the first steps instead): the big things of the release, each with what does
// it right here, until the card is closed. Whom it is for and whether it was closed is kept by
// the server (/api/news, editor/news.py), not by this browser: the window and a browser are told
// alike, and once.

import { get, post } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { loadSettings, useConfig } from "../../core/settings.js";
import { store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { toastError } from "../../ui/toast.js";
import { navigate } from "../../shell/views.js";
import { StepList } from "./overview.js";

/** The release these news are of. */
export const RELEASE = "0.7";

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

/** The news of 0.7: more panels at once, scenes on Start, and the new modules of Design. */
export function WhatsNew() {
  const config = useConfig();
  const [looked, setLooked] = useState({});
  useEffect(() => {
    if (!store.get().settings) loadSettings();
  }, []);
  if (!config) return null;
  const look = (id) => setLooked((now) => ({ ...now, [id]: true }));
  const items = [
    {
      id: "panels",
      title: t("More panels at once"),
      text: t("Up to four panels, each with its own theme, placed where they stand."),
      done: (config.panels || []).length > 0,
      action: html`<button type="button" class="btn" onClick=${() => navigate("settings", "panels")}>${t("Add a panel")}</button>`,
    },
    {
      id: "scenes",
      title: t("Scenes"),
      text: t("A theme for every panel at once, with one click: keep what they show now as a scene."),
      done: Boolean(looked.scenes),
      action: html`<button type="button" class="btn" onClick=${() => (pointAt(".scenes-card"), look("scenes"))}>${t("Show me")}</button>`,
    },
    {
      id: "modules",
      title: t("New modules"),
      text: t("Ping, a timer and the power the PC draws, in the library of Design."),
      done: Boolean(looked.modules),
      action: html`<button type="button" class="btn" onClick=${() => (look("modules"), navigate("design"))}>${t("Open Design")}</button>`,
    },
  ];
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
