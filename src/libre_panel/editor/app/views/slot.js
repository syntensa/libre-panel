// The place a view takes before it is built: its name and what it will hold.

import { html } from "../lib/preact.js";
import { Icon } from "../ui/icons.js";

export function EmptySlot({ icon, title, text, children }) {
  return html`<div class="slot">
    <div class="slot-card">
      <span class="slot-icon"><${Icon} name=${icon} size=${26} /></span>
      <h1>${title}</h1>
      <p>${text}</p>
      ${children ? html`<div class="slot-actions">${children}</div>` : null}
    </div>
  </div>`;
}
