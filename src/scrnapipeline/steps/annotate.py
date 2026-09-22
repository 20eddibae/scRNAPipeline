"""Step 7: annotation.

Ranking marker genes is mechanical; reading them is the part a language model is
actually good at. Claude gets the top markers per cluster and returns a cell-type
label plus its confidence - the one place in the pipeline where prose is the
right output.
"""

from __future__ import annotations

import json
from typing import Any

import scanpy as sc

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
    description = "Rank marker genes per cluster and have Claude name the cell types."
    needs = ("cluster",)

    def __init__(self, annotator: Any = None, context: str = "human PBMC"):
        self.annotator = annotator  # ClaudeClient | None -> marker-only fallback
        self.context = context

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")
        markers = _top_markers(adata, n=10)
        adata.uns["top_markers"] = markers

        if self.annotator is None:
            labels = {cluster: f"cluster_{cluster}" for cluster in markers}
            source = "none (no annotator configured)"
        else:
            labels, source = _ask_claude(self.annotator, markers, self.context)

        adata.obs["cell_type"] = adata.obs["leiden"].map(labels).astype("category")
        state.observe(annotation_source=source, n_annotated=len(labels))
        return adata, {
            "source": source,
            "labels": labels,
            "markers": {k: v[:5] for k, v in markers.items()},
        }


def _top_markers(adata: Any, n: int = 10) -> dict[str, list[str]]:
    names = adata.uns["rank_genes_groups"]["names"]
    return {group: [str(g) for g in names[group][:n]] for group in names.dtype.names}


def _ask_claude(client: Any, markers: dict[str, list[str]], context: str) -> tuple[dict, str]:
    block = "\n".join(f"Cluster {k}: {', '.join(v)}" for k, v in markers.items())
    prompt = ANNOTATION_PROMPT.format(context=context, markers=block)
    text = client.ask(prompt)
    try:
        payload = json.loads(_strip_fence(text))
        labels = {str(c["cluster"]): str(c["label"]) for c in payload["clusters"]}
        return labels, "claude"
    except Exception as exc:
        fallback = {k: f"cluster_{k}" for k in markers}
        return fallback, f"fallback (unparseable response: {exc.__class__.__name__})"


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text.strip()
