// The background app (tray or `libre-panel start`) when it serves this page:
// its state every two seconds, its controls; and the native window, when the
// page runs in one (pywebview), which is told about unsaved changes.

import { confirmDialog } from "../ui/dialog.js";
import { toast, toastError } from "../ui/toast.js";
import { endpoints } from "./api.js";
import { t } from "./i18n.js";
import { store } from "./state.js";

const POLL_MS = 2000;
let timer = null;
let quitting = false;

export async function pollApp() {
  if (quitting || document.hidden) return;
  try {
    store.set({ app: await endpoints.app() });
  } catch (error) {
    // 403: it answers, but not this page, which is from an earlier run (another token); the
    // server's own sentence says what to do, and waiting for it to answer again is no use
    const refused = error?.status === 403 ? error.message : "";
    store.set((state) => ({ app: { ...state.app, gone: true, refused } }));
  }
}

export function startAppPolling() {
  pollApp();
  clearInterval(timer);
  timer = setInterval(pollApp, POLL_MS);
  document.addEventListener("visibilitychange", () => !document.hidden && pollApp());
}

/** One control of the background app: pause, resume, brightness, upside_down, autostart, quit. */
export async function appAction(action, value) {
  try {
    store.set({ app: await endpoints.appAction(action, value) });
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

export async function quitApp() {
  const sure = await confirmDialog({
    title: t("Quit Libre Panel?"),
    message: t("The panel stops updating until you start Libre Panel again."),
    confirm: t("Quit"),
    danger: true,
  });
  if (!sure || !(await appAction("quit"))) return;
  quitting = true;
  clearInterval(timer);
  store.set((state) => ({ app: { ...state.app, quit: true } }));
  if (inNativeWindow()) setTimeout(closeNativeWindow, 600);
  else toast(t("Libre Panel has quit. You can close this tab."), { kind: "warn", timeout: 0 });
}

// -- the native window (pywebview): window.pywebview.api is the window's Python side

const native = () => window.pywebview?.api ?? null;
export const inNativeWindow = () => Boolean(window.pywebview);

let toldDirty = null;
function tellDirty() {
  const { dirty } = store.get();
  const api = native();
  if (dirty === toldDirty || typeof api?.set_dirty !== "function") return;
  toldDirty = dirty;
  Promise.resolve(api.set_dirty(dirty)).catch(() => (toldDirty = null));
}
store.subscribe(tellDirty);

// The page's title: the theme, marked while it has changes that are not saved. The
// window puts it in its title bar.
let toldTitle = null;
function tellTitle() {
  const { doc, dirty } = store.get();
  const name = doc?.theme?.name;
  const title = name ? `${dirty ? "● " : ""}${name} – Libre Panel` : "Libre Panel";
  if (document.title !== title) document.title = title;
  const api = native();
  if (title === toldTitle || typeof api?.set_title !== "function") return;
  toldTitle = title;
  Promise.resolve(api.set_title(title)).catch(() => (toldTitle = null));
}
store.subscribe(tellTitle);

window.addEventListener("pywebviewready", () => {
  toldDirty = toldTitle = null;
  tellDirty();
  tellTitle();
});

export function closeNativeWindow() {
  Promise.resolve(native()?.close?.()).catch(() => {});
}

// In a browser tab, leaving with unsaved changes asks first (the window asks itself).
window.addEventListener("beforeunload", (event) => {
  if (store.get().dirty && !inNativeWindow()) {
    event.preventDefault();
    event.returnValue = "";
  }
});
