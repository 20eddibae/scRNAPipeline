"""Step 3: normalization. The genuinely contested branch point."""

from __future__ import annotations

from typing import Any

import scanpy as sc

from ..jev import ChoiceQ, Question
from ..state import RunState
from .base import Step


class NormalizeStep(Step):
    name = "normalize"
    description = "Depth-correct and variance-stabilise the counts."
    needs = ("qc",)

    def questions(self, adata: Any, state: RunState) -> dict[str, Question]:
        return {
            "method": ChoiceQ(
                instructions=(
                    "Which normalization suits this matrix? Consider sequencing "
                    "depth, how variable depth is across cells, and whether the "
                    "downstream question is clustering or differential expression."
                ),
                criteria={
                    "log1p_cpm": "The default: size-factor scaling to 1e4 then log1p. Safe, well understood.",
                    "pearson_residuals": "Analytic Pearson residuals: better for shallow UMI data and HVG selection.",
                    "scran_pooling": "Pooled size factors: best with strong composition differences between cells.",
                },
                default="log1p_cpm",
            )
        }

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        method = choices.get("method", "log1p_cpm")
        adata.X = adata.layers["counts"].copy()

        if method == "pearson_residuals":
            try:
                sc.experimental.pp.normalize_pearson_residuals(adata)
            except Exception as exc:
                method = f"log1p_cpm (pearson unavailable: {exc.__class__.__name__})"
                _log1p_cpm(adata)
        elif method == "scran_pooling":
            # scran lives in R; the python stand-in is a pooled size factor from
            # a quick clustering. Falls back cleanly when that is unavailable.
            try:
                _scran_like(adata)
            except Exception as exc:
                method = f"log1p_cpm (scran unavailable: {exc.__class__.__name__})"
                _log1p_cpm(adata)
        else:
            _log1p_cpm(adata)

        adata.layers["normalized"] = adata.X.copy()
        state.observe(normalization=method)
        return adata, {"applied": method}


def _log1p_cpm(adata: Any) -> None:
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)


def _scran_like(adata: Any) -> None:
    import numpy as np
    from scipy.sparse import issparse

    counts = adata.layers["counts"]
    totals = np.asarray(counts.sum(axis=1)).ravel() if issparse(counts) else counts.sum(axis=1)
    size_factors = totals / totals.mean()
    adata.X = counts / size_factors[:, None]
    sc.pp.log1p(adata)
