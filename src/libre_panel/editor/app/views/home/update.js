// A newer Libre Panel on Start: once the question whether Libre Panel may look for one (a
// request to GitHub once a day, config.toml's check_updates), then, where one is out, its card
// with what is new and the download for this kind of install, until it is closed ("Later": the
// next version's card comes). The server asks GitHub and keeps the answer (/api/update,
// editor/updates.py): the window and a browser are told alike. "Update to 0.7.1" (here and in
// Settings → About) has the server fetch that download and check it, then put it in place on a
// second click, "Install and restart" (/api/update/install).

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

/** Installed with pip: the command that brings `version` (there is no button for it). */
export const PipCommand = ({ version }) =>
  html`<p class="update-card-command">${t("Installed with pip: this command brings the new version.")}
    <code class="mono">${pipCommand(version)}</code></p>`;

const BUSY = ["downloading", "verifying", "installing"];
const POLL_MS = 500;

/** How the update stands (/api/update/install) into the store; false for a server without it.
 * While the app quits for the new version it does not answer: what was last said stays. */
export async function loadInstall() {
  try {
    store.set({ updateInstall: await get("/api/update/install") });
  } catch (error) {
    if (error.status === 404) store.set({ updateInstall: false });
  }
}

/** The update's state, asked once and again every half second while it is under way. */
function useInstall() {
  const install = useStore((s) => s.updateInstall);
  const busy = BUSY.includes(install?.state);
  useEffect(() => {
    if (store.get().updateInstall === undefined) {
      store.set({ updateInstall: null }); // asked
      loadInstall();
    }
  }, []);
  useEffect(() => {
    if (!busy) return undefined;
    const timer = setInterval(() => !document.hidden && loadInstall(), POLL_MS);
    return () => clearInterval(timer);
  }, [busy]);
  return install;
}

/** What "Update to" does, by how Libre Panel came (the server's `kind`), said before the click. */
const WHAT = () => ({
  setup: t("Libre Panel downloads the setup and checks it. Install and restart then closes Libre Panel, installs the new version without questions (Windows may ask for administrator rights) and starts it."),
  portable: t("Libre Panel downloads the zip and checks it. Install and restart then unpacks it into a new folder beside this one, with your settings, closes Libre Panel and starts the new one; this folder stays, to delete once the new one runs."),
  appimage: t("Libre Panel downloads the AppImage next to this one and checks it. Install and restart then puts it in this one's place, closes Libre Panel and starts the new one."),
  linux: t("Libre Panel downloads the archive and checks it; unpack it in place of this folder yourself, as the first time."),
});

/** A size in megabytes, as the download shows it. */
const megabytes = (bytes) => t("{size} MB", { size: (bytes / 1e6).toFixed(1) });

/**
 * "Update to 0.7.1": the download for this kind of install, fetched and checked by the server,
 * with its progress; then "Install and restart" (or, for the Linux tarball, its folder), and
 * what went wrong in words. Nothing where there is no such download (pip: `PipCommand`).
 * `extra`: more buttons, beside its own.
 */
export function UpdateNow({ update, extra = null }) {
  const install = useInstall();
  const [busy, setBusy] = useState(false);
  const { latest, kind } = update;
  const what = WHAT()[kind];
  if (!latest?.asset || !what || !install) return null;
  const act = async (action) => {
    setBusy(true);
    try {
      store.set({ updateInstall: await post("/api/update/install", { action }) });
    } catch (error) {
      toastError(error);
    } finally {
      setBusy(false);
    }
  };
  const now = install.version === latest.version ? install : { state: "idle" };
  const fetchIt = (label) =>
    html`<button type="button" class="btn btn-primary" disabled=${busy} onClick=${() => act("download")}>${label}</button>`;
  const buttons = (main) => (main || extra ? html`<div class="update-card-form">${main}${extra}</div>` : null);
  let body;
  if (now.state === "downloading" || now.state === "verifying") {
    const checking = now.state === "verifying";
    const known = !checking && now.total > 0; // (else a bar without an end)
    body = html`<progress max=${known ? now.total : undefined} value=${known ? now.done : undefined} aria-label=${t("Download")}></progress>
      <p class="update-now-line" role="status">${checking
        ? t("Checking the download …")
        : now.total
          ? t("Downloading: {done} of {total}", { done: megabytes(now.done), total: megabytes(now.total) })
          : t("Downloading: {done}", { done: megabytes(now.done) })}</p>
      ${buttons(null)}`;
  } else if (now.state === "ready") {
    const open = now.how === "open";
    body = html`<p class="update-now-line" role="status">${now.checked
        ? t("Downloaded and checked against the release's checksums.")
        : t("Downloaded; this release has no checksums, so only its size was checked.")}
        ${open ? html` ${t("It is in {folder}.", { folder: now.where })}` : null}</p>
      ${buttons(html`<button type="button" class="btn btn-primary" disabled=${busy} onClick=${() => act("install")}>
        ${open ? t("Show the download") : t("Install and restart")}
      </button>`)}`;
  } else if (now.state === "installing") {
    body = html`<progress aria-label=${t("Install and restart")}></progress>
      <p class="update-now-line" role="status">${t("Installing: Libre Panel closes and starts again in a moment.")}</p>
      ${buttons(null)}`;
  } else if (now.state === "error") {
    body = html`<p class="update-now-error" role="alert">${now.error}</p>
      ${buttons(fetchIt(t("Try again")))}`;
  } else {
    body = buttons(fetchIt(t("Update to {version}", { version: latest.version })));
  }
  return html`<div class="update-now" data-update-now=${now.state}>
    <p class="update-now-line">${what}</p>
    ${body}
  </div>`;
}

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
      ${kind === "python" && !latest.download ? html`<${PipCommand} version=${latest.version} />` : null}
      <${UpdateNow} update=${update} />
      <div class="update-card-form">
        ${latest.download ? external(latest.download, t("Download"), !latest.asset) : null}
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
