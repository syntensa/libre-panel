// Sharing themes on Start → Themes: a theme as one .zip to give to someone (Export… on its
// card), such a file added to your themes (Import…, or the file dropped on the gallery), and
// the themes the community shares on Libre Panel's GitHub. The server checks a shared file
// before anything is written (theme/share.py) and asks GitHub for the community's list only
// when that section is opened (editor/community.py); the page itself talks to nobody else.

import { refreshThemes, showOnPanel } from "../../core/actions.js";
import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { panelName } from "../../shell/panel-popover.js";
import { Segmented } from "../../ui/controls.js";
import { confirmDialog, openDialog } from "../../ui/dialog.js";
import { Icon } from "../../ui/icons.js";
import { toast, toastError } from "../../ui/toast.js";

const MAX_MB = 20; // what the server takes (theme/share.py MAX_ZIP)
// GitHub's form that a theme is shared with (.github/ISSUE_TEMPLATE/share_theme.yml); a workflow
// checks what comes with it and opens the pull request that puts it on the list
export const SHARE_FORM = "https://github.com/syntensa/libre-panel/issues/new?template=share_theme.yml";
const TEXT_MOST = { name: 80, author: 80, description: 400 }; // the list's lengths
const AUTHOR_KEY = "libre-panel.share-author"; // who shared last time, kept in this browser

/** Save a theme as a .zip: its theme.json and the pictures and fonts it names. */
export async function exportZip(item) {
  return saveZip(item, true);
}

async function saveZip(item, say) {
  try {
    const blob = await endpoints.themeZip(item.id);
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${item.id}.zip`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    if (say) toast(t("Exported {file}", { file: link.download }));
    return link.download;
  } catch (error) {
    toastError(error);
    return null;
  }
}

// -- sharing with everyone --------------------------------------------------------------------------

const kept = () => {
  try {
    return localStorage.getItem(AUTHOR_KEY) || "";
  } catch {
    return "";
  }
};

const keep = (author) => {
  try {
    localStorage.setItem(AUTHOR_KEY, author);
  } catch {
    // (a browser that keeps nothing: asked again next time)
  }
};

/** GitHub's form, filled in with what the person wrote. */
export function shareFormUrl({ name, author, description }) {
  const fields = new URLSearchParams({ title: `Theme: ${name}`, name, author, description });
  return `${SHARE_FORM}&${fields}`;
}

/**
 * Share one of your themes with everyone: what it is called and shows, then the theme saved as a
 * .zip, then GitHub's form, filled in, for the file to be dragged into. Two steps in one dialog.
 */
function ShareDialog({ item, close }) {
  const [fields, setFields] = useState({ name: item.name || item.id, author: kept(), description: "" });
  const [allowed, setAllowed] = useState(false);
  const [file, setFile] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (key) => (event) => setFields({ ...fields, [key]: event.currentTarget.value.slice(0, TEXT_MOST[key]) });
  const ready = fields.name.trim() && fields.description.trim() && allowed && !busy;
  const save = async () => {
    setBusy(true);
    keep(fields.author.trim());
    const saved = await saveZip(item, false);
    setBusy(false);
    if (saved) setFile(saved);
  };
  const size = item.size ? `${item.size[0]} × ${item.size[1]}` : "";
  if (file) {
    return html`<h2 class="dialog-title">${t("Now on GitHub")}</h2>
      <ol class="share-steps">
        <li class="done">${t("{file} is saved in your downloads.", { file })}</li>
        <li>${t("Open the form, drag {file} into “The theme” and send it. Sending needs a free GitHub account.", { file })}</li>
        <li>${t("A check runs by itself and answers on the form. After a look, the theme is among the community themes, for everyone.")}</li>
      </ol>
      <div class="dialog-actions">
        <button type="button" class="btn" onClick=${() => close(true)}>${t("Close")}</button>
        <a class="btn btn-primary" data-share-form href=${shareFormUrl({ ...fields, name: fields.name.trim(), author: fields.author.trim(), description: fields.description.trim() })}
          target="_blank" rel="noopener noreferrer">${t("Open the form")} <${Icon} name="external" size=${14} /></a>
      </div>`;
  }
  return html`<h2 class="dialog-title">${t("Share “{name}” with everyone", { name: item.name || item.id })}</h2>
    <p class="dialog-text">${t("It goes among the community themes: everyone with Libre Panel can install it with one click.")}
      ${size ? ` ${t("It shows for panels of {size}, as it is made.", { size })}` : ""}</p>
    <label class="share-field">${t("Name")}
      <input class="input" type="text" value=${fields.name} maxlength=${TEXT_MOST.name} onInput=${set("name")} />
    </label>
    <label class="share-field">${t("Made by")}
      <input class="input" type="text" value=${fields.author} maxlength=${TEXT_MOST.author} placeholder=${t("Your name or handle")} onInput=${set("author")} />
    </label>
    <label class="share-field">${t("What it shows")}
      <textarea class="input" rows="3" maxlength=${TEXT_MOST.description} placeholder=${t("A sentence or two")} onInput=${set("description")}>${fields.description}</textarea>
      <span class="share-count muted">${fields.description.length} / ${TEXT_MOST.description}</span>
    </label>
    <label class="share-allowed">
      <input type="checkbox" checked=${allowed} onChange=${(event) => setAllowed(event.currentTarget.checked)} />
      ${t("Every picture and font in it may be shared with everyone: they are my own, or their license allows it.")}
    </label>
    <div class="dialog-actions">
      <button type="button" class="btn" onClick=${() => close(false)}>${t("Cancel")}</button>
      <button type="button" class="btn btn-primary" disabled=${!ready} onClick=${save}>${busy ? t("Saving…") : t("Save the theme")}</button>
    </div>`;
}

export function shareWithEveryone(item) {
  return openDialog((close) => html`<${ShareDialog} item=${item} close=${close} />`, { label: t("Share with everyone") });
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
const same = (a, b) => Boolean(a && b) && a[0] === b[0] && a[1] === b[1];

/** Whether an entry of the community is made for `model`'s panel, either way round. */
export const fitsModel = (entry, model) =>
  !model || (entry.sizes || []).some((size) => same(size, model.landscape) || same(size, model.portrait));

/** A theme of the community: its picture, name, author, the panels it is made for (and whether
 * the chosen panel is one), what it is, and Install; once installed, Show on the panel. */
function CommunityCard({ entry, busy, fits, model, installed, onInstall, onShow }) {
  return html`<article class="theme-card community-card" data-community=${entry.id}>
    <div class="theme-card-main">
      <span class="theme-card-picture">
        <img src=${endpoints.communityPreviewUrl(entry.id)} alt="" loading="lazy" fetchpriority="low" decoding="async" />
      </span>
      <span class="theme-card-text">
        <span class="theme-card-title"><span class="theme-card-name">${entry.name}</span></span>
        <span class="theme-card-note">${sizesText(entry.sizes)}</span>
        ${model && fits
          ? html`<span class="theme-card-badge community-fits"><${Icon} name="check" size=${12} strokeWidth=${3} /> ${t("Fits {panel}", { panel: panelName(model) })}</span>`
          : null}
      </span>
      ${entry.author ? html`<span class="community-card-author muted">${t("by {author}", { author: entry.author })}</span>` : null}
      ${entry.description ? html`<p class="community-card-text">${entry.description}</p>` : null}
      ${installed
        ? html`<button type="button" class="btn btn-primary community-card-show" onClick=${() => onShow(installed)}>
            <${Icon} name="to-panel" size=${14} /> ${t("Show on the panel")}
          </button>`
        : html`<button type="button" class="btn community-card-install" disabled=${busy}
            aria-label=${t("Install “{name}”", { name: entry.name })} onClick=${() => onInstall(entry)}>
            <${Icon} name="download" size=${14} /> ${busy ? t("Installing…") : t("Install")}
          </button>`}
    </div>
  </article>`;
}

/**
 * "Community themes": closed until it is opened, and only then is the list asked for (the
 * server asks GitHub, and keeps the answer an hour). Without an answer it says so, quietly.
 * `onInstalled(id)` follows an install, with the id the theme has among yours.
 */
export function CommunityThemes({ model, onInstalled }) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState(null); // {themes, offline}; null while it is asked for
  const [busy, setBusy] = useState("");
  const [scope, setScope] = useState("fits"); // what the chosen panel takes, or every size
  const [installed, setInstalled] = useState({}); // entry id: its id among your themes
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
    if (model && !fitsModel(entry, model)) {
      const sure = await confirmDialog({
        title: t("Made for another panel"),
        message: t("“{name}” is made for {size}, and {panel} is {own}: it would be scaled to fit. Install it anyway?", {
          name: entry.name,
          size: sizesText(entry.sizes),
          panel: panelName(model),
          own: `${model.landscape[0]} × ${model.landscape[1]}`,
        }),
        confirm: t("Install anyway"),
      });
      if (!sure) return;
    }
    setBusy(entry.id);
    try {
      const done = await endpoints.installCommunityTheme(entry.id);
      await refreshThemes();
      added(done);
      setInstalled((now) => ({ ...now, [entry.id]: done.id }));
      onInstalled?.(done.id);
    } catch (error) {
      toastError(error);
    } finally {
      setBusy("");
    }
  };
  const all = list?.themes || [];
  const fitting = all.filter((entry) => fitsModel(entry, model));
  // the chosen panel's first; every size a click away, and all of them where none fits
  const themes = model && scope === "fits" && fitting.length ? fitting : all;
  const offline = html`<p class="community-note muted" role="status">${t("The community themes cannot be reached right now.")}</p>`;
  const body = !list
    ? html`<p class="community-note muted" role="status">${t("Looking for community themes…")}</p>`
    : !all.length
      ? list.offline
        ? offline
        : html`<p class="community-note muted">${t("No one has shared a theme here yet.")}</p>`
      : html`${list.offline ? offline : null}
          ${model && fitting.length && fitting.length < all.length
            ? html`<div class="community-scope"><${Segmented} label=${t("Which community themes")} value=${scope} onChange=${setScope}
                options=${[
                  { value: "fits", label: t("For {panel}", { panel: panelName(model) }) },
                  { value: "all", label: t("All sizes") },
                ]} /></div>`
            : null}
          ${model && !fitting.length
            ? html`<p class="community-note muted">${t("None is made for {panel} yet: these are for other panels.", { panel: panelName(model) })}</p>`
            : null}
          <div class="theme-grid" role="list" aria-label=${t("Community themes")}>
            ${themes.map(
              (entry) => html`<div role="listitem" key=${entry.id} class="theme-grid-item">
                <${CommunityCard} entry=${entry} busy=${busy === entry.id} fits=${fitsModel(entry, model)} model=${model}
                  installed=${installed[entry.id]} onInstall=${install} onShow=${showOnPanel} />
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
