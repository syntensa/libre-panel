// The app's views and the route between them (#/home, #/design, #/look,
// #/live, #/settings; more path after the view is the view's own: #/settings/panel).
//
//   registerView({
//     id: "look",                     // the route: #/look
//     title: () => t("Look"),         // the tab's text
//     tab: true,                      // a tab in the header (settings is reached by its gear)
//     edits: true,                    // shows the theme being edited: Ctrl+Z and Ctrl+Y act here
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

function route() {
  const [name, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  const id = ALIASES[name] ?? name;
  const view = views.has(id) ? id : DEFAULT_VIEW;
  const current = store.get().route;
  const next = { view, rest: rest.join("/") };
  if (current.view !== next.view || current.rest !== next.rest) store.set({ route: next });
}

addEventListener("hashchange", route);
