// A small reader for the Markdown of NOTICE.md: headings, paragraphs, lists, quotes,
// tables, and bold text, code and links in a line. It builds elements, never HTML from
// text, and a link must be http(s).

import { html } from "../../lib/preact.js";

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|<https?:\/\/[^>\s]+>|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g;

function link(href, label) {
  return html`<a href=${href} target="_blank" rel="noopener noreferrer">${label}</a>`;
}

/** The text of a line with its bold parts, code and links as elements. */
export function inline(text) {
  return text.split(INLINE).map((part, index) => {
    if (index % 2 === 0) return part;
    if (part.startsWith("**")) return html`<strong key=${index}>${part.slice(2, -2)}</strong>`;
    if (part.startsWith("`")) return html`<code key=${index}>${part.slice(1, -1)}</code>`;
    if (part.startsWith("<")) return link(part.slice(1, -1), part.slice(1, -1));
    const close = part.indexOf("](");
    return link(part.slice(close + 2, -1), part.slice(1, close));
  });
}

const cells = (line) =>
  line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((cell) => cell.trim());

/** The blocks of a Markdown text: {kind, ...} in order. */
export function blocks(source) {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const found = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (!line.trim()) {
      index += 1;
    } else if (heading) {
      found.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      index += 1;
    } else if (line.startsWith("|") && /^\|?[\s:|-]+\|?$/.test(lines[index + 1] || "")) {
      const rows = [cells(line)];
      index += 2;
      while (index < lines.length && lines[index].startsWith("|")) rows.push(cells(lines[index++]));
      found.push({ kind: "table", head: rows[0], rows: rows.slice(1) });
    } else if (line.startsWith(">")) {
      const quote = [];
      while (index < lines.length && lines[index].startsWith(">")) quote.push(lines[index++].replace(/^>\s?/, ""));
      found.push({ kind: "quote", blocks: blocks(quote.join("\n")) });
    } else if (/^[-*]\s/.test(line)) {
      const items = [];
      while (index < lines.length && /^[-*]\s/.test(lines[index])) {
        let item = lines[index++].slice(2);
        while (index < lines.length && /^\s+\S/.test(lines[index])) item += ` ${lines[index++].trim()}`;
        items.push(item);
      }
      found.push({ kind: "list", items });
    } else {
      const text = [lines[index++].trim()]; // at least this one, whatever it looks like
      while (index < lines.length && lines[index].trim() && !/^(#{1,4}\s|>|[-*]\s|\|)/.test(lines[index])) text.push(lines[index++].trim());
      found.push({ kind: "paragraph", text: text.join(" ") });
    }
  }
  return found;
}

function Block({ block }) {
  if (block.kind === "heading") {
    return html`<${block.level <= 2 ? "h3" : "h4"} class="md-heading">${inline(block.text)}<//>`;
  }
  if (block.kind === "list") return html`<ul>${block.items.map((item, i) => html`<li key=${i}>${inline(item)}</li>`)}</ul>`;
  if (block.kind === "quote") return html`<blockquote>${block.blocks.map((inner, i) => html`<${Block} key=${i} block=${inner} />`)}</blockquote>`;
  if (block.kind === "table") {
    return html`<div class="md-table"><table>
      <thead><tr>${block.head.map((cell, i) => html`<th key=${i}>${inline(cell)}</th>`)}</tr></thead>
      <tbody>${block.rows.map((row, r) => html`<tr key=${r}>${row.map((cell, i) => html`<td key=${i}>${inline(cell)}</td>`)}</tr>`)}</tbody>
    </table></div>`;
  }
  return html`<p>${inline(block.text)}</p>`;
}

/** NOTICE.md and the like, readable in the app. */
export function Markdown({ source }) {
  return html`<div class="markdown">${blocks(source).map((block, i) => html`<${Block} key=${i} block=${block} />`)}</div>`;
}
