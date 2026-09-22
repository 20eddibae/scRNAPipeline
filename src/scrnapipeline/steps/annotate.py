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

    def __init__(self, annotator: Any = None, context: str = "human PBMC"):
        self.annotator = annotator  # ClaudeClient | None
        self.context = context

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
            "sctab": lambda: _annotate_sctab(adata),
        })

        try:
            (labels, per_cell, source), where = executor.run(model)
        except Exception as exc:
            # A model that cannot run here is a recorded fallback, not a dead run.
            labels, per_cell, source = _cluster_names(markers), None, \
                f"fallback from {model} ({exc.__class__.__name__}: {exc})"
            where = "local"

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


def _top_markers(adata: Any, n: int = 10) -> dict[str, list[str]]:
    names = adata.uns["rank_genes_groups"]["names"]
    return {group: [str(g) for g in names[group][:n]] for group in names.dtype.names}


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text.strip()
