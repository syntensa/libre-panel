// Start's question how black looks on a panel whose model nobody has compared yet: smooth motion
// runs on it, and its range is left to the model (/api/panel: `video.range_unseen`, and that of
// each panel beside it in `more_video`). A decoder that shows the video as full range puts a grey
// veil over limited range, and the other way round black is crushed. "Compare" shows both ways
// on that panel (Check the colours' A/B) and goes there, where the answer is written as its
// video_range; "Not now" keeps the card away for that panel. The panel's facts are what the
// health card on Start asks for.

import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { setPref, store, useStore } from "../../core/state.js";
import { html, useState } from "../../lib/preact.js";
import { navigate } from "../../shell/views.js";
import { toastError } from "../../ui/toast.js";

const DISMISSED = "libre-panel-range-dismissed";

function remembered() {
  try {
    const list = JSON.parse(localStorage.getItem(DISMISSED) || "[]");
    return Array.isArray(list) ? list : [];
  } catch {
    return []; // private window or blocked storage: asked again at the next start
  }
}

store.set({ rangeDismissed: remembered() });

/** The panels (1 the first) whose range Start asks for. */
export function rangeUnseen(info) {
  if (!info) return [];
  const first = info.video?.range_unseen ? [1] : [];
  return first.concat((info.more_video || []).filter((more) => more.range_unseen).map((more) => more.index + 2));
}

function dismiss(panel) {
  const list = [...new Set([...store.get().rangeDismissed, panel])];
  store.set({ rangeDismissed: list });
  try {
    localStorage.setItem(DISMISSED, JSON.stringify(list));
  } catch {
    // the choice lasts until the page closes
  }
}

export function RangeCard() {
  const { info, dismissed } = useStore((s) => ({ info: s.panelInfo, dismissed: s.rangeDismissed }));
  const [busy, setBusy] = useState(false);
  const panel = rangeUnseen(info).find((n) => !dismissed.includes(n));
  if (!panel) return null;
  const compare = async () => {
    setBusy(true);
    try {
      setPref("target", panel); // (the card of Check the colours is about that panel)
      await endpoints.colourCheckAction("range", panel);
      navigate("settings", "panel/panel-colour-check");
    } catch (error) {
      toastError(error);
    } finally {
      setBusy(false);
    }
  };
  return html`<aside class="hint-card range-card" role="status" data-panel=${panel}>
    <div class="hint-card-text">
      <strong>${t("Is black black on panel {n}?", { n: panel })}</strong>
      <p>${t("Smooth motion runs on panel {n}, a model whose black level nobody has compared yet. If its video looks washed out, as under a grey veil, compare the two ways on the panel once: the one that looks right is kept.", { n: panel })}</p>
    </div>
    <div class="range-card-actions">
      <button type="button" class="btn" disabled=${busy} onClick=${compare}>${t("Compare on the panel")}</button>
      <button type="button" class="link-btn" onClick=${() => dismiss(panel)}>${t("Not now")}</button>
    </div>
  </aside>`;
}
