// Set up sensors and panel access (Linux): what Libre Panel cannot read or use without an
// administrator, and the one button that brings it. A card in Settings → Sensors and, in a
// smaller form, on Start (views/home/setup.js). The texts say exactly what the button does;
// the system asks for the password (polkit's own window), and the same is `libre-panel
// system-setup --linux` in a terminal. The server is editor/settings.py, the work is
// linux_setup.py.

import { registerCommand } from "../../core/commands.js";
import { t } from "../../core/i18n.js";
import { linuxSetup, runSetup, useSetup } from "../../core/setup.js";
import { store } from "../../core/state.js";
import { html, useState } from "../../lib/preact.js";
import { navigate } from "../../shell/views.js";
import { Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { Card, Note, copyText } from "./parts.js";

/** What each thing the setup brings is, and what the button does for it. */
function offerText(offer) {
  switch (offer.id) {
    case "usb":
      return {
        title: t("Use the USB panel"),
        hint: t("A rule for udev lets your user use the TURZX USB panels. It is applied to a panel that is plugged in, so it need not be unplugged."),
        missing: t("Your user may not use the USB panel yet."),
      };
    case "fans":
      return {
        title: t("Fans and temperatures of the mainboard"),
        hint: t("Loads the Linux driver for the chip that reads them (it tries {names}) and keeps it for every start. Only a driver that finds its chip stays; nothing is forced.", {
          names: offer.modules.join(", "),
        }),
        missing: t("No fan readings without it."),
      };
    case "drives":
      return {
        title: t("Temperatures of SATA drives"),
        hint: t("Loads the Linux driver drivetemp and keeps it for every start. A drive that stands by may wake up when it is read, so Libre Panel reads them only while a theme shows them."),
        missing: "",
      };
    default:
      return { title: offer.id, hint: "", missing: "" };
  }
}

/** What a finished run says, one line for each thing it did: {tone, text}. */
function resultLines(job) {
  const lines = [];
  const add = (tone, text) => lines.push({ tone, text });
  for (const step of job.result?.results || []) {
    if (step.status !== "done") {
      add("error", t("Not done: {detail}", { detail: step.detail || step.id }));
      continue;
    }
    for (const [name, answer] of Object.entries(step.items)) {
      if (step.id === "udev" && answer === "written") add("ok", t("The rule for the USB panel is installed."));
      else if (step.id === "udev" && answer === "removed") add("ok", t("The rule for the USB panel is removed."));
      else if (step.id === "rapl" && answer === "written") add("ok", t("The processor's energy counters can be read now."));
      else if (step.id === "rapl" && answer === "removed") add("ok", t("The processor's energy counters are closed again."));
      else if (step.id === "modules" && answer === "removed") add("ok", t("The list of drivers is removed; they stay loaded until the next start."));
      else if (answer === "loaded") add("ok", t("The driver {name} is loaded now and at every start.", { name }));
      else if (answer === "none") add("warn", t("{name}: no such chip on this board.", { name }));
      else if (answer === "busy") add("warn", t("{name}: the firmware keeps the chip for itself; Libre Panel does not force it.", { name }));
      else if (answer === "missing") add("warn", t("{name}: not part of this kernel.", { name }));
      else if (answer === "failed") add("warn", t("{name} did not load.", { name }));
    }
  }
  return lines;
}

/** How the last run went, or that it goes on. */
function Outcome({ setup }) {
  const { job } = setup;
  if (job.state === "running") return html`<${Note}>${t("Your system is asking for the administrator's password …")}<//>`;
  if (job.state === "cancelled") return html`<${Note}>${t("Cancelled. Nothing was changed.")}<//>`;
  if (job.state === "unavailable") {
    return html`<${Note} tone="warn">${t("Your system has no window to ask for the password. Run this in a terminal instead:")}<//>`;
  }
  if (job.state === "failed") {
    const failed = job.result?.results?.find((step) => step.status !== "done");
    return html`<${Note} tone="error">${t("It did not work: {detail}", { detail: job.detail || failed?.detail || "" })}<//>`;
  }
  if (job.state !== "done") return null;
  return html`<div class="setup-result" role="status">
    ${resultLines(job).map(
      (line, index) => html`<div key=${index} class=${`setup-line ${line.tone}`}>
        <${Icon} name=${line.tone === "ok" ? "check" : "warning"} size=${14} /><span>${line.text}</span>
      </div>`,
    )}
    <div class="settings-row-hint">${t("The readings that come with it show within a few seconds.")}</div>
  </div>`;
}

function Terminal({ setup }) {
  return html`<details class="setup-terminal" open=${setup.job.state === "unavailable" || !setup.method}>
    <summary>${t("Or in a terminal")}</summary>
    <div class="command-row">
      <pre class="command" tabindex="0">${setup.command}</pre>
      <button type="button" class="btn" onClick=${() => copyText(setup.command)}>${t("Copy the command")}</button>
    </div>
  </details>`;
}

/**
 * The things it brings, the one switch (the processor's power) and the button. `compact`: in the
 * card on Start, which has a heading of its own and keeps the terminal out of sight.
 */
export function SetupPanel({ compact = false }) {
  const setup = useSetup();
  const [rapl, setRapl] = useState(null); // null: as it is now
  if (!setup) return null;
  const power = setup.offers.find((offer) => offer.id === "power");
  const showPower = Boolean(power) || setup.granted;
  const wantPower = rapl === null ? setup.granted : rapl;
  const changesPower = showPower && wantPower !== setup.granted;
  const things = setup.offers.filter((offer) => offer.id !== "power");
  const running = setup.job.state === "running";
  const noWay = !setup.method; // no polkit window to ask the password in: only the terminal
  const ask = (options) => runSetup(options).then(() => setRapl(null));
  return html`<div class=${`setup${compact ? " compact" : ""}`}>
    ${things.length || showPower
      ? html`<ul class="setup-list">
          ${things.map((offer) => {
            const text = offerText(offer);
            return html`<li key=${offer.id} data-offer=${offer.id}>
              <strong>${text.title}</strong>
              <span>${text.hint}</span>
              ${offer.needed && text.missing ? html`<em class="setup-missing">${text.missing}</em>` : null}
            </li>`;
          })}
          ${showPower
            ? html`<li key="power" class="setup-power" data-offer="power">
                <div class="setup-power-text">
                  <strong>${t("Power of the processor")}</strong>
                  <span>${t("Linux has kept the energy counters of the processor from everyone but root since 2020, because they can give away what the processor computes. Allowing it lets every program on this PC read them. Leave it off unless you want the processor's power on the panel and trust what you run.")}</span>
                </div>
                <${Toggle} label=${t("Let Libre Panel read the processor's power")} checked=${wantPower} disabled=${running} onChange=${setRapl} />
              </li>`
            : null}
        </ul>`
      : html`<p class="settings-row-hint">${t("Nothing to set up: everything this computer can give is in place.")}</p>`}
    <${Outcome} setup=${setup} />
    ${noWay && setup.job.state !== "unavailable"
      ? html`<${Note} tone="warn">${t("This system cannot show a password window from here. Run this in a terminal instead:")}<//>`
      : null}
    <div class="setup-actions">
      <button
        type="button"
        class="btn btn-primary"
        disabled=${running || noWay || !(things.length || changesPower)}
        onClick=${() => ask({ rapl: changesPower ? wantPower : null })}
      >
        ${running ? t("Waiting for the password …") : t("Set up")}
      </button>
      ${setup.installed.length
        ? html`<button type="button" class="link-btn" disabled=${running || noWay} onClick=${() => ask({ remove: true })}>${t("Remove again")}</button>`
        : null}
    </div>
    ${!compact || noWay || setup.job.state === "unavailable" ? html`<${Terminal} setup=${setup} />` : null}
  </div>`;
}

/** The card in Settings → Sensors. */
export function SetupCard() {
  const setup = useSetup();
  if (!setup) return null;
  return html`<${Card}
    id="sensor-setup"
    title=${t("Set up sensors and panel access")}
    text=${t("Libre Panel reads most sensors without any rights. A few need a change on this computer that only an administrator can make, once, when you press the button; your system then asks for the password. Nothing else is changed, and Remove again takes it all out.")}
  >
    <div class="settings-row stacked setup-body"><${SetupPanel} /></div>
  <//>`;
}

/** Whether the card has anything to say: something to bring, or something that was put in. */
export const setupShows = () => {
  const setup = linuxSetup(store.get().unlockInfo);
  return Boolean(setup) && (setup.offers.length > 0 || setup.installed.length > 0 || setup.granted);
};

registerCommand({
  id: "setup.open",
  title: () => t("Set up sensors and panel access"),
  hint: () => t("fan chip, USB permission, processor power"),
  keywords: () => "linux udev rapl pkexec sudo fans",
  group: () => t("Settings"),
  icon: "sensor",
  rank: 87,
  when: () => Boolean(linuxSetup(store.get().unlockInfo)),
  run: () => navigate("settings", "sensors"),
});
