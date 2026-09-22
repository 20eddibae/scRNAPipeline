"""Step 1-2: start from a processed public matrix.

Read alignment is out of scope for a 6-hour build; the pipeline begins at the
count matrix, as the plan says. Datasets that ship an author label are the ones
worth using here, because the label is the ground truth the run is scored on.
"""

from __future__ import annotations

from pathlib import Path
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
        if spec.startswith("merlin:"):
            adata, label_key = _load_merlin(spec.split(":", 1)[1])
            state.observe(pre_normalized=True)
        elif spec.startswith("h5ad:"):
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

        # A store that is already size-factor + log1p normalised has no counts to
        # keep, and the steps that assume counts must know that rather than run
        # log1p a second time.
        pre_normalized = bool(state.obs.get("pre_normalized"))
        if not pre_normalized:
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
            "n_batches": _n_batches(adata),
            "pre_normalized": pre_normalized,
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


def _load_merlin(path: str, split: str = "val", max_cells: int = 20000):
    """Read an scTab Merlin parquet store into AnnData.

    This store is the scTab *training* format: 19,331 genes in a fixed feature
    order, already size-factor + log1p normalised, already QC-filtered. It is a
    good evaluation substrate -- CELLxGENE ontology `cell_type` labels and real
    `tech_sample` batch structure -- and a poor pipeline input, because the QC
    and normalization decisions have already been made inside it. Loading it
    sets `pre_normalized`, and those two steps skip rather than corrupt the data.
    """
    import anndata as ad
    import numpy as np
    import pandas as pd

    root = Path(path)
    if root.is_file() or not (root / split).exists():
        raise ValueError(
            f"{path!r} must be an unpacked Merlin store directory containing "
            f"{split}/ and var.parquet"
        )

    frame = pd.read_parquet(root / split)
    if len(frame) > max_cells:
        frame = frame.sample(max_cells, random_state=0).reset_index(drop=True)

    var = pd.read_parquet(root / "var.parquet")
    matrix = np.vstack(frame["X"].to_numpy()).astype(np.float32)

    obs = frame.drop(columns=["X"]).reset_index(drop=True)
    for column in ("cell_type", "tissue", "assay", "disease", "tissue_general"):
        lookup = root / "categorical_lookup" / f"{column}.parquet"
        if column in obs and lookup.exists():
            table = pd.read_parquet(lookup)
            obs[column] = table.iloc[:, 0].to_numpy()[obs[column].to_numpy()]

    obs["batch"] = obs["tech_sample"].astype(str) if "tech_sample" in obs else "0"
    obs.index = obs.index.astype(str)

    adata = ad.AnnData(X=matrix, obs=obs)
    adata.var_names = var["feature_name"].astype(str).to_numpy()
    adata.var["feature_id"] = var["feature_id"].to_numpy()
    return adata, "cell_type"
