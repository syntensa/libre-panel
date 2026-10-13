// Settings (#/settings, #/settings/<section>): the overview (what needs attention, each part
// at a glance), the panel, sensors, game mode and more, weather and place, start and system,
// about. The sections are on the left; a part of the app adds its group to one (sections.js).
// Built on the shell: see ../../README.md.

import { registerProvider } from "../../core/commands.js";
import { t } from "../../core/i18n.js";
import { loadSettings } from "../../core/settings.js";
import { loadSetup } from "../../core/setup.js";
import { store, useStore } from "../../core/state.js";
import { loadHardware } from "../../core/hardware.js";
import { html, useEffect } from "../../lib/preact.js";
import { navigate, registerView } from "../../shell/views.js";
import { Icon } from "../../ui/icons.js";
import { aboutGroups } from "./about.js";
import { HardwareNames } from "./hardware.js";
import { AtAGlance, HealthCard, useHealth } from "./health.js";
import { panelGroups } from "./panel.js";
import { Note } from "./parts.js";
import { registerSettingsGroup, registerSettingsSection, settingsGroups, settingsSections } from "./sections.js";
import { sensorGroups } from "./sensors.js";
import { serviceGroups } from "./services.js";
import { SetupCard, setupShows } from "./setup.js";
import { systemGroups } from "./system.js";
import { weatherGroups } from "./weather.js";

registerSettingsSection({
  id: "overview",
  order: 5,
  title: () => t("Overview"),
  text: () => t("What needs attention, and how each part is set now."),
});
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
  text: () => t("The place for the weather and the sun, and the weather itself."),
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

// each section's groups: the overview, the panel, the sources, the services, the weather, the
// system, about
for (const [section, groups] of Object.entries({
  overview: [
    { id: "health", order: 10, component: HealthCard },
    { id: "glance", order: 20, component: AtAGlance },
  ],
  panel: panelGroups,
  sensors: sensorGroups,
  services: serviceGroups,
  weather: weatherGroups,
  system: systemGroups,
  about: aboutGroups,
})) {
  for (const group of groups) registerSettingsGroup(section, group);
}

registerSettingsGroup("sensors", {
  id: "setup",
  order: 15, // after the sources, before the rest: it is what mends missing readings
  component: SetupCard,
  load: loadSetup,
  shows: setupShows, // Linux only, and only while it has something to bring or to take out
});

registerSettingsGroup("sensors", {
  id: "hardware",
  order: 40,
  component: HardwareNames,
  load: () => loadHardware(true), // what the sensors detect now
  shows: () => Boolean(store.get().hardware), // not where the server has no names
});

/** config.toml cannot be read: nothing below it can be shown or changed until it is mended
 * (or, with `down`, until the app answers again). */
function ConfigProblem({ message, down }) {
  return html`<${Note}
    tone="error"
    action=${html`<button type="button" class="btn" onClick=${loadSettings}>${t("Try again")}</button>`}
  >
    ${down ? message : html`<strong>${t("config.toml has a mistake in it.")}</strong> ${message}`}
  <//>`;
}

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

/** Scrolls the card with that id into view once it is there (cards load their data first). */
function showCard(id, tries = 30) {
  const found = document.getElementById(id);
  if (found) found.scrollIntoView({ block: "start", behavior: "smooth" });
  else if (tries) setTimeout(() => showCard(id, tries - 1), 100);
}

/** How many things need attention, beside the overview's name while another section is open. */
function HealthCount() {
  const count = useHealth().items.filter((item) => item.tone !== "info").length;
  if (!count) return null;
  return html`<span class="settings-nav-count" title=${t("What needs attention")}>${count}</span>`;
}

function SettingsView({ rest }) {
  useStore((s) => [s.lang, s.hardware, s.unlockInfo]); // the texts follow the language; groups come and go
  const { problem, down } = useStore((s) => ({ problem: s.settingsError, down: s.settingsDown }));
  const sections = settingsSections();
  const [wanted, card] = (rest || "").split("/"); // #/settings/panel/start-screen: that card in view
  const current = sections.find((section) => section.id === wanted) || sections[0];
  const groups = settingsGroups(current.id);
  useEffect(() => groups.forEach((group) => group.load?.()), [current.id]);
  useEffect(() => {
    if (card) showCard(card);
  }, [rest]);
  const showing = groups.filter((group) => !group.shows || group.shows());
  return html`<div class="settings">
    <nav class="settings-nav" aria-label=${t("Settings")}>
      ${sections.map(
        (section) => html`<button type="button" key=${section.id} aria-current=${section.id === current.id ? "page" : null}
          onClick=${() => navigate("settings", section.id)}>
          ${section.title()}${section.id === "overview" && current.id !== "overview" ? html`<${HealthCount} />` : null}
        </button>`,
      )}
    </nav>
    <main class="settings-main" aria-labelledby="settings-title">
      <h1 id="settings-title">${current.title()}</h1>
      ${problem && current.id !== "about" ? html`<${ConfigProblem} message=${problem} down=${down} />` : null}
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

registerView({
  id: "settings",
  title: () => t("Settings"),
  tab: false,
  order: 60,
  icon: "gear",
  component: SettingsView,
});
