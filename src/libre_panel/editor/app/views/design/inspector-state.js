// Which page the inspector shows: the primary fields ("main") or the details.
// Another selection starts on the main page again.

import { store } from "../../core/state.js";
import { createStore } from "../../core/store.js";

export const inspectorState = createStore({ page: "main" });

let selected = store.get().selection;
store.subscribe((state) => {
  if (state.selection !== selected) {
    selected = state.selection;
    if (inspectorState.get().page !== "main") inspectorState.set({ page: "main" });
  }
});
