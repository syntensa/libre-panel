// The settings of the app (Settings view): config.toml as the server reads it
// (/api/settings), what is written back, and the facts around it: the panel found, the
// start with the system, the sources and services of the running app. The server is
// editor/settings.py; every write goes into config.toml with its comments kept, and the
// running Libre Panel follows. `store.get().settings` is `{config, catalog, restart}`
// once loaded; `settingsError` says why not (config.toml with a mistake in it).

import { toastError } from "../ui/toast.js";
import { get, post } from "./api.js";
import { appAction, pollApp } from "./app.js";
import { store, useStore } from "./state.js";

const api = {
  settings: () => get("/api/settings"),
  write: (changes) => post("/api/settings", { set: changes }),
  panel: () => get("/api/panel"),
  clipQuestion: () => get("/api/clip"),
  system: () => get("/api/system"),
  setSystem: (change) => post("/api/system", change),
  about: () => get("/api/about"),
  open: (folder) => post("/api/open", { folder }),
  place: (query) => post("/api/location", { query }),
  sources: () => get("/api/plugins/control/sources"),
  services: () => get("/api/plugins/control/services"),
  enable: (service, enabled) => post("/api/plugins/control/services/enable", { service, enabled }),
  restart: (service) => post("/api/plugins/control/services/restart", { service }),
};

/** Read config.toml (again); resolves to the settings, or null when it cannot be read. */
export async function loadSettings() {
  try {
    store.set({ settings: await api.settings(), settingsError: null, settingsDown: false });
  } catch (error) {
    // no answer at all (status 0) is not a mistake in config.toml
    store.set({ settingsError: error.message, settingsDown: error.status === 0 });
  }
  return store.get().settings;
}

let writing = Promise.resolve();

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
    try {
      store.set({ settings: await api.write(wanted), settingsError: null });
      pollApp(); // the panel's chip shows brightness, upside down and the rest
      return true;
    } catch (error) {
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
