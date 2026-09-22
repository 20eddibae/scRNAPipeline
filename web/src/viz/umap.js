/* The UMAP: every cell, drawn on a canvas.
 *
 * Colour by cell type, Leiden cluster or the author's label. Hover a cell for
 * its three labels; hover a legend row to pick out one group, click it to keep
 * that group picked. The only motion is a short colour fade when the colouring
 * changes. Cells are drawn in a fixed shuffled order so no group is always
 * painted on top of the others. */

import { categoryScale } from "./primitives.js";

const LAYERS = [
  { key: "label", title: "Cell type", note: "named in the annotate step" },
  { key: "cluster", title: "Cluster", note: "Leiden" },
  { key: "truth", title: "Author label", note: "held out, never seen by the pipeline" },
];

export function umapView(embedding, { initial = "label", height = 460 } = {}) {
  const coords = embedding.coords;
  const layers = LAYERS.filter((l) => Array.isArray(embedding[l.key]) &&
                                      embedding[l.key].length === coords.length);
  if (!layers.length) return null;

  let layer = layers.find((l) => l.key === initial) ?? layers[0];
  let focus = null;      // category under the pointer in the legend
  let pinned = null;     // category clicked in the legend
  let colors = null;     // current per-cell colours, as [r,g,b]
  let fade = null;

  const root = document.createElement("div");
  const bar = document.createElement("div");
  bar.className = "umap-bar";
  const seg = document.createElement("div");
  seg.className = "seg";
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "colour cells by");
  const note = document.createElement("span");
  note.className = "viz-sub";
  bar.append(seg, note);

  const grid = document.createElement("div");
  grid.className = "umap";
  const plot = document.createElement("div");
  plot.className = "umap-plot";
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  const tip = document.createElement("div");
  tip.className = "umap-tip";
  tip.hidden = true;
  plot.append(canvas, tip);
  const legendList = document.createElement("ul");
  legendList.className = "umap-legend";
  grid.append(plot, legendList);
  root.append(bar, grid);

  // fixed draw order, shuffled once
  const order = coords.map((_, i) => i);
  let seed = 7;
  for (let i = order.length - 1; i > 0; i--) {
    seed = (seed * 16807) % 2147483647;
    const j = seed % (i + 1);
    [order[i], order[j]] = [order[j], order[i]];
  }

  const xs = coords.map((c) => c[0]), ys = coords.map((c) => c[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  let px = [], py = [], cssW = 0, cssH = height;

  function layout() {
    cssW = plot.clientWidth || 640;
    cssH = Math.min(height, Math.max(280, cssW * 0.72));
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    canvas.style.height = `${cssH}px`;
    const pad = 14;
    const sx = (cssW - 2 * pad) / (x1 - x0 || 1);
    const sy = (cssH - 2 * pad) / (y1 - y0 || 1);
    const s = Math.min(sx, sy);
    const ox = (cssW - s * (x1 - x0)) / 2, oy = (cssH - s * (y1 - y0)) / 2;
    px = xs.map((x) => ox + (x - x0) * s);
    py = ys.map((y) => cssH - (oy + (y - y0) * s));
    canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function categories() {
    const values = embedding[layer.key].map(String);
    const counts = new Map();
    for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
    const keys = [...counts.keys()].sort(naturalOrder);
    return { values, counts, keys, color: categoryScale(keys) };
  }

  let cat = categories();

  function targetColors() {
    return cat.values.map((v) => hexToRgb(cat.color(v)));
  }

  function draw() {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, cssW, cssH);
    const active = pinned ?? focus;
    const r = cssW < 500 ? 1.6 : 2.1;
    const dim = cssVar("--border-2", "#cfcfc8");

    // background pass: cells outside the picked group
    if (active !== null) {
      ctx.fillStyle = dim;
      ctx.globalAlpha = 0.5;
      for (const i of order) {
        if (cat.values[i] === active) continue;
        ctx.beginPath(); ctx.arc(px[i], py[i], r, 0, Math.PI * 2); ctx.fill();
      }
    }
    ctx.globalAlpha = 0.85;
    for (const i of order) {
      if (active !== null && cat.values[i] !== active) continue;
      const [cr, cg, cb] = colors[i];
      ctx.fillStyle = `rgb(${cr},${cg},${cb})`;
      ctx.beginPath(); ctx.arc(px[i], py[i], r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.globalAlpha = 1;

    drawLabels(ctx, active);
  }

  function drawLabels(ctx, active) {
    const text = cssVar("--text", "#1c1c1a");
    const halo = cssVar("--surface", "#ffffff");
    ctx.font = `600 12px ${cssVar("--font", "sans-serif")}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    for (const key of cat.keys) {
      if (active !== null && key !== active) continue;
      const idx = [];
      cat.values.forEach((v, i) => { if (v === key) idx.push(i); });
      if (idx.length < Math.max(12, cat.values.length * 0.01)) continue;
      const mx = median(idx.map((i) => px[i])), my = median(idx.map((i) => py[i]));
      ctx.lineWidth = 4; ctx.strokeStyle = halo; ctx.strokeText(key, mx, my);
      ctx.fillStyle = text; ctx.fillText(key, mx, my);
    }
  }

  function renderLegend() {
    legendList.replaceChildren(...cat.keys.map((key) => {
      const li = document.createElement("li");
      const active = pinned ?? focus;
      if (pinned === key) li.classList.add("on");
      else if (active !== null && active !== key) li.classList.add("off");
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = cat.color(key);
      const name = document.createElement("span");
      name.textContent = layer.key === "cluster" ? `cluster ${key}` : key;
      const count = document.createElement("span");
      count.className = "n";
      count.textContent = cat.counts.get(key).toLocaleString();
      li.append(sw, name, count);
      li.addEventListener("mouseenter", () => { focus = key; draw(); renderLegendState(); });
      li.addEventListener("mouseleave", () => { focus = null; draw(); renderLegendState(); });
      li.addEventListener("click", () => {
        pinned = pinned === key ? null : key; draw(); renderLegendState();
      });
      return li;
    }));
  }

  function renderLegendState() {
    const active = pinned ?? focus;
    [...legendList.children].forEach((li, i) => {
      const key = cat.keys[i];
      li.classList.toggle("on", pinned === key);
      li.classList.toggle("off", active !== null && active !== key);
    });
  }

  function renderButtons() {
    seg.replaceChildren(...layers.map((l) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = l.title;
      b.setAttribute("aria-pressed", String(l.key === layer.key));
      b.addEventListener("click", () => setLayer(l));
      return b;
    }));
    note.textContent = `${coords.length.toLocaleString()} cells · ${cat.keys.length} groups · ${layer.note}`;
  }

  function setLayer(next) {
    if (next.key === layer.key) return;
    layer = next;
    focus = null; pinned = null;
    const from = colors;
    cat = categories();
    const to = targetColors();
    renderButtons();
    renderLegend();
    if (reducedMotion()) { colors = to; draw(); return; }
    cancelAnimationFrame(fade);
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / 260);
      colors = from.map((c, i) => [0, 1, 2].map((k) => Math.round(c[k] + (to[i][k] - c[k]) * t)));
      draw();
      if (t < 1) fade = requestAnimationFrame(step);
    };
    fade = requestAnimationFrame(step);
  }

  // hover: nearest cell within a few pixels
  canvas.addEventListener("mousemove", (e) => {
    const box = canvas.getBoundingClientRect();
    const mx = e.clientX - box.left, my = e.clientY - box.top;
    let best = -1, bestD = 36;
    for (let i = 0; i < px.length; i++) {
      const d = (px[i] - mx) ** 2 + (py[i] - my) ** 2;
      if (d < bestD) { bestD = d; best = i; }
    }
    if (best < 0) { tip.hidden = true; return; }
    tip.replaceChildren(...layers.map((l) => {
      const row = document.createElement("div");
      const k = document.createElement("span");
      k.className = "k";
      k.textContent = `${l.title}: `;
      row.append(k, document.createTextNode(String(embedding[l.key][best])));
      return row;
    }));
    tip.hidden = false;
    const left = Math.min(px[best] + 12, cssW - tip.offsetWidth - 4);
    tip.style.left = `${Math.max(4, left)}px`;
    tip.style.top = `${Math.max(4, py[best] - tip.offsetHeight - 8)}px`;
  });
  canvas.addEventListener("mouseleave", () => { tip.hidden = true; });

  colors = targetColors();
  renderButtons();
  renderLegend();

  // size once attached, and again whenever the column changes width
  const ro = new ResizeObserver(() => { layout(); draw(); });
  ro.observe(plot);

  return root;
}

function naturalOrder(a, b) {
  const na = Number(a), nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return String(a).localeCompare(String(b));
}

function median(values) {
  const s = [...values].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return [128, 128, 128];
  const v = parseInt(m[1], 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function reducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}
