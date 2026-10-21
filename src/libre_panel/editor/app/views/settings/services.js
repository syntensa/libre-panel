// Settings → Game mode and more (#/settings/services): each service Libre Panel brings (and
// those of plugins), whether it is on, how it is doing, and its options as the service itself
// declares them (`options` of the service: bool, number, string, list). Every service is off
// until it is switched on here; the running app follows at once, and a service whose options
// change starts again by itself (the section's lead says so).

import { refreshPages } from "../../core/actions.js";
import { control } from "../../core/console.js";
import { t } from "../../core/i18n.js";
import {
  enableService,
  loadServiceStates,
  loadSettings,
  restartService,
  saveSettings,
  useCatalog,
  useConfig,
} from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { navigate } from "../../shell/views.js";
import { NumberField, Select, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toastError } from "../../ui/toast.js";
import { PageThemeSelect } from "../design/pages.js";
import { NotifyCard } from "./notify.js";
import { CommitField, SettingRow, Status, usePolling } from "./parts.js";

export const loadServicesSection = () => Promise.all([loadSettings(), loadServiceStates()]);

export const SERVICES = {
  notices: {
    title: () => t("Notices"),
    hint: () => t("Short notices on the panel: the volume, a new track, a sensor past a limit, a restart of the panel."),
  },
  game: {
    title: () => t("Game mode"),
    hint: () => t("Switches the panel to game mode, with its own frame rate and theme, while a game runs."),
  },
  autopilot: {
    title: () => t("Autopilot"),
    hint: () => t("Picks the theme that fits what you are doing, by rules you write."),
  },
  devices: {
    title: () => t("Devices"),
    hint: () => t("Notices when a drive, a USB device or a controller comes or goes, and when a drive is nearly full (Windows)."),
  },
  memory: {
    title: () => t("Round report and history"),
    hint: () => t("Shows a report of the round on the panel after a game, and keeps what happens at the desk over weeks, on this computer."),
  },
  cooling: {
    title: () => t("Cooling analysis"),
    hint: () => t("Checks the fan curves of FanControl against what the PC did. FanControl is only read."),
  },
  downloads: {
    title: () => t("Downloads"),
    hint: () => t("Notes what Steam and the browser are downloading, for a screen that shows it."),
  },
  timer: {
    title: () => t("Timer"),
    hint: () => t("Minutes counted down on the panel, with a notice when they are up; with a break, focus and break take turns. A keyboard shortcut starts it, or the buttons here."),
  },
  power: {
    title: () => t("Power use"),
    hint: () => t("The watts the PC draws, from the sensors of the processor and the graphics card, and the energy of today and of the month with what it costs."),
  },
};

const SECONDS = { unit: "s", min: 0, max: 86400 };
const TRANSITIONS = ["", "cut", "fade", "slide", "tube", "entrance"];

// The words for the options of the services Libre Panel brings; a plugin's show their key.
const OPTIONS = {
  "notices.volume": { label: () => t("The volume") },
  "notices.volume_look": {
    label: () => t("How the volume looks"),
    hint: () => t("On every screen; Look shows each on the picture."),
    choices: () => [
      { id: "", name: t("As the notices") },
      { id: "bar", name: t("Bar") },
      { id: "levels", name: t("Levels") },
      { id: "ring", name: t("Ring") },
      { id: "big", name: t("Large") },
    ],
  },
  "notices.music": { label: () => t("A new track") },
  "notices.microphone": { label: () => t("The microphone muted or on again") },
  "notices.appointments": {
    label: () => t("An appointment that begins soon"),
    hint: () => t("Minutes before it begins, from the calendars of the sensors; 0: none."),
    unit: "min",
    min: 0,
    max: 240,
  },
  "notices.restarted": { label: () => t("The panel was restarted") },
  "notices.warnings": {
    label: () => t("Warnings for sensors"),
    hint: () => t("A message when a sensor reaches a limit. The limits are in config.toml."),
    structured: true,
  },
  "notices.quiet_s": { label: () => t("Quiet time between two warnings"), ...SECONDS },
  "notices.alarm": {
    label: () => t("An alarm on every panel"),
    hint: () => t("A sensor at its critical limit: a red edge on every panel, and the panels beside panel 1 show their theme for an alarm."),
  },
  "game.programs": {
    label: () => t("Programs that are games"),
    hint: () => t("File names; “name.exe = A Better Name” sets the name to show."),
  },
  "game.steam": { label: () => t("Games installed through Steam") },
  "game.steam_path": { label: () => t("Steam's folder"), hint: () => t("Only if Libre Panel does not find Steam.") },
  "game.ignore": { label: () => t("Never count as games"), hint: () => t("Tools and programs in Steam's library.") },
  "game.automatic": { label: () => t("Switch by itself when a game runs") },
  "game.force": { label: () => t("Always in game mode"), hint: () => t("To try the game screens, game or not.") },
  "game.entrance": { label: () => t("Entrance"), hint: () => t("The screen with the game's logo when the mode begins and ends.") },
  "game.logo": { label: () => t("Show the game's logo") },
  "game.linger_s": { label: () => t("Stay in game mode afterwards"), ...SECONDS, max: 300 },
  "game.poll_s": { label: () => t("Look for a game every"), unit: "s", min: 0.25, max: 60, step: 0.25 },
  "autopilot.rules": {
    label: () => t("Rules"),
    hint: () => t("Checked from the top; the first that holds wins."),
    structured: true,
    page: "autopilot",
  },
  "autopilot.hold_s": { label: () => t("A new choice must hold for"), ...SECONDS },
  "autopilot.min_s": { label: () => t("A theme stays at least"), ...SECONDS },
  "autopilot.quiet_after_boot_s": { label: () => t("Quiet after the system started"), ...SECONDS },
  "autopilot.transition": { label: () => t("Transition"), choices: TRANSITIONS },
  "devices.drives": { label: () => t("Drives") },
  "devices.usb": { label: () => t("USB devices") },
  "devices.controllers": { label: () => t("Game controllers"), hint: () => t("With their battery.") },
  "devices.storage": { label: () => t("Free space and temperature of the drives") },
  "devices.usb_ignore": { label: () => t("USB devices to say nothing about"), hint: () => t("As vendor:product, for example 1a86:7523.") },
  "devices.min_free_gb": { label: () => t("A drive is nearly full below"), unit: "GB", min: 0, max: 100000 },
  "devices.min_free_percent": { label: () => t("or below this share of it"), unit: "%", min: 0, max: 100 },
  "devices.ssd_warn_c": { label: () => t("A solid-state drive is hot from"), hint: () => t("0: the limit of the drive itself."), unit: "°C", min: 0, max: 150 },
  "memory.keep_days": { label: () => t("Keep minutes and games for"), hint: () => t("0: for ever."), unit: "days", min: 0, max: 36500 },
  "memory.cooling": { label: () => t("Keep the cooling state") },
  "memory.cooling_days": { label: () => t("Keep the cooling state for"), hint: () => t("0: for ever."), unit: "days", min: 0, max: 36500 },
  "memory.music": { label: () => t("Keep how long music plays"), hint: () => t("Never what plays.") },
  "memory.temperatures": { label: () => t("More temperatures for the cooling state"), hint: () => t("Sensors such as temp.board.system or a pattern like temp.nvme.*.") },
  "memory.fps_levels": { label: () => t("Frame-rate ranges of the report's chart"), numbers: true },
  "memory.report": { label: () => t("A round report when a game ends") },
  "memory.report_minutes": { label: () => t("Only for rounds of at least"), unit: "min", min: 0, max: 600 },
  "memory.report_compare": { label: () => t("Compare with the last rounds"), hint: () => t("Of the same program."), min: 1, max: 1000 },
  "memory.report_seconds": { label: () => t("The report stays on the panel for"), ...SECONDS, max: 600 },
  "memory.report_theme": { label: () => t("Theme of the report"), hint: () => t("Empty: no theme, only the data for a screen of a plugin."), themes: true },
  "memory.report_transition": { label: () => t("Transition to the report"), choices: TRANSITIONS },
  "memory.fancontrol": { label: () => t("FanControl's settings file"), hint: () => t("Only if Libre Panel does not find it.") },
  "memory.database": { label: () => t("Database file"), hint: () => t("Empty: in the service's own folder.") },
  "cooling.limits": {
    label: () => t("Limits"),
    hint: () => t("The highest temperature wanted for a sensor. They are in config.toml."),
    structured: true,
  },
  "cooling.fancontrol": { label: () => t("FanControl's settings file"), hint: () => t("Only if Libre Panel does not find it.") },
  "cooling.database": { label: () => t("The memory's file"), hint: () => t("Empty: the memory service's own.") },
  "downloads.steam": { label: () => t("Downloads of Steam") },
  "downloads.steam_path": { label: () => t("Steam's folder"), hint: () => t("Only if Libre Panel does not find Steam.") },
  "downloads.browser": { label: () => t("Downloads of the browser"), hint: () => t("The part files in the Downloads folder.") },
  "downloads.folder": { label: () => t("Downloads folder"), hint: () => t("Only if it is not the system's.") },
  "downloads.min_mb": { label: () => t("Ignore downloads smaller than"), unit: "MB", min: 0, max: 100000 },
  "timer.minutes": { label: () => t("Minutes"), unit: "min", min: 1, max: 1440 },
  "timer.break_minutes": { label: () => t("Break"), hint: () => t("0: no break, a plain timer."), unit: "min", min: 0, max: 240 },
  "timer.notice": { label: () => t("A notice when the time is up") },
  "power.sensors": { label: () => t("Sensors that add up to the watts"), hint: () => t("Sensors such as cpu.power and gpu.power.") },
  "power.rest_w": { label: () => t("The rest of the PC"), hint: () => t("Mainboard, drives, fans and what the power supply loses, in watts: some 40 to 80."), unit: "W", min: 0, max: 2000 },
  "power.price": { label: () => t("Price of a kWh"), hint: () => t("0: no cost."), min: 0, max: 100, step: 0.01 },
  "power.currency": { label: () => t("Currency") },
};

const optionLabel = (service, option) => OPTIONS[`${service}.${option.key}`]?.label() ?? option.key.replace(/_/g, " ");

// -- editing a list ---------------------------------------------------------------------------

/** A list as items to take out and a field to add one: `onChange(change)` gets a function
 * from the list as it is to the list it becomes. */
function ListField({ value, onChange, label, numbers }) {
  const [text, setText] = useState("");
  const add = () => {
    const item = text.trim();
    setText("");
    if (!item) return;
    const next = numbers ? Number(item.replace(",", ".")) : item;
    if (numbers && !Number.isFinite(next)) return;
    onChange((list) => (list.includes(next) ? list : [...list, next]));
  };
  return html`<div class="list-field">
    ${value.length
      ? html`<ul class="list-items">
          ${value.map(
            (item) => html`<li key=${String(item)}>
              <span>${String(item)}</span>
              <button type="button" aria-label=${t("Remove {name}", { name: String(item) })} onClick=${() => onChange((list) => list.filter((other) => other !== item))}>
                <${Icon} name="close" size=${12} />
              </button>
            </li>`,
          )}
        </ul>`
      : null}
    <input
      type="text"
      class="input"
      aria-label=${label}
      placeholder=${t("Add and press Enter")}
      value=${text}
      spellcheck="false"
      onInput=${(event) => setText(event.currentTarget.value)}
      onKeyDown=${(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          add();
        }
      }}
      onBlur=${add}
    />
  </div>`;
}

// -- one option ---------------------------------------------------------------------------------

function OptionRow({ service, option, given }) {
  const themes = useStore((s) => s.themes);
  const meta = OPTIONS[`${service}.${option.key}`] || {};
  const name = `services.${service}.${option.key}`;
  const set = given !== undefined;
  const value = set ? given : option.default;
  const label = optionLabel(service, option);
  const save = (next) => saveSettings({ [name]: next });
  const reset = set
    ? html`<button type="button" class="link-btn" onClick=${() => save(null)}>${t("Back to the default")}</button>`
    : null;
  const row = (control, stacked = false) => html`<${SettingRow}
    title=${label}
    hint=${meta.hint?.()}
    status=${reset}
    stacked=${stacked}
    class="option-row"
    data-option=${option.key}
  >
    ${control}
  <//>`;
  if (option.type === "bool") {
    return row(html`<${Toggle} label=${label} checked=${value === true} onChange=${save} />`);
  }
  if (option.type === "int" || option.type === "number") {
    return row(html`<${NumberField}
      label=${label}
      value=${value}
      min=${meta.min ?? 0}
      max=${meta.max}
      step=${meta.step ?? 1}
      digits=${option.type === "int" ? 0 : 2}
      unit=${meta.unit}
      onChange=${save}
    />`);
  }
  if (option.type === "string") {
    const choices = meta.themes
      ? [{ id: "", name: t("None") }, ...themes]
      : (typeof meta.choices === "function" ? meta.choices() : meta.choices)?.map((choice) =>
          typeof choice === "object" ? choice : { id: choice, name: choice || t("The one of config.toml") },
        );
    if (choices) {
      const options = choices.map((choice) => ({ value: choice.id, label: choice.name || choice.id }));
      if (!options.some((choice) => choice.value === value)) options.push({ value, label: value });
      return row(html`<${Select} label=${label} value=${value} options=${options} onChange=${save} />`);
    }
    return row(html`<${CommitField} label=${label} value=${value} mono onCommit=${save} />`);
  }
  if (option.type === "list") {
    if (meta.structured || (Array.isArray(value) && value.some((item) => item && typeof item === "object"))) {
      const count = Array.isArray(value) ? value.length : 0;
      return html`<${SettingRow} title=${label} hint=${meta.hint?.()} class="option-row" data-option=${option.key}>
        <span class="muted">${count ? t("{count} entries", { count }) : t("None yet")}</span>
        ${meta.page
          ? html`<button type="button" class="btn" onClick=${() => navigate("plugin", meta.page)}>${t("Edit …")}</button>`
          : null}
      <//>`;
    }
    const base = Array.isArray(option.default) ? option.default : []; // a plugin may declare none
    const numbers = meta.numbers || (base.length > 0 && base.every((item) => typeof item === "number"));
    const change = (edit) =>
      saveSettings((now) => {
        const current = now.services.options[service]?.[option.key];
        return { [name]: edit(Array.isArray(current) ? current : base) };
      });
    return row(
      html`<${ListField} label=${label} value=${Array.isArray(value) ? value : []} numbers=${numbers} onChange=${change} />`,
      true,
    );
  }
  return null;
}

// -- a service ----------------------------------------------------------------------------------

function ServiceStatus({ row, enabled, loaded, background }) {
  if (!loaded) return null;
  if (!background) return enabled ? html`<${Status}>${t("Starts when Libre Panel runs in the background")}<//>` : null;
  const state = row?.state;
  if (!enabled && (!state || state === "off")) return null;
  if (state === "running") return html`<${Status} tone="ok">${t("Running")}<//>`;
  if (state === "failed") return html`<${Status} tone="error">${t("Failed: {reason}", { reason: row.detail })}<//>`;
  if (state === "not-installed") return html`<${Status} tone="error">${t("Not installed")}<//>`;
  if (state === "pending") return html`<${Status} tone="warn">${t("Changing …")}<//>`;
  if (state === "stopped") return html`<${Status}>${t("Stopped")}<//>`;
  return null;
}

/** The timer's buttons: start or pause it and set it back, as its keyboard shortcuts do. */
function TimerButtons() {
  const press = (action) => control.shortcut(action).catch(toastError);
  return html`<${SettingRow} class="timer-buttons" title=${t("The timer now")} hint=${t("As the keyboard shortcuts do.")}>
    <button type="button" class="btn" onClick=${() => press("timer")}>${t("Start or pause")}</button>
    <button type="button" class="btn" onClick=${() => press("timer_reset")}>${t("Back to the start")}</button>
  <//>`;
}

/** The theme game mode shows ([modes.game] in config.toml): chosen here, beside the service,
 * as well as on the pages of Design. */
function GameScreen() {
  const pages = useStore((s) => s.pages);
  useEffect(() => {
    refreshPages();
  }, []);
  const game = pages?.pages?.find((page) => page.id === "game");
  if (!game) return null;
  return html`<${SettingRow} class="game-screen" title=${t("Screen while you play")}
      hint=${t("The theme the panel shows in game mode. Design has it too, on the panel's pages.")}>
    <${PageThemeSelect} page="game" theme=${game.theme} suggested=${game.suggested || []} label=${t("Screen while you play")} />
  <//>`;
}

function ServiceCard({ service, config, row, loaded, background }) {
  const [open, setOpen] = useState(false);
  const words = SERVICES[service.name];
  const enabled = config.services.enabled.includes(service.name);
  const given = config.services.options[service.name] || {};
  const title = words ? words.title() : service.name;
  const hint = words ? words.hint() : service.builtin ? "" : t("From {source}", { source: service.source });
  const id = `service-${service.name}`;
  return html`<section class="settings-card service" data-service=${service.name} aria-labelledby=${`${id}-title`}>
    <div class="settings-row service-head">
      <div class="settings-row-text">
        <span class="settings-row-title" id=${`${id}-title`}>${title}</span>
        ${hint ? html`<span class="settings-row-hint">${hint}</span>` : null}
        ${service.error ? html`<${Status} tone="error">${service.error}<//>` : html`<span class="settings-row-status"><${ServiceStatus} row=${row} enabled=${enabled} loaded=${loaded} background=${background} /></span>`}
      </div>
      <div class="settings-row-control">
        ${enabled && row?.restartable
          ? html`<button type="button" class="btn" onClick=${() => restartService(service.name)}>${t("Restart")}</button>`
          : null}
        <${Toggle} label=${title} checked=${enabled} disabled=${Boolean(service.error) && !enabled} onChange=${(on) => enableService(service.name, on)} />
      </div>
    </div>
    ${service.name === "game" ? html`<${GameScreen} />` : null}
    ${service.name === "timer" && enabled && background && row?.state === "running" ? html`<${TimerButtons} />` : null}
    ${service.options.length
      ? html`<button type="button" class="settings-disclosure small" aria-expanded=${String(open)} aria-controls=${`${id}-options`} onClick=${() => setOpen(!open)}>
            <span>${t("Options")} · ${service.options.length}</span>
            <${Icon} name=${open ? "chevron-up" : "chevron-down"} size=${14} />
          </button>
          ${open
            ? html`<div id=${`${id}-options`} class="settings-disclosure-body">
                ${service.options.map(
                  (option) => html`<${OptionRow} key=${option.key} service=${service.name} option=${option} given=${given[option.key]} />`,
                )}
              </div>`
            : null}`
      : null}
  </section>`;
}

function Services() {
  const config = useConfig();
  const catalog = useCatalog();
  const states = useStore((s) => s.serviceStates);
  usePolling(loadServiceStates, 3000);
  if (!config || !catalog) return null;
  const known = new Set(catalog.services.map((service) => service.name));
  // a service that config.toml switches on although nothing installed provides it
  const missing = config.services.enabled
    .filter((name) => !known.has(name))
    .map((name) => ({ name, source: "", builtin: false, error: "", options: [] }));
  const rows = new Map((states?.services || []).map((row) => [row.name, row]));
  return html`${[...catalog.services, ...missing].map(
      (service) => html`<${ServiceCard}
        key=${service.name}
        service=${service}
        config=${config}
        row=${rows.get(service.name)}
        loaded=${states !== null}
        background=${Boolean(states?.background)}
      />`,
    )}`;
}

export const serviceGroups = [
  { id: "services", order: 10, component: Services, load: loadServicesSection },
  { id: "notify", order: 20, component: NotifyCard },
];
