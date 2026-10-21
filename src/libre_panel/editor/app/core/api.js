// The server's API (editor/server.py). Every request carries this run's access token (the
// header of static/token.js): the server answers nothing without it, to nobody else on this PC.
// A picture, which an <img> asks for by itself, has the token in its address instead.

import { authHeaders, withToken } from "/static/token.js";
import { t } from "./i18n.js";
import { store } from "./state.js";

export { authHeaders };

/**
 * The server's reason as a person reads it: without the name of the exception that was raised
 * and the system's error numbers ("PermissionError: [Errno 13] Permission denied: 'C:\\…'"), and
 * the system's words for the reasons that come up (no permission, a full disk, a missing file)
 * in the language of the app.
 */
export function readable(message, status = 400) {
  let text = String(message || "").trim();
  if (!text) return t("Something went wrong.");
  const internal = status >= 500 && /^[A-Z]\w*(?:Error|Exception): /.test(text);
  text = text.replace(/^[A-Z]\w*(?:Error|Exception): /, "");
  const system = /^\[(?:Errno|WinError) (\d+)\] ([^:]+)(?::\s*'?(.*?)'?)?$/.exec(text);
  if (system) {
    const [, code, words, quoted = ""] = system;
    const path = quoted.replace(/\\\\/g, "\\"); // Python quotes a Windows path with \\ for \
    const where = path || t("the file");
    // no permission (EPERM, EACCES, Windows' access denied), in use (EBUSY, Windows' sharing
    // violation), a read-only drive (EROFS)
    if (["1", "5", "13", "16", "30", "32"].includes(code)) {
      return t("Libre Panel may not change {path}: it is read-only, or another program has it open.", { path: where });
    }
    if (["28", "112"].includes(code)) return t("The disk is full: nothing could be saved.");
    if (["2", "3"].includes(code)) return t("Not found: {path}", { path: where });
    text = path ? `${words}: ${path}` : words;
  }
  return internal ? t("Something went wrong in Libre Panel: {reason}", { reason: text }) : text;
}

/** A request; resolves to the JSON answer, rejects with the server's error text (readable, and
 * the HTTP status as `error.status`; 0 when the server did not answer at all). */
export async function api(method, url, body, { raw = false, signal } = {}) {
  const options = { method, headers: authHeaders(), signal };
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
    const error = new Error(readable(data.error || response.statusText || String(response.status), response.status));
    error.status = response.status;
    if (data.reason === "token") refused(error);
    throw error;
  }
  return data;
}

/**
 * The page is from an earlier run of Libre Panel (another access token): the bar on top says so
 * once (shell/app.js, Offline), and what asked is not told again in a message of its own: a tab
 * left open over a few restarts would else fill up with them.
 */
function refused(error) {
  error.quiet = true;
  store.set((state) => ({ app: { ...state.app, gone: true, refused: error.message } }));
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
  assetUrl: (id, path) =>
    withToken(`/api/themes/${enc(id)}/assets/${path.split("/").map(enc).join("/")}`),
  previewUrl: (id, width = 480) => withToken(`/api/themes/${enc(id)}/preview?w=${width}`),
  fanLookUrl: (look, paint) => withToken(`/api/fan-look?look=${enc(look)}&paint=${enc(paint)}`),
  symbolsLookUrl: (pack) => withToken(`/api/symbols-look?pack=${enc(pack)}`),
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
  // scenes: a theme for every panel at once (scenes.py)
  scenes: () => get("/api/scenes"),
  setScene: (action, name) => post("/api/scenes", { action, name }),
  sequence: () => get("/api/sequence"),
  startScreen: () => get("/api/start-screen"),
  startScreenAction: (action, model) => post("/api/start-screen", { action, model }),
  // (the way the panel is mounted is config.toml's: in the address too, so that a picture
  // of the other way is not taken from the cache)
  startScreenPicture: (model, what, width = 960, way = "") =>
    withToken(`/api/start-screen/picture?model=${enc(model)}&what=${enc(what)}&w=${width}&o=${enc(way)}`),
  startScreenFile: (model, what, way = "") =>
    withToken(`/api/start-screen/file?model=${enc(model)}&what=${enc(what)}&o=${enc(way)}`),
  setSequence: (on, steps) => post("/api/sequence", { on, steps }),
  app: () => get("/api/app"),
  appAction: (action, value) => post("/api/app", { action, value }),
  // Live → Diagnose: the panel test (doctor_session.py) and the log (logview.py)
  doctor: () => get("/api/doctor"),
  doctorAction: (action, answer) => post("/api/doctor", answer === undefined ? { action } : { action, answer }),
  logs: (level = "DEBUG") => get(`/api/logs?level=${enc(level)}`),
  setDetailedLog: (verbose) => post("/api/logs", { verbose }),
  logsFile: () => withToken("/api/logs/file"),
  // Settings → System → Backup: the settings and own themes in one file, and back (backup.py)
  backupFile: () => withToken("/api/backup/file"),
  restoreBackup: (bytes) => api("POST", "/api/backup", bytes, { raw: true }),
  acrossPicture: (stamp = "") => withToken(`/api/across/picture?v=${enc(String(stamp))}`),
  setAcross: (bytes) => api("POST", "/api/across", bytes, { raw: true }),
  panelsPhoto: () => withToken("/api/panels/photo"),
  pluginPages: () => get("/api/plugins/pages"),
  hardware: () => get("/api/hardware"),
  setHardware: (names) => post("/api/hardware", names),
};
