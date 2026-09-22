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
        if state.obs.get("pre_normalized"):
            return {}  # nothing left to decide; the store ships sf-log1p values
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
                    "scran_pooling": "Median size factors: the rescaling target is set by the data rather than a fixed constant. Best with strong composition differences between cells.",
                },
                default="log1p_cpm",
            )
        }

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        if state.obs.get("pre_normalized"):
            adata.layers["normalized"] = adata.X.copy()
            state.observe(normalization="pre-applied (sf-log1p in the store)")
            return adata, {"status": "skipped",
                           "reason": "store already carries sf-log1p values; "
                                     "normalizing again would log1p twice"}

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
                _median_size_factors(adata)
            except Exception as exc:
                method = f"log1p_cpm (median size factors unavailable: {exc.__class__.__name__})"
                _log1p_cpm(adata)
        else:
            _log1p_cpm(adata)

        adata.layers["normalized"] = adata.X.copy()
        state.observe(normalization=method)
        return adata, {"applied": method}


def _log1p_cpm(adata: Any) -> None:
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)


def _median_size_factors(adata: Any) -> None:
    """Median size-factor normalisation.

    This is NOT scran. scran estimates size factors by pooling across similar
    cells and needs the R package; this scales each cell by its own total and
    rescales to the median library size, which is the standard CPU stand-in and
    is genuinely different from the fixed 1e4 target -- the target is set by the
    data rather than by a constant.

    The previous implementation divided a sparse matrix by a dense column and
    raised ValueError on every real run, so the arm silently fell back to
    log1p_cpm and looked identical to it.
    """
    sc.pp.normalize_total(adata, target_sum=None)
    sc.pp.log1p(adata)
