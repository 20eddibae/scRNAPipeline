"""Step 6: kNN graph, Leiden clustering, UMAP."""

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
                    "Standard - the usual cell-type granularity (resolution 1.0)",
                    "Fine - subtypes and states wanted (resolution 1.6)",
                ],
                values=[0.4, 1.0, 1.6],
                default=1.0,
            )
        }

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        resolution = float(choices.get("resolution", 1.0))
        rep = "X_emb" if "X_emb" in adata.obsm else "X_pca"

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
