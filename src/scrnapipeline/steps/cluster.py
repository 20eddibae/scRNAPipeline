"""Step 6: kNN graph, Leiden clustering, UMAP.

The rungs were 0.4 / 1.0 / 1.6. A sweep on two datasets says that ladder sits
too high: ARI peaks at 0.8 on both and falls away monotonically above it.

    resolution   pbmc3k ARI   pbmc68k_reduced ARI
      0.4          0.8271           0.4334
      0.8          0.8704           0.5010
      1.0          0.6679           0.4110
      1.2          0.6209           0.3809
      1.6          0.4016           0.3846

So the old ladder could not reach the best answer on either dataset, and its
middle rung - the default, and what Jev picked on pbmc3k at 0.83 confidence -
cost about 0.20 ARI there. Re-centred on 0.4 / 0.8 / 1.2.

Read that as provisional, not settled. Both datasets are PBMC, so this is two
readings of one tissue rather than two independent confirmations, and the true
optimum is a property of the data, not a constant. What the sweep does
establish is that the previous rungs were placed badly, which is a different
and safer claim than "0.8 is correct".
"""

from __future__ import annotations

from typing import Any

import scanpy as sc

from ..jev import ScoreQ, Question
from ..state import RunState
from .base import Step


class ClusterStep(Step):
    name = "cluster"
    description = "Build the neighbour graph and cluster it; embed for display."
    needs = ("features",)

    def questions(self, adata: Any, state: RunState) -> dict[str, Question]:
        return {
            "resolution": ScoreQ(
                instructions=(
                    "How granular should the clustering be? Judge from the cell "
                    "count and how many distinct populations this tissue plausibly "
                    "contains - over-clustering splits one cell type into several."
                ),
                criteria=[
                    "Coarse - broad lineages only (resolution 0.4)",
                    "Standard - the usual cell-type granularity (resolution 0.8)",
                    "Fine - subtypes and states wanted (resolution 1.2)",
                ],
                values=[0.4, 0.8, 1.2],
                default=0.8,
            )
        }

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        resolution = float(choices.get("resolution", 1.0))
        rep = "X_emb" if "X_emb" in adata.obsm else "X_pca"

        # bbknn's correction IS the neighbour graph. Rebuilding it here would
        # cluster on the uncorrected embedding and report bbknn as applied.
        graph_integrated = state.obs.get("integration") == "bbknn"
        if graph_integrated:
            rep = "bbknn graph"
        else:
            sc.pp.neighbors(adata, use_rep=rep, n_neighbors=15)
        sc.tl.leiden(adata, resolution=resolution, key_added="leiden", flavor="igraph",
                     n_iterations=2, directed=False)
        sc.tl.umap(adata)

        n_clusters = int(adata.obs["leiden"].nunique())
        state.observe(n_clusters=n_clusters, leiden_resolution=resolution)
        return adata, {
            "resolution": resolution,
            "representation": rep,
            "n_clusters": n_clusters,
        }
