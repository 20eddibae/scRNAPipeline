/* The visualization registry - the one modular seam for plots.
 *
 * A step names panels in `src/steps/spec.js`; this maps those names to a
 * builder. To add a plot: write a `(run) => {title, subtitle, node}` function
 * in `panels.js`, add one line to PANELS, and name it in the step spec.
 * Nothing else in the app has to know it exists. */

import * as panels from "./panels.js";

export const PANELS = {
  dataset_shape:     panels.datasetShape,
  qc_scatter:        panels.qcScatter,
  qc_retention:      panels.qcRetention,
  depth_distribution:panels.depthDistribution,
  pca_variance:      panels.pcaVariance,
  batch_summary:     panels.batchSummary,
  umap_clusters:     panels.umapClusters,
  cluster_sizes:     panels.clusterSizes,
  umap_labels:       panels.umapLabels,
  marker_table:      panels.markerTable,
  annotation_scores: panels.annotationScores,
  metrics:           panels.metricsPanel,
  confusion:         panels.confusion,
};

/** Build one panel by name. Returns null when the run has no data for it. */
export function buildPanel(name, run) {
  const builder = PANELS[name];
  if (!builder) return null;
  try {
    const spec = builder(run);
    return spec ? frame(spec) : null;
  } catch (err) {
    console.warn(`panel ${name} failed`, err);
    return frame({
      title: name,
      subtitle: "could not be drawn from this run record",
      node: emptyNode(String(err.message ?? err)),
    });
  }
}

/** Build every panel a step declares; silently skips ones with no data. */
export function buildPanels(names, run) {
  return (names ?? []).map((n) => buildPanel(n, run)).filter(Boolean);
}

function frame({ title, subtitle, node }) {
  const card = document.createElement("figure");
  card.className = "viz";
  card.style.margin = "0";

  const head = document.createElement("figcaption");
  head.className = "viz-head";
  const t = document.createElement("span");
  t.className = "viz-title";
  t.textContent = title;
  head.appendChild(t);
  if (subtitle) {
    const s = document.createElement("span");
    s.className = "viz-sub";
    s.textContent = subtitle;
    head.appendChild(s);
  }

  const body = document.createElement("div");
  body.className = "viz-body";
  body.appendChild(node);

  card.append(head, body);
  return card;
}

function emptyNode(message) {
  const d = document.createElement("div");
  d.className = "viz-empty";
  d.textContent = message;
  return d;
}
