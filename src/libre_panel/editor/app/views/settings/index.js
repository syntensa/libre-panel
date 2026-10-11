// Settings (#/settings, #/settings/<section>): the panel, sensors, services,
// weather and place, start and system, about. The sections are on the left; a part
// of the app adds its group to one (sections.js). Built on the shell: see
// ../../README.md.

import { registerProvider } from "../../core/commands.js";
import { t } from "../../core/i18n.js";
import { store, useStore } from "../../core/state.js";
import { loadHardware } from "../../core/hardware.js";
import { html, useEffect } from "../../lib/preact.js";
import { navigate, registerView } from "../../shell/views.js";
import { Icon } from "../../ui/icons.js";
import { HardwareNames } from "./hardware.js";
import { registerSettingsGroup, registerSettingsSection, settingsGroups, settingsSections } from "./sections.js";

registerSettingsSection({
  id: "panel",
  order: 10,
  title: () => t("Panel"),
  text: () => t("The panel: which one, the video mode and its frame rate, brightness and orientation."),
});
registerSettingsSection({
  id: "sensors",
  order: 20,
  title: () => t("Sensors"),
  text: () => t("Where the readings come from, and the names of the hardware."),
});
registerSettingsSection({
  id: "services",
  order: 30,
  title: () => t("Services"),
  text: () => t("Game mode, the round report, the cooling analysis, downloads and the plugins' services."),
});
registerSettingsSection({
  id: "weather",
  order: 40,
  title: () => t("Weather and place"),
  text: () => t("The place for the weather, the sun and the time zone."),
});
registerSettingsSection({
  id: "system",
  order: 50,
  title: () => t("Start and system"),
  text: () => t("Starting with the system, administrator rights and the language."),
});
registerSettingsSection({
  id: "about",
  order: 60,
  title: () => t("About Libre Panel"),
  text: () => t("The version, the licence and where to find help."),
});

function About() {
  const version = useStore((s) => s.specs?.version);
  return html`<section class="settings-card">
    <div class="settings-card-head">
      <h2>Libre Panel ${version || ""}</h2>
      <p>${t("Free software for USB system-monitor panels, under the GNU General Public License 3.0.")}</p>
    </div>
  </section>`;
}

registerSettingsGroup("sensors", {
  id: "hardware",
  order: 10,
  component: HardwareNames,
  load: () => loadHardware(true), // what the sensors detect now
  shows: () => Boolean(store.get().hardware), // not where the server has no names
});
registerSettingsGroup("about", { id: "version", order: 10, component: About });

/** A section none of whose groups shows anything yet: what it is going to hold. */
function Upcoming({ section }) {
  return html`<section class="settings-card settings-upcoming">
    <span class="slot-icon"><${Icon} name="gear" size=${22} /></span>
    <div>
      <p>${section.text()}</p>
      <p class="muted">${t("This part of the settings is being built; until then config.toml and the tray menu have it.")}</p>
    </div>
  </section>`;
}

function SettingsView({ rest }) {
  useStore((s) => [s.lang, s.hardware]); // the texts follow the language; groups come and go
  const sections = settingsSections();
  const current = sections.find((section) => section.id === rest) || sections[0];
  const groups = settingsGroups(current.id);
  useEffect(() => groups.forEach((group) => group.load?.()), [current.id]);
  const showing = groups.filter((group) => !group.shows || group.shows());
  return html`<div class="settings">
    <nav class="settings-nav" aria-label=${t("Settings")}>
      ${sections.map(
        (section) => html`<button type="button" key=${section.id} aria-current=${section.id === current.id ? "page" : null}
          onClick=${() => navigate("settings", section.id)}>${section.title()}</button>`,
      )}
    </nav>
    <main class="settings-main" aria-labelledby="settings-title">
      <h1 id="settings-title">${current.title()}</h1>
      ${showing.map((group) => html`<${group.component} key=${group.id} />`)}
      ${showing.length ? null : html`<${Upcoming} section=${current} />`}
    </main>
  </div>`;
}

// Ctrl+K: each section, and the hardware's names by their name
registerProvider(() => [
  ...settingsSections().map((section) => ({
    id: `settings.${section.id}`,
    title: () => `${t("Settings")}: ${section.title()}`,
    group: () => t("Go to"),
    icon: "gear",
    rank: 85,
    run: () => navigate("settings", section.id),
  })),
  ...(store.get().hardware === false
    ? []
    : [
        {
          id: "settings.hardware-names",
          title: () => t("Hardware names"),
          hint: () => t("processor, graphics card, memory"),
          group: () => t("Settings"),
          icon: "sensor",
          rank: 86,
          run: () => navigate("settings", "sensors"),
        },
      ]),
]);

const SettingsTitle = () => html`<span class="topbar-title">${t("Settings")}</span>`;

registerView({
  id: "settings",
  title: () => t("Settings"),
  tab: false,
  order: 60,
  icon: "gear",
  component: SettingsView,
  header: { actions: SettingsTitle },
});
