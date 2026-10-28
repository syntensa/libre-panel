// The app's views and the route between them (#/home, #/design, #/look,
// #/live, #/settings; more path after the view is the view's own: #/settings/panel).
//
//   registerView({
//     id: "look",                     // the route: #/look
//     title: () => t("Look"),         // the tab's text
//     tab: true,                      // a tab in the header (settings has its own button)
//     hint: () => text,               // optional: what it is for (the tab's tooltip, Ctrl+K)
//     edits: true,                    // shows the theme being edited: Ctrl+Z and Ctrl+Y act here
//     order: 30,                      // place among the tabs
//     icon: "palette",
//     component: LookView,            // gets {rest}: the route after the view's id
//     header: { lead, center, actions },  // optional parts of the header (components)
//   });

import { t } from "../core/i18n.js";
import { store } from "../core/state.js";

const views = new Map();

export const DEFAULT_VIEW = "home";

export function registerView(view) {
  views.set(view.id, { tab: true, order: 50, header: {}, ...view });
  route();
}

export const allViews = () => [...views.values()].sort((a, b) => a.order - b.order);
export const viewById = (id) => views.get(id) || views.get(DEFAULT_VIEW) || null;

// What the app's own views are for, in a few words (the tabs' tooltips, the hints of Ctrl+K), and
// the words a person may look for them by; a plugin's view brings its own `hint`.
const HINTS = {
  home: () => t("The panel at a glance, the themes and what it shows when"),
  design: () => t("Arrange the building blocks of a theme, and its pages"),
  look: () => t("The colours, the finish and the light of a theme"),
  live: () => t("What the panel shows now, and the history of the last days"),
  player: () => t("Videos and pictures on the panel, instead of its theme"),
  settings: () => t("The panel, sensors, services, weather and start"),
};
const KEYWORDS = {
  home: () => t("home overview dashboard first steps"),
  design: () => t("editor edit layout arrange modules blocks widgets"),
  look: () => t("colours colors palette finish style appearance light"),
  live: () => t("now running status readings values sensors services log"),
  player: () => t("player media video movie gif pictures photos slideshow"),
  settings: () => t("options preferences configuration setup"),
};

export const viewHint = (view) => (typeof view.hint === "function" ? view.hint() : view.hint) || HINTS[view.id]?.() || "";
export const viewKeywords = (view) => KEYWORDS[view.id]?.() || "";

/** Whether the view on show is one that shows the theme being edited (Design, Look). */
export const showsTheme = () => Boolean(viewById(store.get().route.view)?.edits);

/** Show a view: navigate("design"), navigate("settings", "panel"). */
export function navigate(id, rest = "") {
  const hash = `#/${id}${rest ? `/${rest}` : ""}`;
  if (location.hash === hash) route();
  else location.hash = hash;
}

// other names a view is known by: #/start is Start
const ALIASES = { start: "home" };

// where the person was before Settings, for the way back (its sections are one place)
let beforeSettings = null;

/** Back from Settings to the view the person came from (Start where the app was opened on it). */
export function leaveSettings() {
  const back = beforeSettings && viewById(beforeSettings.view)?.id === beforeSettings.view ? beforeSettings : null;
  navigate(back ? back.view : DEFAULT_VIEW, back ? back.rest : "");
}

/** The view Settings goes back to, if any. */
export const settingsReturn = () => (beforeSettings ? viewById(beforeSettings.view) : null);

function route() {
  const [name, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  const id = ALIASES[name] ?? name;
  const view = views.has(id) ? id : DEFAULT_VIEW;
  const current = store.get().route;
  const next = { view, rest: rest.join("/") };
  // `from`: where the person came from, for a "Back" that stays in the app
  if (current.view !== next.view || current.rest !== next.rest) {
    if (next.view === "settings" && current.view !== "settings") beforeSettings = { view: current.view, rest: current.rest };
    store.set({ route: { ...next, from: { view: current.view, rest: current.rest } } });
  }
}

addEventListener("hashchange", route);
