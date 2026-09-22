"""Step 2: QC filtering. The thresholds are the decision, not the code."""

from __future__ import annotations

from typing import Any

import numpy as np
import scanpy as sc

from ..jev import ChoiceQ, NoulQ, Question
from ..state import RunState
from .base import Step

# stringency -> (min genes/cell, min cells/gene, max mito fraction)
PRESETS: dict[str, tuple[int, int, float]] = {
    "lenient": (100, 1, 0.20),
    "standard": (200, 3, 0.10),
    "strict": (500, 5, 0.05),
}


class QCStep(Step):
    name = "qc"
    description = "Drop empty droplets, dying cells and (optionally) doublets."
    needs = ("load",)

    def questions(self, adata: Any, state: RunState) -> dict[str, Question]:
        if state.obs.get("pre_normalized"):
            return {}  # the filtering decision was already made inside the store
        return {
            "stringency": ChoiceQ(
                instructions=(
                    "How aggressively should this dataset be filtered? Weigh the "
                    "median genes per cell, the mitochondrial fraction and how many "
                    "cells there are to spare."
                ),
                criteria={
                    "lenient": "Shallow or precious data: keep almost everything.",
                    "standard": "Healthy 10x-scale data: the usual 200-gene / 10% mito cut.",
                    "strict": "Deep data with visible stress or ambient RNA: cut hard.",
                },
                default="standard",
            ),
            "flag_doublets": NoulQ(
                instructions=(
                    "Is a doublet check worth running here? True when cell count is "
                    "high enough for loading-rate doublets to matter."
                ),
                default=False,
            ),
        }

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        if state.obs.get("pre_normalized"):
            # Mitochondrial fraction is a count ratio; on log1p values it is not
            # the quantity the thresholds were chosen for. Skip, and say so.
            return adata, {"status": "skipped",
                           "reason": "store is pre-normalized and pre-filtered"}

        adata.var["mt"] = adata.var_names.str.upper().str.startswith("MT-")
        sc.pp.calculate_qc_metrics(
            adata, qc_vars=["mt"], percent_top=[20], log1p=True, inplace=True
        )
        outliers = _flag_mad_outliers(adata)
        state.observe(
            median_genes_per_cell=float(np.median(adata.obs["n_genes_by_counts"])),
            median_counts_per_cell=float(np.median(adata.obs["total_counts"])),
            median_pct_mt=float(np.median(adata.obs["pct_counts_mt"])),
        )

        min_genes, min_cells, max_mt = PRESETS[choices.get("stringency", "standard")]
        before = adata.n_obs
        sc.pp.filter_cells(adata, min_genes=min_genes)
        sc.pp.filter_genes(adata, min_cells=min_cells)
        adata = adata[adata.obs["pct_counts_mt"] < max_mt * 100].copy()

        kept = adata.obs["qc_outlier"].to_numpy()
        outliers["kept_by_fixed_thresholds"] = int(kept.sum())
        summary = {
            "outliers": outliers,
            "min_genes": min_genes,
            "min_cells": min_cells,
            "max_pct_mt": max_mt * 100,
            "cells_before": int(before),
            "cells_after": int(adata.n_obs),
            "cells_dropped": int(before - adata.n_obs),
        }

        if choices.get("flag_doublets"):
            summary["doublets"] = _score_doublets(adata)

        state.observe(n_cells=int(adata.n_obs), n_genes=int(adata.n_vars))
        return adata, summary


def _score_doublets(adata: Any) -> str:
    """Scrublet if it is installed; never fail the run over an optional tool."""
    try:
        sc.pp.scrublet(adata)
    except Exception as exc:
        return f"skipped ({exc.__class__.__name__})"
    n = int(adata.obs.get("predicted_doublet", []).sum())
    return f"{n} predicted doublets flagged (not removed)"


def _flag_mad_outliers(adata: Any) -> dict[str, Any]:
    """Flag, never drop, cells that are outliers relative to THIS sample.

    The fixed presets above cut every dataset at the same absolute numbers. The
    standard complement (scater's isOutlier; the single-cell best-practices
    book) asks instead whether a cell is far from the rest of its own sample:
    more than 5 median absolute deviations on log library size, log genes
    detected or top-20-gene share, or more than 3 MADs on mitochondrial
    fraction and above 8%. Each flagged cell records which metric flagged it,
    so the outliers can be inspected rather than silently discarded.
    """
    def mad_out(values: np.ndarray, nmads: float) -> np.ndarray:
        median = np.median(values)
        mad = np.median(np.abs(values - median))
        if mad == 0:
            return np.zeros(len(values), dtype=bool)
        return np.abs(values - median) > nmads * mad

    obs = adata.obs
    tests = {
        "library_size": mad_out(obs["log1p_total_counts"].to_numpy(), 5),
        "genes_detected": mad_out(obs["log1p_n_genes_by_counts"].to_numpy(), 5),
        "top20_share": mad_out(obs["pct_counts_in_top_20_genes"].to_numpy(), 5),
        "mito": (mad_out(obs["pct_counts_mt"].to_numpy(), 3)
                 & (obs["pct_counts_mt"].to_numpy() > 8)),
    }
    reasons = np.array([",".join(k for k, v in tests.items() if v[i])
                        for i in range(adata.n_obs)], dtype=object)
    adata.obs["qc_outlier"] = reasons != ""
    adata.obs["qc_outlier_reason"] = reasons
    return {"flagged": int((reasons != "").sum()),
            "by_metric": {k: int(v.sum()) for k, v in tests.items()},
            "rule": "5 MADs (library, genes, top-20 share); 3 MADs and >8% (mito)"}
