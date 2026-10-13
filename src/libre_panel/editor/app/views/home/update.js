// A newer Libre Panel on Start: once the question whether Libre Panel may look for one (a
// request to GitHub once a day, config.toml's check_updates), then, where one is out, its card
// with what is new and the download for this kind of install, until it is closed ("Later": the
// next version's card comes). The server asks GitHub and keeps the answer (/api/update,
// editor/updates.py): the window and a browser are told alike.

import { get, post } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { saveSettings } from "../../core/settings.js";
import { store, useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { toastError } from "../../ui/toast.js";

/** Ask the server (`now`: GitHub at once, as "Look now" does); the answer goes to the store. */
export async function loadUpdate({ now = false } = {}) {
  try {
    store.set({ update: await get(now ? "/api/update?now=1" : "/api/update") });
  } catch {
    store.set({ update: false }); // an older server: nothing to say
  }
  return store.get().update;
}

/** The server's word ({check, current, kind, latest, newer, asked, error, later}); undefined
 * until asked, false without one. */
export function useUpdate() {
  const update = useStore((s) => s.update);
  useEffect(() => {
    if (store.get().update === undefined) {
      store.set({ update: null }); // asked
      loadUpdate();
    }
  }, []);
  return update;
}

/** Switch the daily look on or off (config.toml), and say what it found. */
export async function setCheckUpdates(on) {
  if (await saveSettings({ check_updates: on })) await loadUpdate();
}

/** How to get the new version where there is no download for it: pip's command (from GitHub). */
const pipCommand = (version) =>
  `pipx install --force "libre-panel[usb,serial,tray] @ git+https://github.com/syntensa/libre-panel@v${version}"`;

const external = (href, label, primary = false) =>
  html`<a class=${primary ? "btn btn-primary" : "btn"} href=${href} target="_blank" rel="noopener noreferrer">
    ${label} <${Icon} name="external" size=${14} />
  </a>`;

function Question() {
  const [busy, setBusy] = useState(false);
  const answer = async (on) => {
    setBusy(true);
    try {
      await setCheckUpdates(on);
    } catch (error) {
      toastError(error);
    } finally {
      setBusy(false);
    }
  };
  return html`<section class="update-card" aria-labelledby="update-card-title" data-update="ask">
    <span class="update-card-icon" aria-hidden="true"><${Icon} name="download" size=${22} /></span>
    <div class="update-card-body">
      <h2 id="update-card-title">${t("Hear of new versions?")}</h2>
      <p>${t("Libre Panel can look once a day whether a new version is out, and say so here. It asks GitHub, where Libre Panel is published; nothing of you or your panel is sent.")}</p>
      <div class="update-card-form">
        <button type="button" class="btn btn-primary" disabled=${busy} onClick=${() => answer(true)}>${t("Yes, look")}</button>
        <button type="button" class="btn" disabled=${busy} onClick=${() => answer(false)}>${t("No, thanks")}</button>
      </div>
    </div>
  </section>`;
}

function NewVersion({ update }) {
  const { latest, kind } = update;
  const later = async () => {
    try {
      store.set({ update: await post("/api/update", { later: latest.version }) });
    } catch (error) {
      toastError(error);
    }
  };
  return html`<section class="update-card" aria-labelledby="update-card-title" data-update="new">
    <span class="update-card-icon" aria-hidden="true"><${Icon} name="download" size=${22} /></span>
    <div class="update-card-body">
      <h2 id="update-card-title">${t("Libre Panel {version} is out", { version: latest.version })}</h2>
      ${latest.notes ? html`<p>${latest.notes}</p>` : null}
      <p class="update-card-now muted">${t("You have {version}.", { version: update.current })}</p>
      ${kind === "python" && !latest.download
        ? html`<p class="update-card-command">${t("Installed with pip: this command brings the new version.")}
            <code class="mono">${pipCommand(latest.version)}</code></p>`
        : null}
      <div class="update-card-form">
        ${latest.download ? external(latest.download, t("Download"), true) : null}
        ${external(latest.page, t("What is new"), !latest.download)}
        <button type="button" class="btn" onClick=${later}>${t("Later")}</button>
      </div>
    </div>
  </section>`;
}

/** Start's card: the question (not answered yet; not beside the question about smooth motion,
 * one question at a time), else a newer version (until "Later"). */
export function UpdateCard() {
  const update = useUpdate();
  const clip = useStore((s) => s.clipQuestion); // asked by the card of that question
  if (!update) return null;
  if (update.check === null) return clip?.ask ? null : html`<${Question} />`;
  if (update.newer && update.latest && update.later !== update.latest.version) {
    return html`<${NewVersion} update=${update} />`;
  }
  return null;
}
