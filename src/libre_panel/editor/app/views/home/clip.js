// The question about the panel's standby clip, on Start: the video mode needs the name of the clip
// the panel plays by itself (docs/CONFIGURATION.md, Video), which Libre Panel never makes up. So
// the app asks once, in plain words, offering the name the vendor app uses: "Turn on" writes it
// (or what is typed after "The clip has another name…"), "Not now" writes only that the
// question was asked. Nothing is written before
// one of the two is pressed. The server says when to ask (/api/clip: a panel the video
// mode is proven on, ffmpeg found, no name yet, not asked before); the tray's line "Smooth
// motion: confirm the clip name…" opens Start at #/home/clip, where this card has the focus.
// A first start asks how the panel should look first: the card comes after that choice, on the
// launcher right under the panel's card, as the next step (unless the tray's line brought the
// person to it).

import { t } from "../../core/i18n.js";
import { answerClipQuestion, loadClipQuestion } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html, useEffect, useRef, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { toast } from "../../ui/toast.js";

/** The question, asked again when this is shown and when the panel the app drives changes. A
 * first start asks it too, for the tray's line, but shows it only to who came for it. */
export function useClipQuestion() {
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
  const [editing, setEditing] = useState(false); // the name in a field, for a clip named otherwise
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
      <h2 id="clip-card-title">${t("Turn on smooth motion")}</h2>
      <p>${t("Your panel can show Libre Panel as video, at up to 50 frames a second: numbers that count, rings that glide. For that Libre Panel needs the name of the clip the panel plays by itself when the PC starts; nothing on the panel is changed.")}</p>
      <form
        class="clip-card-form"
        onSubmit=${(event) => {
          event.preventDefault();
          if (name && !busy) answer(name);
        }}
      >
        <button ref=${use} type="submit" class="btn btn-primary" disabled=${!name || busy}>${t("Turn on")}</button>
        <button type="button" class="btn" disabled=${busy} onClick=${() => answer(null)}>${t("Not now")}</button>
        ${editing
          ? html`<input
              type="text"
              class="input mono"
              aria-label=${t("Name of the standby clip")}
              value=${draft ?? question.name}
              spellcheck="false"
              onInput=${(event) => setDraft(event.currentTarget.value)}
            />`
          : html`<button type="button" class="link-btn" onClick=${() => setEditing(true)}>
              ${t("The clip has another name ({name})…", { name: question.name })}
            </button>`}
      </form>
    </div>
  </section>`;
}
