"""Reference points the pipeline's output is measured against.

`LinearProbe` is implemented (see `steps/evaluate.py`, which calls the same
scikit-learn path). `SCTabBaseline` is the slot for theislab/scTab: a de novo
cell-type classifier trained across CELLxGENE, so it answers the annotation
question without any of this pipeline's decisions. It is NOT implemented yet -
the checkpoint is 8.1 GB and the model expects the Merlin feature space, so the
adapter has to map our var_names onto scTab's gene order before it can be run.

Checkpoints: https://pklab.med.harvard.edu/felix/data/scTab-checkpoints.tar.gz
Paper: Fischer et al., Nat Commun 15 (2024). https://doi.org/10.1038/s41467-024-51059-5
"""

from __future__ import annotations

from typing import Any


class SCTabBaseline:
    """Not implemented. Left explicit so nothing silently reports a fake number."""

    def __init__(self, checkpoint_dir: str):
        self.checkpoint_dir = checkpoint_dir

    def predict(self, adata: Any) -> Any:
        raise NotImplementedError(
            "scTab adapter not wired up: needs the checkpoint, the Merlin gene "
            "order, and a var_names -> scTab feature-space mapping."
        )
