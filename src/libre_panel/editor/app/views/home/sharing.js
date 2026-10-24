// Sharing themes on Start → Themes: a theme as one .zip to give to someone (Export… on its
// card), such a file added to your themes (Import…, or the file dropped on the gallery), and
// the themes the community shares on Libre Panel's GitHub. The server checks a shared file
// before anything is written (theme/share.py) and asks GitHub for the community's list only
// when that section is opened (editor/community.py); the page itself talks to nobody else.

import { refreshThemes } from "../../core/actions.js";
import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { toast, toastError } from "../../ui/toast.js";

const MAX_MB = 20; // what the server takes (theme/share.py MAX_ZIP)

/** Save a theme as a .zip: its theme.json and the pictures and fonts it names. */
export async function exportZip(item) {
  try {
    const blob = await endpoints.themeZip(item.id);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${item.id}.zip`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    toast(t("Exported {file}", { file: link.download }));
  } catch (error) {
    toastError(error);
  }
}

const added = (done) => toast(t("“{name}” is added to your themes", { name: done.name || done.id }), { kind: "success" });

/** Add a shared theme (its .zip) to your themes; resolves to its id there, or null. */
export async function importZip(file) {
  if (!file) return null;
  if (file.size > MAX_MB * 1024 * 1024) {
    toast(t("The file is larger than {size} MB: a shared theme may be at most that.", { size: MAX_MB }), { kind: "error" });
    return null;
  }
  try {
    const done = await endpoints.importThemeZip(await file.arrayBuffer(), file.name);
    await refreshThemes();
    added(done);
    return done.id;
  } catch (error) {
    toastError(error);
    return null;
  }
}

/** Whether what is dragged over the page is a file (not a text or a picture of the page). */
const carriesFile = (event) => [...(event.dataTransfer?.items || [])].some((item) => item.kind === "file");

/**
 * What makes an element take a dropped .zip (`onFile(file)`), and whether one is over it now:
 * `[dropping, handlers]`, the handlers to spread on the element.
 */
export function useDropFile(onFile) {
  const [dropping, setDropping] = useState(false);
  const handlers = {
    onDragOver: (event) => {
      if (!carriesFile(event)) return;
      event.preventDefault(); // (else the window opens the file)
      event.dataTransfer.dropEffect = "copy";
      setDropping(true);
    },
    onDragLeave: (event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setDropping(false);
    },
    onDrop: (event) => {
      if (!carriesFile(event)) return;
      event.preventDefault();
      setDropping(false);
      const file = event.dataTransfer.files?.[0];
      if (file) onFile(file);
    },
  };
  return [dropping, handlers];
}

const sizesText = (sizes) => (sizes || []).map(([w, h]) => `${w} × ${h}`).join(" · ");

/** A theme of the community: its picture, name, author, the panels it is made for, what it is,
 * and Install. */
function CommunityCard({ entry, busy, onInstall }) {
  return html`<article class="theme-card community-card" data-community=${entry.id}>
    <div class="theme-card-main">
      <span class="theme-card-picture">
        <img src=${endpoints.communityPreviewUrl(entry.id)} alt="" loading="lazy" decoding="async" />
      </span>
      <span class="theme-card-text">
        <span class="theme-card-title"><span class="theme-card-name">${entry.name}</span></span>
        <span class="theme-card-note">${sizesText(entry.sizes)}</span>
      </span>
      ${entry.author ? html`<span class="community-card-author muted">${t("by {author}", { author: entry.author })}</span>` : null}
      ${entry.description ? html`<p class="community-card-text">${entry.description}</p>` : null}
      <button type="button" class="btn community-card-install" disabled=${busy}
        aria-label=${t("Install “{name}”", { name: entry.name })} onClick=${() => onInstall(entry)}>
        <${Icon} name="download" size=${14} /> ${busy ? t("Installing…") : t("Install")}
      </button>
    </div>
  </article>`;
}

/**
 * "Community themes": closed until it is opened, and only then is the list asked for (the
 * server asks GitHub, and keeps the answer an hour). Without an answer it says so, quietly.
 * `onInstalled(id)` follows an install, with the id the theme has among yours.
 */
export function CommunityThemes({ onInstalled }) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState(null); // {themes, offline}; null while it is asked for
  const [busy, setBusy] = useState("");
  useEffect(() => {
    if (!open || list) return undefined;
    let shown = true;
    endpoints
      .communityThemes()
      .then((answer) => shown && setList(answer))
      .catch(() => shown && setList({ themes: [], offline: true }));
    return () => {
      shown = false;
    };
  }, [open]);
  const install = async (entry) => {
    setBusy(entry.id);
    try {
      const done = await endpoints.installCommunityTheme(entry.id);
      await refreshThemes();
      added(done);
      onInstalled?.(done.id);
    } catch (error) {
      toastError(error);
    } finally {
      setBusy("");
    }
  };
  const themes = list?.themes || [];
  const offline = html`<p class="community-note muted" role="status">${t("The community themes cannot be reached right now.")}</p>`;
  const body = !list
    ? html`<p class="community-note muted" role="status">${t("Looking for community themes…")}</p>`
    : !themes.length
      ? list.offline
        ? offline
        : html`<p class="community-note muted">${t("No one has shared a theme here yet.")}</p>`
      : html`${list.offline ? offline : null}
          <div class="theme-grid" role="list" aria-label=${t("Community themes")}>
            ${themes.map(
              (entry) => html`<div role="listitem" key=${entry.id} class="theme-grid-item">
                <${CommunityCard} entry=${entry} busy=${busy === entry.id} onInstall=${install} />
              </div>`,
            )}
          </div>`;
  return html`<section class="community-themes">
    <button type="button" class="community-toggle" aria-expanded=${String(open)}
      aria-controls=${open ? "community-themes" : undefined} onClick=${() => setOpen(!open)}>
      <${Icon} name=${open ? "chevron-down" : "chevron-right"} size=${16} />
      <span class="community-text">
        <span class="community-title"><strong>${t("Community themes")}</strong></span>
        <span class="community-lead">${t("Themes others share on Libre Panel's GitHub. Libre Panel asks GitHub for them only once this is open.")}</span>
      </span>
    </button>
    ${open ? html`<div id="community-themes" class="community-body">${body}</div>` : null}
  </section>`;
}
