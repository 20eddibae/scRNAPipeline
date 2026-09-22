/* The figures. Each one is a pure function
 *
 *     (run) -> {title, subtitle, node} | null
 *
 * and returns null when the run has nothing to draw. Registering a new figure
 * is one entry in src/viz/index.js plus one name in src/steps/spec.js.
 *
 * Where a decision set a value, the figure draws that value on the data: the QC
 * cut-offs on the QC scatter, the PC cut on the variance plot. */

import {
  axes, categoryScale, el, extent, histogram, legend, linear, pad, svg,
} from "./primitives.js";
import { umapView } from "./umap.js";

const M = { top: 12, right: 12, bottom: 34, left: 46 };

/* -- embeddings ---------------------------------------------------------- */

export function umapClusters(run) {
  const e = run.viz.embedding;
  if (!e?.coords?.length || !e.cluster) return null;
  return {
    title: "UMAP",
    subtitle: e.subsampled ? `subsampled from ${e.n_total.toLocaleString()} cells` : "",
    node: umapView(e, { initial: "cluster" }),
  };
}

export function umapLabels(run) {
  const e = run.viz.embedding;
  if (!e?.coords?.length || !e.label) return null;
  return {
    title: "UMAP",
    subtitle: "switch to Author label to compare against the held-out annotation",
    node: umapView(e, { initial: "label" }),
  };
}

/* -- QC ------------------------------------------------------------------ */

export function qcScatter(run) {
  const q = run.viz.qc;
  if (!q?.n_genes?.length || !q.pct_mt?.length) return null;

  const width = 560, height = 340;
  const x = linear(pad(extent(q.n_genes)), [M.left, width - M.right]);
  const y = linear(pad(extent(q.pct_mt)), [height - M.bottom, M.top]);

  const root = svg(width, height);
  axes(root, { x, y, height, margin: M, xLabel: "genes detected per cell", yLabel: "% mitochondrial reads" });

  const kept = q.kept ?? q.n_genes.map(() => true);
  const g = el("g");
  q.n_genes.forEach((gn, i) => {
    g.appendChild(el("circle", {
      cx: x(gn).toFixed(1), cy: y(q.pct_mt[i]).toFixed(1), r: 1.8,
      style: kept[i] ? "fill:var(--ink);fill-opacity:.45" : "fill:var(--threshold);fill-opacity:.9",
    }));
  });
  root.appendChild(g);

  // The cut-offs the chosen preset implies. Only survivors are in the matrix,
  // so a cut-off can sit outside the plotted range; pin it to the frame edge.
  const cuts = [
    ["min_genes", x, true, (v) => `≥ ${v} genes`],
    ["max_pct_mt", y, false, (v) => `≤ ${v}% mito`],
  ];
  for (const [key, scale, vertical, text] of cuts) {
    const v = run.summaryValue("qc", key);
    if (!Number.isFinite(v)) continue;
    const at = clamp(scale(v), ...(vertical ? [M.left, width - M.right] : [M.top, height - M.bottom]));
    root.appendChild(el("line", {
      ...(vertical
        ? { x1: at, x2: at, y1: M.top, y2: height - M.bottom }
        : { x1: M.left, x2: width - M.right, y1: at, y2: at }),
      style: "stroke:var(--threshold);stroke-width:1.5", "stroke-dasharray": "5 4",
    }));
    root.appendChild(el("text", {
      class: "label", style: "fill:var(--threshold)",
      x: vertical ? at + 5 : width - M.right - 4,
      y: vertical ? M.top + 10 : at - 5,
      "text-anchor": vertical ? "start" : "end",
    }, text(v)));
  }

  const anyDropped = kept.some((k) => !k);
  const wrap = document.createElement("div");
  wrap.appendChild(root);
  wrap.appendChild(legend([
    { label: "cell kept", color: "var(--ink)" },
    ...(anyDropped ? [{ label: "cell removed", color: "var(--threshold)" }] : []),
    { label: "cut-off set by the decision", color: "var(--threshold)", line: true },
  ]));

  return {
    title: "Genes per cell against mitochondrial fraction",
    subtitle: anyDropped ? "" : "surviving cells only; the cut-offs are drawn where they fall",
    node: wrap,
  };
}

export function qcRetention(run) {
  const s = run.summary("qc");
  if (!s || !Number.isFinite(Number(s.cells_before))) return null;
  const before = Number(s.cells_before), after = Number(s.cells_after);
  const change = before ? ((after - before) / before) * 100 : 0;
  return {
    title: "Cells before and after filtering",
    subtitle: "",
    node: table(
      ["", "before", "after", "change"],
      [["cells", before.toLocaleString(), after.toLocaleString(), `${change.toFixed(1)}%`]],
      { numeric: [1, 2, 3] },
    ),
  };
}

/* -- distributions ------------------------------------------------------- */

export function depthDistribution(run) {
  const values = run.viz.qc?.total_counts;
  if (!values?.length) return null;
  const logged = values.filter((v) => v > 0).map((v) => Math.log10(v));
  return {
    title: "Counts per cell",
    subtitle: "log10 scale; the spread that normalization removes",
    node: barsFromHistogram(histogram(logged, 36), "log10 total counts", "cells"),
  };
}

export function pcaVariance(run) {
  const ratios = run.viz.pca_variance;
  if (!ratios?.length) return null;
  const chosen = run.summaryValue("features", "n_pcs");
  let cum = 0;
  const points = ratios.map((r, i) => ({ i: i + 1, r, cum: (cum += r) }));

  const width = 560, height = 300;
  const x = linear([0.5, points.length + 0.5], [M.left, width - M.right]);
  const y = linear([0, Math.max(...ratios) * 1.1], [height - M.bottom, M.top]);

  const root = svg(width, height);
  axes(root, { x, y, height, margin: M, xLabel: "principal component", yLabel: "variance ratio" });

  const w = Math.max(1, (width - M.left - M.right) / points.length - 1);
  for (const p of points) {
    const used = !Number.isFinite(chosen) || p.i <= chosen;
    root.appendChild(el("rect", {
      x: x(p.i) - w / 2, y: y(p.r), width: w, height: Math.max(0, height - M.bottom - y(p.r)),
      style: used ? "fill:var(--ink)" : "fill:var(--border-2)",
    }));
  }

  if (Number.isFinite(chosen) && chosen <= points.length) {
    const at = x(chosen + 0.5);
    root.appendChild(el("line", {
      x1: at, x2: at, y1: M.top, y2: height - M.bottom,
      style: "stroke:var(--threshold);stroke-width:1.5", "stroke-dasharray": "5 4",
    }));
    root.appendChild(el("text", {
      class: "label", x: at + 5, y: M.top + 10, style: "fill:var(--threshold)",
    }, `${chosen} PCs used`));
  }

  const kept = points[Math.min(points.length, chosen || points.length) - 1].cum;
  return {
    title: "Variance per principal component",
    subtitle: `the PCs used carry ${(kept * 100).toFixed(1)}% of the variance in the selected genes`,
    node: wrapOf(root),
  };
}

/* -- composition --------------------------------------------------------- */

export function clusterSizes(run) {
  const e = run.viz.embedding;
  if (!e?.cluster?.length) return null;
  const counts = tally(e.cluster);
  const keys = [...counts.keys()].sort(byNaturalOrder);
  const color = categoryScale(keys);
  const max = Math.max(...counts.values(), 1);

  const wrap = document.createElement("div");
  wrap.className = "bars";
  for (const k of keys) {
    const row = document.createElement("div");
    row.style.cssText = "display:grid;grid-template-columns:72px 1fr 48px;gap:8px;align-items:center;font-size:12.5px;margin:3px 0";
    const name = document.createElement("span");
    name.textContent = `cluster ${k}`;
    const track = document.createElement("span");
    track.style.cssText = "height:10px;background:var(--surface-2);border-radius:2px;overflow:hidden";
    const fill = document.createElement("i");
    fill.style.cssText = `display:block;height:100%;width:${(counts.get(k) / max) * 100}%;background:${color(k)}`;
    track.appendChild(fill);
    const v = document.createElement("span");
    v.style.cssText = "text-align:right;color:var(--text-faint)";
    v.textContent = counts.get(k).toLocaleString();
    row.append(name, track, v);
    wrap.appendChild(row);
  }
  return {
    title: "Cells per cluster",
    subtitle: `resolution ${run.summaryValue("cluster", "resolution") ?? "?"}`,
    node: wrap,
  };
}

export function markerTable(run) {
  const markers = run.viz.markers;
  if (!markers || !Object.keys(markers).length) return null;
  const labels = run.labels();
  const keys = Object.keys(markers).sort(byNaturalOrder);
  const color = categoryScale(keys);

  const rows = keys.map((k) => [
    swatchText(`${k}`, color(k)),
    labels[k] ?? "",
    genesText(markers[k]),
  ]);
  return {
    title: "Marker genes per cluster",
    subtitle: "the evidence each cell-type name was read from",
    node: table(["cluster", "named as", "top markers"], rows),
  };
}

/* -- evaluation ---------------------------------------------------------- */

export function confusion(run) {
  const c = run.viz.confusion;
  if (!c?.matrix?.length) return null;

  const rows = c.rows, cols = c.cols;
  const cell = 26, left = 150, top = 8;
  const width = left + cols.length * cell + 10;
  const height = top + rows.length * cell + 40;
  const max = Math.max(...c.matrix.flat(), 1);

  const root = svg(width, height);
  rows.forEach((r, i) => {
    root.appendChild(el("text", {
      class: "tick", x: left - 8, y: top + i * cell + cell / 2 + 3, "text-anchor": "end",
    }, truncate(r, 24)));
    cols.forEach((_, j) => {
      const v = c.matrix[i][j];
      const rect = el("rect", {
        x: left + j * cell, y: top + i * cell, width: cell - 2, height: cell - 2, rx: 2,
        style: `fill:var(--ink);fill-opacity:${v === 0 ? 0.04 : (0.1 + 0.9 * (v / max)).toFixed(3)}`,
      });
      rect.appendChild(el("title", {}, `${r} / cluster ${cols[j]}: ${v} cells`));
      root.appendChild(rect);
    });
  });
  cols.forEach((cname, j) => {
    root.appendChild(el("text", {
      class: "tick", x: left + j * cell + cell / 2 - 1, y: top + rows.length * cell + 14,
      "text-anchor": "middle",
    }, truncate(String(cname), 4)));
  });
  root.appendChild(el("text", {
    class: "label", x: left + (cols.length * cell) / 2, y: height - 4, "text-anchor": "middle",
  }, "Leiden cluster"));

  return {
    title: "Author label by cluster",
    subtitle: "one dark cell per row means the clustering matches the annotation",
    node: wrapOf(root),
  };
}

export function metricsPanel(run) {
  const m = run.metrics;
  if (!m || !Object.keys(m).length) return null;
  const rows = [
    ["ARI", m.ari, "clusters vs. author labels"],
    ["NMI", m.nmi, "shared information, same comparison"],
    ["label accuracy", m.annotation_matched_accuracy, "cells given the right type, naming differences removed"],
    ["probe accuracy", m.probe_accuracy, "logistic regression on the PCA embedding"],
    ["probe macro F1", m.probe_macro_f1, "same probe, each type weighted equally"],
  ].filter(([, v]) => Number.isFinite(v));
  if (!rows.length) return null;

  return {
    title: "Scores",
    subtitle: `${(m.n_evaluated ?? 0).toLocaleString()} labelled cells; ` +
      `${m.n_true_types ?? "?"} author types, ${m.n_clusters ?? "?"} clusters`,
    node: tiles(rows.map(([k, v, note]) => [v.toFixed(3), k, note])),
  };
}

/** How the naming scored, shown at the step that produced it. */
export function annotationScores(run) {
  const m = run.metrics ?? {};
  if (!Number.isFinite(m.annotation_normalised_accuracy)) return null;

  // `matched` asks whether the right cells were grouped and given one
  // consistent name. The string metrics below it drop when the annotator uses
  // different words from the author, so the gap measures vocabulary, not biology.
  const rows = [
    ["matched accuracy", m.annotation_matched_accuracy, "best one-to-one map from names to author types"],
    ["normalised string match", m.annotation_normalised_accuracy, "after folding case, plurals and suffixes"],
    ["exact string match", m.annotation_exact_accuracy, "identical strings only"],
    ["macro F1", m.annotation_macro_f1, "per type, on the folded names"],
  ].filter(([, v]) => Number.isFinite(v));

  const names = Object.values(run.labels());
  const placeholder = names.length > 0 && names.every((x) => /^cluster[_ ]?\d+$/i.test(x));
  return {
    title: "How the names scored",
    subtitle: placeholder
      ? "this run named clusters cluster_0, cluster_1 and so on, so these read 0"
      : "string matches are lower when the two label sets use different words",
    node: table(["metric", "value", ""],
      rows.map(([k, v, note]) => [k, v.toFixed(3), dim(note)]), { numeric: [1] }),
  };
}

/* -- small summaries ----------------------------------------------------- */

export function datasetShape(run) {
  const s = run.summary("load");
  if (!s) return null;
  return {
    title: "Input matrix",
    subtitle: "",
    node: kvGrid([
      ["cells", s.n_cells?.toLocaleString()],
      ["genes", s.n_genes?.toLocaleString()],
      ["batches", s.n_batches ?? 1],
      ["cells with an author label", s.n_labelled_cells?.toLocaleString()],
    ]),
  };
}

export function batchSummary(run) {
  const s = run.summary("integrate");
  if (!s) return null;
  return {
    title: "Batches",
    subtitle: "",
    node: kvGrid([
      ["batches", run.obs.n_batches ?? 1],
      ["batch column", s.batch_key ?? "none"],
      ["correction", s.applied ?? "none"],
    ]),
  };
}

/* -- shared builders ----------------------------------------------------- */

function barsFromHistogram(binned, xLabel, yLabel) {
  const width = 560, height = 260;
  const x = linear([binned[0].x0, binned[binned.length - 1].x1], [M.left, width - M.right]);
  const y = linear([0, Math.max(...binned.map((b) => b.count))], [height - M.bottom, M.top]);

  const root = svg(width, height);
  axes(root, { x, y, height, margin: M, xLabel, yLabel });
  for (const b of binned) {
    root.appendChild(el("rect", {
      x: x(b.x0), y: y(b.count),
      width: Math.max(1, x(b.x1) - x(b.x0) - 1),
      height: Math.max(0, height - M.bottom - y(b.count)),
      style: "fill:var(--ink)",
    }));
  }
  return wrapOf(root);
}

export function table(head, rows, { numeric = [] } = {}) {
  const t = document.createElement("table");
  t.className = "plain";
  const tr = document.createElement("tr");
  head.forEach((hname, i) => {
    const th = document.createElement("th");
    th.textContent = hname;
    if (numeric.includes(i)) th.className = "num";
    tr.appendChild(th);
  });
  const thead = document.createElement("thead");
  thead.appendChild(tr);
  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const r = document.createElement("tr");
    row.forEach((cell, i) => {
      const td = document.createElement("td");
      if (numeric.includes(i)) td.className = "num";
      if (cell instanceof Node) td.appendChild(cell);
      else td.textContent = cell ?? "";
      r.appendChild(td);
    });
    tbody.appendChild(r);
  }
  t.append(thead, tbody);
  return t;
}

function tiles(items) {
  const node = document.createElement("div");
  node.className = "tiles";
  for (const [v, k, note] of items) {
    const tile = document.createElement("div");
    tile.className = "tile";
    for (const [cls, text] of [["tile-v", v], ["tile-k", k], ["tile-note", note]]) {
      const d = document.createElement("div");
      d.className = cls;
      d.textContent = text;
      tile.appendChild(d);
    }
    node.appendChild(tile);
  }
  return node;
}

function kvGrid(pairs) {
  const grid = document.createElement("div");
  grid.className = "kv";
  for (const [k, v] of pairs) {
    const cell = document.createElement("div");
    cell.className = "kv-cell";
    const kk = document.createElement("div");
    kk.className = "kv-k";
    kk.textContent = k;
    const vv = document.createElement("div");
    vv.className = "kv-v";
    vv.textContent = v ?? "—";
    cell.append(kk, vv);
    grid.appendChild(cell);
  }
  return grid;
}

function swatchText(text, color) {
  const s = document.createElement("span");
  s.style.cssText = "display:inline-flex;align-items:center;gap:6px";
  const dot = document.createElement("i");
  dot.style.cssText = `width:9px;height:9px;border-radius:50%;background:${color}`;
  s.append(dot, document.createTextNode(text));
  return s;
}

function genesText(genes) {
  const s = document.createElement("span");
  s.className = "genes";
  s.textContent = (genes ?? []).join("  ");
  return s;
}

function dim(text) {
  const s = document.createElement("span");
  s.className = "dim";
  s.textContent = text;
  return s;
}

function wrapOf(node) {
  const wrap = document.createElement("div");
  wrap.appendChild(node);
  return wrap;
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
