// Line icons, 24×24, drawn with the text colour: html`<${Icon} name="search" size=${16} />`.

import { html } from "../lib/preact.js";

const DOT = (x, y) => `<circle cx="${x}" cy="${y}" r="1.3" fill="currentColor" stroke="none"/>`;

export const ICONS = {
  // the app's mark, as branding.mark_svg() draws it (tests/test_branding.py keeps them alike)
  logo: '<path d="M21.66 10.88A10 10 0 0 1 19.07 20.54" fill="none" stroke="#2a3440" stroke-width="2.6" stroke-linecap="round"/><path d="M4.93 20.54A10 10 0 1 1 21.66 10.88" fill="none" stroke="#2ee6d6" stroke-width="2.6" stroke-linecap="round"/><rect x="6.9" y="13.26" width="2.4" height="4.2" rx="0.8" fill="#e8eef4" stroke="none"/><rect x="10.8" y="10.86" width="2.4" height="6.6" rx="0.8" fill="#e8eef4" stroke="none"/><rect x="14.7" y="8.46" width="2.4" height="9" rx="0.8" fill="#2ee6d6" stroke="none"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 010 11H11"/>',
  redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 000 11H13"/>',
  "to-panel": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M10 9.5l4 2.5-4 2.5z"/>',
  blocks:
    '<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><path d="M17.25 14v6.5M14 17.25h6.5"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  pages: '<rect x="3" y="5" width="13" height="9" rx="1.5"/><path d="M8 18h13V9"/>',
  images: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.8"/><path d="M21 16l-5-5-9 9"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  "chevron-down": '<path d="M6 9l6 6 6-6"/>',
  "chevron-up": '<path d="M6 15l6-6 6 6"/>',
  "chevron-right": '<path d="M9 6l6 6-6 6"/>',
  "chevron-left": '<path d="M15 6l-6 6 6 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  more: DOT(5, 12) + DOT(12, 12) + DOT(19, 12),
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  "eye-off": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><path d="M4 4l16 16"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 018 0v4"/>',
  unlock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 017.6-1.7"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  duplicate: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 00-1-1H5a1 1 0 00-1 1v10a1 1 0 001 1h3"/>',
  upload: '<path d="M12 15V3M7 8l5-5 5 5"/><path d="M4 15v4a2 2 0 002 2h12a2 2 0 002-2v-4"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 15v4a2 2 0 002 2h12a2 2 0 002-2v-4"/>',
  save: '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v5h7V3M8 21v-7h8v7"/>',
  file: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
  home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
  pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  palette:
    '<path d="M12 3a9 9 0 100 18c1.1 0 2-.8 2-1.8 0-.5-.2-.9-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1 .8-1.8 1.8-1.8H17a4 4 0 004-4c0-4.4-4-7.8-9-7.8z"/>' +
    DOT(7.5, 11) +
    DOT(10, 7.5) +
    DOT(14.5, 7.5),
  pencil: '<path d="M4 20h4l11-11-4-4L4 16z"/>',
  ring: '<path d="M12 3a9 9 0 1 1-8.5 6"/>',
  gauge: '<path d="M12 3a9 9 0 1 1-8.5 6"/>',
  bar: '<rect x="3" y="8.5" width="18" height="7" rx="2"/><path d="M6.5 12h7"/>',
  graph: '<path d="M3 16l5-6 4 3 4-7 5 6"/><path d="M3 20h18"/>',
  text: '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
  metric: '<path d="M5 9h14M5 15h14M10 4L8 20M16 4l-2 16"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  analog: '<circle cx="12" cy="12" r="9"/><path d="M12 6.5V12l-3.5 2.5"/><path d="M12 3v1.5M21 12h-1.5M12 21v-1.5M3 12h1.5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
  countdown: '<path d="M7 3h10M7 21h10"/><path d="M8 3c0 5 8 6 8 9s-8 4-8 9M16 3c0 5-8 6-8 9s8 4 8 9"/>',
  agent:
    '<circle cx="12" cy="12" r="8" stroke-opacity=".35"/>' +
    '<circle cx="12" cy="4" r="2.2" fill="currentColor" stroke="none"/>' +
    '<circle cx="6.6" cy="6.2" r="1.7" fill="currentColor" fill-opacity=".6" stroke="none"/>' +
    DOT(4, 11.4),
  moon: '<path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.8"/><path d="M21 16l-5-5-9 9"/>',
  weather: '<path d="M7 18h10a4 4 0 00.5-8 6 6 0 00-11.5 1.5A3.5 3.5 0 007 18z"/>',
  icon: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13"/>' + DOT(4, 6) + DOT(4, 12) + DOT(4, 18),
  rect: '<rect x="3" y="5" width="18" height="14" rx="2"/>',
  module:
    '<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="18" height="7.5" rx="1.5"/>',
  fan: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/><path d="M12 10c0-4 2-6 4-6M14 12c4 0 6 2 6 4M12 14c0 4-2 6-4 6M10 12c-4 0-6-2-6-4"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  volume: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>',
  game: '<rect x="2" y="7" width="20" height="10" rx="5"/><path d="M7 10v4M5 12h4"/>' + DOT(16, 11) + DOT(18, 13),
  "select-all":
    '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/><path d="M13 7h8M3 17h8" stroke-dasharray="2 2"/>',
  "align-left": '<path d="M4 3v18"/><rect x="7" y="6" width="10" height="4" rx="1"/><rect x="7" y="14" width="14" height="4" rx="1"/>',
  "align-center": '<path d="M12 3v18"/><rect x="7" y="6" width="10" height="4" rx="1"/><rect x="5" y="14" width="14" height="4" rx="1"/>',
  "align-right": '<path d="M20 3v18"/><rect x="7" y="6" width="10" height="4" rx="1"/><rect x="3" y="14" width="14" height="4" rx="1"/>',
  "align-top": '<path d="M3 4h18"/><rect x="6" y="7" width="4" height="10" rx="1"/><rect x="14" y="7" width="4" height="14" rx="1"/>',
  "align-middle": '<path d="M3 12h18"/><rect x="6" y="7" width="4" height="10" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
  "align-bottom": '<path d="M3 20h18"/><rect x="6" y="7" width="4" height="10" rx="1"/><rect x="14" y="3" width="4" height="14" rx="1"/>',
  "distribute-h": '<path d="M4 3v18M20 3v18"/><rect x="9" y="7" width="6" height="10" rx="1"/>',
  "distribute-v": '<path d="M3 4h18M3 20h18"/><rect x="7" y="9" width="10" height="6" rx="1"/>',
  "arrow-up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
  "arrow-down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
  detach: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M8.1 8.1L20 20M8.1 15.9L20 4"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>',
  play: '<path d="M8 5l11 7-11 7z" fill="currentColor"/>',
  power: '<path d="M12 3v9"/><path d="M6.3 6.3a8 8 0 1011.4 0"/>',
  landscape: '<rect x="3" y="7" width="18" height="10" rx="2"/>',
  portrait: '<rect x="7" y="3" width="10" height="18" rx="2"/>',
  warning: '<path d="M12 3.5l9.5 17h-19z"/><path d="M12 10v4.5"/>' + DOT(12, 17.5),
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/>' + DOT(12, 7.6),
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M7 14h10"/>' + DOT(6, 10) + DOT(10, 10) + DOT(14, 10) + DOT(18, 10),
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 010 18M12 3a14 14 0 000 18"/>',
  plugin: '<path d="M10 4a2 2 0 114 0v2h4v4h-2a2 2 0 100 4h2v4h-4v-2a2 2 0 10-4 0v2H6v-4h2a2 2 0 100-4H6V6h4z"/>',
  grid: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
  magnet: '<path d="M6 3v8a6 6 0 0012 0V3"/><path d="M6 7h4M14 7h4"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5"/>',
  font: '<path d="M3 19l5.5-14L14 19M5 14h7"/><path d="M15.5 12.5a2.5 2.5 0 015 0V19M20.5 15.5h-3a2 2 0 100 3.5h3"/>',
  sensor: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  frame: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 7h18"/>',
  swap: '<path d="M7 4l-4 4 4 4M3 8h14M17 12l4 4-4 4M21 16H7"/>',
  refresh: '<path d="M20 11a8 8 0 00-14.4-4M4 4v4h4"/><path d="M4 13a8 8 0 0014.4 4M20 20v-4h-4"/>',
  // three quarters of a circle with the arrow at its end: a turn to the left, and to the right
  "turn-left": '<path d="M4 12a8 8 0 108-8"/><path d="M15 1l-3 3 3 3"/>',
  "turn-right": '<path d="M20 12a8 8 0 11-8-8"/><path d="M9 1l3 3-3 3"/>',
  link: '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>',
};

/** An icon by name; unknown names draw nothing. */
export function Icon({ name, size = 16, strokeWidth = 2, class: className = "" }) {
  const markup = ICONS[name] || "";
  return html`<svg
    class=${`icon ${className}`}
    width=${size}
    height=${size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width=${strokeWidth}
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
    dangerouslySetInnerHTML=${{ __html: markup }}
  ></svg>`;
}
