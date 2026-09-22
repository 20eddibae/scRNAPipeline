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
        batch_key, pooled = _pool_small_batches(adata, batch_key)
        try:
            if method == "harmony":
                adata.obsm["X_emb"] = _harmony(adata, batch_key)
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
                "reason": f"{method} unavailable: {exc.__class__.__name__}: {exc}"[:240],
            }

        state.observe(integration=method, batch_key=batch_key)
        return adata, {"applied": method, "batch_key": batch_key,
                       "pooled_small_batches": pooled}


def _harmony(adata: Any, batch_key: str):
    """Harmony on the PCA embedding, whichever way round harmonypy returns it.

    scanpy's wrapper transposes `Z_corr`, which was PCs x cells before
    harmonypy 2.0 and is cells x PCs from it on. With 2.x the wrapper raises a
    shape ValueError AFTER harmony has converged, and this step's fallback then
    reported "harmony unavailable" on every multi-batch run.
    """
    import harmonypy

    pcs = adata.obsm["X_pca"]
    corrected = harmonypy.run_harmony(pcs, adata.obs, [batch_key],
                                      verbose=False).Z_corr
    corrected = getattr(corrected, "to_numpy", lambda: corrected)()
    return corrected if corrected.shape == pcs.shape else corrected.T


MIN_BATCH_CELLS = 10


def _pool_small_batches(adata: Any, batch_key: str) -> tuple[str, int]:
    """Fold batches too small to correct into one shared level.

    A study that contributes one or two cells is not a batch any method can
    estimate an effect for, and bbknn refuses outright: the scTab blood subset
    has studies of 8, 2 and 1 cells against its 3-per-batch neighbour count.
    Returns the key to integrate on and how many batches were pooled.
    """
    counts = adata.obs[batch_key].astype(str).value_counts()
    small = counts.index[counts < MIN_BATCH_CELLS]
    if len(small) == 0:
        return batch_key, 0
    # If the pooled bucket is itself too small, it joins the largest batch.
    bucket = ("pooled_small_batches" if counts[small].sum() >= MIN_BATCH_CELLS
              else counts.index[0])
    pooled = adata.obs[batch_key].astype(str).where(
        ~adata.obs[batch_key].astype(str).isin(small), bucket)
    adata.obs["batch_integrated"] = pooled.astype("category")
    return "batch_integrated", int(len(small))


def _batch_key(adata: Any) -> str | None:
    for key in BATCH_KEYS:
        if key in adata.obs and adata.obs[key].nunique() > 1:
            return key
    return None
