/* The eight steps, as the UI describes them.
 *
 * Mirrors `src/scrnapipeline/registry.py::DEFAULT_ORDER`. The prose here is
 * static; every number, option and probability on the page comes from the run
 * record. `result(run)` reads a step's own summary into one line, and `brief`
 * is the short form used in the step list.
 *
 * `viz` names the figures a step shows; the names resolve in src/viz/index.js. */

/** The landing view's id. Not a step - it sits above them in the list. */
export const OVERVIEW = "overview";

const n = (v) => (Number.isFinite(Number(v)) ? Number(v).toLocaleString() : "?");

export const STEPS = [
  {
    name: "load",
    title: "Load",
    blurb: "Read the count matrix and attach the author's cell-type labels, held out for scoring.",
    detail:
      "The run starts from counts; alignment and UMI deduplication are out of scope. " +
      "The labels never reach the pipeline, only the evaluation step.",
    decidedBy: "neither",
    viz: ["dataset_shape"],
    result: (s) => `${n(s.n_cells)} cells × ${n(s.n_genes)} genes, ` +
      `${n(s.n_batches ?? 1)} batch${Number(s.n_batches) > 1 ? "es" : ""}` +
      (s.n_labelled_cells ? `; ${n(s.n_labelled_cells)} cells carry an author label` : ""),
    brief: (s) => `${n(s.n_cells)} cells`,
  },
  {
    name: "qc",
    title: "Quality control",
    blurb: "Remove empty droplets and dying cells, and optionally flag doublets.",
    detail:
      "A stringency preset sets three cut-offs together: minimum genes per cell, " +
      "minimum cells per gene, and the ceiling on mitochondrial reads.",
    decidedBy: "jev",
    viz: ["qc_scatter", "qc_retention"],
    result: (s) => `Kept ${n(s.cells_after)} of ${n(s.cells_before)} cells ` +
      `(${n(s.cells_dropped)} removed). Cut-offs: at least ${n(s.min_genes)} genes per cell, ` +
      `at most ${n(s.max_pct_mt)}% mitochondrial reads, genes seen in at least ${n(s.min_cells)} cells.`,
    brief: (s) => `${n(s.cells_after)} of ${n(s.cells_before)} kept`,
  },
  {
    name: "normalize",
    title: "Normalize",
    blurb: "Correct for sequencing depth and stabilise the variance.",
    detail:
      "Log-CPM, Pearson residuals and pooled size factors each change which genes " +
      "look variable in the next step.",
    decidedBy: "jev",
    viz: ["depth_distribution"],
    result: (s) => s.skipped || s.reason
      ? `Skipped: ${s.reason ?? "input already normalized"}.`
      : `Applied ${s.applied ?? s.method}.`,
    brief: (s) => s.applied ?? s.method ?? "",
  },
  {
    name: "features",
    title: "Features",
    blurb: "Keep the most variable genes, then reduce them to principal components.",
    detail:
      "Fewer genes sharpen the dominant structure; more keep rare populations visible.",
    decidedBy: "jev",
    viz: ["pca_variance"],
    result: (s) => `${n(s.n_hvg)} variable genes, reduced to ${n(s.n_pcs)} PCs` +
      (Number.isFinite(s.variance_explained)
        ? ` carrying ${(s.variance_explained * 100).toFixed(1)}% of the variance.` : "."),
    brief: (s) => `${n(s.n_hvg)} genes · ${n(s.n_pcs)} PCs`,
  },
  {
    name: "integrate",
    title: "Integrate",
    blurb: "Correct batch effects, if there is more than one batch.",
    detail:
      "On single-batch data this step does nothing and no question is asked.",
    decidedBy: "jev",
    viz: ["batch_summary"],
    result: (s) => s.applied && s.applied !== "none"
      ? `Applied ${s.applied}${s.batch_key ? ` on ${s.batch_key}` : ""}.`
      : `Not applied: ${s.reason ?? "no batches"}.`,
    brief: (s) => (s.applied && s.applied !== "none" ? s.applied : "skipped"),
  },
  {
    name: "cluster",
    title: "Cluster",
    blurb: "Build a neighbour graph, split it with Leiden, and embed it with UMAP.",
    detail:
      "Resolution sets how many groups the analysis can report, so it bounds " +
      "everything after it.",
    decidedBy: "jev",
    viz: ["umap_clusters", "cluster_sizes"],
    result: (s) => `${n(s.n_clusters)} clusters at resolution ${s.resolution}.`,
    brief: (s) => `${n(s.n_clusters)} clusters`,
  },
  {
    name: "annotate",
    title: "Annotate",
    blurb: "Choose an annotator, then name each cluster from its marker genes.",
    detail:
      "Which annotator to use is a choice Jev makes. Reading a marker list into a " +
      "cell type is done cluster by cluster, and the evidence is shown below.",
    decidedBy: "claude",
    viz: ["umap_labels", "marker_table", "annotation_scores"],
    result: (s) => `${n(s.n_types_assigned)} cell types named` +
      (s.model ? ` using ${s.model}` : "") + ".",
    brief: (s) => `${n(s.n_types_assigned)} cell types`,
  },
  {
    name: "evaluate",
    title: "Evaluate",
    blurb: "Score the clusters and the labels against the author's annotation.",
    detail:
      "ARI and NMI compare the clustering with the author's labels. The probe is a " +
      "logistic regression on the PCA embedding: how much cell-type information the " +
      "representation holds before any clustering.",
    decidedBy: "neither",
    viz: ["metrics", "confusion"],
    result: (s) => `ARI ${fix(s.ari)}, label accuracy ${fix(s.annotation_matched_accuracy)}, ` +
      `probe accuracy ${fix(s.probe_accuracy)}.`,
    brief: (s) => `ARI ${fix(s.ari)}`,
  },
];

/** Plain-English prompt for each decision id. Unknown ids fall back to the id. */
export const QUESTIONS = {
  "qc.stringency": "How strict should the cell filter be?",
  "qc.flag_doublets": "Run a doublet detector?",
  "normalize.method": "Which normalization?",
  "features.n_hvg": "How many variable genes to keep?",
  "features.n_pcs": "How many principal components?",
  "integrate.method": "Which integration method?",
  "integrate.needed": "Is batch correction needed?",
  "cluster.resolution": "What clustering resolution?",
  "annotate.model": "Which annotator should name the clusters?",
};

export const STEP_INDEX = Object.fromEntries(STEPS.map((s, i) => [s.name, i]));

export function stepSpec(name) {
  return STEPS[STEP_INDEX[name]] ?? null;
}

function fix(v) {
  return Number.isFinite(Number(v)) ? Number(v).toFixed(3) : "?";
}
