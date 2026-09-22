/* The plot panels themselves. Each one is a pure function
 *
 *     (run) -> {title, subtitle, node} | null
 *
 * and returns null when the run has nothing to draw. Registering a new panel
 * is one entry in `src/viz/index.js` plus one name in `src/steps/spec.js`. */

import {
  axes, categoryScale, el, extent, format, histogram,
  legend, linear, pad, svg,
} from "./primitives.js";

const M = { top: 10, right: 10, bottom: 28, left: 40 };

/* -- embeddings ---------------------------------------------------------- */

function scatter(coords, keys, { width = 520, height = 360, radius = 1.9 } = {}) {
  const order = [...new Set(keys.map(String))].sort(byNaturalOrder);
  const color = categoryScale(order);
  const x = linear(pad(extent(coords.map((c) => c[0]))), [M.left, width - M.right]);
  const y = linear(pad(extent(coords.map((c) => c[1]))), [height - M.bottom, M.top]);

  const root = svg(width, height);
  axes(root, { x, y, height, margin: M, xLabel: "UMAP 1", yLabel: "UMAP 2", ticks: 3 });

  const g = el("g");
  coords.forEach((c, i) => {
    g.appendChild(el("circle", {
      cx: x(c[0]).toFixed(1), cy: y(c[1]).toFixed(1), r: radius,
      fill: color(keys[i]), "fill-opacity": 0.75,
    }));
  });
  root.appendChild(g);

  const wrap = document.createElement("div");
  wrap.appendChild(root);
  wrap.appendChild(legend(order.map((k) => ({ label: k, color: color(k) }))));
  return wrap;
}

export function umapClusters(run) {
  const e = run.viz.embedding;
  if (!e?.coords?.length || !e.cluster) return null;
  return {
    title: "UMAP, coloured by Leiden cluster",
    subtitle: `${e.coords.length.toLocaleString()} cells drawn` +
      (e.subsampled ? ` (subsampled from ${e.n_total.toLocaleString()})` : ""),
    node: scatter(e.coords, e.cluster),
  };
}

export function umapLabels(run) {
  const e = run.viz.embedding;
  if (!e?.coords?.length || !e.label) return null;
  return {
    title: "UMAP, coloured by the label Claude read off the markers",
    subtitle: "Same embedding, cluster ids replaced by cell types",
    node: scatter(e.coords, e.label),
  };
}

/* -- QC ------------------------------------------------------------------ */

export function qcScatter(run) {
  const q = run.viz.qc;
  if (!q?.n_genes?.length || !q.pct_mt?.length) return null;

  const width = 520, height = 340;
  const x = linear(pad(extent(q.n_genes)), [M.left, width - M.right]);
  const y = linear(pad(extent(q.pct_mt)), [height - M.bottom, M.top]);

  const root = svg(width, height);
  axes(root, { x, y, height, margin: M, xLabel: "genes detected per cell", yLabel: "% mito" });

  const kept = q.kept ?? q.n_genes.map(() => true);
  const g = el("g");
  q.n_genes.forEach((gn, i) => {
    g.appendChild(el("circle", {
      cx: x(gn).toFixed(1), cy: y(q.pct_mt[i]).toFixed(1), r: 1.7,
      fill: kept[i] ? "#4c8dff" : "#f0736a", "fill-opacity": kept[i] ? 0.5 : 0.85,
    }));
  });
  root.appendChild(g);

  // The thresholds the chosen stringency preset implies. Every surviving cell
  // is on the safe side of them, so a threshold can fall outside the plotted
  // range; clamp it to the axis rather than draw outside the frame.
  for (const [key, scale, vertical] of [["min_genes", x, true], ["max_pct_mt", y, false]]) {
    const v = run.summaryValue("qc", key);
    if (!Number.isFinite(v)) continue;
    const at = clamp(scale(v), ...(vertical
      ? [M.left, width - M.right]
      : [M.top, height - M.bottom]));
    root.appendChild(el("line", {
      ...(vertical
        ? { x1: at, x2: at, y1: M.top, y2: height - M.bottom }
        : { x1: M.left, x2: width - M.right, y1: at, y2: at }),
      stroke: "#e3b341", "stroke-dasharray": "3 3",
    }));
  }

  const anyDropped = kept.some((k) => !k);
  const wrap = document.createElement("div");
  wrap.appendChild(root);
  wrap.appendChild(legend([
    { label: "cell", color: "#4c8dff" },
    // the processed matrix only carries survivors, so this entry appears only
    // when the record was exported with the pre-QC cells still in it
    ...(anyDropped ? [{ label: "dropped", color: "#f0736a" }] : []),
    { label: "threshold this decision set", color: "#e3b341" },
  ]));

  return {
    title: "Cells against the thresholds the decision set",
    subtitle: anyDropped
      ? "red cells fall outside the chosen stringency preset"
      : "drawn after filtering, with the chosen cut-offs marked",
    node: wrap,
  };
}

export function qcRetention(run) {
  const s = run.summary("qc");
  if (!s || !Number.isFinite(s.cells_before)) return null;
  return {
    title: "What the stringency decision cost",
    subtitle: `${s.cells_dropped?.toLocaleString() ?? 0} cells dropped of ` +
      `${s.cells_before.toLocaleString()}`,
    node: stackedBar([
      { label: "kept", value: s.cells_after, color: "#2dd4a7" },
      { label: "dropped", value: s.cells_dropped, color: "#f0736a" },
    ]),
  };
}

/* -- distributions ------------------------------------------------------- */

export function depthDistribution(run) {
  const values = run.viz.qc?.total_counts;
  if (!values?.length) return null;
  const logged = values.filter((v) => v > 0).map((v) => Math.log10(v));
  return {
    title: "Sequencing depth per cell",
    subtitle: "log10 total counts - the spread normalization has to remove",
    node: barsFromHistogram(histogram(logged, 36), "log10 counts", "cells"),
  };
}

export function pcaVariance(run) {
  const ratios = run.viz.pca_variance;
  if (!ratios?.length) return null;
  let cum = 0;
  const points = ratios.map((r, i) => ({ i: i + 1, r, cum: (cum += r) }));

  const width = 520, height = 300;
  const x = linear([1, points.length], [M.left, width - M.right]);
  const y = linear([0, Math.max(...ratios) * 1.1], [height - M.bottom, M.top]);

  const root = svg(width, height);
  axes(root, { x, y, height, margin: M, xLabel: "principal component", yLabel: "variance ratio" });

  const w = Math.max(1, (width - M.left - M.right) / points.length - 1);
  for (const p of points) {
    root.appendChild(el("rect", {
      x: x(p.i) - w / 2, y: y(p.r), width: w, height: Math.max(0, height - M.bottom - y(p.r)),
      fill: "#4c8dff", "fill-opacity": 0.75,
    }));
  }

  const chosen = run.summaryValue("features", "n_pcs");
  if (Number.isFinite(chosen) && chosen <= points.length) {
    const line = el("line", {
      x1: x(chosen), x2: x(chosen), y1: M.top, y2: height - M.bottom,
      stroke: "#2dd4a7", "stroke-dasharray": "3 3",
    });
    root.appendChild(line);
    root.appendChild(el("text", {
      class: "label", x: x(chosen) + 4, y: M.top + 10, fill: "#2dd4a7",
    }, `n_pcs = ${chosen}`));
  }

  const wrap = document.createElement("div");
  wrap.appendChild(root);
  return {
    title: "Variance carried by each principal component",
    subtitle: `the chosen cut keeps ` +
      `${(points[Math.min(points.length, chosen || points.length) - 1].cum * 100).toFixed(1)}% ` +
      `of the variance in the HVG space`,
    node: wrap,
  };
}

/* -- composition --------------------------------------------------------- */

export function clusterSizes(run) {
  const e = run.viz.embedding;
  if (!e?.cluster?.length) return null;
  const counts = tally(e.cluster);
  const keys = [...counts.keys()].sort(byNaturalOrder);
  const color = categoryScale(keys);
  return {
    title: "Cluster sizes",
    subtitle: `${keys.length} clusters at resolution ` +
      `${run.summaryValue("cluster", "resolution") ?? "?"}`,
    node: rowBars(keys.map((k) => ({ label: k, value: counts.get(k), color: color(k) }))),
  };
}

export function markerTable(run) {
  const markers = run.viz.markers;
  if (!markers || !Object.keys(markers).length) return null;
  const labels = run.labels();

  const table = document.createElement("div");
  table.className = "options";
  for (const key of Object.keys(markers).sort(byNaturalOrder)) {
    const row = document.createElement("div");
    row.className = "option";
    const name = document.createElement("span");
    name.className = "option-key";
    name.textContent = labels[key] ? `${key} · ${labels[key]}` : `cluster ${key}`;
    const genes = document.createElement("span");
    genes.className = "option-desc mono";
    genes.textContent = markers[key].join(", ");
    row.append(name, genes);
    table.appendChild(row);
  }
  return {
    title: "Top marker genes per cluster",
    subtitle: "the exact evidence handed to Claude",
    node: table,
  };
}

/* -- evaluation ---------------------------------------------------------- */

export function confusion(run) {
  const c = run.viz.confusion;
  if (!c?.matrix?.length) return null;

  const rows = c.rows, cols = c.cols;
  const cell = 22, left = 128, top = 8;
  const width = left + cols.length * cell + 10;
  const height = top + rows.length * cell + 96;
  const max = Math.max(...c.matrix.flat(), 1);

  const root = svg(width, height);
  rows.forEach((r, i) => {
    root.appendChild(el("text", {
      class: "tick", x: left - 6, y: top + i * cell + cell / 2 + 3, "text-anchor": "end",
    }, truncate(r, 20)));
    cols.forEach((_, j) => {
      const v = c.matrix[i][j];
      root.appendChild(el("rect", {
        x: left + j * cell, y: top + i * cell, width: cell - 1.5, height: cell - 1.5,
        rx: 2, fill: "#2dd4a7", "fill-opacity": v === 0 ? 0.04 : 0.12 + 0.88 * (v / max),
      }));
    });
  });
  cols.forEach((cname, j) => {
    const cx = left + j * cell + cell / 2;
    const cy = top + rows.length * cell + 6;
    root.appendChild(el("text", {
      class: "tick", x: cx, y: cy, "text-anchor": "end",
      transform: `rotate(-90 ${cx} ${cy})`,
    }, truncate(String(cname), 12)));
  });

  const wrap = document.createElement("div");
  wrap.appendChild(root);
  return {
    title: "Leiden cluster vs. held-out label",
    subtitle: "rows are author labels, columns are clusters; a clean run is one bright cell per row",
    node: wrap,
  };
}

export function metricsPanel(run) {
  const m = run.metrics;
  if (!m || !Object.keys(m).length) return null;
  const rows = [
    ["ARI", m.ari, "partition vs. author labels"],
    ["NMI", m.nmi, "shared information"],
    ["probe accuracy", m.probe_accuracy, "logistic probe on PCA"],
    ["probe macro F1", m.probe_macro_f1, "per-type, unweighted"],
    ["label accuracy", m.annotation_normalised_accuracy,
     "Claude's cell type vs. the author's, name-normalised"],
    ["label macro F1", m.annotation_macro_f1, "per-type, unweighted"],
  ].filter(([, v]) => Number.isFinite(v));
  if (!rows.length) return null;

  const node = document.createElement("div");
  node.className = "tiles";
  for (const [k, v, note] of rows) {
    const tile = document.createElement("div");
    tile.className = "tile";
    tile.innerHTML =
      `<div class="tile-v">${v.toFixed(3)}</div>` +
      `<div class="tile-k">${k}</div>` +
      `<div class="tile-note">${note}</div>`;
    node.appendChild(tile);
  }
  return {
    title: "Scores against held-out ground truth",
    subtitle: `${(m.n_evaluated ?? 0).toLocaleString()} labelled cells, ` +
      `${m.n_true_types ?? "?"} author types vs. ${m.n_clusters ?? "?"} clusters`,
    node,
  };
}

/** How Claude's naming scored, shown at the step that produced it. */
export function annotationScores(run) {
  const m = run.metrics ?? {};
  if (!Number.isFinite(m.annotation_normalised_accuracy)) return null;

  const node = document.createElement("div");
  node.className = "tiles";
  const rows = [
    ["exact", m.annotation_exact_accuracy, "string-identical to the author label"],
    ["normalised", m.annotation_normalised_accuracy, "after name normalisation"],
    ["macro F1", m.annotation_macro_f1, "per-type, unweighted"],
    ["types named", m.annotation_n_predicted_types, "distinct labels returned"],
  ].filter(([, v]) => Number.isFinite(v));
  for (const [k, v, note] of rows) {
    const tile = document.createElement("div");
    tile.className = "tile";
    tile.innerHTML =
      `<div class="tile-v">${Number.isInteger(v) && v > 1 ? v : v.toFixed(3)}</div>` +
      `<div class="tile-k">${k}</div><div class="tile-note">${note}</div>`;
    node.appendChild(tile);
  }
  // An offline run names clusters `cluster_0`, which scores 0 by construction.
  // A real naming that still scores 0 means the scorer did not match the
  // author's vocabulary - a different statement, so do not conflate them.
  const names = Object.values(run.labels());
  const placeholder = names.length > 0 && names.every((n) => /^cluster[_ ]?\d+$/i.test(n));

  return {
    title: "How the naming scored",
    subtitle: placeholder
      ? "this run named clusters `cluster_0`, so these read 0 by construction"
      : "scored against the author's label vocabulary, which it has to match by name",
    node,
  };
}

/* -- small summaries ----------------------------------------------------- */

export function datasetShape(run) {
  const s = run.summary("load");
  if (!s) return null;
  return {
    title: "What was loaded",
    subtitle: "the matrix every later decision is made about",
    node: kvGrid([
      ["cells", s.n_cells?.toLocaleString()],
      ["genes", s.n_genes?.toLocaleString()],
      ["label key", s.label_key ?? "none"],
      ["labelled cells", s.n_labelled_cells?.toLocaleString()],
    ]),
  };
}

export function batchSummary(run) {
  const s = run.summary("integrate");
  if (!s) return null;
  return {
    title: "Batch structure",
    subtitle: s.reason ?? "integration outcome",
    node: kvGrid([
      ["applied", s.applied ?? "none"],
      ["batch key", s.batch_key ?? "none found"],
      ["batches", run.obs.n_batches ?? 1],
    ]),
  };
}

/* -- shared builders ----------------------------------------------------- */

function barsFromHistogram(binned, xLabel, yLabel) {
  const width = 520, height = 260;
  const x = linear([binned[0].x0, binned[binned.length - 1].x1], [M.left, width - M.right]);
  const y = linear([0, Math.max(...binned.map((b) => b.count))], [height - M.bottom, M.top]);

  const root = svg(width, height);
  axes(root, { x, y, height, margin: M, xLabel, yLabel });
  for (const b of binned) {
    root.appendChild(el("rect", {
      x: x(b.x0), y: y(b.count),
      width: Math.max(1, x(b.x1) - x(b.x0) - 1),
      height: Math.max(0, height - M.bottom - y(b.count)),
      fill: "#4c8dff", "fill-opacity": 0.75,
    }));
  }
  const wrap = document.createElement("div");
  wrap.appendChild(root);
  return wrap;
}

function stackedBar(parts) {
  const total = parts.reduce((a, p) => a + (p.value || 0), 0) || 1;
  const wrap = document.createElement("div");
  const bar = document.createElement("div");
  bar.style.cssText =
    "display:flex;height:22px;border-radius:6px;overflow:hidden;border:1px solid var(--border)";
  for (const p of parts) {
    const seg = document.createElement("div");
    seg.style.cssText =
      `width:${((p.value || 0) / total) * 100}%;background:${p.color};opacity:.75`;
    seg.title = `${p.label}: ${(p.value || 0).toLocaleString()}`;
    bar.appendChild(seg);
  }
  wrap.appendChild(bar);
  wrap.appendChild(legend(parts.map((p) => ({
    label: `${p.label} — ${(p.value || 0).toLocaleString()}`, color: p.color,
  }))));
  return wrap;
}

function rowBars(items) {
  const max = Math.max(...items.map((i) => i.value), 1);
  const wrap = document.createElement("div");
  wrap.className = "options";
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "option";
    const fill = document.createElement("span");
    fill.className = "fill";
    fill.style.width = `${(item.value / max) * 100}%`;
    fill.style.background = item.color;
    fill.style.opacity = "0.22";
    const k = document.createElement("span");
    k.className = "option-key";
    k.textContent = item.label;
    const v = document.createElement("span");
    v.className = "option-p";
    v.textContent = item.value.toLocaleString();
    row.append(fill, k, v);
    wrap.appendChild(row);
  }
  return wrap;
}

function kvGrid(pairs) {
  const grid = document.createElement("div");
  grid.className = "kv";
  for (const [k, v] of pairs) {
    const cell = document.createElement("div");
    cell.className = "kv-cell";
    cell.innerHTML = `<div class="kv-k"></div><div class="kv-v"></div>`;
    cell.firstChild.textContent = k;
    cell.lastChild.textContent = v ?? "—";
    grid.appendChild(cell);
  }
  return grid;
}

function tally(values) {
  const counts = new Map();
  for (const v of values) counts.set(String(v), (counts.get(String(v)) ?? 0) + 1);
  return counts;
}

function byNaturalOrder(a, b) {
  const na = Number(a), nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return String(a).localeCompare(String(b));
}

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

function truncate(s, n) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export { format };
