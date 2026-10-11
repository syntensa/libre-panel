// The preview of the theme being edited, drawn by the panel's own renderer
// (POST /api/render). Requests are throttled: one at a time, and while it is
// drawn, changes collect into the next one. An answer for a document that is
// no longer open is dropped (and its request cancelled). Nothing waits for it.

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

/** Live values: draw again every refresh while the page is visible. */
function updateLive() {
  const { prefs, doc } = store.get();
  clearInterval(liveTimer);
  liveTimer = null;
  if (!prefs.live || !doc) return;
  const every = Math.max(1000, doc.theme?.refresh_ms || 1000);
  liveTimer = setInterval(() => {
    if (!document.hidden) requestRender(0);
  }, every);
}

let seen = { rev: -1, doc: null, serial: -1, live: false };
store.subscribe((state) => {
  if (docSerial() !== seen.serial) {
    if (flying && flying.serial !== docSerial()) flying.controller.abort();
    requestRender(0);
  } else if (state.rev !== seen.rev) {
    requestRender();
  }
  if (state.prefs.live !== seen.live || docSerial() !== seen.serial) {
    if (state.prefs.live !== seen.live) requestRender(0);
    seen.live = state.prefs.live;
    seen.serial = docSerial();
    updateLive();
  }
  seen.rev = state.rev;
  seen.doc = state.doc;
});
