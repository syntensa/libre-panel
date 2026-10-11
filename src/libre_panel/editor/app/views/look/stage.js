// Look's stage: the open theme drawn by the panel's own renderer (core/preview.js follows every
// change), as large as the room allows, with a few things to look at it by: the preview with
// this computer's sensors, and a test notice drawn in the theme's own style.

import { post } from "../../core/api.js";
import { control, usePolled } from "../../core/console.js";
import { t } from "../../core/i18n.js";
import { setPref, store, useStore } from "../../core/state.js";
import { html, useEffect, useLayoutEffect, useRef, useState } from "../../lib/preact.js";
import { currentModel } from "../../shell/panel-popover.js";
import { Icon } from "../../ui/icons.js";
import { Menu, Popover } from "../../ui/layer.js";
import { toastError } from "../../ui/toast.js";

const ROOM = { x: 48, top: 40, bottom: 110 }; // the room around the picture: its frame and the buttons
const NOTICE_MS = 5000; // how long a test notice stays on the stage

/** The preview with a test notice on it (the server draws it in the theme's toast style). */
function useNotice(rev) {
  const [picture, setPicture] = useState(null);
  useEffect(() => setPicture(null), [rev]); // a change of the theme shows its own preview again
  useEffect(() => {
    if (!picture) return undefined;
    const timer = setTimeout(() => setPicture(null), NOTICE_MS);
    return () => clearTimeout(timer);
  }, [picture]);
  const show = async (kind) => {
    const { doc, prefs } = store.get();
    try {
      const data = await post("/api/render", { theme: doc.theme, base: doc.id, live: prefs.live, notice: kind });
      setPicture(`data:image/png;base64,${data.png}`);
    } catch (error) {
      toastError(error);
    }
  };
  return [picture, show];
}

function NoticeButton({ onShow }) {
  const kinds = usePolled(control.probes, 600000).data?.probes || [];
  const [open, setOpen] = useState(false);
  const anchor = useRef(null);
  if (!kinds.length) return null;
  return html`<button ref=${anchor} type="button" class="btn" aria-haspopup="menu" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>
      <${Icon} name="info" size=${15} /> ${t("Show a notice")} <${Icon} name="chevron-down" size=${13} />
    </button>
    <${Popover} anchor=${anchor} open=${open} onClose=${() => setOpen(false)} placement="top-start" label=${t("Show a notice")} width=${220}>
      <${Menu} label=${t("Show a notice")} onClose=${() => setOpen(false)}
        items=${kinds.map((probe) => ({ label: probe.label, icon: "info", run: () => onShow(probe.kind) }))} />
    <//>`;
}

export function Stage() {
  const { render, renderError, live, rev } = useStore((s) => ({ render: s.render, renderError: s.renderError, live: s.prefs.live, rev: s.rev }));
  const stage = useRef(null);
  const [area, setArea] = useState([900, 600]);
  const [notice, showNotice] = useNotice(rev);
  useLayoutEffect(() => {
    const watcher = new ResizeObserver(([entry]) => setArea([entry.contentRect.width, entry.contentRect.height]));
    watcher.observe(stage.current);
    return () => watcher.disconnect();
  }, []);
  const [width, height] = render.size;
  const scale = Math.max(0.1, Math.min((area[0] - 2 * ROOM.x - 22) / width, (area[1] - ROOM.top - ROOM.bottom - 22) / height, 3));
  const round = currentModel()?.shape === "round";
  const picture = notice || render.png;
  return html`<main class="look-stage" ref=${stage} aria-label=${t("Preview of the panel")}>
    <div class="look-frame">
      <div class=${`look-picture${round ? " round" : ""}${scale >= 2 ? " pixelated" : ""}`}
        style=${{ width: `${Math.round(width * scale)}px`, height: `${Math.round(height * scale)}px` }}>
        ${picture ? html`<img src=${picture} alt=${t("Preview of the panel")} draggable="false" />` : html`<span class="spinner"></span>`}
      </div>
    </div>
    <div class="look-stage-actions">
      <button type="button" class="btn" role="switch" aria-checked=${String(live)} title=${t("the preview with this computer's sensors")}
        onClick=${() => setPref("live", !live)}>
        <${Icon} name="pulse" size=${15} /> ${t("Live values")}
      </button>
      <${NoticeButton} onShow=${showNotice} />
    </div>
    ${renderError ? html`<p class="look-stage-error" role="alert"><${Icon} name="warning" size=${14} /> ${renderError}</p>` : null}
  </main>`;
}
