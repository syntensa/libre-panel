// Settings → Weather and place: the place first (the weather needs it, and the sun and the moon
// use it as well), then the weather (off until it is turned on), its units and how often it is
// asked for, then the look of the weather symbols and the moon (it takes effect at once). Searching
// a place sends the name that was typed to Open-Meteo, and the weather sends the place's
// coordinates there: nothing else leaves this computer for either, and neither happens
// unasked. The sources are made when the panel starts, so a change takes effect after a
// restart.

import { endpoints } from "../../core/api.js";
import { formatNumber, t } from "../../core/i18n.js";
import { loadSettings, saveSettings, searchPlaces, useConfig } from "../../core/settings.js";
import { html, useState } from "../../lib/preact.js";
import { Segmented, Toggle } from "../../ui/controls.js";
import { confirmDialog } from "../../ui/dialog.js";
import { Card, Note, RestartNote, SettingRow } from "./parts.js";

const INTERVALS = [5, 15, 30, 60];

const coordinates = (latitude, longitude) =>
  `${formatNumber(latitude, 2, { fixed: true })}, ${formatNumber(longitude, 2, { fixed: true })}`;

function WeatherCard() {
  const config = useConfig();
  if (!config) return null;
  const { enabled, latitude, longitude, units, update_minutes: minutes } = config.weather;
  const hasPlace = latitude !== null && longitude !== null;
  const intervals = INTERVALS.includes(minutes) ? INTERVALS : [...INTERVALS, minutes].sort((a, b) => a - b);
  return html`<${Card} id="weather">
      <${SettingRow}
        title=${t("Weather")}
        hint=${!hasPlace
          ? t("Choose a place first. Off until you turn it on; then only its coordinates go to Open-Meteo.")
          : enabled
            ? t("Only the coordinates of your place go to Open-Meteo, every {minutes} minutes. Nothing else leaves this computer for it.", { minutes })
            : t("Off until you turn it on. Then only the coordinates of your place go to Open-Meteo, every {minutes} minutes.", { minutes })}
      >
        <${Toggle}
          label=${t("Weather")}
          checked=${enabled}
          disabled=${!hasPlace && !enabled}
          onChange=${(on) => saveSettings({ "weather.enabled": on })}
        />
      <//>
      <${SettingRow} title=${t("Units")} hint=${t("Temperatures and wind on the panel.")}>
        <${Segmented}
          label=${t("Units")}
          value=${units}
          onChange=${(value) => saveSettings({ "weather.units": value })}
          options=${[
            { value: "metric", label: "°C, km/h" },
            { value: "imperial", label: "°F, mph" },
          ]}
        />
      <//>
      <${SettingRow} title=${t("Update")} hint=${t("How often the weather is asked for.")}>
        <${Segmented}
          label=${t("Update")}
          value=${minutes}
          onChange=${(value) => saveSettings({ "weather.update_minutes": value })}
          options=${intervals.map((value) => ({ value, label: t("{minutes} min", { minutes: value }) }))}
        />
      <//>
    <//>
    <p class="settings-credit">
      ${t("Weather data by")} <a href="https://open-meteo.com" target="_blank" rel="noopener noreferrer">Open-Meteo.com</a> (CC BY 4.0)
    </p>`;
}

// -- the look of the symbols -------------------------------------------------------------------

// config.toml's ``symbols`` (config.SYMBOL_PACKS), the first first
const PACKS = [
  { id: "classic", label: () => t("Classic"), hint: () => t("the first: calm and simple") },
  { id: "vivid", label: () => t("Vivid"), hint: () => t("sharper, with more to it: a corona, snow crystals, the moon as it looks") },
];

/** The look of the weather symbols and the moon, tiles with a picture of each; a theme may give a
 * symbol a look of its own. */
function SymbolsCard() {
  const config = useConfig();
  if (!config) return null;
  const current = PACKS.some((pack) => pack.id === config.symbols) ? config.symbols : PACKS[0].id;
  return html`<${Card} id="symbols" title=${t("Weather symbols and moon")}
      text=${t("How they look on the panel, where a theme leaves it to you. The panel takes it at once.")}>
    <div class="fan-look-tiles symbol-pack-tiles" role="radiogroup" aria-label=${t("Look of the weather symbols")}>
      ${PACKS.map(
        (pack) => html`<button type="button" role="radio" key=${pack.id} class="fan-look-tile symbol-pack-tile" data-pack=${pack.id}
          aria-checked=${String(pack.id === current)} onClick=${() => pack.id !== current && saveSettings({ symbols: pack.id })}>
          <img src=${endpoints.symbolsLookUrl(pack.id)} alt="" width="240" height="80" decoding="async" />
          <span>${pack.label()}</span>
          <span class="muted">${pack.hint()}</span>
        </button>`,
      )}
    </div>
  <//>`;
}

// -- the place -----------------------------------------------------------------------------------

function PlaceCard() {
  const config = useConfig();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState(null); // null: not searched yet
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  if (!config) return null;
  const { enabled, latitude, longitude, place } = config.weather;
  const hasPlace = latitude !== null && longitude !== null;
  const search = async () => {
    if (busy || query.trim().length < 2) return;
    setBusy(true);
    setProblem("");
    try {
      setFound(await searchPlaces(query.trim()));
    } catch (error) {
      setFound(null);
      setProblem(error.message);
    }
    setBusy(false);
  };
  const choose = async (item) => {
    const ok = await saveSettings({
      "weather.latitude": item.latitude,
      "weather.longitude": item.longitude,
      "weather.place": item.name,
    });
    if (ok) {
      setFound(null);
      setQuery("");
    }
  };
  const remove = async () => {
    const sure =
      !enabled ||
      (await confirmDialog({
        title: t("Remove the place?"),
        message: t("The weather needs a place, so it switches off with it."),
        confirm: t("Remove"),
        danger: true,
      }));
    if (sure) {
      await saveSettings({
        "weather.enabled": false,
        "weather.latitude": null,
        "weather.longitude": null,
        "weather.place": null,
      });
    }
  };
  return html`<${RestartNote} prefix="weather." />
    <${Card} id="place" title=${t("Place")} text=${t("For the weather, and for sunrise, sunset and the moon, also while the weather is off.")}>
    <${SettingRow}
      title=${hasPlace ? place || t("A place set in config.toml") : t("No place yet")}
      hint=${hasPlace ? coordinates(latitude, longitude) : t("Search for a town or a district below.")}
    >
      ${hasPlace ? html`<button type="button" class="link-btn" onClick=${remove}>${t("Remove")}</button>` : null}
    <//>
    <${SettingRow}
      title=${hasPlace ? t("Another place") : t("Search")}
      hint=${t("The name you type goes to the place search of Open-Meteo, and only when you press Search. The place is kept to about a kilometre.")}
      stacked
    >
      <form
        class="place-search"
        onSubmit=${(event) => {
          event.preventDefault();
          search();
        }}
      >
        <input
          type="text"
          class="input"
          aria-label=${t("Name of a place")}
          placeholder=${t("Name of a place")}
          value=${query}
          maxlength="100"
          spellcheck="false"
          onInput=${(event) => setQuery(event.currentTarget.value)}
        />
        <button type="submit" class="btn" disabled=${busy || query.trim().length < 2}>${busy ? t("Searching …") : t("Search")}</button>
      </form>
      ${problem ? html`<${Note} tone="error">${problem}<//>` : null}
      ${found && !found.length ? html`<span class="muted">${t("Nothing found. Try another spelling, or only the town.")}</span>` : null}
      ${found && found.length
        ? html`<ul class="places" aria-label=${t("Places found")}>
            ${found.map(
              (item) => html`<li key=${`${item.latitude},${item.longitude},${item.name}`}>
                <button type="button" onClick=${() => choose(item)}>
                  <span>${item.name}</span>
                  <span class="muted mono">${coordinates(item.latitude, item.longitude)}</span>
                </button>
              </li>`,
            )}
          </ul>`
        : null}
    <//>
  <//>`;
}

export const weatherGroups = [
  { id: "place", order: 10, component: PlaceCard, load: loadSettings },
  { id: "weather", order: 20, component: WeatherCard },
  { id: "symbols", order: 30, component: SymbolsCard },
];
