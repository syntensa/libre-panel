// The console API (docs/ARCHITECTURE.md, "The console API"): the panel's state and numbers,
// the picture on it, the sensor sources and services, test messages and the log. Views
// poll it politely: only while they are shown and the page is visible, and the next
// request waits for the last one.

import { useCallback, useEffect, useRef, useState } from "../lib/preact.js";
import { authHeaders, get, post } from "./api.js";

const BASE = "/api/plugins/control";

export const control = {
  panel: () => get(`${BASE}/panel`),
  sources: () => get(`${BASE}/sources`),
  services: () => get(`${BASE}/services`),
  probes: () => get(`${BASE}/probes`),
  log: (lines = 60) => get(`${BASE}/log?lines=${lines}`),
  probe: (kind) => post(`${BASE}/probe`, { kind }),
  enableService: (service, enabled) => post(`${BASE}/services/enable`, { service, enabled }),
  restartService: (service) => post(`${BASE}/services/restart`, { service }),
  liveUrl: (width = 960, panel = 1) => `${BASE}/live?w=${width}&format=jpeg${panel > 1 ? `&panel=${panel}` : ""}`,
};

/**
 * `load()` now and then every `ms` while the page is visible; the next request is made
 * `ms` after the last one is answered, so none waits behind another. Returns the last
 * answer (`data`), the last failure's message (`error`, until the next answer) and
 * `reload()`, which asks again at once (after an action). `on: false` stops it.
 */
export function usePolled(load, ms, { on = true, deps = [] } = {}) {
  const [state, setState] = useState({ data: null, error: null, loaded: false });
  const latest = useRef(load);
  latest.current = load;
  const again = useRef(() => {});
  useEffect(() => {
    if (!on) return undefined;
    let alive = true;
    let timer = 0;
    let busy = false;
    const tick = async () => {
      clearTimeout(timer);
      if (!alive || busy) return;
      if (!document.hidden) {
        busy = true;
        try {
          const data = await latest.current();
          if (alive) setState({ data, error: null, loaded: true });
        } catch (error) {
          if (alive) setState((now) => ({ ...now, error: error.message || String(error), loaded: true }));
        }
        busy = false;
      }
      if (alive) timer = setTimeout(tick, ms);
    };
    again.current = tick;
    const visible = () => !document.hidden && tick();
    document.addEventListener("visibilitychange", visible);
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [ms, on, ...deps]);
  const reload = useCallback(() => again.current(), []);
  return { ...state, reload };
}

const NO_PICTURE = { src: null, status: 0 };

/**
 * The picture on the panel now (`control/live`), one after the other: the next is asked for
 * `every` ms after the last has come, and the old one stays on show meanwhile. Returns
 * `{src, status}`: `src` is a blob URL (or null before the first picture and while the panel
 * has none), `status` is 0 until an answer, 200, or the error's HTTP status (404: none yet,
 * which is also what a paused panel says; -1: no answer at all). `on: false` stops it.
 */
export function useLivePicture({ width = 960, every = 250, on = true, panel = 1 } = {}) {
  const [shown, setShown] = useState(NO_PICTURE);
  const url = useRef(null);
  const keep = (src, status) => {
    if (url.current && url.current !== src) URL.revokeObjectURL(url.current);
    url.current = src;
    setShown({ src, status });
  };
  useEffect(() => () => url.current && URL.revokeObjectURL(url.current), []);
  useEffect(() => {
    if (!on) return undefined;
    let alive = true;
    let timer = 0;
    const next = async () => {
      if (!alive) return;
      let wait = every;
      if (!document.hidden) {
        try {
          const response = await fetch(control.liveUrl(width, panel), { headers: authHeaders() });
          if (!alive) return;
          if (response.ok) {
            const picture = await response.blob();
            if (!alive) return; // the view is gone: nobody revokes a URL made now
            keep(URL.createObjectURL(picture), 200);
          } else {
            keep(null, response.status);
            wait = Math.max(every, 1500);
          }
        } catch {
          if (!alive) return;
          keep(null, -1);
          wait = Math.max(every, 1500);
        }
      }
      if (alive) timer = setTimeout(next, wait);
    };
    next();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [width, every, on, panel]);
  return on ? shown : NO_PICTURE;
}
