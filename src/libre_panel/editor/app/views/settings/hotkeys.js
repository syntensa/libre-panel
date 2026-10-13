// Settings → Start and system → Keyboard shortcuts: a key combination for the whole PC for each
// action of the background app (config.toml's [hotkeys], hotkeys.py), set by pressing the keys.
// The app says which ones it holds, which another program has taken, and where there can be
// none (Wayland). A switch turns them all off at once ([hotkeys] enabled) and keeps the keys.

import { t } from "../../core/i18n.js";
import { saveSettings, useConfig } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html, useLayoutEffect, useState } from "../../lib/preact.js";
import { Icon } from "../../ui/icons.js";
import { Toggle } from "../../ui/controls.js";
import { toastError } from "../../ui/toast.js";
import { Card, Note, SettingRow } from "./parts.js";

const ACTIONS = () => [
  { id: "pause", name: t("Pause or resume the panel") },
  { id: "brightness_up", name: t("Brightness up") },
  { id: "brightness_down", name: t("Brightness down") },
  { id: "open", name: t("Open Libre Panel") },
];

const NAMED = {
  Space: "Space",
  ArrowUp: "Up",
  ArrowDown: "Down",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  Home: "Home",
  End: "End",
  PageUp: "PageUp",
  PageDown: "PageDown",
  Insert: "Insert",
  Delete: "Delete",
  Pause: "Pause",
  ScrollLock: "ScrollLock",
};

/** The combination a key press makes, written as hotkeys.py writes it ("Ctrl+Alt+P"); null
 * while only Ctrl, Alt, Shift or Win is down. By the key's place, not its letter, so a German
 * keyboard's Z is Y as Windows counts it too. */
export function comboOf(event) {
  const code = event.code || "";
  let key = null;
  if (/^Key[A-Z]$/.test(code)) key = code.slice(3);
  else if (/^Digit[0-9]$/.test(code)) key = code.slice(5);
  else if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) key = code;
  else key = NAMED[code] || null;
  if (!key) return null;
  const mods = [event.ctrlKey && "Ctrl", event.altKey && "Alt", event.shiftKey && "Shift", event.metaKey && "Win"];
  return [...mods.filter(Boolean), key].join("+");
}

/** A combination as the keyboard at hand names its keys (Strg on a German one). */
const shown = (combo) =>
  combo
    .split("+")
    .map((part) => ({ Ctrl: t("Ctrl"), Shift: t("Shift") })[part] || part)
    .join("+");

/** The button that shows a shortcut and, clicked, takes the next keys pressed. */
function Recorder({ action, combo, onSet, disabled = false }) {
  const [listening, setListening] = useState(false);
  // (as the button turns, not after the next paint: keys pressed at once are not lost)
  useLayoutEffect(() => {
    if (!listening) return undefined;
    const down = (event) => {
      event.preventDefault();
      event.stopPropagation(); // the app's own keys (Ctrl+K) wait
      if (event.key === "Escape") return setListening(false);
      if (event.key === "Backspace" && !event.ctrlKey && !event.altKey && !event.metaKey) {
        setListening(false);
        return onSet(null);
      }
      const made = comboOf(event);
      if (!made) return undefined; // a modifier alone: the key comes next
      setListening(false);
      return onSet(made);
    };
    const away = () => setListening(false);
    window.addEventListener("keydown", down, true);
    window.addEventListener("blur", away);
    return () => {
      window.removeEventListener("keydown", down, true);
      window.removeEventListener("blur", away);
    };
  }, [listening]);
  return html`<button
    type="button"
    class=${`btn hotkey-recorder mono${listening ? " listening" : ""}`}
    aria-pressed=${String(listening)}
    data-hotkey=${action}
    disabled=${disabled}
    onClick=${() => setListening(!listening)}
  >
    ${listening ? t("Press the keys …") : combo ? shown(combo) : t("Not set")}
  </button>`;
}

export function HotkeysCard() {
  const config = useConfig();
  const app = useStore((s) => s.app);
  if (!config) return null;
  const state = app?.available && !app.quit ? app.hotkeys : null; // what the background app holds
  const on = config.hotkeys_enabled !== false;
  const set = (action, combo) =>
    saveSettings({ [`hotkeys.${action}`]: combo }).catch(toastError);
  const problem = (action) => {
    const error = state?.keys?.[action]?.error;
    if (!error) return null;
    return error === "taken" ? t("Another program has this combination: pick another.") : error;
  };
  return html`<${Card}
    id="hotkeys"
    title=${t("Keyboard shortcuts")}
    text=${t("Key combinations for the whole PC, whatever window is in front. Click one, then press the keys: Ctrl, Alt or Win with a key. Backspace takes it away.")}
  >
    <${SettingRow} title=${t("Use the shortcuts")} hint=${on ? null : t("Off: no key is taken, and the combinations stay for later.")} data-hotkeys-switch>
      <${Toggle}
        label=${t("Use the shortcuts")}
        checked=${on}
        onChange=${(value) => saveSettings({ "hotkeys.enabled": value }).catch(toastError)}
      />
    <//>
    ${on && state && !state.supported ? html`<div class="settings-row"><${Note} tone="warn">${state.why}<//></div>` : null}
    ${on && !state
      ? html`<div class="settings-row"><${Note}>${t("They work while Libre Panel runs in the background.")}<//></div>`
      : null}
    ${ACTIONS().map((action) => {
      const combo = config.hotkeys?.[action.id] || "";
      const wrong = on ? problem(action.id) : null;
      return html`<${SettingRow}
        key=${action.id}
        title=${action.name}
        status=${wrong ? html`<span class="status warn"><span class="dot"></span>${wrong}</span>` : null}
      >
        <${Recorder} action=${action.id} combo=${combo} disabled=${!on} onSet=${(made) => set(action.id, made)} />
        ${combo && on
          ? html`<button type="button" class="icon-btn" aria-label=${t("Take {name}'s shortcut away", { name: action.name })} title=${t("Take {name}'s shortcut away", { name: action.name })}
              onClick=${() => set(action.id, null)}><${Icon} name="close" size=${14} /></button>`
          : null}
      <//>`;
    })}
  <//>`;
}
