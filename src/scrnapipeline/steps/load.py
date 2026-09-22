"""Step 1-2: start from a processed public matrix.

Read alignment is out of scope for a 6-hour build; the pipeline begins at the
count matrix, as the plan says. Datasets that ship an author label are the ones
worth using here, because the label is the ground truth the run is scored on.
"""

from __future__ import annotations

import os
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


# Datasets that live as files rather than as a scanpy loader. Resolved against
# SCRNA_DATA_DIR, then the working directory, so the same name works locally and
# on a Modal volume.
#
# The scTab pair is the one worth understanding before using it. The RAW export
# is the dataset this pipeline actually wants: real counts, so the QC and
# normalization decisions are live, plus CELLxGENE ontology labels and real batch
# structure. The PREPROCESSED export has had those decisions made inside it
# already -- useful as an evaluation substrate and for the scTab comparison,
# useless for demonstrating the decision layer. Nothing here trusts those names:
# `_prepare_h5ad` measures whether X holds counts and sets `pre_normalized`
# accordingly, because a filename is not evidence.
FILE_DATASETS: dict[str, tuple[str, str]] = {
    "sctab_val_raw": (
        "sctab_raw_1pct_VAL.h5ad",
        "1% of the scTab validation split, raw counts -- every decision is live.",
    ),
    "sctab_train_preprocessed": (
        "sctab_preprocessed_1pct_TRAIN.h5ad",
        "1% of the scTab training split, already preprocessed -- qc and "
        "normalize will skip.",
    ),
}


# Named row subsets of a file dataset: (parent, obs column, value, blurb).
#
# The blood subset is the one the annotation comparison needs. The full split
# spans 55 tissues and 164 types, which no PBMC vocabulary can name; its blood
# rows are PBMC-like, and their labels were assigned by the authors of 25
# independent CELLxGENE studies -- not by reading this run's markers, which is
# what pbmc3k's labels were.
FILE_SUBSETS: dict[str, tuple[str, str, str, str]] = {
    "sctab_val_blood": (
        "sctab_val_raw", "tissue_general", "blood",
        "The blood rows of the scTab validation split: raw counts, 25 studies, "
        "author-assigned CELLxGENE labels independent of any marker list.",
    ),
}


def _resolve_file_dataset(name: str) -> Path:
    filename = FILE_DATASETS[name][0]
    roots = [Path(os.environ["SCRNA_DATA_DIR"])] if os.environ.get("SCRNA_DATA_DIR") else []
    roots += [Path.cwd(), Path.cwd().parent, Path(__file__).resolve().parents[3]]
    for root in roots:
        candidate = root / filename
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"dataset {name!r} expects {filename!r}; looked in "
        f"{[str(r) for r in roots]}. Set SCRNA_DATA_DIR to where it lives."
    )


class LoadStep(Step):
    name = "load"
    description = "Load a public count matrix and attach ground-truth labels if any."

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        spec = state.dataset
        if spec in FILE_SUBSETS:
            parent, column, value, _ = FILE_SUBSETS[spec]
            path = _resolve_file_dataset(parent)
            # Backed read, so only the kept rows are ever brought into memory.
            backed = sc.read_h5ad(path, backed="r")
            rows = (backed.obs[column].astype(str) == value).to_numpy()
            adata = backed[rows].to_memory()
            backed.file.close()
            state.observe(subset=f"{column} == {value}")
            adata, label_key = _prepare_h5ad(adata, state)
        elif spec in FILE_DATASETS:
            path = _resolve_file_dataset(spec)
            if path.stat().st_size < 1024:
                raise ValueError(f"{path} looks empty -- still being written?")
            adata = sc.read_h5ad(path)
            adata, label_key = _prepare_h5ad(adata, state)
        elif spec.startswith("merlin:"):
            adata, label_key = _load_merlin(spec.split(":", 1)[1])
            state.observe(pre_normalized=True, sctab_feature_space=True)
        elif spec.startswith("h5ad:"):
            adata = sc.read_h5ad(spec.split(":", 1)[1])
            adata, label_key = _prepare_h5ad(adata, state)
        elif spec in DATASETS:
            loader, label_key = DATASETS[spec]
            adata = getattr(sc.datasets, loader)()
            if spec == "pbmc3k":
                label_key = _graft_pbmc3k_labels(adata)
        else:
            raise ValueError(
                f"unknown dataset {spec!r}; use one of "
                f"{sorted(list(DATASETS) + list(FILE_DATASETS) + list(FILE_SUBSETS))}, "
                "h5ad:<path> or "
                "merlin:<dir>"
            )

        adata.var_names_make_unique()
        # The scTab exports repeat barcodes across studies. Anything that
        # realigns by name (CellTypist does) then returns more rows than cells.
        adata.obs_names_make_unique()

        # `annotate` writes its predictions to obs["cell_type"]. CELLxGENE and
        # scTab keep their GROUND TRUTH in a column of that exact name, so left
        # there the truth is overwritten by the predictions before `evaluate`
        # reads it -- and the run scores itself: ARI 1.0, accuracy 1.0, measured
        # on the first scTab run. The truth moves to a column nothing writes.
        if label_key and label_key != "ground_truth":
            adata.obs["ground_truth"] = adata.obs[label_key].astype(str).where(
                adata.obs[label_key].notna())
            if label_key == "cell_type":
                del adata.obs["cell_type"]
            label_key = "ground_truth"

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


def _load_merlin(path: str, split: str = "val", max_cells: int | None = None):
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

    # 19,331 genes is a wide matrix: scanpy's scale step densifies to float64, so
    # n cells x 19,331 x 8 bytes has to fit. 20k cells is 3 GB there and will not
    # survive a memory-capped login session -- run the full split on Modal, where
    # the function asks for 32 GB. SCRNA_MERLIN_MAX_CELLS raises the cap.
    if max_cells is None:
        max_cells = int(os.environ.get("SCRNA_MERLIN_MAX_CELLS", "5000"))

    frame = pd.read_parquet(root / split)
    if 0 < max_cells < len(frame):
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


# Columns that carry a cell-type label, in the order we would trust them.
LABEL_CANDIDATES = (
    "cell_type", "ground_truth", "celltype", "cell_types", "CellType",
    "cell_ontology_class", "bulk_labels", "louvain", "leiden_labels", "labels",
)


def _prepare_h5ad(adata: Any, state: RunState):
    """Work out what we were handed, rather than assuming.

    Three things have to be detected and not assumed, because getting any of
    them wrong is silent:

      * which column holds the ground-truth label,
      * whether X is raw counts or has already been normalised,
      * whether the matrix sits in scTab's fixed feature space.

    The second is the one that bit us on the Merlin store: a matrix that has
    already been size-factor + log1p normalised looks fine to every downstream
    step, and normalising it again is wrong in a way nothing raises about.
    """
    label_key = state.obs.get("label_key") or _detect_label_key(adata)
    _attach_sctab_gene_names(adata, state)
    pre_normalized = not _looks_like_counts(adata)
    state.observe(pre_normalized=pre_normalized,
                  sctab_feature_space=_looks_like_sctab_space(adata))

    max_cells = int(os.environ.get("SCRNA_MAX_CELLS", "0"))
    if 0 < max_cells < adata.n_obs:
        import numpy as np

        rng = np.random.default_rng(0)
        keep = rng.choice(adata.n_obs, size=max_cells, replace=False)
        adata = adata[np.sort(keep)].copy()
        state.observe(subsampled_to=max_cells)

    # A batch key with a level every few cells is not a batch structure any
    # integration method can use: the scTab blood rows carry 2,106 donors over
    # 8,483 cells, so the study (dataset_id) is the batch there.
    if "batch" not in adata.obs:
        for key in ("tech_sample", "sample", "donor_id", "dataset_id", "batch_id"):
            if key in adata.obs and 1 < adata.obs[key].nunique() <= adata.n_obs / 50:
                adata.obs["batch"] = adata.obs[key].astype(str)
                break

    return adata, label_key


def _attach_sctab_gene_names(adata: Any, state: RunState) -> None:
    """The scTab h5ad exports ship an EMPTY var: genes are named '0'..'19330'.

    Every marker list, the CellTypist gene match and every prompt would then be
    integers -- nothing raises, and every label is noise. The names come from
    the Merlin store's var.parquet, whose index is those same integers; the
    column order was checked against biology (B cells -> CD79A/MS4A1,
    monocytes -> S100A8/LYZ, NK -> NKG7/GNLY, neurons -> SYT1/NRXN1).
    """
    if adata.n_vars != 19331 or not all(str(v).isdigit() for v in adata.var_names[:50]):
        return
    import pandas as pd

    table = pd.read_csv(Path(__file__).resolve().parent.parent / "resources"
                        / "sctab_features.csv")
    order = adata.var_names.astype(int).to_numpy()
    adata.var["feature_id"] = table["feature_id"].to_numpy()[order]
    adata.var_names = table["feature_name"].astype(str).to_numpy()[order]
    state.observe(gene_names="attached from scTab var.parquet (export had none)")


def _detect_label_key(adata: Any) -> str | None:
    for key in LABEL_CANDIDATES:
        if key in adata.obs and adata.obs[key].notna().any():
            return key
    return None


def _looks_like_counts(adata: Any, n: int = 200) -> bool:
    """Raw counts are non-negative integers. Normalised values are not.

    Checked on a sample of rows rather than the whole matrix -- this runs before
    anything has decided how much of the data to keep in memory.
    """
    import numpy as np
    from scipy.sparse import issparse

    if "counts" in adata.layers:
        return True
    block = adata.X[: min(n, adata.n_obs)]
    values = block.data if issparse(block) else np.asarray(block).ravel()
    if values.size == 0:
        return True
    values = np.asarray(values, dtype=float)
    return bool(values.min() >= 0 and np.allclose(values, np.round(values)))


def _looks_like_sctab_space(adata: Any) -> bool:
    """scTab expects a fixed 19,331-gene feature space in a fixed order."""
    return adata.n_vars == 19331
