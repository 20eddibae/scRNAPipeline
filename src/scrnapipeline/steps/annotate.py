"""Step 7: annotation — and *which model does the annotating* is itself a decision.

This is the stage where the three beats are clearest. Claude frames the choice
against what it can see about the data (is the tissue well described? is there a
matching reference? is the matrix already in scTab's feature space?), Jev picks
with a calibrated probability, and the chosen model then runs — in-process, or on
Modal when it wants a GPU.

That framing is deliberate. "Use scTab" is a hypothesis about this dataset, not a
fact about the world: it is the strongest option when the matrix is already in
its 19,331-gene space and the label set is CELLxGENE's, and the worst option when
neither holds. Making it a branch Jev selects means the run records *why* that
model was used, instead of it being whatever the author wired in.
"""

from __future__ import annotations

import json
from typing import Any

import scanpy as sc

from ..executors import Executor
from ..jev import ChoiceQ, Question
from ..state import RunState
from .base import Step

ANNOTATION_PROMPT = """You are annotating clusters from a single-cell RNA-seq run.

Tissue context: {context}

For each cluster below you are given its top marker genes, ranked by differential
expression against all other clusters.

{markers}

Return JSON only, no prose, with this shape:
{{"clusters": [{{"cluster": "0", "label": "CD14+ Monocyte", "confidence": 0.9,
  "evidence": "LYZ, S100A8, CD14"}}]}}

Use "Unknown" as the label when the markers do not identify a type. Confidence is
your own calibration between 0 and 1."""


class AnnotateStep(Step):
    name = "annotate"
    description = (
        "Choose an annotation model, then run it to assign a cell type per cell."
    )
    needs = ("cluster",)

    def __init__(self, annotator: Any = None, context: str = "human PBMC",
                 settings: Any = None):
        self.annotator = annotator  # ClaudeClient | None
        self.context = context
        self.settings = settings

    def questions(self, adata: Any, state: RunState) -> dict[str, Question]:
        criteria = {
            "markers_llm": (
                "Claude reads the top marker genes per cluster and names it. No "
                "reference model and no GPU; strongest when the tissue is well "
                "described in the literature and the clusters are clean."
            ),
            "celltypist": (
                "Logistic-regression classifier over curated references. Per-cell "
                "rather than per-cluster, CPU-cheap; strongest when a reference "
                "model matching this tissue exists."
            ),
            "jev_markers": (
                "Jev scores each cluster's marker genes against a fixed cell-type "
                "vocabulary and returns a calibrated probability per type. "
                "Per-cluster, sub-second, and the only option that can decline to "
                "answer when the markers are ambiguous."
            ),
            "sctab": (
                "De novo classifier trained across CELLxGENE. Per-cell, wants a "
                "GPU, and expects a fixed 19,331-gene feature space; strongest "
                "when this matrix is already in that space and the label "
                "vocabulary is the CELLxGENE ontology."
            ),
        }
        # Only offer what this matrix can actually support. scTab on a matrix in
        # a different gene space is not a worse answer, it is a broken one.
        if not state.obs.get("sctab_feature_space"):
            criteria.pop("sctab")
        return {
            "model": ChoiceQ(
                instructions=(
                    "Which model should assign cell types here? Weigh how many "
                    "clusters there are, whether the tissue is one with a "
                    "well-known marker vocabulary, and what feature space the "
                    "matrix is in."
                ),
                criteria=criteria,
                default="markers_llm",
            )
        }

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")
        markers = _top_markers(adata, n=10)
        adata.uns["top_markers"] = markers

        model = choices.get("model", "markers_llm")
        executor = Executor(local={
            "markers_llm": lambda: _annotate_markers(self.annotator, markers, self.context),
            "celltypist": lambda: _annotate_celltypist(adata),
            "jev_markers": lambda: _annotate_jev(adata, markers, self.settings,
                                                 self.context),
            "sctab": lambda: _annotate_sctab(adata),
        })

        try:
            (labels, per_cell, source), where = executor.run(model)
        except Exception as exc:
            # A model that cannot run here is a recorded fallback, not a dead run.
            labels, per_cell, source = _cluster_names(markers), None, \
                f"fallback from {model} ({exc.__class__.__name__}: {exc})"
            where = "local"

        if state.obs.get("label_key") == "cell_type":
            raise RuntimeError("ground truth lives in obs['cell_type']; writing "
                               "predictions there would score the run against itself")
        if per_cell is not None:
            adata.obs["cell_type"] = per_cell
        else:
            adata.obs["cell_type"] = adata.obs["leiden"].map(labels).astype("category")

        state.observe(annotation_model=model, annotation_source=source)
        return adata, {
            "model": model,
            "ran_on": where,
            "source": source,
            "granularity": "per-cell" if per_cell is not None else "per-cluster",
            "n_types_assigned": int(adata.obs["cell_type"].nunique()),
            "markers": {k: v[:5] for k, v in markers.items()},
        }


# -- the three annotation models -----------------------------------------

def _annotate_markers(client: Any, markers: dict[str, list[str]], context: str):
    if client is None:
        return _cluster_names(markers), None, "none (no annotator configured)"
    block = "\n".join(f"Cluster {k}: {', '.join(v)}" for k, v in markers.items())
    text = client.ask(ANNOTATION_PROMPT.format(context=context, markers=block))
    try:
        payload = json.loads(_strip_fence(text))
        labels = {str(c["cluster"]): str(c["label"]) for c in payload["clusters"]}
        return labels, None, "claude (markers)"
    except Exception as exc:
        return _cluster_names(markers), None, \
            f"fallback (unparseable response: {exc.__class__.__name__})"


def _annotate_celltypist(adata: Any, model: str = "Immune_All_Low.pkl"):
    """CellTypist wants log1p-normalised-to-10k counts over the FULL gene set.

    By the time this step runs, `adata.X` has been subset to highly variable
    genes and then scaled to zero mean -- feeding that in would produce labels
    that look plausible and mean nothing. `features` stashes the pre-subset,
    pre-scaling matrix in `adata.raw`, which is exactly the required input.
    """
    import celltypist
    from celltypist import models

    if adata.raw is None:
        raise RuntimeError(
            "celltypist needs the full log1p-normalised gene set; adata.raw is "
            "unset, so the pre-HVG matrix was not kept"
        )
    source = adata.raw.to_adata()

    try:
        models.download_models(model=model, force_update=False)
    except Exception as exc:
        raise RuntimeError(f"could not fetch celltypist model {model!r}: {exc}") from exc

    result = celltypist.annotate(source, model=model, majority_voting=True)
    labels = result.predicted_labels
    column = "majority_voting" if "majority_voting" in labels else "predicted_labels"
    per_cell = labels[column].astype(str).to_numpy()
    return {}, per_cell, f"celltypist ({model}, {column})"


def _annotate_sctab(adata: Any):
    from ..baselines import SCTabBaseline

    return {}, SCTabBaseline(checkpoint_dir="").predict(adata), "sctab"


def _cluster_names(markers: dict[str, list[str]]) -> dict[str, str]:
    return {cluster: f"cluster_{cluster}" for cluster in markers}


# Ribosomal and mitochondrial genes win differential-expression tests for the
# largest cluster and identify nothing. pbmc3k's 805-cell naive-CD4 cluster came
# back as RPS12, RPS27, RPS6, RPS25, RPL32 -- a list from which no annotator,
# model or human, could name a cell type. Dropping them is not cosmetic: it is
# the difference between asking a question and asking an unanswerable one.
UNINFORMATIVE = ("RPS", "RPL", "MT-", "MTRNR", "MALAT1", "EEF1", "TMSB")


def _top_markers(adata: Any, n: int = 10, drop_uninformative: bool = True
                 ) -> dict[str, list[str]]:
    names = adata.uns["rank_genes_groups"]["names"]
    out: dict[str, list[str]] = {}
    for group in names.dtype.names:
        genes = [str(g) for g in names[group]]
        if drop_uninformative:
            kept = [g for g in genes if not g.upper().startswith(UNINFORMATIVE)]
            # If a cluster is *nothing but* housekeeping genes, say so by leaving
            # the list short rather than backfilling it with more of the same.
            genes = kept if kept else genes
        out[group] = genes[:n]
    return out


# Asked only of clusters the model could not resolve. Canonical lineage markers,
# so the follow-up question is targeted rather than "here are more genes".
DISAMBIGUATION_PANEL = (
    "CD3D", "CD3E", "CD2",           # T lineage -- the CD8-vs-NK discriminator
    "CD4", "IL7R", "CCR7",           # CD4 helper
    "CD8A", "CD8B",                  # CD8 cytotoxic
    "NKG7", "GNLY", "KLRD1",         # cytotoxic, NK-leaning
    "MS4A1", "CD79A",                # B
    "CD14", "FCGR3A", "LYZ",         # myeloid
    "FCER1A", "PPBP",                # DC, platelet
)


def _panel_expression(adata: Any, cluster: str) -> dict[str, float]:
    """Mean expression of the canonical panel inside one cluster.

    The full gene set lives on `adata.raw`; `adata.X` has been subset to HVGs
    and scaled by this point, so CD3D may not even be present there.
    """
    import numpy as np

    source = adata.raw.to_adata() if adata.raw is not None else adata
    in_cluster = (adata.obs["leiden"] == cluster).to_numpy()
    present = [g for g in DISAMBIGUATION_PANEL if g in source.var_names]
    if not present or not in_cluster.any():
        return {}

    block = source[in_cluster, present].X
    means = np.asarray(block.mean(axis=0)).ravel()
    return {gene: round(float(value), 3) for gene, value in zip(present, means)}


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text.strip()


# The vocabulary Jev chooses from. Written from standard PBMC immunology rather
# than read off the ground-truth column -- a model handed the answer key's exact
# strings is being scored on a different, easier task. The headline metric
# (matched accuracy) is vocabulary-independent anyway; exact-match is not, and
# would be inflated by lifting these from the labels.
PBMC_VOCABULARY = {
    "CD4 T cell": "CD3+ and CD4+, IL7R, CCR7, LTB. Helper T lineage.",
    "CD8 T cell": "CD3+ and CD8A/CD8B, CCL5, GZMK. Cytotoxic T lineage.",
    "NK cell": "GNLY, NKG7, KLRD1, no CD3. Cytotoxic, non-T.",
    "B cell": "CD79A, MS4A1, CD19, HLA-DR high.",
    "Monocyte": "LYZ, S100A8/S100A9, CD14 or FCGR3A. Myeloid.",
    "Dendritic cell": "FCER1A, CST3, HLA-DR very high. Antigen presenting.",
    "Megakaryocyte/Platelet": "PPBP, PF4. Platelet lineage.",
    "Unclear": "The markers do not point to one of the above with confidence.",
}


# Asked of every cluster BEFORE it is named. Experiment 4 showed per-cluster
# naming is capped by clustering: pbmc3k's cluster 5 holds ~316 CD8 T cells and
# most of the NK cells, so every namer -- Claude, Jev, even the oracle -- has to
# drop one type. Choosing a better namer cannot fix a cluster that is two
# things. This question is where that gets caught.
#
# It is a semantic decision with no internal criterion, which is where the
# decision model belongs (RESULTS.md, "Where the decision model belongs"), and
# the boundary is genuinely biological: T-lineage plus cytotoxic genes in the
# SAME cells is a CD8 T cell, T-lineage plus myeloid genes in the same cells
# is a doublet, and T-lineage in HALF the cells of a cytotoxic cluster is two
# populations. Co-positivity alone cannot say which -- knowing which lineages
# legitimately co-express is the judgement.
CLUSTER_NATURE = {
    "one_type": "One population. The lineage markers that are present agree "
                "with each other and are carried by most cells.",
    "two_types": "Two populations in DIFFERENT cells: a lineage marker is "
                 "carried by a large minority of cells and absent from the rest "
                 "(e.g. CD3 in half of a cytotoxic cluster = CD8 T cells mixed "
                 "with NK cells). Split the cluster before naming it.",
    "doublets": "Heterotypic doublets: markers of lineages that never co-occur "
                "in one real cell (T and myeloid, B and platelet) are co-expressed "
                "in the SAME cells.",
    "low_quality": "Damaged or stressed cells: mitochondrial, ribosomal or "
                   "heat-shock genes dominate and lineage markers are weak.",
}

# Fraction-of-cells-positive, per lineage. Deliberately coarse: the question is
# whether a lineage is carried by all, some or none of the cells.
LINEAGES = {
    "T (CD3D/CD3E)": ("CD3D", "CD3E"),
    "cytotoxic (NKG7/GNLY)": ("NKG7", "GNLY"),
    "B (MS4A1/CD79A)": ("MS4A1", "CD79A"),
    "myeloid (LYZ/CD14)": ("LYZ", "CD14"),
    "platelet (PPBP/PF4)": ("PPBP", "PF4"),
}
STRESS_PREFIXES = ("MT-", "RPS", "RPL", "HSPA", "HSPB", "DNAJ")


def _lineage_evidence(adata: Any, cells: Any) -> dict[str, Any]:
    """Per-cell lineage positivity inside one set of cells, plus stress share.

    Pairs are reported only when both lineages are carried by >=10% of cells;
    for a pair, `co_positive` / `expected_if_independent` near 1 means the two
    lineages sit in the same cells about as often as chance would put them there.
    """
    import numpy as np

    source = adata.raw.to_adata() if adata.raw is not None else adata
    sub = source[cells]
    pos: dict[str, Any] = {}
    for name, genes in LINEAGES.items():
        present = [g for g in genes if g in sub.var_names]
        if not present:
            continue
        block = sub[:, present].X
        block = block.toarray() if hasattr(block, "toarray") else np.asarray(block)
        pos[name] = (block > 0).any(axis=1)
    frac = {k: round(float(v.mean()), 3) for k, v in pos.items()}
    # Positivity alone cannot separate a doublet from ambient RNA: on pbmc3k,
    # LYZ reads >1.5 log1p in 45% of CD4 T cells because lysed monocytes spill
    # it into every droplet, and the first version of this question called
    # 7 of 9 real clusters "doublets". So also report each lineage's mean level
    # here relative to the cluster that owns that lineage. A real doublet
    # carries a large share of the owner's level; ambient carries a sliver.
    relative = {}
    if "leiden" in adata.obs:
        groups = adata.obs["leiden"].astype(str).to_numpy()
        for name, genes in LINEAGES.items():
            present = [g for g in genes if g in source.var_names]
            if not present:
                continue
            block = source[:, present].X
            block = block.toarray() if hasattr(block, "toarray") else np.asarray(block)
            per_cell = block.mean(axis=1)
            owner = max(per_cell[groups == g].mean() for g in np.unique(groups))
            relative[name] = round(float(per_cell[cells].mean() / max(owner, 1e-9)), 3)
    pairs = {}
    names = [k for k in pos if frac[k] >= 0.10]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            co = float((pos[a] & pos[b]).mean())
            pairs[f"{a} & {b}"] = {"co_positive": round(co, 3),
                                   "expected_if_independent": round(frac[a] * frac[b], 3)}
    stress = [g for g in sub.var_names if str(g).upper().startswith(STRESS_PREFIXES)]
    total = np.asarray(sub.X.sum(axis=1)).ravel()
    stress_share = (np.asarray(sub[:, stress].X.sum(axis=1)).ravel() / np.maximum(total, 1e-9)
                    if stress else np.zeros(sub.n_obs))
    return {"fraction_of_cells_positive": frac, "co_positivity": pairs,
            "level_relative_to_owning_cluster": relative,
            "median_stress_gene_share": round(float(np.median(stress_share)), 3)}


def cluster_nature_question(cluster: str, size: int) -> ChoiceQ:
    return ChoiceQ(
        instructions=(
            f"Cluster {cluster} ({size} cells). Before this cluster is given a "
            "cell-type name, decide what kind of cluster it is. You are given its "
            "top differentially expressed genes, the fraction of its cells "
            "positive for each immune lineage, how often pairs of lineages are "
            "positive in the same cell, and the share of expression taken by "
            "mitochondrial/ribosomal/heat-shock genes. Some lineages legitimately "
            "co-express (CD8 T cells are CD3+ AND cytotoxic); others never do. "
            "Abundant transcripts (LYZ, PPBP, hemoglobin) leak into every droplet "
            "as ambient RNA, so low-level positivity is expected everywhere: "
            "`level_relative_to_owning_cluster` is this cluster's mean level of "
            "each lineage as a fraction of the cluster that owns it. Ambient "
            "sits well below ~0.2; doublets carry a large share."),
        criteria=CLUSTER_NATURE, default="one_type")


def _split_cluster(adata: Any, cluster: str) -> dict[str, str]:
    """Two-way split of one cluster in the embedding the clustering used.

    k-means with k=2 rather than a finer Leiden pass: the decision already said
    *two*, so a method that can return three would be second-guessing it.
    Returns {old barcode -> new cluster id}; the ids are `<cluster>a` / `<cluster>b`.
    """
    import numpy as np
    from sklearn.cluster import KMeans

    emb_key = "X_emb" if "X_emb" in adata.obsm else "X_pca"
    mask = (adata.obs["leiden"].astype(str) == cluster).to_numpy()
    parts = KMeans(n_clusters=2, n_init=10, random_state=0).fit_predict(
        np.asarray(adata.obsm[emb_key])[mask])
    return dict(zip(adata.obs_names[mask], np.where(parts == 0, f"{cluster}a", f"{cluster}b")))


def _top2_margin(probabilities: Any) -> float | None:
    if not isinstance(probabilities, dict) or len(probabilities) < 2:
        return None
    top = sorted((float(v) for v in probabilities.values()), reverse=True)
    return top[0] - top[1]


def _annotate_jev(adata: Any, markers: dict[str, list[str]], settings: Any,
                  context: str, abstain_below: float | None = None,
                  retry_with_evidence: bool = True, split_mixed: bool = True,
                  margin_below: float = 0.30, split_p: float = 0.30):
    """Ask Jev, per cluster, which cell type the marker genes indicate.

    One call per cluster rather than one per run. The probabilities are kept on
    `adata.uns` so an abstention threshold can be swept afterwards without
    paying for the calls again -- the whole point of a model whose output tokens
    are free.
    """
    from ..jev import ChoiceQ, JevDecider
    from ..state import RunState

    if settings is None:
        raise RuntimeError("jev_markers needs Settings to reach the decision model")
    decider = JevDecider(settings)
    floor = settings.confidence_floor if abstain_below is None else abstain_below

    def ask(cluster: str, size: int, genes: list[str],
            panel: dict[str, float] | None = None):
        state = RunState(f"annotate-{cluster}", context)
        state.observe(tissue=context, cluster=cluster, n_cells_in_cluster=size,
                      top_markers=genes)
        extra = ""
        if panel:
            state.observe(canonical_marker_expression=panel)
            extra = (" You are also given mean expression of a canonical lineage "
                     "panel within this cluster; use it to break ties that the "
                     "ranked markers alone cannot settle (CD3 presence separates "
                     "cytotoxic T cells from NK cells, for instance).")
        return decider.decide(
            "annotate_cluster",
            {"cell_type": ChoiceQ(
                instructions=(
                    f"These are the genes most enriched in cluster {cluster} "
                    f"({size} cells) of a {context} sample, ranked by "
                    f"differential expression against all other clusters.{extra} "
                    "Which cell type do they indicate?"
                ),
                criteria=PBMC_VOCABULARY,
                default="Unclear")},
            state,
        )["cell_type"]

    def nature(cluster: str, size: int, genes: list[str], cells: Any):
        state = RunState(f"nature-{cluster}", context)
        state.observe(tissue=context, cluster=cluster, n_cells_in_cluster=size,
                      top_markers=genes, **_lineage_evidence(adata, cells))
        return decider.decide("cluster_nature",
                              {"nature": cluster_nature_question(cluster, size)},
                              state)["nature"]

    # Pass 1: what kind of cluster is each one? Split the ones that are two
    # populations, before any of them is named.
    natures: dict[str, Any] = {}
    if split_mixed:
        leiden = adata.obs["leiden"].astype(str)
        relabel: dict[str, str] = {}
        for cluster in list(markers):
            cells = (leiden == cluster).to_numpy()
            d = nature(cluster, int(cells.sum()), markers[cluster], cells)
            natures[cluster] = {"nature": d.value, "confidence": d.confidence,
                                "probabilities": d.raw.get("probabilities"),
                                "source": d.source}
            # Not the confidence floor. The two errors cost very different
            # amounts: on pbmc3k, forcing a split of cluster 5 (CD8 + NK) lifts
            # cell accuracy 0.8685 -> 0.9572, while forcing splits of two pure
            # CD4 clusters costs nothing (both halves get the same name). With
            # a free false positive and a ~9-point false negative, the rule is
            # "split when two_types is plausible", not "when Jev is sure". Jev's
            # top answer on cluster 5 was two_types at 0.48 -- right, and below
            # a 0.55 floor. NB: split_p=0.30 was set after seeing that number.
            p_two = (d.raw.get("probabilities") or {}).get("two_types")
            natures[cluster]["p_two_types"] = p_two
            if p_two is not None and p_two >= split_p and cells.sum() >= 40:
                relabel.update(_split_cluster(adata, cluster))
                natures[cluster]["split"] = True
        if relabel:
            new = leiden.copy()
            new.loc[list(relabel)] = [relabel[b] for b in relabel]
            adata.obs["leiden_presplit"] = adata.obs["leiden"]
            adata.obs["leiden"] = new.astype("category")
            sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")
            markers = _top_markers(adata, n=10)
            adata.uns["top_markers"] = markers
        adata.uns["cluster_nature"] = natures

    per_cluster: dict[str, dict[str, Any]] = {}
    for cluster, genes in markers.items():
        size = int((adata.obs["leiden"] == cluster).sum())
        decision = ask(cluster, size, genes)
        attempts = 1
        resolved_by = "ranked markers"

        # The abstention-triggered evidence loop. An unresolved answer is not a
        # dead end, it is a request: the model has told us the ranked markers do
        # not settle this cluster, so fetch the evidence that would and ask once
        # more. One extra call, only for the clusters that need it.
        # Two triggers. Low absolute confidence is "I don't know"; a small
        # margin between the top two types is "I'm torn between two I do know"
        # -- the pbmc3k CD8-vs-NK cluster sat at 0.64, cleared the floor, and
        # never got its CD3 panel under the first trigger alone.
        margin = _top2_margin(decision.raw.get("probabilities"))
        unresolved = (decision.value == "Unclear"
                      or (decision.confidence is not None
                          and decision.confidence < floor)
                      or (margin is not None and margin < margin_below))
        if unresolved and retry_with_evidence:
            panel = _panel_expression(adata, cluster)
            if panel:
                retried = ask(cluster, size, genes, panel=panel)
                attempts = 2
                # Keep the retry only when it resolved something. An abstention
                # answered with a real type is progress; a low-confidence guess
                # is only worth replacing with a more confident one.
                if retried.value == "Unclear":
                    resolved_by = "unresolved after panel"
                elif decision.value == "Unclear":
                    decision, resolved_by = retried, "canonical panel"
                elif (retried.confidence or 0) > (decision.confidence or 0):
                    decision, resolved_by = retried, "canonical panel"
                else:
                    resolved_by = "panel did not improve on ranked markers"

        per_cluster[cluster] = {
            "label": decision.value,
            "confidence": decision.confidence,
            "probabilities": decision.raw.get("probabilities"),
            "markers": genes[:5],
            "attempts": attempts,
            "resolved_by": resolved_by,
        }

    adata.uns["jev_annotation"] = per_cluster
    labels = {
        cluster: (info["label"]
                  if (info["confidence"] is None or info["confidence"] >= floor)
                  else "Unclear")
        for cluster, info in per_cluster.items()
    }
    n_abstained = sum(1 for v in labels.values() if v == "Unclear")
    n_retried = sum(1 for i in per_cluster.values() if i["attempts"] > 1)
    n_rescued = sum(1 for i in per_cluster.values()
                    if i["resolved_by"] == "canonical panel")
    n_split = sum(1 for n in natures.values() if n.get("split"))
    flagged = sorted(c for c, n in natures.items() if n["nature"] in ("doublets", "low_quality"))
    return labels, None, (f"jev_markers (floor {floor:.2f}, {n_abstained} abstained, "
                          f"{n_retried} retried, {n_rescued} rescued by panel, "
                          f"{n_split} split, flagged {flagged or 'none'})")
