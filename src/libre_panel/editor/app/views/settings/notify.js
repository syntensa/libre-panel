// Settings → Game mode and more → Notices from other programs: the switch (config.toml's allow_notify),
// then the command and the address that show a notice, with the key they carry
// (/api/notify-key, notify.py), a new key, and a try on the panel.

import { get, post } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { saveSettings, useConfig } from "../../core/settings.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Toggle } from "../../ui/controls.js";
import { confirmDialog } from "../../ui/dialog.js";
import { toast, toastError } from "../../ui/toast.js";
import { Card, SettingRow, copyText } from "./parts.js";

/** Show the address's notice on the panel, as a Stream Deck button would. */
async function tryIt(address) {
  try {
    const response = await fetch(address);
    const answer = await response.json();
    if (!response.ok) throw new Error(answer.error || response.statusText);
    toast(answer.shown ? t("Shown on the panel.") : t("Not shown: {why}.", { why: answer.reason }), {
      kind: answer.shown ? "success" : "warn",
    });
  } catch (error) {
    toastError(error);
  }
}

export function NotifyCard() {
  const config = useConfig();
  const on = Boolean(config?.allow_notify);
  const [info, setInfo] = useState(null);
  useEffect(() => {
    if (on) get("/api/notify-key").then(setInfo).catch(toastError);
    else setInfo(null);
  }, [on]);
  if (!config) return null;
  const renew = async () => {
    const sure = await confirmDialog({
      title: t("Make a new key?"),
      message: t("Buttons and scripts that carry the old key stop working until they get the new address."),
      confirm: t("New key"),
    });
    if (!sure) return;
    try {
      setInfo(await post("/api/notify-key", {}));
      toast(t("A new key is made."), { kind: "success" });
    } catch (error) {
      toastError(error);
    }
  };
  return html`<${Card}
    id="notify"
    title=${t("Notices from other programs")}
    text=${t("A script, a Stream Deck button or the command line shows a short notice on the panel: a render is done, a download has finished. Only from this computer, and only with the key below.")}
  >
    <${SettingRow} title=${t("Let other programs show notices")} data-notify>
      <${Toggle}
        label=${t("Let other programs show notices")}
        checked=${on}
        onChange=${(value) => saveSettings({ allow_notify: value }).catch(toastError)}
      />
    <//>
    ${on && info?.on
      ? html`<${SettingRow} stacked title=${t("From the command line")}>
            <div class="command-row">
              <pre class="command" tabindex="0" data-notify-command>${info.command}</pre>
              <button type="button" class="btn" onClick=${() => copyText(info.command)}>${t("Copy the command")}</button>
            </div>
          <//>
          <${SettingRow}
            stacked
            title=${t("As an address")}
            hint=${t("For a Stream Deck's Website action (as a request in the background) or a script, while Libre Panel runs. The address may also carry a title, seconds (1 to 60), an icon and a level (info, warning, error).")}
          >
            <div class="command-row">
              <pre class="command" tabindex="0" data-notify-address>${info.address}</pre>
              <button type="button" class="btn" onClick=${() => copyText(info.address)}>${t("Copy the address")}</button>
            </div>
            <div class="command-row">
              <button type="button" class="btn" onClick=${() => tryIt(info.address)}>${t("Try it")}</button>
              <button type="button" class="btn" onClick=${renew}>${t("New key")}</button>
            </div>
          <//>`
      : null}
  <//>`;
}
