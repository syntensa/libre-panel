// A small observable store and the hook components read it with.

import { useLayoutEffect, useReducer, useRef } from "../lib/preact.js";

/** One state object, replaced (shallowly) on every change. */
export function createStore(initial) {
  let state = initial;
  const listeners = new Set();
  return {
    get: () => state,
    /** Merge `patch` (or what `patch(state)` returns) into the state. */
    set(patch) {
      const next = typeof patch === "function" ? patch(state) : patch;
      if (!next) return;
      state = { ...state, ...next };
      for (const listener of [...listeners]) listener(state);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** Equal values, or arrays and plain objects whose members are the same. */
export function shallowEqual(a, b) {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => Object.is(a[key], b[key]));
}

/** The part of the store `selector` picks; the component renders again when it changes. */
export function useSelector(store, selector) {
  const [, rerender] = useReducer((n) => n + 1, 0);
  const ref = useRef({});
  const value = selector(store.get());
  ref.current.selector = selector;
  ref.current.value = value;
  useLayoutEffect(
    () =>
      store.subscribe((state) => {
        const next = ref.current.selector(state);
        if (!shallowEqual(next, ref.current.value)) rerender();
      }),
    [store],
  );
  return value;
}
