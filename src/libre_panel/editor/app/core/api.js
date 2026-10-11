// The server's API (editor/server.py). Writes carry the header that browsers
// never send cross-origin without a preflight, which the server refuses.

import { t } from "./i18n.js";

/** A request; resolves to the JSON answer, rejects with the server's error text (and
 * the HTTP status as `error.status`; 0 when the server did not answer at all). */
export async function api(method, url, body, { raw = false, signal } = {}) {
  const options = { method, headers: {}, signal };
  if (method !== "GET") options.headers["X-Libre-Panel"] = "1";
  if (body !== undefined) {
    options.body = raw ? body : JSON.stringify(body);
    options.headers["Content-Type"] = raw ? "application/octet-stream" : "application/json";
  }
  let response;
  try {
    response = await fetch(url, options);
  } catch (failure) {
    if (failure?.name === "AbortError") throw failure;
    const error = new Error(t("Libre Panel does not answer.")); // the browser says "Failed to fetch"
    error.status = 0;
    throw error;
  }
  const data = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) {
    const error = new Error(data.error || response.statusText || String(response.status));
    error.status = response.status;
    throw error;
  }
  return data;
}

export const get = (url, options) => api("GET", url, undefined, options);
export const post = (url, body, options) => api("POST", url, body ?? {}, options);

const enc = encodeURIComponent;

export const endpoints = {
  i18n: () => get("/api/i18n"),
  setLanguage: (language) => post("/api/language", { language }),
  specs: () => get("/api/specs"),
  themes: () => get("/api/themes"),
  theme: (id) => get(`/api/themes/${enc(id)}`),
  saveTheme: (id, theme, source) => post(`/api/themes/${enc(id)}`, { theme, source }),
  assets: (id) => get(`/api/themes/${enc(id)}/assets`),
  assetUrl: (id, path) => `/api/themes/${enc(id)}/assets/${path.split("/").map(enc).join("/")}`,
  previewUrl: (id, width = 480) => `/api/themes/${enc(id)}/preview?w=${width}`,
  uploadAsset: (id, name, bytes) =>
    api("POST", `/api/themes/${enc(id)}/assets?name=${enc(name)}`, bytes, { raw: true }),
  sensors: () => get("/api/sensors"),
  readings: (keys = [], history = []) =>
    get(`/api/readings?keys=${enc(keys.join(","))}&history=${enc(history.join(","))}`),
  render: (theme, base, live, signal) => post("/api/render", { theme, base, live }, { signal }),
  adapt: (options) => post("/api/adapt", options),
  templates: (width, height, model) => post("/api/templates", { width, height, model }),
  detach: (theme, base, id) => post("/api/detach", { theme, base, id }),
  modulePreviews: (palette, style) => post("/api/module-previews", { palette, style }),
  presetPreviews: (palette) => post("/api/preset-previews", { palette }),
  pages: () => get("/api/pages"),
  setPage: (page, change) => post("/api/pages", { page, ...change }),
  app: () => get("/api/app"),
  appAction: (action, value) => post("/api/app", { action, value }),
  pluginPages: () => get("/api/plugins/pages"),
  hardware: () => get("/api/hardware"),
  setHardware: (names) => post("/api/hardware", names),
};
