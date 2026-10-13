// The command palette (Ctrl+K): finds any command by a few letters of its name.
// ↑ ↓ choose, ↵ runs, Tab shows a preview, Esc closes.

import { commandById, run, searchCommands, text } from "../core/commands.js";
import { t } from "../core/i18n.js";
import { bindKey, keyLabel } from "../core/shortcuts.js";
import { createStore, useSelector } from "../core/store.js";
import { html, useEffect, useLayoutEffect, useMemo, useRef, useState } from "../lib/preact.js";
import { dialogOpen } from "./dialog.js";
import { Icon } from "./icons.js";
import { toastError } from "./toast.js";

const palette = createStore({ open: false, query: "" });

export function openPalette(query = "") {
  palette.set({ open: true, query });
}

export function closePalette() {
  palette.set({ open: false });
}

export const paletteOpen = () => palette.get().open;

bindKey(["mod+k", "mod+shift+p"], () => (paletteOpen() ? closePalette() : openPalette()), {
  inInputs: true,
  when: () => !dialogOpen(),
});

/** A title with the found letters marked. */
function Marked({ title, ranges }) {
  if (!ranges.length) return title;
  const chars = Array.from(title);
  const parts = [];
  let at = 0;
  for (const [start, end] of ranges) {
    if (start > at) parts.push(chars.slice(at, start).join(""));
    parts.push(html`<b>${chars.slice(start, end).join("")}</b>`);
    at = end;
  }
  if (at < chars.length) parts.push(chars.slice(at).join(""));
  return parts;
}

function grouped(results, query) {
  const groups = [];
  const recentFirst = !query.trim();
  if (recentFirst) {
    const recent = results.filter((r) => r.recent >= 0).sort((a, b) => a.recent - b.recent);
    if (recent.length) groups.push({ name: t("Recent"), items: recent });
    // what most people come for, before the long list of everything
    const suggested = results.filter((r) => r.recent < 0 && r.suggestion !== null).sort((a, b) => a.suggestion - b.suggestion);
    if (suggested.length) groups.push({ name: t("Suggested"), items: suggested });
  }
  for (const result of results) {
    if (recentFirst && (result.recent >= 0 || result.suggestion !== null)) continue;
    let group = groups.find((g) => g.name === result.group);
    if (!group) groups.push((group = { name: result.group, items: [] }));
    group.items.push(result);
  }
  return groups;
}

function Preview({ result }) {
  if (!result) return null;
  const { command } = result;
  const body = command.preview ? command.preview() : null;
  return html`<aside class="palette-preview" aria-live="polite">
    ${body || html`<div class="palette-preview-icon"><${Icon} name=${command.icon || "chevron-right"} size=${28} /></div>`}
    <strong>${result.title}</strong>
    ${command.hint ? html`<span>${text(command.hint)}</span>` : null}
    <span class="muted">${result.group}</span>
    ${command.keys ? html`<kbd class="kbd">${keyLabel(command.keys)}</kbd>` : null}
  </aside>`;
}

/** Run a command by its id once the palette is gone (help, from its foot or an empty search). */
function runLater(id) {
  closePalette();
  setTimeout(() => {
    const command = commandById(id);
    if (command) Promise.resolve(run(command)).catch(toastError);
  }, 0);
}

/** Nothing found: what to try instead, a few words that do find something, and the help. */
function NothingFound({ query, onTry }) {
  const words = [t("brightness"), t("game"), t("weather"), t("start screen")];
  return html`<div class="palette-empty" role="status">
    <p><strong>${t("Nothing found for “{query}”", { query })}</strong></p>
    <p>${t("Try fewer letters or another word for it. These find something:")}</p>
    <div class="palette-try">
      ${words.map((word) => html`<button type="button" class="chip" key=${word} onClick=${() => onTry(word)}>${word}</button>`)}
      <button type="button" class="link-btn" onClick=${() => runLater("help.shortcuts")}>${t("Help")}</button>
    </div>
  </div>`;
}

function PaletteBox({ initial }) {
  const [query, setQuery] = useState(initial);
  const [active, setActive] = useState(0);
  const [preview, setPreview] = useState(false);
  const list = useRef(null);
  const input = useRef(null);
  const results = useMemo(() => searchCommands(query), [query]);
  const groups = useMemo(() => grouped(results, query), [results, query]);
  const flat = groups.flatMap((group) => group.items);
  const current = flat[Math.min(active, flat.length - 1)];

  useEffect(() => setActive(0), [query]);
  useLayoutEffect(() => {
    list.current?.querySelector(".palette-item.active")?.scrollIntoView({ block: "nearest" });
  }, [active, query]);
  // at once, not after the next paint: keys typed right after Ctrl+K go into the input, and
  // the focus the palette gives back is the one before it, not its own input
  useLayoutEffect(() => {
    const before = document.activeElement;
    input.current?.focus();
    const unbind = bindKey("escape", () => closePalette(), { inInputs: true });
    return () => {
      unbind();
      // back to where it was, unless the focus has gone somewhere else (a dialog a command opened)
      const here = document.activeElement;
      const free = !here || here === document.body || here === input.current;
      if (free && before instanceof HTMLElement && document.contains(before)) before.focus({ preventScroll: true });
    };
  }, []);

  const choose = (result) => {
    if (!result) return;
    closePalette();
    // after the palette is gone, so the command's own dialogs get the focus
    setTimeout(() => Promise.resolve(run(result.command)).catch(toastError), 0);
  };
  const keydown = (event) => {
    const count = flat.length;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (count) setActive((index) => (index + (event.key === "ArrowDown" ? 1 : -1) + count) % count);
    } else if (event.key === "PageDown" || event.key === "PageUp") {
      event.preventDefault();
      if (count) setActive((index) => Math.max(0, Math.min(count - 1, index + (event.key === "PageDown" ? 8 : -8))));
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(current);
    } else if (event.key === "Tab") {
      event.preventDefault();
      setPreview((on) => !on);
    }
  };

  let index = -1;
  return html`<div class="palette-backdrop" onPointerDown=${(event) => event.target === event.currentTarget && closePalette()}>
    <div class=${`palette${preview ? " with-preview" : ""}`} role="dialog" aria-modal="true" aria-label=${t("Commands")}>
      <label class="palette-search">
        <${Icon} name="search" size=${18} />
        <input
          ref=${input}
          type="text"
          value=${query}
          placeholder=${t("Search or command …")}
          aria-label=${t("Search for a command")}
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant=${current ? `palette-${current.command.id}` : undefined}
          spellcheck="false"
          autocomplete="off"
          onInput=${(event) => setQuery(event.currentTarget.value)}
          onKeyDown=${keydown}
        />
        <kbd class="kbd">Esc</kbd>
      </label>
      <div class="palette-body">
        <div class="palette-list" id="palette-list" role="listbox" ref=${list} aria-label=${t("Commands")}>
          ${flat.length
            ? groups.map(
                (group) => html`<div class="palette-group" role="presentation" key=${group.name}>
                  <div class="palette-group-title" role="presentation">${group.name}</div>
                  ${group.items.map((result) => {
                    index += 1;
                    const mine = index;
                    const on = mine === Math.min(active, flat.length - 1);
                    return html`<div
                      id=${`palette-${result.command.id}`}
                      key=${result.command.id}
                      role="option"
                      aria-selected=${String(on)}
                      class=${`palette-item${on ? " active" : ""}`}
                      onPointerMove=${() => !on && setActive(mine)}
                      onClick=${() => choose(result)}
                    >
                      <${Icon} name=${result.command.icon || "chevron-right"} size=${18} />
                      <span class="palette-title"><${Marked} title=${result.title} ranges=${result.ranges} /></span>
                      ${result.command.hint ? html`<span class="palette-hint">${text(result.command.hint)}</span>` : null}
                      ${on
                        ? html`<kbd class="kbd">↵</kbd>`
                        : result.command.keys
                          ? html`<kbd class="kbd quiet">${keyLabel(result.command.keys)}</kbd>`
                          : null}
                    </div>`;
                  })}
                </div>`,
              )
            : html`<${NothingFound} query=${query} onTry=${(word) => (setQuery(word), input.current?.focus())} />`}
        </div>
        ${preview ? html`<${Preview} result=${current} />` : null}
      </div>
      <footer class="palette-foot">
        <span><span class="mono">↑ ↓</span> ${t("choose")}</span>
        <span><span class="mono">↵</span> ${t("run")}</span>
        <span><span class="mono">Tab</span> ${t("preview")}</span>
        <span class="grow"></span>
        <span class="palette-foot-note">${t("Everything in Libre Panel can be reached from here")}</span>
        <button type="button" class="link-btn palette-help" onClick=${() => runLater("help.shortcuts")}>
          ${t("Help")} <kbd class="kbd quiet">F1</kbd>
        </button>
      </footer>
    </div>
  </div>`;
}

export function CommandPalette() {
  const { open, query } = useSelector(palette, (state) => ({ open: state.open, query: state.query }));
  return open ? html`<${PaletteBox} initial=${query} />` : null;
}
