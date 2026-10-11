// The preview of the theme being edited, drawn by the panel's own renderer
// (POST /api/render). Requests are throttled: one at a time, and while it is
// drawn, changes collect into the next one. An answer for a document that is
// no longer open is dropped (and its request cancelled). Nothing waits for it:
// until a theme that has just been opened is drawn, its views show a spinner at
// its size, not the picture of the theme before it.

import { endpoints } from "./api.js";
import { docSerial } from "./doc.js";
import { store } from "./state.js";

const THROTTLE_MS = 45;

let timer = null;
let flying = null; // {serial, controller}
let wanted = 0; // counts requested renders
let liveTimer = null;

function apply(data) {
  store.set({
    render: {
      png: `data:image/png;base64,${data.png}`,
      boxes: data.boxes || {},
      size: [data.width, data.height],
      grid: data.grid || null,
      gridActive: Boolean(data.grid_active),
      moduleBoxes: data.module_boxes || {},
      warnings: data.warnings || [],
    },
    renderError: null,
  });
}

function send() {
  const { doc, prefs } = store.get();
  if (flying || !doc?.theme) return;
  const asked = wanted;
  const serial = docSerial();
  const controller = new AbortController();
  flying = { serial, controller };
  endpoints
    .render(doc.theme, doc.id, prefs.live, controller.signal)
    .then((data) => {
      if (serial === docSerial()) apply(data);
    })
    .catch((error) => {
      if (error.name !== "AbortError" && serial === docSerial()) store.set({ renderError: error.message });
    })
    .finally(() => {
      flying = null;
      if (wanted > asked) send();
    });
}

/** Draw the preview again soon (at once with delay 0). */
export function requestRender(delay = THROTTLE_MS) {
  wanted++;
  if (delay === 0) {
    clearTimeout(timer);
    timer = null;
    send();
    return;
  }
  if (timer === null) {
    timer = setTimeout(() => {
      timer = null;
      send();
    }, delay);
  }
}

const VIEWS = ["design", "look"]; // the views that show the picture

/** Live values: draw again every refresh while the page is visible and a view shows the picture. */
function updateLive() {
  const { prefs, doc, route } = store.get();
  clearInterval(liveTimer);
  liveTimer = null;
  if (!prefs.live || !doc || !VIEWS.includes(route.view)) return;
  const every = Math.max(1000, doc.theme?.refresh_ms || 1000);
  liveTimer = setInterval(() => {
    if (!document.hidden) requestRender(0);
  }, every);
}

/** What shows until the opened theme is drawn: nothing, as large as the theme says. */
function waiting(doc) {
  const { width, height } = doc?.theme?.display || {};
  return {
    png: null,
    boxes: {},
    size: width > 0 && height > 0 ? [width, height] : store.get().render.size,
    grid: null,
    gridActive: false,
    moduleBoxes: {},
    warnings: [],
  };
}

let seen = { rev: -1, serial: -1, live: false, view: null };
store.subscribe((state) => {
  const opened = docSerial() !== seen.serial;
  const changed = state.rev !== seen.rev;
  const moved = state.route.view !== seen.view;
  const liveChanged = state.prefs.live !== seen.live;
  // (noted first: the store changes below come back here)
  seen = { rev: state.rev, serial: docSerial(), live: state.prefs.live, view: state.route.view };
  if (opened) {
    if (flying && flying.serial !== seen.serial) flying.controller.abort();
    store.set({ render: waiting(state.doc), renderError: null });
    requestRender(0);
  } else if (changed) {
    requestRender();
  }
  if (liveChanged || opened || moved) {
    if (liveChanged) requestRender(0);
    updateLive();
  }
});
