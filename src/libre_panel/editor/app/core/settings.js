// The settings of the app (Settings view): config.toml as the server reads it
// (/api/settings), what is written back, and the facts around it: the panel found, the
// start with the system, the sources and services of the running app, what a report of a
// panel says of this computer. The server is editor/settings.py; every write goes into
// config.toml with its comments kept, and the running Libre Panel follows.
// `store.get().settings` is `{config, catalog, restart}` once loaded; `settingsError` says why
// not (config.toml with a mistake in it).

import { toast, toastError } from "../ui/toast.js";
import { get, post } from "./api.js";
import { appAction, pollApp } from "./app.js";
import { t } from "./i18n.js";
import { store, useStore } from "./state.js";

const api = {
  settings: () => get("/api/settings"),
  write: (changes) => post("/api/settings", { set: changes }),
  panel: () => get("/api/panel"),
  clipQuestion: () => get("/api/clip"),
  system: () => get("/api/system"),
  setSystem: (change) => post("/api/system", change),
  unlock: () => get("/api/unlock"),
  setUnlock: (action, options = {}) => post("/api/unlock", { action, ...options }),
  about: () => get("/api/about"),
  report: () => get("/api/report"),
  open: (folder) => post("/api/open", { folder }),
  place: (query) => post("/api/location", { query }),
  sources: () => get("/api/plugins/control/sources"),
  services: () => get("/api/plugins/control/services"),
  enable: (service, enabled) => post("/api/plugins/control/services/enable", { service, enabled }),
  restart: (service) => post("/api/plugins/control/services/restart", { service }),
};

let writing = Promise.resolve();
let writes = 0; // writes sent
let unanswered = 0; // writes sent and not answered yet

/** Read config.toml (again); resolves to the settings, or null when it cannot be read. */
export async function loadSettings() {
  const since = writes;
  const quiet = !unanswered; // no write on its way: the file is read as it stays
  // a write sent meanwhile answers with the file as it left it: an older read is not kept (a
  // section that reads on opening would else undo a quick click, and the next click be "none")
  const kept = () => quiet && writes === since;
  try {
    const settings = await api.settings();
    if (kept()) store.set({ settings, settingsError: null, settingsDown: false });
  } catch (error) {
    // no answer at all (status 0) is not a mistake in config.toml
    if (kept()) store.set({ settingsError: error.message, settingsDown: error.status === 0 });
  }
  return store.get().settings;
}

/**
 * Write settings by their names in config.toml, all in one go: `{"device.brightness": 70,
 * "refresh_ms": null}` (null takes a setting out where that is a thing). Resolves to
 * whether it was written; the person is told why not. Writes follow one another: a change
 * that depends on what is there (a list gets one more item) is a function of the config
 * as the last write left it, `(config) => changes`, so two quick ones do not undo each other.
 */
export function saveSettings(changes) {
  const write = async () => {
    const wanted = typeof changes === "function" ? changes(store.get().settings?.config) : changes;
    if (!wanted) return true;
    writes += 1;
    unanswered += 1;
    try {
      const settings = await api.write(wanted);
      unanswered -= 1;
      store.set({ settings, settingsError: null });
      pollApp(); // the panel's chip shows brightness, upside down and the rest
      return true;
    } catch (error) {
      unanswered -= 1;
      toastError(error);
      await loadSettings(); // what the file really says
      return false;
    }
  };
  writing = writing.then(write);
  return writing;
}

/** A setting by its dotted name from the loaded config ("device.brightness"). */
export function setting(path, config = store.get().settings?.config) {
  return path.split(".").reduce((node, key) => (node == null ? undefined : node[key]), config);
}

/** The config the settings read (undefined until it is loaded), and what to offer choices from. */
export const useConfig = () => useStore((s) => s.settings?.config);
export const useCatalog = () => useStore((s) => s.settings?.catalog);

/** The panel found, and what the video mode makes of it (/api/panel). */
export async function loadPanelInfo() {
  try {
    store.set({ panelInfo: await api.panel() });
  } catch (error) {
    store.set({ panelInfo: null });
    if (error.status !== 409) toastError(error); // 409: config.toml is broken, said by the settings
  }
}

let asking = 0; // the question asked last: an older answer that comes late is not kept

/**
 * Whether Start is to ask for the name of the panel's standby clip (/api/clip):
 * `store.get().clipQuestion` is `{ask, name}`, the name being the one the vendor app uses, which
 * is only offered. Null while config.toml cannot be read: then there is nothing to ask.
 */
export async function loadClipQuestion() {
  const mine = ++asking;
  let question = null;
  try {
    question = await api.clipQuestion();
  } catch {
    // no answer is no question
  }
  if (mine === asking) store.set({ clipQuestion: question });
}

/**
 * The answer to the question about the standby clip: the name to use, or null for "not now".
 * Either way the question is not asked again (`video.clip_asked`). Nothing is written before this.
 * Resolves to whether it was written.
 */
export async function answerClipQuestion(name) {
  const changes = name ? { "video.local_clip": name, "video.clip_asked": true } : { "video.clip_asked": true };
  const written = await saveSettings(changes);
  await loadClipQuestion();
  return written;
}

/** Starting with the system (/api/system). */
export async function loadSystem() {
  try {
    store.set({ systemInfo: await api.system() });
  } catch (error) {
    if (error.status !== 409) toastError(error);
  }
}

/** {"autostart": true} or {"elevated": true}; resolves to whether it worked. */
export async function saveSystem(change) {
  try {
    store.set({ systemInfo: await api.setSystem(change) });
    pollApp();
    return true;
  } catch (error) {
    toastError(error);
    await loadSystem();
    return false;
  }
}

/** The version, licence, notices, links and folders (/api/about). */
export async function loadAbout() {
  try {
    store.set({ aboutInfo: await api.about() });
  } catch (error) {
    toastError(error);
  }
}

/** What a report of a panel says of this computer (/api/report): `{text, product, form, platform}`,
 * the form's address with the panel found and the list filled in. Rejects with the reason. */
export const loadReport = () => api.report();

/** Show a folder of Libre Panel ("config", "themes" or "logs") in the file manager. */
export async function openFolder(folder) {
  try {
    await api.open(folder);
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

/** Places of that name: [{name, latitude, longitude}]. Rejects with the reason. */
export async function searchPlaces(query) {
  return (await api.place(query)).places;
}

/** The sources the running app reads, with what each says about itself (the console API). */
export async function loadSourceStates() {
  try {
    store.set({ sourceStates: await api.sources() });
  } catch {
    store.set({ sourceStates: null }); // no console: nothing to tell about them
  }
}

/** The services and their states (the console API). */
export async function loadServiceStates() {
  try {
    store.set({ serviceStates: await api.services() });
  } catch {
    store.set({ serviceStates: null });
  }
}

/** Ask for the services' states again in a moment: the app follows a change of config.toml
 * within a second or two, and the state is "changing" until it has. */
const askAgainSoon = () => setTimeout(loadServiceStates, 1500);

/** Switch a service on or off in config.toml; the running app follows within a moment. */
export async function enableService(service, enabled) {
  try {
    await api.enable(service, enabled);
    await Promise.all([loadServiceStates(), loadSettings()]);
    askAgainSoon();
    return true;
  } catch (error) {
    toastError(error);
    await loadServiceStates();
    return false;
  }
}

/** Stop an enabled service and start it again with its settings. */
export async function restartService(service) {
  try {
    await api.restart(service);
  } catch (error) {
    toastError(error);
  }
  await loadServiceStates();
  askAgainSoon();
}

/**
 * Start the panel loop again so that it reads the sources and the weather from config.toml
 * anew (the panel is paused and resumed: services go on, the panel is dark for a moment).
 */
export async function restartPanel() {
  if (!(await appAction("pause"))) return false;
  const resumed = await appAction("resume");
  await loadSettings(); // the server knows the loop started again
  return resumed;
}

// -- the one-click setup of the system: the sensor service (Windows), the rights (Linux) -----------

/**
 * What the setup of this system would bring and how a run of it goes (/api/unlock): on Windows
 * the sensor service and what "Unlock all sensors" sets up, on Linux the permission for the panel,
 * the mainboard's sensor driver and the processor's power (core/setup.js); null where nothing
 * answers. `platform` says which.
 */
export async function loadUnlock() {
  try {
    store.set({ unlockInfo: await api.unlock() });
  } catch {
    store.set({ unlockInfo: null });
  }
  return store.get().unlockInfo;
}

/** How a run of system-setup ended, in a sentence. */
function tellUnlock(info) {
  const job = info?.job;
  if (!job || job.running || info.platform === "linux") return; // Linux's card tells it itself
  if (job.cancelled) {
    toast(t("Nothing was changed: Windows' question was answered with no."), { kind: "warn" });
  } else if (job.error) {
    toast(t("Windows did not start the setup: {error}", { error: job.error }), { kind: "error" });
  } else if (job.code !== 0) {
    toast(t("Not everything could be set up. In a terminal opened as administrator, this command says why: {command}", { command: info.command }), {
      kind: "error",
    });
  } else if (job.action === "remove") {
    toast(t("The sensor service is removed."), { kind: "success" });
  } else {
    const later = info.frame_rates === "sign-in" ? ` ${t("Game frame rates count from your next sign-in.")}` : "";
    toast(t("All sensors are unlocked: the readings arrive within a few seconds.") + later, { kind: "success", timeout: 9000 });
  }
}

let following = null;

/** Follow a run until it is over, then say how it went. */
function followUnlock() {
  if (following) return;
  following = setInterval(async () => {
    const info = await loadUnlock();
    if (info?.job?.running) return;
    clearInterval(following);
    following = null;
    tellUnlock(info);
    loadSourceStates(); // the sources say how LibreHardwareMonitor is read now
    if (info?.platform === "linux" && info.job?.state === "done") {
      // the chips of a driver that was loaded are found by the next rescan of the sensors
      pollApp();
      setTimeout(() => {
        loadSourceStates();
        pollApp();
      }, 4000);
    }
  }, 1000);
}

/**
 * "unlock" or "remove": libre-panel system-setup, after the system asks for administrator rights
 * (one UAC prompt on Windows, polkit's password window on Linux, where `{rapl: true}` or
 * `{rapl: false}` also opens or closes the processor's energy counters). Resolves to whether it
 * was started; how it ended is said when it has.
 */
export async function runUnlock(action, options = {}) {
  try {
    store.set({ unlockInfo: await api.setUnlock(action, options) });
  } catch (error) {
    toastError(error);
    return false;
  }
  followUnlock();
  return true;
}
