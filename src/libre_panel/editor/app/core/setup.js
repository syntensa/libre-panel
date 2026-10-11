// The one-click setup of Linux (`libre-panel system-setup --linux`): what it would bring this
// computer (/api/setup), and a run of it. `store.get().setup` is the server's answer, null until
// asked and false where there is nothing to set up (another system, or an app without the
// route). A run makes the person's system ask for the administrator's password (polkit's own
// window), which takes as long as the person wants, so the run is polled until it ends.

import { toastError } from "../ui/toast.js";
import { get, post } from "./api.js";
import { pollApp } from "./app.js";
import { loadSourceStates } from "./settings.js";
import { store, useStore } from "./state.js";

const DISMISSED = "libre-panel-setup-dismissed";
const POLL_MS = 800;

function remembered() {
  try {
    return localStorage.getItem(DISMISSED) || "";
  } catch {
    return ""; // private window or blocked storage: asked again at the next start
  }
}

store.set({ setup: null, setupDismissed: remembered(), setupRan: false });

/** Ask what the setup would bring this computer (again); resolves to `store.get().setup`. */
export async function loadSetup() {
  try {
    const setup = await get("/api/setup");
    store.set({ setup: setup.supported ? setup : false });
  } catch {
    store.set({ setup: false }); // an older background app, or no answer: nothing to offer
  }
  return store.get().setup;
}

export const useSetup = () => useStore((s) => s.setup);

/** What is missing now that the setup brings: the ids of the offers that are `needed`. */
export const setupNeeds = (setup) => (setup ? setup.offers.filter((offer) => offer.needed).map((offer) => offer.id) : []);

/** "Not now": the card on Start stays away until something else is missing. */
export function dismissSetup(needs) {
  const key = needs.join(",");
  store.set({ setupDismissed: key });
  try {
    localStorage.setItem(DISMISSED, key);
  } catch {
    // the choice lasts until the page closes
  }
}

/** The card on Start has shown a run, and stays until it is closed. */
export const closeSetupRun = () => store.set({ setupRan: false });

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Ask for the rights and set it up: `{rapl: true}` opens the energy counters, `{rapl: false}`
 * closes them, `{remove: true}` takes the setup out. Resolves to how the run ended: "done",
 * "failed", "cancelled" (the password window was closed), "unavailable" (there is none), or
 * "refused" (the server would not start it; the person is told why).
 */
export async function runSetup(options = {}) {
  try {
    const started = await post("/api/setup", options);
    store.set({ setup: started, setupRan: true });
  } catch (error) {
    toastError(error);
    return "refused";
  }
  let setup = store.get().setup;
  while (setup && setup.job.state === "running") {
    await pause(POLL_MS);
    setup = await loadSetup();
  }
  const state = setup ? setup.job.state : "failed";
  if (state === "done") {
    // the chips the drivers brought are found by the next rescan of the sensors: a few seconds
    loadSourceStates();
    pollApp();
    setTimeout(() => {
      loadSourceStates();
      pollApp();
    }, 4000);
  }
  return state;
}
