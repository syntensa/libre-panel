// Settings → Panel → Check the colours (under the video experiment): whether the graphics card
// turns the panel's colours into video exactly as the processor does. One button runs the check
// (editor/colours.py: a test picture through every encoder that works here, the new feed and the
// old one, measured away from the panel); each encoder shows its two rows of swatches, the
// largest difference and the verdict, and the one thing to do comes with its button, which
// writes video.pixels. The test picture can also be shown on the panel chosen in the header, A
// and B in turn, for the eye.

import { endpoints } from "../../core/api.js";
import { usePolled } from "../../core/console.js";
import { t } from "../../core/i18n.js";
import { loadPanelInfo, saveSettings } from "../../core/settings.js";
import { useTarget } from "../../core/target.js";
import { html } from "../../lib/preact.js";
import { toastError } from "../../ui/toast.js";
import { Card, Status, copyText } from "./parts.js";

const ENCODER_NAMES = () => ({
  h264_amf: t("AMD (h264_amf)"),
  h264_nvenc: t("NVIDIA (h264_nvenc)"),
  h264_qsv: t("Intel (h264_qsv)"),
  libx264: t("x264 (processor)"),
});

const PATCH_NAMES = () => ({
  black: t("black"),
  "grey-32": t("grey {n}", { n: 32 }),
  "grey-80": t("grey {n}", { n: 80 }),
  "grey-128": t("grey {n}", { n: 128 }),
  "grey-176": t("grey {n}", { n: 176 }),
  "grey-224": t("grey {n}", { n: 224 }),
  white: t("white"),
  red: t("red"),
  green: t("green"),
  blue: t("blue"),
  accent: t("accent"),
  violet: t("violet"),
  orange: t("orange"),
  skin: t("skin tone"),
});

/** The verdict on an encoder in words: its state where it was not measured. */
function verdictText(result) {
  if (result.state === "unavailable") return t("Not available here");
  if (result.state === "refused") return t("Takes no four bytes a pixel: the processor converts for it");
  if (result.state === "failed") return t("Failed");
  return {
    identical: t("Identical"),
    level: t("Level shift: black and white differ (full against limited range)"),
    matrix: t("Matrix shift: the greys agree, saturated colours differ (BT.709 against BT.601)"),
    other: t("Other differences"),
  }[result.verdict] || result.verdict || "";
}

const tone = (result) =>
  result.verdict === "identical" ? "ok" : ["level", "matrix", "other", "failed"].includes(result.verdict || result.state) ? "warn" : "off";

const rgb = (values) => `rgb(${values.join(", ")})`;
const num = (value) => (Math.round(value * 10) / 10).toString();

/** A row of swatches: each patch as the panel shows it, drawn from what was measured. */
function Swatches({ label, side, patches }) {
  const names = PATCH_NAMES();
  return html`<div class="colour-row" data-side=${side}>
    <span class="colour-row-label">${label}</span>
    <div class="colour-swatches">
      ${patches.map((patch) => {
        const seen = patch[side];
        const title = seen
          ? `${names[patch.id] || patch.id}: Y ${num(seen.yuv[0])} U ${num(seen.yuv[1])} V ${num(seen.yuv[2])} → RGB ${seen.rgb.join(", ")}`
          : names[patch.id] || patch.id;
        return html`<span key=${patch.id} class=${`colour-swatch${seen ? "" : " none"}`} title=${title} style=${seen ? { background: rgb(seen.rgb) } : null}></span>`;
      })}
    </div>
  </div>`;
}

function EncoderResult({ result }) {
  const names = ENCODER_NAMES();
  const worst = result.worst;
  const feeds = result.feeds || {};
  const decoded = Object.values(feeds).map((feed) => feed.decoded).find((format) => format && format !== "yuv420p");
  return html`<div class="colour-encoder" data-encoder=${result.encoder} data-verdict=${result.verdict || result.state}>
    <div class="colour-encoder-head">
      <b>${names[result.encoder] || result.encoder}</b>
      <${Status} tone=${tone(result)}>${verdictText(result)}<//>
      ${worst && worst.patch
        ? html`<span class="colour-worst mono">${t("Largest difference: {n} in {channel} of {patch}", { n: num(worst.difference), channel: worst.channel, patch: PATCH_NAMES()[worst.patch] || worst.patch })}</span>`
        : worst
          ? html`<span class="colour-worst mono">${t("Largest difference: {n}", { n: num(worst.difference) })}</span>`
          : null}
    </div>
    ${result.patches
      ? html`<${Swatches} label=${t("New feed")} side="new" patches=${result.patches} />
          <${Swatches} label=${t("Old feed")} side="old" patches=${result.patches} />`
      : null}
    ${decoded ? html`<p class="colour-note muted">${t("The stream decodes to {format}, not yuv420p.", { format: decoded })}</p>` : null}
    ${result.error || result.said ? html`<p class="colour-note muted mono">${result.error || result.said}</p>` : null}
  </div>`;
}

/** The check as plain text, to paste into an issue: ids and numbers, in no language. */
function checkText(data) {
  const check = data.check;
  const picture = check.picture || {};
  const lines = [
    `Libre Panel colour check: ${(picture.size || []).join("x")} (${picture.model || "-"}), video.pixels ${data.pixels}`,
  ];
  if (data.in_use) lines.push(`panel ${data.panel}${data.in_use.running ? "" : " (would start)"}: ${data.in_use.encoder}, ${data.in_use.feed}`);
  if (data.auto?.picked) lines.push(`video.encoder ${data.auto.set}: ${data.auto.picked}`);
  for (const passed of data.auto?.passed || []) lines.push(`  passed over: ${passed.encoder} (${passed.said || "no picture"})`);
  for (const result of check.results) {
    const worst = result.worst?.patch ? ` (largest difference ${num(result.worst.difference)} in ${result.worst.channel} of ${result.worst.patch})` : "";
    const said = Object.entries(result.feeds || {})
      .map(([pixels, feed]) => `${pixels}: ${feed.pixels}${feed.card ? " on the card" : ""}${feed.decoded ? `, ${feed.decoded}` : ""}${feed.says ? ` (${feed.says})` : ""}${feed.error ? `, ${feed.error}` : ""}`)
      .join("; ");
    lines.push(`${result.encoder}: ${result.verdict || result.state}${worst}${said ? ` [${said}]` : ""}${result.said ? ` (${result.said})` : ""}`);
    for (const patch of result.patches || []) {
      const side = (seen) => (seen ? `${seen.yuv.map(num).join(" ")} -> ${seen.rgb.join(",")}` : "-");
      lines.push(`  ${patch.id.padEnd(9)} ${patch.rgb.join(",").padEnd(11)} new ${side(patch.new).padEnd(28)} old ${side(patch.old)}`);
    }
  }
  return lines.join("\n");
}

/** What the panel runs now, and with it the one thing to do. */
function Advice({ data, write }) {
  const names = ENCODER_NAMES();
  const use = data.in_use;
  const encoder = use ? names[use.encoder] || use.encoder : "";
  const result = use ? data.check.results.find((r) => r.encoder === use.encoder) : null;
  const shifted = result && ["level", "matrix", "other"].includes(result.verdict);
  let line = null;
  let button = null;
  if (data.advice === "shift") {
    line = t("The colours shift with {encoder}: the processor's conversion gives the colours of before.", { encoder });
    button = html`<button type="button" class="btn btn-primary" onClick=${() => write("rgb")}>${t("Use the processor's conversion")}</button>`;
  } else if (data.advice === "back") {
    line = shifted
      ? t("The panels use the processor's conversion, as the check advises for {encoder}.", { encoder })
      : t("The panels use the processor's conversion. The card's takes less of the processor.");
    button = html`<button type="button" class="btn" onClick=${() => write("auto")}>${t("Back to the card's conversion")}</button>`;
  } else if (data.advice === "nothing") {
    line = t("Nothing to do: the card converts as the processor does");
  } else if (data.advice === "processor") {
    line = t("Nothing to do: ffmpeg converts the colours on the processor for {encoder} either way.", { encoder });
  } else if (data.advice === "refused") {
    line = t("Nothing to do: {encoder} takes no four bytes a pixel here, so the processor converts for it by itself.", { encoder });
  }
  return line ? html`<div class="colour-advice" data-advice=${data.advice}><span>${line}</span>${button}</div>` : null;
}

function nowText(data) {
  const names = ENCODER_NAMES();
  const use = data.in_use;
  if (!use) return t("Panel {n} shows no video now.", { n: data.panel });
  const encoder = names[use.encoder] || use.encoder;
  if (!use.running) return t("Panel {n} shows no video now; it would start {encoder}.", { n: data.panel, encoder });
  return use.card
    ? t("Panel {n} now: {encoder}, its colours converted on the graphics card", { n: data.panel, encoder })
    : t("Panel {n} now: {encoder}, its colours converted on the processor", { n: data.panel, encoder });
}

/** What "auto" picks for the encoder, and each of the card's it passed over with why (the last
 * line ffmpeg said when the video mode's command was tried with it). */
function AutoPick({ auto }) {
  const names = ENCODER_NAMES();
  if (!auto || auto.set !== "auto" || !auto.picked) return null;
  return html`<div class="colour-auto">
    <p class="colour-pick">${t("Automatic picks {encoder}.", { encoder: names[auto.picked] || auto.picked })}</p>
    ${auto.passed.map(
      (passed) => html`<p class="colour-note muted" key=${passed.encoder} data-passed=${passed.encoder}>
        ${t("{encoder} was passed over:", { encoder: names[passed.encoder] || passed.encoder })}${" "}
        <span class="mono">${passed.said || t("no picture came from it")}</span>
      </p>`,
    )}
  </div>`;
}

/** The test picture on the panel for the eye: A and B in turn, then the theme again. */
function OnThePanel({ data, act }) {
  const show = data.show;
  if (show.running && show.panel) {
    const letter = show.letter === "A" ? t("A: the card's conversion") : t("B: the processor's conversion");
    return html`<div class="colour-ab" data-showing=${show.letter}>
      <button type="button" class="btn" onClick=${() => act("stop")}>${t("Stop showing it")}</button>
      <span>${t("On panel {n}: {letter} — {s} s to go", { n: show.panel, letter, s: show.left_s })}</span>
    </div>`;
  }
  const why = !data.app
    ? t("Showing it on the panel needs Libre Panel running in the background.")
    : !data.video
      ? t("Panel {n} shows no smooth motion now: the picture on the panel needs it.", { n: data.panel })
      : null;
  return html`<div class="colour-ab">
    <button type="button" class="btn" disabled=${Boolean(why)} onClick=${() => act("show")}>${t("Show it on the panel")}</button>
    <span class="muted">${why || t("For {s} seconds panel {n} shows the test picture, every {every} seconds the other way: A the card's conversion, B the processor's. Then its theme again.", { s: show.seconds, n: data.panel, every: show.switch_s })}</span>
    ${show.error ? html`<p class="experiment-run-error">${show.error}</p>` : null}
  </div>`;
}

export function ColourCard() {
  const panel = useTarget();
  const run = usePolled(() => endpoints.colourCheck(panel), 1000, { deps: [panel] });
  const data = run.data;
  const act = async (action) => {
    try {
      await endpoints.colourCheckAction(action, panel);
      run.reload();
    } catch (error) {
      toastError(error);
    }
  };
  const write = async (pixels) => {
    if (await saveSettings({ "video.pixels": pixels })) {
      loadPanelInfo();
      run.reload();
    }
  };
  const checking = Boolean(data?.check.running);
  return html`<${Card}
    id="panel-colour-check"
    title=${t("Check the colours")}
    text=${t("Whether the graphics card turns the panel's colours into video exactly as the processor does. The check sends a test picture through every encoder that works here, the new way and the old way, and measures what comes out; the panels go on as they are.")}
    action=${html`<button type="button" class="btn btn-primary" disabled=${checking || !data} onClick=${() => act("check")}>${checking ? t("Checking…") : t("Check the colours")}</button>`}
  >
    ${data
      ? html`<div class="colour-check">
          <p class="colour-now">${nowText(data)}</p>
          <${AutoPick} auto=${data.auto} />
          ${data.check.error ? html`<p class="experiment-run-error">${data.check.error}</p>` : null}
          ${data.check.results.map((result) => html`<${EncoderResult} key=${result.encoder} result=${result} />`)}
          ${checking ? html`<p class="muted">${t("Checking…")}</p>` : null}
          <${Advice} data=${data} write=${write} />
          ${data.check.results.length && !checking
            ? html`<button type="button" class="link-btn" onClick=${() => copyText(checkText(data))}>${t("Copy as text")}</button>`
            : null}
          <${OnThePanel} data=${data} act=${act} />
        </div>`
      : null}
  <//>`;
}
