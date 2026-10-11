// The app's views and the route between them (#/home, #/design, #/look,
// #/live, #/settings; more path after the view is the view's own: #/settings/panel).
//
//   registerView({
//     id: "look",                     // the route: #/look
//     title: () => t("Look"),         // the tab's text
//     tab: true,                      // a tab in the header (settings is reached by its gear)
//     order: 30,                      // place among the tabs
//     icon: "palette",
//     component: LookView,            // gets {rest}: the route after the view's id
//     header: { lead, center, actions },  // optional parts of the header (components)
//   });

import { store } from "../core/state.js";

const views = new Map();

export const DEFAULT_VIEW = "home";

export function registerView(view) {
  views.set(view.id, { tab: true, order: 50, header: {}, ...view });
  route();
}

export const allViews = () => [...views.values()].sort((a, b) => a.order - b.order);
export const viewById = (id) => views.get(id) || views.get(DEFAULT_VIEW) || null;

/** Show a view: navigate("design"), navigate("settings", "panel"). */
export function navigate(id, rest = "") {
  const hash = `#/${id}${rest ? `/${rest}` : ""}`;
  if (location.hash === hash) route();
  else location.hash = hash;
}

function route() {
  const [id, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  const view = views.has(id) ? id : DEFAULT_VIEW;
  const current = store.get().route;
  const next = { view, rest: rest.join("/") };
  if (current.view !== next.view || current.rest !== next.rest) store.set({ route: next });
}

addEventListener("hashchange", route);
