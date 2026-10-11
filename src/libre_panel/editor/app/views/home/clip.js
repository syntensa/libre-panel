// The question about the panel's standby clip, on Start: the video mode needs the name of the clip
// the panel plays by itself (docs/CONFIGURATION.md, Video), which Libre Panel never makes up. So
// the app asks once, with the name the vendor app uses already in the field: "Use it" writes what
// is in the field, "Not now" writes only that the question was asked. Nothing is written before
// one of the two is pressed. The server says when to ask (/api/clip: a panel the video
// mode is proven on, ffmpeg found, no name yet, not asked before); the tray's line "Smooth
// motion: confirm the clip name…" opens Start at #/home/clip, where this card has the focus.

import { t } from "../../core/i18n.js";
import { answerClipQuestion, loadClipQuestion } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html, useEffect, useRef, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { toast } from "../../ui/toast.js";

/** The question, asked again when this is shown and when the panel the app drives changes. */
function useClipQuestion() {
  const target = useStore((s) => s.app.panel?.target);
  useEffect(() => {
    loadClipQuestion();
  }, [target]);
  return useStore((s) => s.clipQuestion);
}

/** The card, or nothing while there is nothing to ask. `focus`: the person came for it. */
export function ClipQuestion({ focus = false }) {
  const question = useClipQuestion();
  const running = useStore((s) => s.app.available && !s.app.quit); // the app takes a name in by itself
  const [draft, setDraft] = useState(null); // what is typed; until then the name offered
  const [busy, setBusy] = useState(false);
  const use = useRef(null);
  const asked = Boolean(question?.ask);
  useEffect(() => {
    if (focus && asked) use.current?.focus();
  }, [focus, asked]);
  if (!asked) return null;
  const name = (draft ?? question.name).trim();
  const answer = async (clip) => {
    setBusy(true);
    const written = await answerClipQuestion(clip);
    if (!written) setBusy(false);
    else if (clip) {
      toast(
        running
          ? t("Saved. The panel switches to smooth motion in a moment.")
          : t("Saved. Smooth motion starts after Libre Panel is started again."),
        { kind: "success" },
      );
    }
  };
  return html`<section class="clip-card" aria-labelledby="clip-card-title">
    <span class="clip-card-icon" aria-hidden="true"><${Icon} name="pulse" size=${22} /></span>
    <div class="clip-card-body">
      <h2 id="clip-card-title">${t("Smooth motion")}</h2>
      <p>${t("Smooth motion at up to 50 frames a second needs the name of the panel's own standby clip.")} ${t("The vendor app keeps it as {name}.", { name: question.name })}</p>
      <form
        class="clip-card-form"
        onSubmit=${(event) => {
          event.preventDefault();
          if (name && !busy) answer(name);
        }}
      >
        <input
          type="text"
          class="input mono"
          aria-label=${t("Name of the standby clip")}
          value=${draft ?? question.name}
          spellcheck="false"
          onInput=${(event) => setDraft(event.currentTarget.value)}
        />
        <button ref=${use} type="submit" class="btn btn-primary" disabled=${!name || busy}>${t("Use it")}</button>
        <button type="button" class="btn" disabled=${busy} onClick=${() => answer(null)}>${t("Not now")}</button>
      </form>
    </div>
  </section>`;
}
