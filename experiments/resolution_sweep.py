"""Where should the clustering-resolution rungs sit?

RESULTS.md experiment 1 found 0.4 beating 1.0 on pbmc3k and flagged two limits:
ARI may simply prefer fewer clusters, and n = 1 dataset. This addresses both by
sweeping a finer grid on two datasets and reporting ARI *and* NMI, which do not
share ARI's preference for a matched cluster count.

    python experiments/resolution_sweep.py

Everything upstream of `cluster` is computed once per dataset and reused, so the
arms differ in resolution and nothing else.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

GRID = (0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0)


def prepare(dataset: str):
    """Run the pipeline's prefix once. Returns (adata, ground-truth key)."""
    import scanpy as sc

    if dataset == "pbmc3k":
        adata = sc.datasets.pbmc3k()
        adata.var_names_make_unique()
        processed = sc.datasets.pbmc3k_processed()
        adata.obs["truth"] = processed.obs["louvain"].astype(str).reindex(adata.obs_names)
        adata.layers["counts"] = adata.X.copy()
        adata.var["mt"] = adata.var_names.str.upper().str.startswith("MT-")
        sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None,
                                   log1p=False, inplace=True)
        sc.pp.filter_cells(adata, min_genes=200)
        sc.pp.filter_genes(adata, min_cells=3)
        adata = adata[adata.obs["pct_counts_mt"] < 10].copy()
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)
        sc.pp.highly_variable_genes(adata, n_top_genes=2000)
        adata.raw = adata
        adata = adata[:, adata.var["highly_variable"]].copy()
        sc.pp.scale(adata, max_value=10)
        sc.tl.pca(adata, n_comps=50, svd_solver="arpack")
    else:
        # pbmc68k_reduced ships pre-reduced with bulk-sorted labels, which are a
        # genuinely independent reference rather than another pipeline's output.
        adata = sc.datasets.pbmc68k_reduced()
        adata.obs["truth"] = adata.obs["bulk_labels"].astype(str)

    sc.pp.neighbors(adata, use_rep="X_pca", n_neighbors=15)
    return adata


def sweep(dataset: str) -> list[dict]:
    import numpy as np
    import scanpy as sc
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    adata = prepare(dataset)
    mask = adata.obs["truth"].notna().values
    truth = adata.obs["truth"].values[mask].astype(str)

    rows = []
    for resolution in GRID:
        sc.tl.leiden(adata, resolution=resolution, key_added="sweep",
                     flavor="igraph", n_iterations=2, directed=False)
        found = adata.obs["sweep"].values[mask].astype(str)
        rows.append({
            "dataset": dataset,
            "resolution": resolution,
            "n_clusters": int(adata.obs["sweep"].nunique()),
            "n_true_types": int(len(set(truth))),
            "ari": round(float(adjusted_rand_score(truth, found)), 4),
            "nmi": round(float(normalized_mutual_info_score(truth, found)), 4),
        })
    return rows


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

    everything = []
    for dataset in ("pbmc3k", "pbmc68k_reduced"):
        rows = sweep(dataset)
        everything.extend(rows)
        print(f"\n{dataset}  ({rows[0]['n_true_types']} true types)")
        print(f"{'resolution':>10} {'clusters':>9} {'ARI':>8} {'NMI':>8}")
        best_ari = max(r["ari"] for r in rows)
        best_nmi = max(r["nmi"] for r in rows)
        for r in rows:
            mark = " <- best" if r["ari"] == best_ari and r["nmi"] == best_nmi else ""
            print(f"{r['resolution']:>10} {r['n_clusters']:>9} "
                  f"{r['ari']:>8.4f} {r['nmi']:>8.4f}{mark}")

    out = Path(__file__).resolve().parent / "results" / "resolution_sweep.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(everything, indent=1))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
