"""Step 1-2: start from a processed public matrix.

Read alignment is out of scope for a 6-hour build; the pipeline begins at the
count matrix, as the plan says. Datasets that ship an author label are the ones
worth using here, because the label is the ground truth the run is scored on.
"""

from __future__ import annotations

from typing import Any

import scanpy as sc

from ..state import RunState
from .base import Step

# name -> (loader, ground-truth obs key or None)
DATASETS: dict[str, tuple[str, str | None]] = {
    "pbmc3k": ("pbmc3k", None),  # labels are grafted from pbmc3k_processed
    "pbmc3k_processed": ("pbmc3k_processed", "louvain"),
    "pbmc68k_reduced": ("pbmc68k_reduced", "bulk_labels"),
}


class LoadStep(Step):
    name = "load"
    description = "Load a public count matrix and attach ground-truth labels if any."

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        spec = state.dataset
        if spec.startswith("h5ad:"):
            adata = sc.read_h5ad(spec.split(":", 1)[1])
            label_key = state.obs.get("label_key")
        elif spec in DATASETS:
            loader, label_key = DATASETS[spec]
            adata = getattr(sc.datasets, loader)()
            if spec == "pbmc3k":
                label_key = _graft_pbmc3k_labels(adata)
        else:
            raise ValueError(
                f"unknown dataset {spec!r}; use one of {sorted(DATASETS)} or h5ad:<path>"
            )

        adata.var_names_make_unique()
        adata.layers["counts"] = adata.X.copy()

        n_labelled = int(adata.obs[label_key].notna().sum()) if label_key else 0
        state.observe(
            n_cells=int(adata.n_obs),
            n_genes=int(adata.n_vars),
            label_key=label_key,
            n_labelled_cells=n_labelled,
            n_batches=_n_batches(adata),
        )
        return adata, {
            "n_cells": int(adata.n_obs),
            "n_genes": int(adata.n_vars),
            "label_key": label_key,
            "n_labelled_cells": n_labelled,
        }


def _graft_pbmc3k_labels(adata: Any) -> str | None:
    """pbmc3k ships raw; its processed twin carries the author's cell types.

    Joining them by barcode gives a held-out label for the same cells without
    letting any label touch the pipeline itself.
    """
    try:
        processed = sc.datasets.pbmc3k_processed()
    except Exception:
        return None
    labels = processed.obs["louvain"].astype(str)
    adata.obs["ground_truth"] = labels.reindex(adata.obs_names)
    return "ground_truth"


def _n_batches(adata: Any) -> int:
    for key in ("batch", "sample", "donor", "dataset"):
        if key in adata.obs:
            return int(adata.obs[key].nunique())
    return 1
