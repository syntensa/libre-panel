// Settings → Panel → Check the colours (under the video experiment): who turns the panel's
// colours into video, and why. With video.pixels "auto" the graphics card does where a check
// found it converting exactly as the processor does, else the processor (devices/
// card_choice.py: the check runs once by itself in the background); the card says which, when
// it was checked, and offers to check again. One button runs the check here (editor/colours.py:
// a test picture through every encoder that works here, converted by the card and by the
// processor, measured away from the panel, in the range the panel gets); each encoder shows its
// two rows of swatches, the largest difference and the verdict. The test picture can also be
// shown on the panel chosen in the header, A and B in turn, for the eye: the card's conversion
// against the processor's, or limited against full range, where the answer ("A looks right")
// is written as that panel's video_range.

import { endpoints } from "../../core/api.js";
import { usePolled } from "../../core/console.js";
import { language, t } from "../../core/i18n.js";
import { loadPanelInfo, loadSettings, saveSettings } from "../../core/settings.js";
import { useTarget } from "../../core/target.js";
import { store } from "../../core/state.js";
import { html, useState } from "../../lib/preact.js";
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
  if (result.state === "refused") return t("Gives no picture converting itself: the processor converts for it");
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
const dayOf = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleDateString(language()) : "");
const SHIFT_WORDS = () => ({ level: t("level shift"), matrix: t("matrix shift"), other: t("other differences") });
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
      ? (result.compared || [])[0] === "card"
        ? html`<${Swatches} label=${t("Graphics card")} side="new" patches=${result.patches} />
            <${Swatches} label=${t("Processor")} side="old" patches=${result.patches} />`
        : html`<${Swatches} label=${t("Four bytes a pixel")} side="new" patches=${result.patches} />
            <${Swatches} label=${t("Three bytes a pixel")} side="old" patches=${result.patches} />`
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
  if (data.in_use) lines.push(`panel ${data.panel}${data.in_use.running ? "" : " (would start)"}: ${data.in_use.encoder}, ${data.in_use.feed}${data.in_use.card ? ", card" : ""}`);
  const choice = data.choice || {};
  if (choice.encoder) {
    const when = choice.when ? `, checked ${new Date(choice.when * 1000).toISOString().slice(0, 10)}${choice.by ? ` (${choice.by})` : ""}` : "";
    lines.push(`who converts: ${choice.way} (${choice.why}${choice.verdict ? `, ${choice.verdict}` : ""}${when}); ${choice.full ? "full" : "limited"} range (video_range ${choice.range})`);
  }
  if (picture.full != null) lines.push(`measured for ${picture.full ? "full" : "limited"} range`);
  if (check.saving_ms != null) lines.push(`the processor's conversion: ${check.saving_ms} ms a frame`);
  if (data.auto?.picked) lines.push(`video.encoder ${data.auto.set}: ${data.auto.picked}`);
  for (const passed of data.auto?.passed || []) lines.push(`  passed over: ${passed.encoder} (${passed.said || "no picture"})`);
  for (const result of check.results) {
    const worst = result.worst?.patch ? ` (largest difference ${num(result.worst.difference)} in ${result.worst.channel} of ${result.worst.patch})` : "";
    const said = Object.entries(result.feeds || {})
      .map(([pixels, feed]) => `${pixels}: ${feed.pixels}${feed.card ? " on the card" : ""}${feed.decoded ? `, ${feed.decoded}` : ""}${feed.says ? ` (${feed.says})` : ""}${feed.error ? `, ${feed.error}` : ""}`)
      .join("; ");
    const [first, second] = result.compared || ["new", "old"];
    lines.push(`${result.encoder}: ${result.verdict || result.state}${worst}${said ? ` [${said}]` : ""}${result.said ? ` (${result.said})` : ""}`);
    for (const patch of result.patches || []) {
      const side = (seen) => (seen ? `${seen.yuv.map(num).join(" ")} -> ${seen.rgb.join(",")}` : "-");
      lines.push(`  ${patch.id.padEnd(9)} ${patch.rgb.join(",").padEnd(11)} ${first} ${side(patch.new).padEnd(28)} ${second} ${side(patch.old)}`);
    }
  }
  return lines.join("\n");
}

/** Who converts the colours for the encoder the panel runs, and why: the automatic choice
 * (the card where a check found it identical, with the day), or video.pixels forcing a way, with
 * the way back to the automatic choice. */
function Choice({ data, act, write }) {
  const names = ENCODER_NAMES();
  const choice = data.choice;
  if (!choice || !choice.encoder) return null;
  const encoder = names[choice.encoder] || choice.encoder;
  const verdict = SHIFT_WORDS()[choice.verdict] || "";
  const worst = choice.worst;
  const date = dayOf(choice.when);
  const checking = Boolean(data.check.running);
  const again = html`<button type="button" class="btn" disabled=${checking} onClick=${() => act("check")}>${t("Check again")}</button>`;
  const back = html`<button type="button" class="btn" onClick=${() => write("auto")}>${t("Back to the automatic choice")}</button>`;
  let line = "";
  let note = null;
  let button = null;
  if (choice.why === "identical") {
    line = t("The graphics card converts the colours: checked identical on {date}.", { date });
    const saving = data.check.saving_ms;
    if (saving != null && saving >= 0.5) note = t("It saves the processor about {n} ms a frame.", { n: Math.round(saving) });
    button = again;
  } else if (choice.why === "differs") {
    line = t("The processor converts the colours: the card's differ ({verdict}, checked on {date}).", { verdict, date });
    if (worst && worst.patch) note = t("Largest difference: {n} in {channel} of {patch}", { n: num(worst.difference), channel: worst.channel, patch: PATCH_NAMES()[worst.patch] || worst.patch });
    button = again;
  } else if (choice.why === "unchecked") {
    line = choice.checking
      ? t("The processor converts the colours while the card's conversion is checked in the background.")
      : t("The processor converts the colours: the card's conversion has not been checked yet.");
  } else if (choice.why === "refused") {
    line = t("The processor converts the colours: {encoder} gives no picture converting them itself.", { encoder });
    if (data.pixels === "card") button = back;
  } else if (choice.why === "limited-only") {
    line = t("The processor converts the colours: {encoder} makes limited range only, and this panel gets full range.", { encoder });
    if (data.pixels === "card") button = back;
  } else if (choice.why === "x264") {
    line = t("The processor converts the colours for {encoder}.", { encoder });
    if (data.pixels !== "auto") button = back;
  } else if (choice.why === "card") {
    line = t('The graphics card converts the colours: video.pixels is "card".');
    if (verdict) note = t("The check found the card's colours differing ({verdict}).", { verdict });
    button = back;
  } else if (choice.why === "rgb") {
    line = t('The processor converts the colours, fed three bytes a pixel: video.pixels is "rgb".');
    button = back;
  }
  return line
    ? html`<div class="colour-advice" data-why=${choice.why}>
        <span>${line}${note ? html`<br /><span class="muted">${note}</span>` : null}</span>${button}
      </div>`
    : null;
}

/** The range the panel gets its video in, and the A/B of limited against full range on it: the
 * person's answer is written as that panel's video_range. */
function Range({ data, act, writeRange, answered }) {
  const choice = data.choice || {};
  const show = data.show;
  const ranges = { limited: t("A: limited range (16-235)"), full: t("B: full range (0-255)") };
  const now = choice.full ? t("Panel {n} gets its video in full range (0-255).", { n: data.panel }) : t("Panel {n} gets its video in limited range (16-235).", { n: data.panel });
  const how = choice.range === "auto" ? t("Automatic: as its model is known to show video.") : t("Set for this panel.");
  const ours = show.kind === "range" && show.of === data.panel && !answered;
  const answer = ours && (show.running || show.ended)
    ? html`<span class="colour-range-answer">
        <button type="button" class="btn" onClick=${() => writeRange("limited")}>${t("A looks right")}</button>
        <button type="button" class="btn" onClick=${() => writeRange("full")}>${t("B looks right")}</button>
      </span>`
    : null;
  let control;
  if (show.running && show.kind === "range" && show.panel) {
    control = html`<div class="colour-ab" data-showing=${show.letter} data-kind="range">
      <button type="button" class="btn" onClick=${() => act("stop")}>${t("Stop showing it")}</button>
      <span>${t("On panel {n}: {letter} — {s} s to go", { n: show.panel, letter: ranges[show.value] || show.letter, s: show.left_s })}</span>
      ${answer}
    </div>`;
  } else {
    const why = !data.app
      ? t("Showing it on the panel needs Libre Panel running in the background.")
      : !data.video
        ? t("Panel {n} shows no smooth motion now: the picture on the panel needs it.", { n: data.panel })
        : null;
    control = html`<div class="colour-ab" data-kind="range">
      <button type="button" class="btn" disabled=${Boolean(why) || show.running} onClick=${() => act("range")}>${t("Is black too light on the panel? Compare")}</button>
      <span class="muted">${why || t("For {s} seconds panel {n} shows the test picture, every {every} seconds the other way: A in limited range, B in full range. Black should look black and the greys apart; say which looks right.", { s: show.seconds, n: data.panel, every: show.switch_s })}</span>
      ${answer}
    </div>`;
  }
  return html`<div class="colour-range" data-range=${choice.full ? "full" : "limited"}>
    <p class="colour-range-now">${now} <span class="muted">${how}</span></p>
    ${control}
  </div>`;
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
  if (show.running && show.panel && show.kind !== "range") {
    const letter = show.letter === "A" ? t("A: the card's conversion") : t("B: the processor's conversion");
    return html`<div class="colour-ab" data-showing=${show.letter}>
      <button type="button" class="btn" onClick=${() => act("stop")}>${t("Stop showing it")}</button>
      <span>${t("On panel {n}: {letter} — {s} s to go", { n: show.panel, letter, s: show.left_s })}</span>
    </div>`;
  }
  const names = ENCODER_NAMES();
  const why = {
    app: t("Showing it on the panel needs Libre Panel running in the background."),
    video: t("Panel {n} shows no smooth motion now: the picture on the panel needs it.", { n: data.panel }),
    x264: t("Panel {n} runs {encoder}: the processor converts for it either way, so A and B would be the same.", { n: data.panel, encoder: names[data.in_use?.encoder] || data.in_use?.encoder || "" }),
    "limited-only": t("Panel {n} gets full range, which {encoder} makes on the processor only, so A and B would be the same.", { n: data.panel, encoder: names[data.in_use?.encoder] || data.in_use?.encoder || "" }),
  }[data.ab] || null;
  return html`<div class="colour-ab">
    <button type="button" class="btn" disabled=${Boolean(why) || show.running} onClick=${() => act("show")}>${t("Show it on the panel")}</button>
    <span class="muted">${why || t("For {s} seconds panel {n} shows the test picture, every {every} seconds the other way: A the card's conversion, B the processor's. Then its theme again.", { s: show.seconds, n: data.panel, every: show.switch_s })}</span>
    ${show.error ? html`<p class="experiment-run-error">${show.error}</p>` : null}
  </div>`;
}

export function ColourCard() {
  const panel = useTarget();
  const run = usePolled(() => endpoints.colourCheck(panel), 1000, { deps: [panel] });
  const data = run.data;
  const [answered, setAnswered] = useState(false); // the range's A/B was answered
  const act = async (action) => {
    if (action === "range") setAnswered(false);
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
  // the answer to the range's A/B, as that panel's video_range ([device] for the first, its
  // [[panels]] table for one beside it); the A/B ends, the panel shows its theme again
  const writeRange = async (range) => {
    if (panel > 1 && !store.get().settings?.config) await loadSettings();
    const changes =
      panel > 1
        ? (config) => ({ panels: (config?.panels || []).map((table, n) => (n === panel - 2 ? { ...table, video_range: range } : table)) })
        : { "device.video_range": range };
    if (await saveSettings(changes)) {
      setAnswered(true);
      if (data?.show.running) await act("stop");
      loadPanelInfo();
      run.reload();
    }
  };
  const checking = Boolean(data?.check.running);
  return html`<${Card}
    id="panel-colour-check"
    title=${t("Check the colours")}
    text=${t("Who turns the panel's colours into video. The graphics card does where a check found it converting exactly as the processor does (it saves the processor work), else the processor; the first check runs by itself in the background. Here the check sends a test picture through every encoder that works here, converted by the card and by the processor, and measures what comes out; the panels go on as they are.")}
    action=${html`<button type="button" class="btn btn-primary" disabled=${checking || !data} onClick=${() => act("check")}>${checking ? t("Checking…") : t("Check the colours")}</button>`}
  >
    ${data
      ? html`<div class="colour-check">
          <p class="colour-now">${nowText(data)}</p>
          <${AutoPick} auto=${data.auto} />
          <${Choice} data=${data} act=${act} write=${write} />
          ${data.check.error ? html`<p class="experiment-run-error">${data.check.error}</p>` : null}
          ${data.check.results.length && data.check.picture?.full != null
            ? html`<p class="colour-note muted">${data.check.picture.full ? t("Measured for full range (0-255), as panel {n} gets its video.", { n: data.check.picture.panel }) : t("Measured for limited range (16-235), as panel {n} gets its video.", { n: data.check.picture.panel })}</p>`
            : null}
          ${data.check.results.map((result) => html`<${EncoderResult} key=${result.encoder} result=${result} />`)}
          ${checking ? html`<p class="muted">${t("Checking…")}</p>` : null}
          ${data.check.results.length && !checking
            ? html`<button type="button" class="link-btn" onClick=${() => copyText(checkText(data))}>${t("Copy as text")}</button>`
            : null}
          <${OnThePanel} data=${data} act=${act} />
          <${Range} data=${data} act=${act} writeRange=${writeRange} answered=${answered} />
        </div>`
      : null}
  <//>`;
}
