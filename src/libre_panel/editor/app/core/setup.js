// The one-click setup of Linux (`libre-panel system-setup --linux`) in the app. What it would
// bring this computer, and how a run of it goes, is what /api/unlock tells where the system is
// Linux (`store.get().unlockInfo`, core/settings.js: the same route and the same polling that
// Windows' "Unlock all sensors" uses). A run makes the person's system ask for the administrator's
// password (polkit's own window), which takes as long as the person wants, and is followed until
// it ends. This file is what only Linux needs besides: the question "Not now" on Start, and the
// card there that stays to show how a run ended.

import { loadUnlock, runUnlock } from "./settings.js";
import { store, useStore } from "./state.js";

const DISMISSED = "libre-panel-setup-dismissed";

function remembered() {
  try {
    return localStorage.getItem(DISMISSED) || "";
  } catch {
    return ""; // private window or blocked storage: asked again at the next start
  }
}

store.set({ setupDismissed: remembered(), setupRan: false });

/**
 * The setup of Linux out of what /api/unlock told: the answer where the system is Linux (it has
 * `offers`), null until it was asked and false where it is another system (Windows has a card of
 * its own).
 */
export const linuxSetup = (info) => {
  if (info === null || info === undefined) return null;
  return info.platform === "linux" && Array.isArray(info.offers) ? info : false;
};

export const useSetup = () => useStore((s) => linuxSetup(s.unlockInfo));

/** Ask what the setup would bring this computer (again). */
export const loadSetup = () => loadUnlock().then((info) => linuxSetup(info));

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

/**
 * Ask for the rights and set it up: `{rapl: true}` opens the energy counters, `{rapl: false}`
 * closes them, `{remove: true}` takes the setup out. Resolves to whether the run was started (the
 * server may refuse it, and the person is told why); how it ends is in `job` of the setup.
 */
export async function runSetup(options = {}) {
  store.set({ setupRan: true });
  const started = options.remove ? await runUnlock("remove") : await runUnlock("unlock", { rapl: options.rapl ?? null });
  if (!started) store.set({ setupRan: false });
  return started;
}
