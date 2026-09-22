/* The eight steps, as the UI describes them.
 *
 * This mirrors `src/scrnapipeline/registry.py::DEFAULT_ORDER` and each step
 * module's docstring. It is static copy only - every number, option and
 * probability shown in the UI comes from the run record, never from here.
 *
 * `viz` names the visual panels a step renders; the names resolve in
 * `src/viz/index.js`. Adding a panel to a step is a one-line change here. */

/** The landing view's id. Not a step - it sits above them in the rail. */
export const OVERVIEW = "overview";

export const STEPS = [
  {
    name: "load",
    title: "Load",
    blurb: "Read a public count matrix and attach held-out ground-truth labels.",
    detail:
      "Alignment and UMI dedup are out of scope; the run starts at the count matrix. " +
      "pbmc3k is loaded raw and its author labels are grafted by barcode from " +
      "pbmc3k_processed, so no label ever touches the pipeline.",
    decidedBy: "neither",
    viz: ["dataset_shape"],
  },
  {
    name: "qc",
    title: "Quality control",
    blurb: "Drop empty droplets, dying cells and (optionally) doublets.",
    detail:
      "The thresholds are the decision, not the code. A stringency preset fixes " +
      "min genes/cell, min cells/gene and the mitochondrial ceiling together.",
    decidedBy: "jev",
    viz: ["qc_scatter", "qc_retention"],
  },
  {
    name: "normalize",
    title: "Normalize",
    blurb: "Depth-correct and variance-stabilise the counts.",
    detail:
      "The genuinely contested branch point: log1p-CPM, analytic Pearson residuals, " +
      "or pooled size factors. Each one changes which genes look variable downstream.",
    decidedBy: "jev",
    viz: ["depth_distribution"],
  },
  {
    name: "features",
    title: "Features",
    blurb: "Select highly variable genes, then reduce to principal components.",
    detail:
      "Two coupled knobs: how many HVGs to keep, and how many PCs carry the signal. " +
      "Narrow sharpens dominant structure; broad preserves rare populations.",
    decidedBy: "jev",
    viz: ["pca_variance"],
  },
  {
    name: "integrate",
    title: "Integrate",
    blurb: "Correct batch effects - if there are batches, and if they matter.",
    detail:
      "The first question is whether integration is needed at all. On single-batch " +
      "data the step is a declared no-op and Jev is never asked.",
    decidedBy: "jev",
    viz: ["batch_summary"],
  },
  {
    name: "cluster",
    title: "Cluster",
    blurb: "Build the neighbour graph, run Leiden, embed with UMAP.",
    detail:
      "Resolution is the single most consequential number in the run: it decides " +
      "how many cell types the analysis is even able to report.",
    decidedBy: "jev",
    viz: ["umap_clusters", "cluster_sizes"],
  },
  {
    name: "annotate",
    title: "Annotate",
    blurb: "Pick an annotation route, then name each cluster from its markers.",
    detail:
      "Two decisions of different kinds. Which annotator to use is a typed choice " +
      "about this matrix, so Jev makes it. Reading a ranked marker list into " +
      "'CD14+ monocyte' is the one place in this pipeline where prose is the " +
      "right output, so Claude does that.",
    decidedBy: "claude",
    viz: ["umap_labels", "annotation_scores", "marker_table"],
  },
  {
    name: "evaluate",
    title: "Evaluate",
    blurb: "Score the partition and the embedding against held-out labels.",
    detail:
      "Two readouts of different kinds: ARI/NMI ask whether the decisions recovered " +
      "known structure; a logistic probe on the PCA embedding asks how much cell-type " +
      "information the representation carries at all.",
    decidedBy: "neither",
    viz: ["metrics", "confusion"],
  },
];

export const STEP_INDEX = Object.fromEntries(STEPS.map((s, i) => [s.name, i]));

export function stepSpec(name) {
  return STEPS[STEP_INDEX[name]] ?? null;
}
