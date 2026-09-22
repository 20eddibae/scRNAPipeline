"""Pay numba's compile cost before anyone presses Run.

`neighbors`, `umap`, `rank_genes_groups` and `calculate_qc_metrics` are numba
kernels compiled on first call in each process. On pbmc3k that first call is
~20 s of the ~22 s of compute in a whole run (neighbors 11.8 s, umap 4.5 s,
rank_genes_groups 3.4 s) - so without this the first demo run after a restart
or a Modal cold start is the slow one. A few hundred random cells trigger the
same compilation.
"""

from __future__ import annotations

import threading
import time


def warm_up() -> float:
    """Run each numba-backed scanpy call once on a toy matrix. Returns seconds."""
    import anndata as ad
    import numpy as np
    import scanpy as sc
    from scipy import sparse

    started = time.time()
    rng = np.random.default_rng(0)
    # Sparse float32 CSR, as the loaders deliver: numba specialises on the
    # array type, and a dense toy compiles kernels the real run never uses.
    counts = rng.poisson(1.0, size=(300, 200)).astype(np.float32)
    adata = ad.AnnData(sparse.csr_matrix(counts))
    adata.var_names = [f"g{i}" for i in range(adata.n_vars)]
    adata.var_names = ["MT-1", *adata.var_names[1:]]
    adata.var["mt"] = adata.var_names.str.startswith("MT-")
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None,
                               log1p=False, inplace=True)
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    sc.pp.highly_variable_genes(adata, n_top_genes=100)
    adata.raw = adata  # rank_genes_groups reads raw, as in the annotate step
    sc.pp.scale(adata, max_value=10)
    sc.tl.pca(adata, n_comps=10, svd_solver="arpack")
    sc.pp.neighbors(adata, n_neighbors=15)
    sc.tl.leiden(adata, resolution=0.8, flavor="igraph", n_iterations=2,
                 directed=False)
    sc.tl.umap(adata)
    sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")

    # The model SDKs are imported lazily on the first question, which puts
    # their import (seconds on a network filesystem) inside the first step.
    for module in ("anthropic", "typesafe_sdk"):
        try:
            __import__(module)
        except ImportError:
            pass  # offline installs run without them
    return time.time() - started


def warm_up_in_background() -> threading.Thread:
    """Start the warm-up without holding up the server's first response.

    A run that starts while it is still going just waits on the same compile,
    so this can only save time, never cost it.
    """
    def _run() -> None:
        try:
            print(f"[warmup] numba kernels compiled in {warm_up():.1f}s", flush=True)
        except Exception as exc:  # a failed warm-up only means a slower first run
            print(f"[warmup] skipped ({exc.__class__.__name__}: {exc})", flush=True)

    thread = threading.Thread(target=_run, name="numba-warmup", daemon=True)
    thread.start()
    return thread
