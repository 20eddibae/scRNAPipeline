/* Tiny SVG toolkit. No chart library, no build step, no CDN.
 *
 * Everything the panels in this folder draw is composed from these six
 * helpers, so a panel is ~30 lines and the whole viz layer stays readable. */

const NS = "http://www.w3.org/2000/svg";

// scanpy's default_20 (tab10, then the rest of tab20): the colours a
// practitioner already associates with a Leiden UMAP.
export const PALETTE = [
  "#1f77b4", "#ff7f0e", "#279e68", "#d62728", "#aa40fc",
  "#8c564b", "#e377c2", "#b5bd61", "#17becf", "#aec7e8",
  "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5", "#c49c94",
  "#f7b6d2", "#dbdb8d", "#9edae5", "#ad494a", "#8c6d31",
];

export function colorFor(i) {
  return PALETTE[((i % PALETTE.length) + PALETTE.length) % PALETTE.length];
}

/** Stable colour for a categorical key, given the ordered key list. */
export function categoryScale(keys) {
  const index = new Map(keys.map((k, i) => [String(k), i]));
  return (key) => colorFor(index.get(String(key)) ?? 0);
}

export function svg(width, height) {
  const el = document.createElementNS(NS, "svg");
  el.setAttribute("viewBox", `0 0 ${width} ${height}`);
  el.setAttribute("preserveAspectRatio", "xMidYMid meet");
  el.setAttribute("role", "img");
  return el;
}

export function el(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== null && v !== undefined) node.setAttribute(k, String(v));
  }
  if (text !== undefined) node.textContent = String(text);
  return node;
}

/** Linear scale from a data extent onto a pixel range. */
export function linear([d0, d1], [r0, r1]) {
  const span = d1 - d0 || 1;
  const f = (v) => r0 + ((v - d0) / span) * (r1 - r0);
  f.domain = [d0, d1];
  f.range = [r0, r1];
  return f;
}

export function extent(values) {
  let lo = Infinity, hi = -Infinity;
  for (const v of values) {
    if (!Number.isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (!Number.isFinite(lo)) return [0, 1];
  return lo === hi ? [lo - 0.5, hi + 0.5] : [lo, hi];
}

export function pad([lo, hi], fraction = 0.05) {
  const p = (hi - lo) * fraction;
  return [lo - p, hi + p];
}

/** A bottom + left axis pair with `ticks` labelled gridless ticks. */
export function axes(root, { x, y, height, margin, xLabel, yLabel, ticks = 4 }) {
  const g = el("g");
  g.appendChild(el("line", {
    class: "axis", x1: margin.left, y1: height - margin.bottom,
    x2: x.range[1], y2: height - margin.bottom,
  }));
  g.appendChild(el("line", {
    class: "axis", x1: margin.left, y1: margin.top,
    x2: margin.left, y2: height - margin.bottom,
  }));

  // Round-number ticks. Splitting the domain into equal parts put ticks at
  // values like 0.0156 and 0.5, which printed as "0.03" beside "0.01" on evenly
  // spaced ticks and as "0.50" for the first principal component.
  const xs = niceTicks(x.domain, ticks), ys = niceTicks(y.domain, ticks);
  for (const xv of xs.values) {
    g.appendChild(el("text", {
      class: "tick", x: x(xv), y: height - margin.bottom + 12, "text-anchor": "middle",
    }, xs.format(xv)));
  }
  for (const yv of ys.values) {
    g.appendChild(el("text", {
      class: "tick", x: margin.left - 6, y: y(yv) + 3, "text-anchor": "end",
    }, ys.format(yv)));
  }

  if (xLabel) {
    g.appendChild(el("text", {
      class: "label", x: (margin.left + x.range[1]) / 2, y: height - 2,
      "text-anchor": "middle",
    }, xLabel));
  }
  if (yLabel) {
    const cy = (margin.top + (height - margin.bottom)) / 2;
    g.appendChild(el("text", {
      class: "label", x: 10, y: cy, "text-anchor": "middle",
      transform: `rotate(-90 10 ${cy})`,
    }, yLabel));
  }
  root.appendChild(g);
  return g;
}

/** About `count` ticks at 1/2/5 x 10^k inside [lo, hi], with a formatter that
 * shows as many decimals as the step needs and no more. */
export function niceTicks([lo, hi], count = 4) {
  const span = hi - lo;
  if (!(span > 0) || !Number.isFinite(span)) return { values: [lo], format };
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [0.5, 1, 2, 5, 10].map((m) => m * mag)
    .reduce((best, s) => (Math.abs(span / s - count) < Math.abs(span / best - count) ? s : best));
  const values = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) {
    values.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  }
  const decimals = Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
  const fmt = (v) => (Math.abs(v) >= 10000 ? format(v) : v.toFixed(decimals));
  return { values, format: fmt };
}

export function format(v) {
  const a = Math.abs(v);
  if (a >= 10000) return `${Math.round(v / 1000)}k`;
  if (a >= 100) return String(Math.round(v));
  if (a >= 10) return v.toFixed(0);
  if (a >= 1) return v.toFixed(1);
  return v.toFixed(2);
}

/** Legend strip under a plot. Returns an HTML element, not SVG. */
export function legend(items) {
  const wrap = document.createElement("div");
  wrap.className = "viz-legend";
  for (const { label, color, line } of items) {
    const item = document.createElement("span");
    item.className = "item";
    const sw = document.createElement("span");
    sw.className = line ? "swatch line" : "swatch";
    if (line) sw.style.borderColor = color;
    else sw.style.background = color;
    item.append(sw, document.createTextNode(label));
    wrap.appendChild(item);
  }
  return wrap;
}

/** Equal-width histogram bins over `values`. */
export function histogram(values, bins = 30) {
  const finite = values.filter(Number.isFinite);
  const [lo, hi] = extent(finite);
  const width = (hi - lo) / bins || 1;
  const counts = new Array(bins).fill(0);
  for (const v of finite) {
    const i = Math.min(bins - 1, Math.max(0, Math.floor((v - lo) / width)));
    counts[i] += 1;
  }
  return counts.map((count, i) => ({ x0: lo + i * width, x1: lo + (i + 1) * width, count }));
}
