// Commands: everything the app can do, by name. Views register theirs; the
// palette (Ctrl+K) finds them by a fuzzy search and runs them.
//
//   registerCommand({
//     id: "theme.save",                // unique
//     title: () => t("Save"),          // texts are functions: they follow the language
//     group: () => t("Theme"),         // the palette's section
//     icon: "save",                    // a name from ui/icons.js
//     hint: () => "…",                 // optional, shown at the right
//     keywords: () => "…",             // optional, more words to find it by
//     keys: "mod+s",                   // optional, its shortcut, shown as a key hint
//     bind: true,                      // optional: also bind `keys` (core/shortcuts.js)
//     when: () => true,                // optional: offered only then
//     rank: 10,                        // optional: order without a search (lower first)
//     preview: () => html`…`,          // optional: what Tab shows in the palette
//     run: () => {},
//   });
//
// registerProvider(() => [...commands]) adds commands made when the palette
// opens (one per theme, per module kind, ...).

import { bindKey } from "./shortcuts.js";

const commands = new Map();
const providers = new Set();
const RECENT_KEY = "libre-panel-recent-commands";
let recent = [];
try {
  recent = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
} catch {
  recent = [];
}

/** Register a command; returns a function that removes it again. */
export function registerCommand(command) {
  commands.set(command.id, command);
  const unbind = command.bind && command.keys ? bindKey(command.keys, () => run(command), { when: command.when, inInputs: command.inInputs }) : null;
  return () => {
    if (commands.get(command.id) === command) commands.delete(command.id);
    unbind?.();
  };
}

export function registerCommands(list) {
  const removers = list.map(registerCommand);
  return () => removers.forEach((remove) => remove());
}

/** A function that returns commands of the moment; called whenever the palette searches. */
export function registerProvider(provider) {
  providers.add(provider);
  return () => providers.delete(provider);
}

export const text = (value) => (typeof value === "function" ? value() : value) ?? "";

/** Every command that is offered now. */
export function availableCommands() {
  const all = [...commands.values()];
  for (const provider of providers) {
    try {
      all.push(...provider());
    } catch (error) {
      console.error("command provider failed", error);
    }
  }
  return all.filter((command) => {
    try {
      return !command.when || command.when();
    } catch {
      return false;
    }
  });
}

export function commandById(id) {
  return availableCommands().find((command) => command.id === id) || null;
}

export async function run(command) {
  recent = [command.id, ...recent.filter((id) => id !== command.id)].slice(0, 6);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(recent));
  } catch {
    // remembered for this session only
  }
  return command.run();
}

export async function runCommand(id) {
  const command = commandById(id);
  if (!command) throw new Error(`no command ${id}`);
  return run(command);
}

export const recentIds = () => recent;

// -- fuzzy search ------------------------------------------------------------

/** A letter without accent and case: "Ü" → "u", so "luf" finds "Lüfter". */
const fold = (char) => char.normalize("NFD")[0].toLowerCase();
const BOUNDARY = /[\s\-_.:/(·]/;

/**
 * How well `query` matches `text`: the letters of the query in order, better
 * at the start of words and together. Returns {score, ranges} (ranges are
 * [start, end) indices into Array.from(text)) or null when it does not match.
 */
export function fuzzy(query, text) {
  const wanted = Array.from(query.trim()).filter((c) => !/\s/.test(c)).map(fold);
  if (!wanted.length) return { score: 0, ranges: [] };
  const chars = Array.from(text);
  const folded = chars.map(fold);
  let best = null;
  for (let start = 0; start < folded.length; start++) {
    if (folded[start] !== wanted[0]) continue;
    let score = 0;
    let next = 0;
    let previous = -2;
    let good = 0; // letters at the start of a word or right after the one before
    const hits = [];
    for (let i = start; i < folded.length && next < wanted.length; i++) {
      if (folded[i] !== wanted[next]) continue;
      const wordStart = i === 0 || BOUNDARY.test(chars[i - 1]);
      const together = i === previous + 1;
      score += 10 + (wordStart ? 12 : 0) + (together ? 14 : 0);
      if (wordStart || together) good++;
      if (previous >= 0) score -= Math.min(6, i - previous - 1);
      hits.push(i);
      previous = i;
      next++;
    }
    if (next < wanted.length) break; // a later start has fewer letters left: no match either
    // letters strewn over the text are no match ("ring" in "wetter einfügen")
    if (wanted.length > 2 && good < wanted.length / 3) continue;
    score -= start * 0.25;
    if (!best || score > best.score) best = { score, hits };
  }
  if (!best) return null;
  const ranges = [];
  for (const i of best.hits) {
    const last = ranges[ranges.length - 1];
    if (last && last[1] === i) last[1] = i + 1;
    else ranges.push([i, i + 1]);
  }
  return { score: best.score, ranges };
}

/**
 * The commands for a query, best first: [{command, title, group, ranges, score}].
 * Without a query: the recent ones first, then by rank.
 */
export function searchCommands(query, limit = 80) {
  const results = [];
  for (const command of availableCommands()) {
    const title = text(command.title);
    const group = text(command.group);
    if (!query.trim()) {
      const recentIndex = recent.indexOf(command.id);
      results.push({ command, title, group, ranges: [], score: 0, recent: recentIndex });
      continue;
    }
    const inTitle = fuzzy(query, title);
    const inMore = inTitle ? null : fuzzy(query, `${group} ${text(command.keywords)} ${text(command.hint)}`);
    const found = inTitle || (inMore && { score: inMore.score * 0.6, ranges: [] });
    if (found) results.push({ command, title, group, ranges: found.ranges, score: found.score });
  }
  if (!query.trim()) {
    results.sort((a, b) => (a.command.rank ?? 50) - (b.command.rank ?? 50));
    return results.slice(0, limit);
  }
  results.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
  return results.slice(0, limit);
}
