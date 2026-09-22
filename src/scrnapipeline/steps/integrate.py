"""Step 5: batch integration, including whether it is needed at all."""

from __future__ import annotations

from typing import Any

import scanpy as sc

from ..jev import ChoiceQ, NoulQ, Question
from ..state import RunState
from .base import Step

BATCH_KEYS = ("batch", "sample", "donor", "dataset")


class IntegrateStep(Step):
    name = "integrate"
    description = "Correct batch effects, if there are batches and they matter."
    needs = ("features",)

    def questions(self, adata: Any, state: RunState) -> dict[str, Question]:
        if _batch_key(adata) is None:
            return {}
        return {
            "integrate": NoulQ(
                instructions=(
                    "Does this run need batch integration? True only when the "
                    "batches are separate samples or donors likely to separate in "
                    "the embedding for technical reasons."
                ),
                default=True,
            ),
            "method": ChoiceQ(
                instructions="Which integration method fits the batch structure and scale?",
                criteria={
                    "harmony": "Fast linear correction on the PC embedding. The default.",
                    "bbknn": "Graph-level correction; good with many small batches.",
                    "none": "Leave the embedding uncorrected.",
                },
                default="harmony",
            ),
        }

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        batch_key = _batch_key(adata)
        if batch_key is None:
            adata.obsm["X_emb"] = adata.obsm["X_pca"]
            return adata, {"applied": "none", "reason": "single batch"}

        if not choices.get("integrate", True) or choices.get("method") == "none":
            adata.obsm["X_emb"] = adata.obsm["X_pca"]
            return adata, {"applied": "none", "batch_key": batch_key}

        method = choices.get("method", "harmony")
        try:
            if method == "harmony":
                sc.external.pp.harmony_integrate(adata, key=batch_key)
                adata.obsm["X_emb"] = adata.obsm["X_pca_harmony"]
            elif method == "bbknn":
                sc.external.pp.bbknn(adata, batch_key=batch_key)
                adata.obsm["X_emb"] = adata.obsm["X_pca"]
            else:
                raise ValueError(f"unknown method {method!r}")
        except Exception as exc:
            adata.obsm["X_emb"] = adata.obsm["X_pca"]
            return adata, {
                "applied": "none",
                "batch_key": batch_key,
                "reason": f"{method} unavailable: {exc.__class__.__name__}",
            }

        state.observe(integration=method, batch_key=batch_key)
        return adata, {"applied": method, "batch_key": batch_key}


def _batch_key(adata: Any) -> str | None:
    for key in BATCH_KEYS:
        if key in adata.obs and adata.obs[key].nunique() > 1:
            return key
    return None
