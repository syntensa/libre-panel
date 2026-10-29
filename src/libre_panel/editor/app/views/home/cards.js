// The cards of the card slot (config.toml cards): what Lumen's slot shows, in each of its
// versions, in the order they are asked; the first that has something to show is on the panel,
// or they take turns, each for its minutes (card_order, "forecast for 5m"), and go from one to
// the next as card_transition says. On Start while such a theme is on the panel, and in Look.

import { t } from "../../core/i18n.js";
import { loadSettings, saveSettings, useConfig } from "../../core/settings.js";
import { store } from "../../core/state.js";
import { html, useEffect } from "../../lib/preact.js";
import { navigate } from "../../shell/views.js";
import { Field, IconButton, NumberField, Segmented, Select, Toggle } from "../../ui/controls.js";

// as config.py has them (DEFAULT_CARDS), until the settings are read
const DEFAULT_CARDS = ["playback", "ai", "forecast", "date"];
export const TURN_MINUTES = 5; // a card's turn when its line names none (the slot's turn)

/** The card a line names: "forecast for 5m" → "forecast" (theme.modules card_line). */
export const cardOfLine = (line) => String(line).split("#")[0].split(" if ")[0].split(" for ")[0].trim();

/** The minutes of a line's own turn ("forecast for 5m" → 5, "for 90s" → 1.5), else null. */
export function minutesOf(line) {
  const time = String(line).split("#")[0].split(" if ")[0].split(" for ")[1];
  const found = time && /^\s*([0-9]+(?:\.[0-9]+)?)\s*(s|sec|m|min|h)?\s*$/i.exec(time);
  if (!found) return null;
  const size = { s: 1 / 60, sec: 1 / 60, m: 1, min: 1, h: 60 }[(found[2] || "min").toLowerCase()];
  return Number(found[1]) * size;
}

/** A line with the card's turn ("forecast for 5m", "clock for 90s"), its condition kept. */
export function lineWith(line, minutes) {
  const [head, ...rule] = String(line).split(" if ");
  const card = cardOfLine(head);
  const seconds = Math.round(minutes * 60);
  const time = seconds % 3600 === 0 ? `${seconds / 3600}h` : seconds % 60 === 0 ? `${seconds / 60}m` : `${seconds}s`;
  return [`${card} for ${time}`, ...rule].join(" if ");
}

/** How a slot shows its cards (theme.modules CARD_ORDERS). */
export const ORDERS = () => [
  { value: "first", label: t("first with something to show") },
  { value: "turns", label: t("in turn") },
];

/** How a slot goes from one card to the next (theme.modules CARD_TRANSITIONS). */
export const TRANSITIONS = () => [
  { value: "light", label: t("Line of light") },
  { value: "fade", label: t("Fade") },
  { value: "slide", label: t("Push to the left") },
  { value: "rise", label: t("Push upwards") },
  { value: "wipe", label: t("Wipe") },
  { value: "cut", label: t("At once") },
  { value: "random", label: t("Random") },
];

/** What the order means, for its field's hint. */
export const orderHint = (order) =>
  order === "turns"
    ? t("Each card is on show for its minutes, then the next; one with nothing to show is passed over.")
    : t("The card on show is the first that has something to show: what plays while music plays, else the next.");

/** Which card is on show and how one goes to the next: the person's (config.toml card_order,
 * card_transition) or a slot's own (its order and transition, in Design). */
export function CardsHow({ order, transition, onOrder, onTransition }) {
  return html`<div class="cards-choice-how">
    <${Field} label=${t("Which card is on show")} hint=${orderHint(order)}>
      <${Segmented} label=${t("Which card is on show")} value=${order} options=${ORDERS()} onChange=${onOrder} />
    <//>
    <${Field} label=${t("From one card to the next")}>
      <${Select} label=${t("From one card to the next")} value=${transition} options=${TRANSITIONS()} onChange=${onTransition} />
    <//>
  </div>`;
}

/** The cards offered, each with when it has something to show (render/slot.py). */
export const CARD_CHOICES = () => [
  { id: "playback", name: t("Music"), when: t("while a track plays") },
  { id: "ai", name: t("AI activity"), when: t("while Claude Code or Codex works or waits for you") },
  { id: "forecast", name: t("Weather"), when: t("once the weather is set up") },
  { id: "download", name: t("Download"), when: t("while a download runs") },
  { id: "date", name: t("Calendar"), when: t("always"), always: true },
  { id: "analog", name: t("Analog clock"), when: t("always"), always: true },
  { id: "clock", name: t("Clock and date"), when: t("always"), always: true },
  { id: "world", name: t("World clocks"), when: t("always"), always: true },
  { id: "outlook", name: t("The week's weather"), when: t("once the weather is set up") },
  { id: "moon", name: t("Moon"), when: t("always"), always: true },
  { id: "sun", name: t("Sun and moon"), when: t("always"), always: true },
  { id: "agenda", name: t("Agenda"), when: t("always: your appointments once a calendar is set up"), always: true },
  { id: "net", name: t("Network"), when: t("always"), always: true },
];

/** The cards as chosen, then those that are not, each once; how they take turns and change. */
export function CardsChoice() {
  const config = useConfig();
  useEffect(() => {
    if (!store.get().settings) loadSettings();
  }, []);
  if (!config) return null;
  const cardsOf = (settings) => (Array.isArray(settings?.cards) && settings.cards.length ? settings.cards : DEFAULT_CARDS);
  const lines = cardsOf(config);
  const chosen = lines.map(cardOfLine);
  const order = config.card_order || "first";
  const transition = config.card_transition || "light";
  const offered = CARD_CHOICES();
  const known = (id) => offered.find((card) => card.id === id) || { id, name: id, when: "" };
  const left = offered.filter((card) => !chosen.includes(card.id)); // (behind "More cards")
  // from the cards as they are when the write goes (a click while one is on its way counts too)
  const save = (change) => saveSettings((now) => ({ cards: change([...cardsOf(now)]) }));
  const turn = (id, on) =>
    save((cards) => {
      const others = cards.filter((line) => cardOfLine(line) !== id);
      return on ? [...others, id] : others;
    });
  const move = (id, by) =>
    save((cards) => {
      const at = cards.findIndex((line) => cardOfLine(line) === id);
      if (at < 0 || at + by < 0 || at + by >= cards.length) return cards;
      [cards[at], cards[at + by]] = [cards[at + by], cards[at]];
      return cards;
    });
  const time = (id, minutes) => save((cards) => cards.map((line) => (cardOfLine(line) === id ? lineWith(line, minutes) : line)));
  const agentsOff = !(config.services?.enabled || []).includes("agents");
  // first with something to show: a card after one that always has something never comes
  const blocker = order === "first" ? chosen.map(known).find((card) => card.always) : null;
  const hidden = (at) => blocker && at > chosen.indexOf(blocker.id);
  const row = (card) => {
    const at = chosen.indexOf(card.id);
    const on = at >= 0;
    return html`<li key=${card.id} class=${`pages-card-row${on ? "" : " off"}`} data-card=${card.id}>
      <span class="cards-choice-place" aria-hidden="true">${on ? at + 1 : ""}</span>
      <span class="pages-card-text">
        <strong>${card.name}</strong>
        <span class="muted">${card.when}</span>
        ${on && hidden(at)
          ? html`<span class="cards-choice-never">${t("Never on show: {card} before it always has something. Move this one up, or let the cards take turns.", { card: blocker.name })}</span>`
          : null}
        ${card.id === "ai" && on && agentsOff
          ? html`<button type="button" class="link-btn cards-choice-setup" onClick=${() => navigate("settings", "services")}>
              ${t("Set up AI activity first")}
            </button>`
          : null}
      </span>
      ${on && order === "turns"
        ? html`<span class="cards-choice-time">
            <${NumberField} label=${t("Minutes on show: {card}", { card: card.name })} value=${minutesOf(lines[at]) ?? TURN_MINUTES}
              min=${0.25} max=${1440} step=${0.25} digits=${2} unit=${t("min")} onChange=${(minutes) => time(card.id, minutes)} />
          </span>`
        : null}
      ${on
        ? html`<span class="cards-choice-move">
            <${IconButton} icon="chevron-up" label=${t("Earlier: {card}", { card: card.name })} disabled=${at === 0}
              onClick=${() => move(card.id, -1)} />
            <${IconButton} icon="chevron-down" label=${t("Later: {card}", { card: card.name })} disabled=${at === chosen.length - 1}
              onClick=${() => move(card.id, 1)} />
          </span>`
        : null}
      <${Toggle} label=${t("Show the card {card}", { card: card.name })} checked=${on}
        disabled=${on && chosen.length === 1} onChange=${(value) => turn(card.id, value)} />
    </li>`;
  };
  return html`<${CardsHow} order=${order} transition=${transition}
      onOrder=${(value) => saveSettings({ card_order: value })} onTransition=${(value) => saveSettings({ card_transition: value })} />
    <ol class="pages-card-rows cards-choice" aria-label=${t("Cards")}>
      ${chosen.map((id) => row(known(id)))}
    </ol>
    ${left.length
      ? html`<details class="cards-choice-more">
          <summary>${t("More cards ({n})", { n: left.length })}</summary>
          <ol class="pages-card-rows cards-choice" aria-label=${t("More cards")}>
            ${left.map(row)}
          </ol>
        </details>`
      : null}`;
}

/** Start's card, while the theme on the panel has a slot that leaves its cards to the person. */
export function CardsCard({ shown }) {
  if (!shown?.cards) return null;
  return html`<section class="cards-card pages-card" aria-labelledby="cards-card-title">
    <div class="pages-card-head">
      <h2 id="cards-card-title">${t("Cards on the panel")}</h2>
    </div>
    <p class="muted cards-card-hint">
      ${t("The choice holds for every version of {theme}.", { theme: shown.name })}
    </p>
    <${CardsChoice} />
  </section>`;
}
