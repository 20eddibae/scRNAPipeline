"""Step 4: highly variable genes then PCA."""

from __future__ import annotations

from typing import Any

import scanpy as sc

from ..jev import ScoreQ, Question
from ..state import RunState
from .base import Step


class FeatureStep(Step):
    name = "features"
    description = "Select highly variable genes and reduce to principal components."
    needs = ("normalize",)

    def questions(self, adata: Any, state: RunState) -> dict[str, Question]:
        return {
            "n_hvg": ScoreQ(
                instructions=(
                    "How broad should the highly variable gene set be? A narrow set "
                    "sharpens dominant structure; a broad set preserves rare "
                    "populations at the cost of noise."
                ),
                criteria=[
                    "Narrow - few cell types, strong signal, ~1000 genes",
                    "Standard - a typical tissue, ~2000 genes",
                    "Broad - heterogeneous tissue or rare populations expected, ~4000 genes",
                ],
                values=[1000, 2000, 4000],
                default=2000,
            ),
            "n_pcs": ScoreQ(
                instructions=(
                    "How many principal components should carry the signal? More "
                    "components keep finer structure but admit more technical noise."
                ),
                criteria=["Compact - 20 PCs", "Standard - 50 PCs", "Generous - 100 PCs"],
                values=[20, 50, 100],
                default=50,
            ),
        }

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        n_hvg = int(choices.get("n_hvg", 2000))
        n_pcs = int(choices.get("n_pcs", 50))
        n_pcs = min(n_pcs, adata.n_obs - 1, adata.n_vars - 1)

        sc.pp.highly_variable_genes(adata, n_top_genes=n_hvg)
        adata.raw = adata
        adata = adata[:, adata.var["highly_variable"]].copy()
        sc.pp.scale(adata, max_value=10)
        sc.tl.pca(adata, n_comps=n_pcs, svd_solver="arpack")

        variance = float(adata.uns["pca"]["variance_ratio"].sum())
        state.observe(n_hvg=n_hvg, n_pcs=n_pcs, pca_variance_explained=round(variance, 3))
        return adata, {
            "n_hvg": n_hvg,
            "n_pcs": n_pcs,
            "variance_explained": round(variance, 3),
        }
