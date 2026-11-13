// What is new in a release, on Start, for someone who used an older Libre Panel (a first start
// of this one gets the first steps instead): the big things of the release, each with what does
// it right here, until the card is closed. Whom it is for and whether it was closed is kept by
// the server (/api/news, editor/news.py), not by this browser: the window and a browser are told
// alike, and once.

import { endpoints, get, post } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { loadSettings, useConfig } from "../../core/settings.js";
import { store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { toastError } from "../../ui/toast.js";
import { navigate } from "../../shell/views.js";
import { StepList } from "./overview.js";

/** The release these news are of. */
export const RELEASE = "0.9";

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

// what "Show me" has shown since the page loaded: it leads away from Start, and is kept for
// the way back
let seen = {};

/** The news of 0.9: Libre Panel written anew as one small program (half the load), the icon in
 * the notification area (a switch now, off at first), and, where the panel is mounted upside
 * down and has a start screen, that it can be written upright. */
export function WhatsNew() {
  const config = useConfig();
  const [looked, setLooked] = useState(seen);
  const [startScreen, setStartScreen] = useState(false);
  useEffect(() => {
    if (!store.get().settings) loadSettings();
    endpoints
      .startScreen()
      .then((data) => setStartScreen(Boolean(data.model)))
      .catch(() => setStartScreen(false));
  }, []);
  if (!config) return null;
  const look = (id) => {
    seen = { ...seen, [id]: true };
    setLooked(seen);
  };
  const items = [
    {
      id: "lighter",
      title: t("Half the load on your PC"),
      text: t("Libre Panel is written anew as one small program: about half of 0.8's load on the processor and half its memory, with the same picture on the panel."),
      done: Boolean(looked.lighter),
      action: html`<button type="button" class="btn" onClick=${() => (look("lighter"), navigate("live", "diagnose"))}>${t("Show me")}</button>`,
    },
    {
      id: "tray",
      title: t("The icon in the notification area is a switch"),
      text: t("It is off at first: Libre Panel runs on when you close its window, with or without it. Turn it on to open the window and switch themes from there."),
      done: Boolean(config.tray_icon),
      action: html`<button type="button" class="btn" onClick=${() => navigate("settings", "system")}>${t("Show me")}</button>`,
    },
    startScreen && config.device?.rotate === 180 && {
      id: "start-screen",
      title: t("The start screen, upright"),
      text: t("Your panel is mounted upside down: write its logo and its clip again, and they are upright on it."),
      done: Boolean(looked.startScreen),
      action: html`<button type="button" class="btn" onClick=${() => (look("startScreen"), navigate("settings", "panel/start-screen"))}>${t("Show me")}</button>`,
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
