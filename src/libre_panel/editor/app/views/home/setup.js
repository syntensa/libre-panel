// Start's card for the setup of sensors and panel access (Linux), shown while something it brings
// is missing: the USB panel may not be used yet, there are no fan readings, the processor's power
// cannot be read. The card says what the button does and does it (views/settings/setup.js); "Not
// now" keeps it away until something else is missing. Settings → Sensors has the same, always.
// A first start shows it only where the panel cannot be used without it (`blocking`).

import { t } from "../../core/i18n.js";
import { closeSetupRun, dismissSetup, loadSetup, setupNeeds, useSetup } from "../../core/setup.js";
import { useStore } from "../../core/state.js";
import { html, useEffect } from "../../lib/preact.js";
import { usePolling } from "../settings/parts.js";
import { SetupPanel } from "../settings/setup.js";

const PROBLEMS = () => ({
  usb: t("Your user may not use the USB panel yet."),
  fans: t("No fan readings: the driver for the chip on the mainboard is not loaded."),
  power: t("The processor's power cannot be read: Linux keeps its energy counters to root."),
});

export function SetupCard({ blocking = false }) {
  const setup = useSetup();
  const { dismissed, ran } = useStore((s) => ({ dismissed: s.setupDismissed, ran: s.setupRan }));
  useEffect(() => {
    loadSetup();
  }, []);
  usePolling(loadSetup, 15000);
  if (!setup) return null;
  const needs = setupNeeds(setup);
  const asking = needs.length > 0 && dismissed !== needs.join(",") && (!blocking || needs.includes("usb"));
  if (!asking && !ran) return null;
  const problems = PROBLEMS();
  const finished = ran && setup.job.state !== "running" && setup.job.state !== "idle";
  const close = () => {
    closeSetupRun();
    dismissSetup(needs);
  };
  return html`<aside class="hint-card setup-card" aria-label=${t("Set up sensors and panel access")}>
    <strong>${t("Set up sensors and panel access")}</strong>
    ${needs.map((id) => html`<p key=${id}>${problems[id]}</p>`)}
    <${SetupPanel} compact />
    ${finished
      ? html`<button type="button" class="link-btn" onClick=${close}>${t("Close")}</button>`
      : html`<button type="button" class="link-btn" disabled=${setup.job.state === "running"} onClick=${() => dismissSetup(needs)}>${t("Not now")}</button>`}
  </aside>`;
}
