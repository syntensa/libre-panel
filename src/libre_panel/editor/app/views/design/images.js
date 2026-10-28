// The pictures and fonts in the theme's folder: drag a picture onto the panel,
// add new ones. A picture may move: an animated GIF, or a video (MP4, WebM, MOV) that plays
// round and round (render/moving.py). A theme keeps its files in its own folder, so it needs
// a name of its own first.

import { refreshAssets, saveAs, uploadAsset } from "../../core/actions.js";
import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { useStore } from "../../core/state.js";
import { html, useEffect } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { dropItem, startLibraryDrag } from "./canvas.js";

export function Images() {
  const { id, builtin, assets } = useStore((s) => ({ id: s.doc?.id, builtin: s.doc?.builtin, assets: s.doc?.assets || [] }));
  useEffect(() => {
    refreshAssets();
  }, [id]);
  const pictures = assets.filter((path) => /\.(png|jpe?g|gif|webp|mp4|webm|mov|m4v|mkv)$/i.test(path));
  const video = (path) => /\.(mp4|webm|mov|m4v|mkv)$/i.test(path);
  const fonts = assets.filter((path) => /\.(ttf|otf)$/i.test(path));
  const own = id && !builtin;
  return html`<div class="side-scroll">
    <h2 class="side-title">${t("Pictures")}</h2>
    ${own
      ? null
      : html`<div class="notice">
          <span>${t("Save the theme under your own name (Save as…) before adding files.")}</span>
          <button type="button" class="btn" onClick=${saveAs}>${t("Save as…")}</button>
        </div>`}
    ${pictures.length
      ? html`<div class="tile-grid">
          ${pictures.map((path) => {
            const src = endpoints.assetUrl(id, path);
            const item = { kind: "picture", path };
            return html`<button type="button" key=${path} class="tile" title=${t("Click to add it, or drag it onto the panel")}
              onPointerDown=${(event) => startLibraryDrag(event, { ...item, label: path }, src)}
              onClick=${(event) => event.detail === 0 && dropItem(item, null)}>
              <span class="tile-picture checker">
                ${video(path)
                  ? html`<video src=${src} muted playsinline autoplay loop preload="metadata" draggable="false" data-video-tile></video>`
                  : html`<img src=${src} alt="" draggable="false" loading="lazy" />`}
              </span>
              <span class="tile-name">${path.replace(/^assets\//, "")}</span>
            </button>`;
          })}
        </div>`
      : html`<p class="side-hint">${t("No pictures in this theme yet.")}</p>`}
    <button type="button" class="btn btn-dashed" disabled=${!own} onClick=${() => uploadAsset(".png,.jpg,.jpeg,.gif,.webp,.mp4,.webm,.mov,.m4v,.mkv")}>
      <${Icon} name="upload" size=${15} /> ${t("Add a picture…")}
    </button>
    <p class="side-hint">${t("An animated GIF moves, and so does a video (MP4, WebM, MOV): round and round, on a panel that is sent every frame at full speed.")}</p>
    <h2 class="side-title">${t("Fonts")}</h2>
    ${fonts.length
      ? html`<ul class="font-list">${fonts.map((path) => html`<li key=${path}><${Icon} name="font" size=${15} /><span>${path.replace(/^assets\//, "")}</span></li>`)}</ul>`
      : html`<p class="side-hint">${t("The built-in fonts are always there; add your own here.")}</p>`}
    <button type="button" class="btn btn-dashed" disabled=${!own} onClick=${() => uploadAsset(".ttf,.otf")}>
      <${Icon} name="upload" size=${15} /> ${t("Add a font…")}
    </button>
  </div>`;
}
