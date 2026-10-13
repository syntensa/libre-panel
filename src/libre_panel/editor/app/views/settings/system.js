// Settings → Start and system: starting with the system, starting with the highest rights
// (Windows; only when asked for, and only an administrator can set it up), the language, the
// daily look for a new version, and, where the app is shown in the browser, what a window of
// its own needs.

import { inNativeWindow } from "../../core/app.js";
import { languageSetting, languages, setLanguage, t } from "../../core/i18n.js";
import { loadSystem, saveSystem } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html, useState } from "../../lib/preact.js";
import { Segmented, Toggle } from "../../ui/controls.js";
import { confirmDialog, openDialog } from "../../ui/dialog.js";
import { Icon } from "../../ui/icons.js";
import { toastError } from "../../ui/toast.js";
import { loadUpdate, setCheckUpdates, useUpdate } from "../home/update.js";
import { Card, Note, SettingRow, Status, copyText } from "./parts.js";

/** When GitHub was asked last, as the clock reads it ("" before the first time). */
const askedAt = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleString() : "");

/** Whether Libre Panel looks for a new version once a day, what it found, and "Look now". */
function UpdatesRow() {
  const update = useUpdate();
  const [looking, setLooking] = useState(false);
  if (!update) return null;
  const look = async () => {
    setLooking(true);
    try {
      await loadUpdate({ now: true });
    } finally {
      setLooking(false);
    }
  };
  const status = !update.check
    ? null
    : update.error
      ? html`<${Status} tone="off">${t("GitHub did not answer: {error}", { error: update.error })}<//>`
      : update.newer
        ? html`<${Status} tone="warn">${t("Libre Panel {version} is out.", { version: update.latest.version })}<//>`
        : update.asked
          ? html`<${Status} tone="ok">${t("Up to date ({version}), looked {time}.", { version: update.current, time: askedAt(update.asked) })}<//>`
          : null;
  return html`<${SettingRow}
    title=${t("New versions")}
    hint=${t("Once a day Libre Panel asks GitHub whether a new version is out, and Start says so. Nothing of you or your panel is sent.")}
    status=${status}
    data-updates
  >
    ${update.check && update.newer
      ? html`<a class="btn" href=${update.latest.download || update.latest.page} target="_blank" rel="noopener noreferrer">
          ${t("Get {version}", { version: update.latest.version })} <${Icon} name="external" size=${14} />
        </a>`
      : null}
    ${update.check
      ? html`<button type="button" class="btn" disabled=${looking} onClick=${look}>${t("Look now")}</button>`
      : null}
    <${Toggle} label=${t("Look for new versions")} checked=${Boolean(update.check)} onChange=${(on) => setCheckUpdates(on).catch(toastError)} />
  <//>`;
}

/** What to do when the app is not run as administrator: open a terminal that is. */
function Elevate({ command, close }) {
  return html`<h2 class="dialog-title">${t("Start with administrator rights")}</h2>
    <p class="dialog-text">
      ${t(
        "Setting this up needs an administrator, and Libre Panel does not run as one. Open a terminal with “Run as administrator” and run this command; it makes Libre Panel start with the highest rights at every login, without a question.",
      )}
    </p>
    <pre class="command" tabindex="0">${command}</pre>
    <div class="dialog-actions">
      <button type="button" class="btn" onClick=${() => copyText(command)}>${t("Copy the command")}</button>
      <button type="button" class="btn btn-primary" autofocus onClick=${() => close()}>${t("Close")}</button>
    </div>`;
}

async function startWithRights(info, on) {
  if (!on) {
    await saveSystem({ elevated: false });
    return;
  }
  if (!info.can_elevate) {
    await openDialog((close) => html`<${Elevate} command=${info.command} close=${close} />`, { label: t("Start with administrator rights") });
    return;
  }
  const warning = info.risky
    ? ` ${t(
        "Warning: {program} is in your user folder, where any program you run could replace it and so get administrator rights at the next login. Safer: install Libre Panel under Program Files.",
        { program: info.program },
      )}`
    : "";
  const sure = await confirmDialog({
    title: t("Start with administrator rights?"),
    message: t("Libre Panel will start with the highest rights at every login, without a question. Turn it off again here, as an administrator.") + warning,
    confirm: t("Set it up"),
  });
  if (sure) await saveSystem({ elevated: true });
}

function StartCard() {
  const info = useStore((s) => s.systemInfo);
  if (!info) return null;
  const windows = info.kind === "windows";
  const unsupported = info.kind === "unsupported";
  return html`<${Card} id="start">
    <${SettingRow}
      title=${windows ? t("Start with Windows") : t("Start when you log in")}
      hint=${unsupported
        ? t("Not supported on this system.")
        : windows
          ? t("Libre Panel runs in the background, its icon sits in the notification area.")
          : t("Libre Panel runs in the background, its icon sits in the tray.")}
      status=${info.enabled && info.location ? html`<span class="settings-row-where mono">${info.location}</span>` : null}
    >
      <${Toggle}
        label=${windows ? t("Start with Windows") : t("Start when you log in")}
        checked=${info.enabled === true}
        disabled=${unsupported || info.enabled === null}
        onChange=${(on) => saveSystem({ autostart: on })}
      />
    <//>
    ${info.task_hint ? html`<div class="settings-row"><${Note} tone="warn">${info.task_hint}<//></div>` : null}
    ${windows
      ? html`<${SettingRow}
          title=${t("With administrator rights")}
          hint=${t("Rarely needed: for the temperature and power of the processor and the mainboard's fans only where the sensor service is not set up (Settings → Sensors), or for a sensor plugin that reads the hardware itself. Off unless you turn it on.")}
          status=${!info.elevated && !info.can_elevate
            ? html`<${Status}>${t("Setting it up needs Libre Panel to run as administrator.")}<//>`
            : null}
        >
          <${Toggle}
            label=${t("With administrator rights")}
            checked=${info.elevated}
            onChange=${(on) => startWithRights(info, on)}
          />
        <//>`
      : null}
    <${SettingRow} title=${t("Language")} hint=${t("The app, the tray menu and the texts on the panel.")}>
      <${Segmented}
        label=${t("Language")}
        value=${languageSetting()}
        onChange=${(value) => setLanguage(value).catch(toastError)}
        options=${[
          { value: "auto", label: t("System") },
          // each language by its own name, the way the server lists them
          ...Object.entries(languages())
            .sort(([, a], [, b]) => a.localeCompare(b))
            .map(([value, name]) => ({ value, label: name })),
        ]}
      />
    <//>
    <${UpdatesRow} />
    ${inNativeWindow()
      ? null
      : html`<${SettingRow}
          title=${t("Window")}
          hint=${windows
            ? t("The app is shown in the browser. A window of its own needs Microsoft's WebView2 Runtime, which Windows 11 has: install it from Microsoft, then start Libre Panel again.")
            : info.kind === "xdg"
              ? t("The app is shown in the browser. A window of its own needs WebKitGTK: install it from your software centre, then start Libre Panel again.")
              : t("The app is shown in the browser.")}
        />`}
  <//>`;
}

export const systemGroups = [{ id: "start", order: 10, component: StartCard, load: loadSystem }];
