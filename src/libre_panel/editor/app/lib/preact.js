// Preact with its hooks and htm, vendored (vendor/SOURCES.txt). Components are
// written as html`<div class=${name}>…</div>`: no build step, plain ES modules.

import {
  Fragment,
  cloneElement,
  createContext,
  createRef,
  h,
  render,
  toChildArray,
} from "../vendor/preact.module.js";
import {
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useErrorBoundary,
  useReducer,
  useRef,
  useState,
} from "../vendor/hooks.module.js";
import htm from "../vendor/htm.module.js";

export const html = htm.bind(h);

export {
  Fragment,
  cloneElement,
  createContext,
  createRef,
  h,
  render,
  toChildArray,
  useCallback,
  useContext,
  useEffect,
  useErrorBoundary,
  useLayoutEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
};
