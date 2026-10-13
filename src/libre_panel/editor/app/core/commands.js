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
//     suggest: 3,                      // optional: its place among the suggestions of an
//                                      // empty search (a number, or a function: null for none)
//     preview: () => html`…`,          // optional: what Tab shows in the palette
//     run: () => {},
//   });
//
// registerProvider(() => [...commands]) adds commands made when the palette
// opens (one per theme, per module kind, ...). describeCommand(id, {keywords, suggest})
// gives a command that another part of the app registers more words to be found by.

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

const descriptions = new Map();

/**
 * More words to find a command by, and its place among the suggestions, for a command that
 * another part of the app registers ("settings.weather": "place city sun"): the words a person
 * types are not always those of its title. `keywords` is a function, as the title is.
 */
export function describeCommand(id, { keywords, suggest } = {}) {
  descriptions.set(id, { keywords, suggest });
}

function described(command) {
  const more = descriptions.get(command.id);
  if (!more) return command;
  return {
    ...command,
    keywords: () => `${text(command.keywords)} ${text(more.keywords)}`,
    suggest: command.suggest ?? more.suggest,
  };
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
  return all
    .filter((command) => {
      try {
        return !command.when || command.when();
      } catch {
        return false;
      }
    })
    .map(described);
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

/** Where a word of a text begins, besides at its start ("Panel: Turing", "(Videomodus)", "8.8″"). */
const WORD_EDGE = /[\s\-_.:/()·,"“”„'’″]/;
const QUERY_WORDS = /[\s\-_/·:,]+/;

/**
 * How well the words of `query` are found in `text`, each one at the start of a word ("helli"
 * in "Helligkeit 50 %") or, from three letters on, inside one ("logo" in "Startlogo"), in any
 * order. Returns {score, ranges, starts} or null when a word is missing; `starts`: every word was
 * found at the start of one. One letter on its own ("a" in "report a bug") is only looked for when
 * it is all there is.
 */
export function wordMatch(query, text) {
  let wanted = query.split(QUERY_WORDS).filter(Boolean).map((word) => Array.from(word).map(fold).join(""));
  if (wanted.length > 1) wanted = wanted.filter((word) => word.length > 1 || /\d/.test(word));
  if (!wanted.length) return null;
  const chars = Array.from(text);
  const folded = chars.map(fold).join("");
  const edge = (index) => index <= 0 || index >= chars.length || WORD_EDGE.test(chars[index - 1]);
  let score = 0;
  let starts = true;
  const found = [];
  for (const word of wanted) {
    let best = null;
    for (let at = folded.indexOf(word); at >= 0; at = folded.indexOf(word, at + 1)) {
      const start = edge(at);
      if (!start && word.length < 3) continue;
      const whole = start && edge(at + word.length);
      const points = word.length * 10 + (start ? 30 : 0) + (whole ? 15 : 0) - Math.min(at, 40) * 0.1;
      if (!best || points > best.points) best = { points, at, start };
    }
    if (!best) return null;
    score += best.points;
    starts &&= best.start;
    found.push([best.at, best.at + word.length]);
  }
  found.sort((a, b) => a[0] - b[0]);
  const ranges = [];
  for (const [start, end] of found) {
    const last = ranges[ranges.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else ranges.push([start, end]);
  }
  return { score, ranges, starts };
}

/** Where a command is in the suggestions of an empty search, or null. */
const suggestion = (command) => {
  try {
    const place = typeof command.suggest === "function" ? command.suggest() : command.suggest;
    return typeof place === "number" ? place : null;
  } catch {
    return null;
  }
};

/**
 * How well a query fits a command: the words at the start of words of its title first (marked
 * there), then the words anywhere in its title, its other words, its hint and its group (a whole
 * word there before a part of one in the title, so that log finds Open log before Analog), then
 * the letters of the query strewn over the title ("pnl paus") or the rest. A word found always
 * ranks before letters strewn; among equals the command's rank decides.
 */
function scoreOf(command, query, title, group) {
  const more = `${text(command.keywords)} ${text(command.hint)} ${group}`;
  const inTitle = wordMatch(query, title);
  if (inTitle?.starts) return { score: 300 + inTitle.score + (inTitle.ranges[0][0] === 0 ? 20 : 0), ranges: inTitle.ranges };
  const anywhere = wordMatch(query, `${title} | ${more}`);
  if (anywhere) {
    const end = Array.from(title).length;
    const ranges = anywhere.ranges.filter(([start]) => start < end).map(([start, stop]) => [start, Math.min(stop, end)]);
    return { score: 200 + anywhere.score * 0.8, ranges };
  }
  // letters in order: a share of the best they could do (36 points a letter), below any word
  const letters = Array.from(query).filter((c) => !/\s/.test(c)).length || 1;
  const strewn = fuzzy(query, title);
  if (strewn) return { score: Math.min(150, (strewn.score / (36 * letters)) * 150), ranges: strewn.ranges };
  const elsewhere = fuzzy(query, more);
  if (elsewhere) return { score: Math.min(90, (elsewhere.score / (36 * letters)) * 90), ranges: [] };
  return null;
}

/**
 * The commands for a query, best first: [{command, title, group, ranges, score}].
 * Without a query: the recent ones first, then the suggestions (`suggestion` is their place),
 * then by rank.
 */
export function searchCommands(query, limit = 80) {
  const results = [];
  const empty = !query.trim();
  for (const command of availableCommands()) {
    const title = text(command.title);
    const group = text(command.group);
    if (empty) {
      const recentIndex = recent.indexOf(command.id);
      results.push({ command, title, group, ranges: [], score: 0, recent: recentIndex, suggestion: suggestion(command) });
      continue;
    }
    const found = scoreOf(command, query, title, group);
    if (found) results.push({ command, title, group, ranges: found.ranges, score: found.score - (command.rank ?? 50) * 0.05 });
  }
  if (empty) {
    // the recent ones and the suggestions are not cut off by the limit
    const first = (result) => (result.recent >= 0 ? 0 : result.suggestion !== null ? 1 : 2);
    results.sort((a, b) => first(a) - first(b) || (a.command.rank ?? 50) - (b.command.rank ?? 50));
    return results.slice(0, limit);
  }
  results.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
  return results.slice(0, limit);
}
