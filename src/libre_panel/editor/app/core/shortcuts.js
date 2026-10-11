// Keyboard shortcuts: one listener, bindings by combination ("mod+k",
// "shift+arrowleft", "delete"). "mod" is Ctrl. The newest binding that applies
// wins, so a dialog can take a key over while it is open.

import { t } from "./i18n.js";

const bindings = [];

/** The combination of a key event: "mod+shift+z", "arrowleft", "mod+plus". */
export function comboOf(event) {
  let key = event.key.toLowerCase();
  key = { " ": "space", esc: "escape", "+": "plus", "-": "minus", "=": "equal" }[key] || key;
  const parts = [];
  if (event.ctrlKey || event.metaKey) parts.push("mod");
  if (event.altKey) parts.push("alt");
  // shift belongs to letters and named keys, not to a symbol it produced ("+")
  const named = key.length > 1 && !["plus", "minus", "equal"].includes(key);
  if (event.shiftKey && (named || /[a-z0-9]/.test(key))) parts.push("shift");
  parts.push(key);
  return parts.join("+");
}

const typingIn = (target) =>
  target instanceof HTMLElement &&
  (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

/**
 * Call `handler(event)` for `combo` (one or a list). Options: `when()` (only
 * then), `inInputs` (also while typing in a field). The handler returns false to
 * leave the key to others. Returns a function that removes the binding.
 */
export function bindKey(combo, handler, { when, inInputs = false } = {}) {
  const binding = { combos: [combo].flat(), handler, when, inInputs };
  bindings.push(binding);
  return () => {
    const index = bindings.indexOf(binding);
    if (index >= 0) bindings.splice(index, 1);
  };
}

function dispatch(event) {
  if (event.defaultPrevented || event.isComposing) return;
  const combo = comboOf(event);
  const typing = typingIn(event.target);
  for (let i = bindings.length - 1; i >= 0; i--) {
    const binding = bindings[i];
    if (!binding.combos.includes(combo)) continue;
    if (typing && !binding.inInputs) continue;
    if (binding.when && !binding.when(event)) continue;
    if (binding.handler(event) === false) continue;
    event.preventDefault();
    event.stopPropagation();
    return;
  }
}

document.addEventListener("keydown", dispatch);

const KEY_NAMES = {
  plus: "+",
  minus: "−",
  equal: "=",
  arrowleft: "←",
  arrowright: "→",
  arrowup: "↑",
  arrowdown: "↓",
  enter: "↵",
  space: "␣",
};

/** How a combination is written on keys: "Strg K", "Strg ⇧ Z". */
export function keyLabel(combo) {
  const first = [combo].flat()[0];
  if (!first) return "";
  return first
    .split("+")
    .map((part) => {
      if (part === "mod") return t("Ctrl");
      if (part === "shift") return "⇧";
      if (part === "alt") return "Alt";
      if (part === "escape") return "Esc";
      if (part === "delete") return t("Del");
      if (part === "tab") return "Tab";
      return KEY_NAMES[part] || part.toUpperCase();
    })
    .join(" ");
}
