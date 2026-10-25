// Settings → About Libre Panel: the version (and, where a newer one is out, "Update to" it) and
// the licence, where to find help and how to report a panel that is not on the list of supported
// ones (step by step, with what is connected listed by the app itself), the folders (with
// "open"), and the notices of the software Libre Panel is built on.

import { t } from "../../core/i18n.js";
import { loadAbout, loadReport, openFolder } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { navigate } from "../../shell/views.js";
import { Icon } from "../../ui/icons.js";
import { PipCommand, UpdateNow, useUpdate } from "../home/update.js";
import { Markdown } from "./markdown.js";
import { Card, Disclosure, Note, SettingRow, copyText } from "./parts.js";

const external = (href, label) =>
  html`<a class="btn" href=${href} target="_blank" rel="noopener noreferrer">${label}<${Icon} name="external" size=${14} /></a>`;

/** A newer version, where Libre Panel looks for one (Settings → Start and system): what is new
 * and "Update to" it, or, installed with pip, the command. */
function NewVersion() {
  const update = useUpdate();
  if (!update?.check || !update.newer || !update.latest) return null;
  const { latest } = update;
  const page = external(latest.page, t("What is new"));
  return html`<${SettingRow}
    title=${t("Libre Panel {version} is out", { version: latest.version })}
    hint=${latest.notes || null}
    stacked
    data-about-update
  >
    ${update.kind === "python" ? html`<${PipCommand} version=${latest.version} />` : null}
    ${latest.asset ? html`<${UpdateNow} update=${update} extra=${page} />` : html`<div class="update-card-form">${page}</div>`}
  <//>`;
}

function Version() {
  const info = useStore((s) => s.aboutInfo);
  if (!info) return null;
  return html`<${Card} id="about-version">
    <${SettingRow}
      title=${html`Libre Panel ${info.version}`}
      hint=${t("Free software for USB system-monitor panels.")}
    >
      ${external(info.links.project, t("Project page"))}
    <//>
    <${NewVersion} />
    <${SettingRow}
      title=${t("Licence")}
      hint=${t("GNU General Public License, version 3 or later (GPL-3.0-or-later). You may use, study, share and change it.")}
    >
      ${external(info.license.url, t("Read the licence"))}
    <//>
    <${SettingRow} title=${t("Help and problems")} hint=${t("A bug, an idea, or a question: tell the project.")}>
      ${external(info.links.issues, t("Open an issue"))}
    <//>
  <//>`;
}

/** A command to run in a terminal, with the button that copies it. */
function Command({ command }) {
  return html`<div class="command-row">
    <pre class="command" tabindex="0">${command}</pre>
    <button type="button" class="btn" onClick=${() => copyText(command)}>${t("Copy the command")}</button>
  </div>`;
}

/** One step of the report: its number, what it is, and what is done for it. */
function Step({ number, id, title, children }) {
  return html`<li class="report-step" data-step=${id}>
    <span class="report-number" aria-hidden="true">${number}</span>
    <div class="report-body">
      <strong>${title}</strong>
      ${children}
    </div>
  </li>`;
}

/**
 * Report a panel, as three steps: what is connected (the app lists it itself, as `libre-panel
 * devices` would, without serial numbers), the doctor's check for a panel that is found but shows
 * something wrong (it sends the panel test pictures, so it runs outside the app, with the panel
 * to itself), and the form, which opens with the list filled in.
 */
function HardwareReport() {
  const info = useStore((s) => s.aboutInfo);
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const look = async () => {
    setBusy(true);
    try {
      setReport(await loadReport());
    } catch (error) {
      setReport({ problem: error.message });
    }
    setBusy(false);
  };
  useEffect(() => {
    look();
  }, []);
  if (!info) return null;
  const windows = report?.platform === "win32";
  return html`<${Card}
    id="about-report"
    title=${t("Report a panel")}
    text=${t("Your panel is not on the list, or it does something odd? Three steps tell the project, and help the next person with the same panel.")}
  >
    <ol class="report-steps">
      <${Step} number="1" id="list" title=${t("What is connected")}>
        <span class="settings-row-hint">
          ${t("Libre Panel lists it for you: its version, your system, the panel found and what is plugged in that could be one, by its USB ids. No serial numbers, names or folders.")}
        </span>
        ${report?.problem ? html`<${Note} tone="error">${report.problem}<//>` : null}
        <pre class="command report-list" tabindex="0" aria-label=${t("What is connected")}>${report?.text || (busy ? t("Looking …") : "")}</pre>
        <div class="report-actions">
          <button type="button" class="btn" disabled=${!report?.text} onClick=${() => copyText(report.text)}>${t("Copy the list")}</button>
          <button type="button" class="link-btn" disabled=${busy} onClick=${look}>${t("Look again")}</button>
        </div>
      <//>
      <${Step} number="2" id="doctor" title=${t("Check the panel (if it is found but shows something wrong)")}>
        <span class="settings-row-hint">
          ${t("The panel test shows test pictures on the panel, asks what you see and writes the report, without serial numbers; one click then opens the form with it filled in.")}
        </span>
        <div class="report-actions">
          <button type="button" class="btn" data-open="panel-test" onClick=${() => navigate("live", "diagnose")}>${t("Open the panel test")}</button>
        </div>
        <details class="report-terminal">
          <summary>${t("Or in a terminal")}</summary>
        <span class="settings-row-hint">
          ${t("The doctor shows test pictures on the panel, asks what you see and writes a report file, without serial numbers. It needs the panel to itself, so it runs outside the app:")}
        </span>
        <ol class="report-how">
          <li>${t("Copy the command.")}</li>
          <li>${t("Quit Libre Panel: right-click its icon in the tray, by the clock, and choose Quit Libre Panel.")}</li>
          <li>${windows
            ? t("Press Windows+R, type cmd and press Enter. Paste the command with a right click and press Enter.")
            : t("Open a terminal, paste the command and press Enter.")}</li>
          <li>${t("At the end it says where it wrote its file: add that file to the report. Then start Libre Panel again.")}</li>
        </ol>
        <${Command} command=${info.doctor} />
        </details>
      <//>
      <${Step} number="3" id="send" title=${t("Send it")}>
        <span class="settings-row-hint">
          ${t("The form on GitHub opens with the list of step 1 filled in. Say what the panel does; a photo of it and of the label on its back help a lot. You need a GitHub account.")}
        </span>
        <div class="report-actions">
          <a class="btn btn-primary" href=${report?.form || info.links.hardware_report} target="_blank" rel="noopener noreferrer">
            ${t("Open the report form")}<${Icon} name="external" size=${14} />
          </a>
        </div>
      <//>
    </ol>
  <//>`;
}

const FOLDERS = () => [
  { id: "config", name: t("Settings"), hint: t("config.toml, plugins and the rest") },
  { id: "themes", name: t("Your themes"), hint: t("The themes you made or imported") },
  { id: "logs", name: t("Log"), hint: t("What Libre Panel did, for finding a problem") },
];

function Folders() {
  const info = useStore((s) => s.aboutInfo);
  if (!info) return null;
  return html`<${Card} id="about-folders" title=${t("Folders")}>
    ${FOLDERS().map(
      (folder) => html`<${SettingRow} key=${folder.id} title=${folder.name} hint=${folder.hint} status=${html`<span class="settings-row-where mono">${info.folders[folder.id]}</span>`} data-folder=${folder.id}>
        <button type="button" class="btn" onClick=${() => openFolder(folder.id)}>${t("Open folder")}</button>
      <//>`,
    )}
  <//>`;
}

function Notices() {
  const info = useStore((s) => s.aboutInfo);
  if (!info || !info.notice) return null;
  return html`<${Disclosure}
    id="about-notices"
    title=${t("Notices and credits")}
    hint=${t("The software and the work Libre Panel is built on, with their licences.")}
  >
    <div class="notices"><${Markdown} source=${info.notice} /></div>
  <//>`;
}

export const aboutGroups = [
  { id: "version", order: 10, component: Version, load: loadAbout },
  { id: "report", order: 20, component: HardwareReport },
  { id: "folders", order: 30, component: Folders },
  { id: "notices", order: 40, component: Notices },
];
