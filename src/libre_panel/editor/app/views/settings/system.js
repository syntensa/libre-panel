// Settings → Start and system: starting with the system, starting with the highest rights
// (Windows; only when asked for, and only an administrator can set it up), the language
// and the window the app is shown in.

import { inNativeWindow } from "../../core/app.js";
import { languageSetting, languages, setLanguage, t } from "../../core/i18n.js";
import { loadSystem, saveSystem } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html } from "../../lib/preact.js";
import { Segmented, Toggle } from "../../ui/controls.js";
import { confirmDialog, openDialog } from "../../ui/dialog.js";
import { toastError } from "../../ui/toast.js";
import { Card, Note, SettingRow, Status, copyText } from "./parts.js";

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
          hint=${t("Needed for the temperature and power of the processor and for the fans on the mainboard only where the sensor service is not set up (Settings → Sensors), and for sensor plugins that read the hardware themselves. Off unless you turn it on.")}
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
    <${SettingRow}
      title=${t("Window")}
      hint=${inNativeWindow()
        ? t("The app is shown in a window of its own.")
        : t("The app is shown in the browser. A window of its own needs WebView2 (Windows) or WebKitGTK (Linux).")}
      status=${inNativeWindow() ? null : html`${t("What is missing:")} <span class="mono">libre-panel window --check</span>`}
    />
  <//>`;
}

export const systemGroups = [{ id: "start", order: 10, component: StartCard, load: loadSystem }];
