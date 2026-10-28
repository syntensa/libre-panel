// The cards of the card slot (config.toml cards): what Lumen's slot shows, in each of its
// versions, in the order they are asked; the first that has something to show is on the panel.
// On Start while such a theme is on the panel, and in Look.

import { t } from "../../core/i18n.js";
import { loadSettings, saveSettings, useConfig } from "../../core/settings.js";
import { store } from "../../core/state.js";
import { html, useEffect } from "../../lib/preact.js";
import { navigate } from "../../shell/views.js";
import { IconButton, Toggle } from "../../ui/controls.js";

// as config.py has them (DEFAULT_CARDS), until the settings are read
const DEFAULT_CARDS = ["playback", "ai", "forecast", "date"];

/** The cards offered, each with when it has something to show (render/slot.py). */
export const CARD_CHOICES = () => [
  { id: "playback", name: t("Music"), when: t("while a track plays") },
  { id: "ai", name: t("AI activity"), when: t("while Claude Code or Codex works or waits for you") },
  { id: "forecast", name: t("Weather"), when: t("once the weather is set up") },
  { id: "download", name: t("Download"), when: t("while a download runs") },
  { id: "date", name: t("Calendar"), when: t("always: on show when no card before it has something") },
];

/** The cards as chosen, then those that are not, each once. */
export function CardsChoice() {
  const config = useConfig();
  useEffect(() => {
    if (!store.get().settings) loadSettings();
  }, []);
  if (!config) return null;
  const cardsOf = (settings) => (Array.isArray(settings?.cards) && settings.cards.length ? settings.cards : DEFAULT_CARDS);
  const chosen = cardsOf(config);
  const offered = CARD_CHOICES();
  const known = (id) => offered.find((card) => card.id === id) || { id, name: id, when: "" };
  const rows = [...chosen.map(known), ...offered.filter((card) => !chosen.includes(card.id))];
  // from the cards as they are when the write goes (a click while one is on its way counts too)
  const save = (change) => saveSettings((now) => ({ cards: change([...cardsOf(now)]) }));
  const turn = (id, on) => save((cards) => (on ? [...cards.filter((name) => name !== id), id] : cards.filter((name) => name !== id)));
  const move = (id, by) =>
    save((cards) => {
      const at = cards.indexOf(id);
      if (at < 0 || at + by < 0 || at + by >= cards.length) return cards;
      [cards[at], cards[at + by]] = [cards[at + by], cards[at]];
      return cards;
    });
  const agentsOff = !(config.services?.enabled || []).includes("agents");
  return html`<ol class="pages-card-rows cards-choice" aria-label=${t("Cards")}>
    ${rows.map((card) => {
      const at = chosen.indexOf(card.id);
      const on = at >= 0;
      return html`<li key=${card.id} class=${`pages-card-row${on ? "" : " off"}`} data-card=${card.id}>
        <span class="cards-choice-place" aria-hidden="true">${on ? at + 1 : ""}</span>
        <span class="pages-card-text">
          <strong>${card.name}</strong>
          <span class="muted">${card.when}</span>
          ${card.id === "ai" && on && agentsOff
            ? html`<button type="button" class="link-btn cards-choice-setup" onClick=${() => navigate("settings", "services")}>
                ${t("Set up AI activity first")}
              </button>`
            : null}
        </span>
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
    })}
  </ol>`;
}

/** Start's card, while the theme on the panel has a slot that leaves its cards to the person. */
export function CardsCard({ shown }) {
  if (!shown?.cards) return null;
  return html`<section class="cards-card pages-card" aria-labelledby="cards-card-title">
    <div class="pages-card-head">
      <h2 id="cards-card-title">${t("Cards on the panel")}</h2>
    </div>
    <p class="muted cards-card-hint">
      ${t("The card on the panel is the first of these that has something to show. The choice holds for every version of {theme}.", { theme: shown.name })}
    </p>
    <${CardsChoice} />
  </section>`;
}
