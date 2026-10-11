// The app's frame: header, the current view, and what floats above them
// (command palette, dialogs, messages).

import { t } from "../core/i18n.js";
import { useStore } from "../core/state.js";
import { html, useEffect, useErrorBoundary } from "../lib/preact.js";
import { Dialogs } from "../ui/dialog.js";
import { Icon } from "../ui/icons.js";
import { CommandPalette } from "../ui/palette.js";
import { Toasts } from "../ui/toast.js";
import { Header } from "./header.js";
import { viewById } from "./views.js";

function Loading() {
  return html`<div class="view-message" aria-busy="true"><span class="spinner"></span>${t("Loading…")}</div>`;
}

function Failure({ message }) {
  return html`<div class="view-message error" role="alert">
    <${Icon} name="warning" size=${22} />
    <strong>${t("Libre Panel could not start")}</strong>
    <span>${message}</span>
    <button type="button" class="btn" onClick=${() => location.reload()}>${t("Try again")}</button>
  </div>`;
}

/**
 * A view that throws while it is drawn (a plugin's odd data, a bug) would leave the page blank:
 * it says so instead, and the other views stay reachable. Another address, `at`, is another try.
 */
function Boundary({ children, at }) {
  const [error, reset] = useErrorBoundary((caught) => console.warn("a view could not be drawn:", caught));
  useEffect(() => reset(), [at]);
  if (!error) return children;
  return html`<div class="view-message error" role="alert">
    <${Icon} name="warning" size=${22} />
    <strong>${t("This view could not be drawn")}</strong>
    <span>${error.message || String(error)}</span>
    <div class="view-message-actions">
      <button type="button" class="btn" onClick=${reset}>${t("Try again")}</button>
      <a class="btn" href="#/home">${t("Start")}</a>
    </div>
  </div>`;
}

/** The background app stopped answering (it quit, or crashed): the page says so and goes on asking. */
function Offline() {
  return html`<div class="offline-bar" role="alert">
    <${Icon} name="warning" size=${16} />
    <span>${t("Libre Panel does not answer. If it has quit, start it again: this page tries again by itself.")}</span>
  </div>`;
}

export function App() {
  useStore((s) => s.lang); // a new language: everything is drawn again
  const gone = useStore((s) => Boolean(s.app.gone));
  const { ready, failed, route } = useStore((s) => ({ ready: s.ready, failed: s.failed, route: s.route }));
  const view = viewById(route.view);
  const View = view?.component;
  let body = html`<${Loading} />`;
  if (failed) body = html`<${Failure} message=${failed} />`;
  else if (ready && View) {
    body = html`<${Boundary} key=${view.id} at=${route.rest}><${View} rest=${route.rest} /><//>`;
  }
  return html`<div class="app" data-view=${route.view}>
    <${Header} view=${ready ? view : null} />
    ${gone ? html`<${Offline} />` : null}
    <main class="view" id="main">${body}</main>
    <${CommandPalette} />
    <${Dialogs} />
    <${Toasts} />
  </div>`;
}
