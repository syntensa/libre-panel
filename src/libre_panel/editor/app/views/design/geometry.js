// Geometry of the canvas, without the page: boxes ([x, y, w, h] in panel
// pixels), the grid modules sit on, snapping to edges and centres.

/** The box around several boxes, or null. */
export function unionBox(boxes) {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const box of boxes) {
    if (!box) continue;
    x0 = Math.min(x0, box[0]);
    y0 = Math.min(y0, box[1]);
    x1 = Math.max(x1, box[0] + box[2]);
    y1 = Math.max(y1, box[1] + box[3]);
  }
  return x0 === Infinity ? null : [x0, y0, x1 - x0, y1 - y0];
}

export const intersects = (a, b) => a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3];

// -- the grid of modules (theme.modules.Grid.to_dict()) ------------------------

/** Cells as a box in pixels. */
export function cellBox(grid, col, row, cols = 1, rows = 1) {
  const x0 = grid.x + col * (grid.cell_w + grid.gap);
  const y0 = grid.y + row * (grid.cell_h + grid.gap);
  const x1 = x0 + cols * grid.cell_w + (cols - 1) * grid.gap;
  const y1 = y0 + rows * grid.cell_h + (rows - 1) * grid.gap;
  return [Math.round(x0), Math.round(y0), Math.round(x1) - Math.round(x0), Math.round(y1) - Math.round(y0)];
}

/** The cell under a point, kept inside the grid. */
export function cellAt(grid, x, y) {
  const col = Math.floor((x - grid.x + grid.gap / 2) / (grid.cell_w + grid.gap));
  const row = Math.floor((y - grid.y + grid.gap / 2) / (grid.cell_h + grid.gap));
  return [Math.max(0, Math.min(grid.columns - 1, col)), Math.max(0, Math.min(grid.rows - 1, row))];
}

/** A module's cells [col, row, cols, rows], moved into the grid as the renderer does. */
export function placed(grid, widget) {
  const col = Math.min(Math.max(0, widget.col), grid.columns - 1);
  const row = Math.min(Math.max(0, widget.row), grid.rows - 1);
  const cols = Math.min(Math.max(1, widget.cols), grid.columns - col);
  const rows = Math.min(Math.max(1, widget.rows), grid.rows - row);
  return [col, row, cols, rows];
}

/** The modules in an area of cells, but those named in `except`. */
export function blockers(widgets, grid, area, ...except) {
  return widgets.filter((w) => w.type === "module" && !except.includes(w.id) && intersects(placed(grid, w), area));
}

/** The largest free area up to cols × rows: at (or around) a preferred cell, or anywhere. */
export function freeSpot(widgets, grid, cols, rows, prefer = null, exceptId = null) {
  const sizes = [];
  for (let c = Math.min(cols, grid.columns); c >= 1; c--) {
    for (let r = Math.min(rows, grid.rows); r >= 1; r--) sizes.push([c, r]);
  }
  sizes.sort((a, b) => b[0] * b[1] - a[0] * a[1]);
  for (const [c, r] of sizes) {
    const candidates = [];
    if (prefer) {
      for (let dr = 0; dr < r; dr++) for (let dc = 0; dc < c; dc++) candidates.push([prefer[0] - dc, prefer[1] - dr]);
    } else {
      for (let row = 0; row + r <= grid.rows; row++) for (let col = 0; col + c <= grid.columns; col++) candidates.push([col, row]);
    }
    for (const [col, row] of candidates) {
      if (col < 0 || row < 0 || col + c > grid.columns || row + r > grid.rows) continue;
      if (!blockers(widgets, grid, [col, row, c, r], exceptId).length) return [col, row, c, r];
    }
  }
  return null;
}

// -- snapping ------------------------------------------------------------------

/** Strips of the frame the panel's bezel hides, when the theme is exactly that panel's size. */
export function hiddenEdges(model, size) {
  const [w, h] = size || [0, 0];
  const orientation = w > h ? "landscape" : "portrait";
  if (!model?.hidden || model[orientation]?.[0] !== w || model[orientation]?.[1] !== h) return null;
  const edges = model.hidden[orientation];
  return Object.values(edges).some((px) => px > 0) ? edges : null;
}

/** Lines to snap to: the panel's edges and centre, hidden strips, and the other boxes' edges and centres. */
export function snapLines(size, boxes, skip, hidden) {
  const xs = [0, size[0] / 2, size[0]];
  const ys = [0, size[1] / 2, size[1]];
  if (hidden) {
    xs.push(hidden.left, size[0] - hidden.right);
    ys.push(hidden.top, size[1] - hidden.bottom);
  }
  for (const [id, box] of Object.entries(boxes)) {
    if (skip.has(id)) continue;
    xs.push(box[0], box[0] + box[2] / 2, box[0] + box[2]);
    ys.push(box[1], box[1] + box[3] / 2, box[1] + box[3]);
  }
  return { xs, ys };
}

/**
 * Where a box moved by (dx, dy) lands: its edge or centre on a line within
 * `threshold`, else on the snap step. Returns {dx, dy, guides: [{axis, at}]}.
 */
export function snapMove(bounds, dx, dy, { lines, threshold, step = 0, guides: useGuides = true }) {
  if (!bounds) return { dx: Math.round(dx), dy: Math.round(dy), guides: [] };
  let x = bounds[0] + dx;
  let y = bounds[1] + dy;
  const guides = [];
  const toLine = (pos, length, candidates, axis) => {
    let best = null;
    for (const offset of [0, length / 2, length]) {
      for (const line of candidates) {
        const distance = Math.abs(pos + offset - line);
        if (distance <= threshold && (!best || distance < best.distance)) best = { distance, pos: line - offset, at: line };
      }
    }
    if (!best) return null;
    guides.push({ axis, at: best.at });
    return best.pos;
  };
  const sx = useGuides && lines ? toLine(x, bounds[2], lines.xs, "v") : null;
  const sy = useGuides && lines ? toLine(y, bounds[3], lines.ys, "h") : null;
  if (sx !== null) x = sx;
  else if (step > 1) x = Math.round(x / step) * step;
  if (sy !== null) y = sy;
  else if (step > 1) y = Math.round(y / step) * step;
  return { dx: Math.round(x - bounds[0]), dy: Math.round(y - bounds[1]), guides };
}

/** One edge or corner dragged: the new box, at least `min` big; `keep` keeps the aspect. */
export function resizeBox(start, edge, dx, dy, { min = 4, keep = false } = {}) {
  let [x, y, w, h] = start;
  if (edge.includes("e")) w = start[2] + dx;
  if (edge.includes("s")) h = start[3] + dy;
  if (edge.includes("w")) w = start[2] - dx;
  if (edge.includes("n")) h = start[3] - dy;
  if (keep && start[2] > 0 && start[3] > 0) {
    const ratio = start[2] / start[3];
    if (edge.length === 2) {
      if (Math.abs(w / start[2]) > Math.abs(h / start[3])) h = w / ratio;
      else w = h * ratio;
    } else if (edge === "e" || edge === "w") h = w / ratio;
    else w = h * ratio;
  }
  w = Math.max(min, Math.round(w));
  h = Math.max(min, Math.round(h));
  if (edge.includes("w")) x = start[0] + start[2] - w;
  if (edge.includes("n")) y = start[1] + start[3] - h;
  return [Math.round(x), Math.round(y), w, h];
}
