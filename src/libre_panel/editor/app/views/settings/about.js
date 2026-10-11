// Settings → About Libre Panel: the version and the licence, where to find help and how to
// report a panel that is not on the list of supported ones, the folders (with "open"), and
// the notices of the software Libre Panel is built on.

import { t } from "../../core/i18n.js";
import { loadAbout, openFolder } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { Markdown } from "./markdown.js";
import { Card, Disclosure, SettingRow, copyText } from "./parts.js";

const external = (href, label) =>
  html`<a class="btn" href=${href} target="_blank" rel="noopener noreferrer">${label}<${Icon} name="external" size=${14} /></a>`;

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

function HardwareReport() {
  const info = useStore((s) => s.aboutInfo);
  if (!info) return null;
  return html`<${Card}
    id="about-report"
    title=${t("Report a panel")}
    text=${t("Own a panel that is not on the list of supported ones, or one that behaves oddly? A report from the doctor tells what it does, without serial numbers.")}
  >
    <${SettingRow}
      stacked
      title=${t("Run this in a terminal")}
      hint=${t("Quit Libre Panel first (the tray menu): the doctor needs the panel to itself. It writes a file; attach it to a hardware report.")}
    >
      <div class="command-row">
        <pre class="command" tabindex="0">${info.doctor}</pre>
        <button type="button" class="btn" onClick=${() => copyText(info.doctor)}>${t("Copy the command")}</button>
        ${external(info.links.hardware_report, t("Open the report form"))}
      </div>
    <//>
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
  const [open, setOpen] = useState(false);
  if (!info || !info.notice) return null;
  return html`<${Disclosure}
    id="about-notices"
    title=${t("Notices and credits")}
    hint=${t("The software and the work Libre Panel is built on, with their licences.")}
    open=${open}
    onToggle=${() => setOpen(!open)}
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
