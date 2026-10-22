// Settings → Panel → Start screen: the panel's own two screens in Libre Panel's look, made for
// its size: the boot logo it shows when it is switched on, and the standby clip it plays when
// the PC starts, until Libre Panel shows themes (devices/panel_files.py). Each is written only
// on a click, after a question; the panel's themes are paused for it, and the panel then shows
// what was written until "Show the themes again".

import { appAction } from "../../core/app.js";
import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { useMounted } from "../../core/orientation.js";
import { useConfig } from "../../core/settings.js";
import { useTarget } from "../../core/target.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Select } from "../../ui/controls.js";
import { confirmDialog } from "../../ui/dialog.js";
import { Icon } from "../../ui/icons.js";
import { toast, toastError } from "../../ui/toast.js";
import { Card, Note } from "./parts.js";

const kilobytes = (bytes) => `${Math.max(1, Math.round(bytes / 1024))} KB`;

function Tile({ title, text, picture, upright, children }) {
  return html`<div class=${`boot-tile${upright ? " upright" : ""}`}>
    <div class="boot-picture"><img src=${picture} alt=${title} decoding="async" /></div>
    <div class="boot-tile-text">
      <strong>${title}</strong>
      <span class="muted">${text}</span>
    </div>
    <div class="boot-actions">${children}</div>
  </div>`;
}

export function StartScreenCard() {
  const config = useConfig(); // (none while config.toml cannot be read: nothing here is written then)
  const way = useMounted() === "portrait" ? "portrait" : "landscape"; // drawn upright on a bar on end
  const panel = useTarget(); // the panel chosen in the header: its own logo and clip
  const [info, setInfo] = useState(null);
  const [model, setModel] = useState(null);
  const [busy, setBusy] = useState("");
  const [held, setHeld] = useState(false); // the themes are paused for what the panel shows
  const [found, setFound] = useState(null); // what "What is on the panel" read
  useEffect(() => {
    setFound(null);
    endpoints
      .startScreen(panel)
      .then((data) => {
        setInfo(data);
        setModel(data.model || data.models.find((item) => item.measured)?.id || data.models[0]?.id);
      })
      .catch(() => setInfo({ models: [] }));
  }, [panel]);
  if (!config || !info?.models?.length || !model) return null;
  const chosen = info.models.find((item) => item.id === model);

  const run = async (action, question) => {
    if (question && !(await confirmDialog(question))) return;
    setBusy(action);
    try {
      const answer = await endpoints.startScreenAction(action, model, panel);
      if (info.runs_panel) setHeld(true);
      if (action === "state") setFound(answer);
      else if (action === "logo") toast(t("The boot logo is on the panel ({size}); it shows at the next power-on.", { size: kilobytes(answer.boot_logo) }), { kind: "success" });
      else if (action === "remove-logo") toast(t("The factory logo shows again at the next power-on."), { kind: "success" });
      else toast(t("The standby clip is on the panel ({size}) and plays now; the panel starts with it.", { size: kilobytes(answer.standby_clip) }), { kind: "success" });
    } catch (error) {
      if (info.runs_panel) setHeld(true);
      toastError(error);
    } finally {
      setBusy("");
    }
  };
  const resume = async () => {
    await appAction("resume");
    setHeld(false);
  };
  const writing = Boolean(busy);
  const more = (config.panels || []).length > 0; // more panels: this card is the one chosen at the top
  return html`<${Card} id="start-screen"
    title=${more ? `${t("Start screen")} · ${t("Panel {number}", { number: panel })}` : t("Start screen")}
    text=${t("The panel's own screens, in Libre Panel's look and made for its size: the logo it shows when it is switched on, and the clip it plays when the PC starts, until Libre Panel shows its themes.")}>
    ${more ? html`<${Note}>${t("For the panel chosen at the top; choose another there for its own logo and clip.")}<//>` : null}
    <div class="boot-body">
    <div class="boot-panel">
      <span class="muted">${t("Made for")}</span>
      <${Select} label=${t("Panel")} value=${model} onChange=${(id) => { setModel(id); setFound(null); }}
        options=${info.models.map((item) => {
          const [w, h] = way === (info.orientation || "landscape") ? item.size : [item.size[1], item.size[0]];
          return { value: item.id, label: `${item.label} · ${w} × ${h}` };
        })} />
    </div>
    ${chosen && !chosen.measured
      ? html`<${Note}>${t("Measured on the 9.2\"; this panel has the same firmware, but writing to it has not been tried yet.")}<//>`
      : null}
    ${held
      ? html`<${Note} action=${html`<button type="button" class="btn btn-primary" onClick=${resume}>${t("Show the themes again")}</button>`}>
          ${t("The panel shows what was written; its themes are paused.")}
        <//>`
      : null}
    <div class="boot-tiles">
      <${Tile} title=${t("Boot logo")} text=${t("When the panel is switched on. The factory logo stays on the panel and can always come back.")}
        picture=${endpoints.startScreenPicture(model, "logo", 960, way, panel)} upright=${way === "portrait"}>
        <button type="button" class="btn btn-primary" disabled=${writing}
          onClick=${() => run("logo", {
            title: t("Write the boot logo to the panel?"),
            message: t("It shows when the panel is switched on. The factory logo is not touched: \"Factory logo again\" brings it back."),
            confirm: t("Write it"),
          })}>
          ${busy === "logo" ? t("Writing…") : t("Put on the panel")}
        </button>
        <button type="button" class="btn" disabled=${writing}
          onClick=${() => run("remove-logo", { title: t("Bring back the factory logo?"), message: t("Libre Panel's boot logo is taken off the panel."), confirm: t("Factory logo again") })}>
          ${t("Factory logo again")}
        </button>
        <a class="btn" href=${endpoints.startScreenFile(model, "logo", way, panel)} download><${Icon} name="download" size=${15} /> JPEG</a>
      <//>
      <${Tile} title=${t("Standby clip")} text=${t("When the PC starts, until Libre Panel shows its themes: a light that runs out from the middle.")}
        picture=${endpoints.startScreenPicture(model, "clip", 960, way, panel)} upright=${way === "portrait"}>
        <button type="button" class="btn btn-primary" disabled=${writing}
          onClick=${() => run("clip", {
            title: t("Write the standby clip to the panel?"),
            message: t("It replaces the clip the panel plays when the PC starts ({name}); the one there now cannot be read back first. The panel is set to start with it.", { name: info.names.clip }),
            confirm: t("Write it"),
            danger: true,
          })}>
          ${busy === "clip" ? t("Writing…") : t("Put on the panel")}
        </button>
        <a class="btn" href=${endpoints.startScreenFile(model, "clip", way, panel)} download><${Icon} name="download" size=${15} /> H.264</a>
      <//>
    </div>
    <div class="boot-foot">
      <button type="button" class="btn" disabled=${writing} onClick=${() => run("state")}>
        ${busy === "state" ? t("Asking the panel…") : t("What is on the panel")}
      </button>
      ${found
        ? html`<span class="muted">
            ${t("Boot logo: {logo} · standby clip: {clip}", {
              logo: found.boot_logo ? kilobytes(found.boot_logo) : t("the factory's"),
              clip: found.standby_clip ? kilobytes(found.standby_clip) : t("none"),
            })}
            ${found.files?.length ? html` · ${found.files.join(", ")}` : null}
          </span>`
        : null}
    </div>
    </div>
  <//>`;
}
