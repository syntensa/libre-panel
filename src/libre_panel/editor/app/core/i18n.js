// Texts are written in English and looked up in the language catalog the
// server sends (locale/<lang>.json). Call t() with the text as a plain string
// literal and the values as an object: the tests collect the texts from these
// calls, so a text built from parts would be missed.

import { endpoints } from "./api.js";
import { touch } from "./doc.js";
import { store } from "./state.js";

let catalog = { language: "en", setting: "auto", languages: { en: "English", de: "Deutsch" } };

/** Translate an English text; {name} placeholders are filled from `vars`. */
export function t(text, vars = {}) {
  const message = catalog.messages?.[text] ?? text;
  return message.replace(/\{(\w+)\}/g, (all, name) => (name in vars ? String(vars[name]) : all));
}

export const language = () => catalog.language || "en";
export const languageSetting = () => catalog.setting || "auto";
export const languages = () => catalog.languages || {};

// The widget types' names in English (the catalogs name them in other languages).
const WIDGET_NAMES = {
  rect: "Box",
  "text": "Text",
  metric: "Value",
  bar: "Bar",
  gauge: "Ring",
  graph: "Graph",
  clock: "Clock",
  analog: "Analog clock",
  countdown: "Countdown",
  moon: "Moon phase",
  agent: "AI sign",
  fan: "Fan",
  image: "Picture",
  weather: "Weather",
  icon: "Icon",
  symbol: "Weather symbol",
  downloads: "Download display",
  calendar: "Month calendar",
  list: "List",
  module: "Module",
};

/** Field names from the theme's field specs ("font_size" → "font size"). */
export const fieldLabel = (key) => catalog.fields?.[key] ?? key.replace(/_/g, " ");
export const widgetLabel = (type) =>
  store.get().specs?.widget_labels?.[type] ?? catalog.widgets?.[type] ?? WIDGET_NAMES[type] ?? type;
export const enumLabel = (value) => catalog.enums?.[value] ?? store.get().specs?.enum_labels?.[value] ?? value;
export const iconLabel = (name) => catalog.icons?.[name] ?? name;

/** A number in the language's way: 1,5 in German. `fixed`: always `digits` decimals. */
export function formatNumber(value, digits = 0, { fixed = false, grouping = true } = {}) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "–";
  return value.toLocaleString(language(), {
    maximumFractionDigits: digits,
    minimumFractionDigits: fixed ? digits : 0,
    useGrouping: grouping,
  });
}

function apply(data) {
  catalog = data;
  document.documentElement.lang = data.language || "en";
  store.set((state) => ({ lang: state.lang + 1 }));
}

export async function loadLanguage() {
  apply(await endpoints.i18n());
}

/**
 * Switch the language ("auto" or one the server lists); config.toml keeps it, the tray and the panel follow.
 * The server translates some of what it sends (the building blocks' names, the panels' notes,
 * the titles of plugins' pages), so that is asked for again before the app changes over; and
 * the panel's preview has dates and weather in the language, so it is drawn again.
 */
export async function setLanguage(setting) {
  const data = await endpoints.setLanguage(setting);
  const [specs, plugins] = await Promise.all([
    endpoints.specs().catch(() => null),
    endpoints.pluginPages().catch(() => null),
  ]);
  store.set({ ...(specs && { specs }), ...(plugins && { plugins }) });
  apply(data);
  touch();
}
