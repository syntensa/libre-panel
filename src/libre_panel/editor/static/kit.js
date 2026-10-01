// Libre Panel kit for plugin editor pages (docs/PLUGINS.md, "Editor pages").
//
//   <link rel="stylesheet" href="/static/editor.css">
//   <script type="module" src="app.js"></script>
//
//   import { api, loadTexts, t, el, field, button, setStatus } from "/static/kit.js";
//
// The page runs at /plugins/<id>/; api() talks to the plugin's own handler at
// /api/plugins/<id>/<path>. Writes (POST) carry the editor's header.

const pageId = () => decodeURIComponent(location.pathname.split("/")[2] || "");

// Editor colours and fonts without the editor's own layout.
document.body?.classList.add("plugin-page");

export async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (method !== "GET") {
    options.headers["X-Libre-Panel"] = "1";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body ?? {});
  }
  const response = await fetch(`/api/plugins/${encodeURIComponent(pageId())}/${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

let catalog = { language: "en", messages: {} };

// The editor's language and its texts (the page brings its own texts).
export async function loadTexts() {
  const response = await fetch("/api/i18n");
  catalog = await response.json();
  document.documentElement.lang = catalog.language || "en";
  return catalog;
}

export const language = () => catalog.language || "en";

export function t(text, vars = {}) {
  const message = catalog.messages?.[text] ?? text;
  return message.replace(/\{(\w+)\}/g, (all, name) => (name in vars ? String(vars[name]) : all));
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// A labelled control, as in the editor's inspector.
export function field(label, control, hint) {
  return el("label", { class: "field" }, el("span", { text: label }), control, hint ? el("small", { text: hint }) : null);
}

export function button(text, onclick, { primary = false, title } = {}) {
  return el("button", { type: "button", class: primary ? "primary" : null, text, title, onclick });
}

// A one-line message; kind is "", "warn" or "error".
export function setStatus(node, text, kind = "") {
  node.textContent = text;
  node.className = `status ${kind}`.trim();
}
