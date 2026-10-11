// What the built-in pages share: messages, requests, polling and small building blocks.
import { api, button, el, field, setStatus, t, withToken } from "/static/kit.js";

export { api, button, el, field, t };

// The address of the page's own API, for what is not JSON (a picture): an <img> sends no header,
// so the token goes in the address.
export const pageUrl = (path) =>
  withToken(`/api/plugins/${encodeURIComponent(location.pathname.split("/")[2] || "")}/${path}`);

export const $ = (id) => document.getElementById(id);

let statusTimer = 0;

// A line at the bottom of the page; kind is "", "warn" or "error". It goes after a few seconds.
export function say(text, kind = "", seconds = 6) {
  setStatus($("status"), text, kind);
  clearTimeout(statusTimer);
  if (seconds) statusTimer = setTimeout(() => setStatus($("status"), ""), seconds * 1000);
}

// A POST whose error is shown where the user looks. Gives null instead of throwing.
export async function send(path, body) {
  try {
    return await api(path, { method: "POST", body });
  } catch (error) {
    say(error.message, "error", 12);
    return null;
  }
}

// Runs `work` every `ms` while the page can be seen, never two at once. Gives a stop function.
export function every(ms, work) {
  let busy = false;
  const timer = setInterval(async () => {
    if (document.hidden || busy) return;
    busy = true;
    try {
      await work();
    } catch {
      /* the next round tries again */
    } finally {
      busy = false;
    }
  }, ms);
  return () => clearInterval(timer);
}

// A box with a message; kind is "", "warn" or "error".
export const note = (kind, text) => el("p", { class: `note ${kind}`.trim() }, text);

export const section = (title, ...content) => el("section", { class: "block" }, el("h2", { text: title }), ...content);

// The state dots of the editor (editor.css): showing = green, waiting = amber, error = red, gone = grey.
export const dot = (state) => el("span", { class: "state", "data-state": state }, el("span", { class: "dot" }));

export function duration(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours) return t("{h} h {m} min", { h: hours, m: minutes });
  if (minutes) return t("{m} min {s} s", { m: minutes, s: seconds % 60 });
  return t("{s} s", { s: seconds });
}
