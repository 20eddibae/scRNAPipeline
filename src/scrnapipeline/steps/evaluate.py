"""Step 8: score the run against the ground truth.

Two readouts, deliberately different in kind:

  * unsupervised - ARI / NMI of the Leiden partition against the author labels,
    which asks whether the pipeline's choices recovered the known structure;
  * supervised - a linear (logistic) probe on the PCA embedding, which asks how
    much cell-type information the representation carries at all.

The scTab comparison slots in beside the probe as a third column once its
checkpoint is wired up; see `scrnapipeline.baselines`.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import adjusted_rand_score, f1_score, normalized_mutual_info_score
from sklearn.model_selection import train_test_split

from ..state import RunState
from .base import Step


class EvaluateStep(Step):
    name = "evaluate"
    description = "Score clusters and the embedding against held-out ground-truth labels."
    needs = ("cluster",)

    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        label_key = state.obs.get("label_key")
        if not label_key or label_key not in adata.obs:
            return adata, {"status": "skipped", "reason": "no ground-truth labels"}

        mask = adata.obs[label_key].notna().values
        if mask.sum() < 50:
            return adata, {"status": "skipped", "reason": "too few labelled cells"}

        truth = adata.obs[label_key].values[mask].astype(str)
        clusters = adata.obs["leiden"].values[mask].astype(str)

        metrics: dict[str, Any] = {
            "n_evaluated": int(mask.sum()),
            "n_true_types": int(len(set(truth))),
            "n_clusters": int(len(set(clusters))),
            "ari": round(float(adjusted_rand_score(truth, clusters)), 4),
            "nmi": round(float(normalized_mutual_info_score(truth, clusters)), 4),
        }
        metrics.update(_linear_probe(adata.obsm["X_pca"][mask], truth))
        if "cell_type" in adata.obs:
            metrics.update(_score_annotation(adata.obs["cell_type"].values[mask], truth))

        state.metrics.update(metrics)
        return adata, metrics


def _linear_probe(embedding: np.ndarray, labels: np.ndarray, seed: int = 0) -> dict[str, Any]:
    """Logistic regression on the embedding, stratified 70/30."""
    counts = {label: int((labels == label).sum()) for label in set(labels)}
    keep = np.array([counts[label] >= 4 for label in labels])
    X, y = embedding[keep], labels[keep]
    if len(set(y)) < 2:
        return {"probe": "skipped (one class)"}

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.3, random_state=seed, stratify=y
    )
    clf = LogisticRegression(max_iter=2000, multi_class="auto")
    clf.fit(X_tr, y_tr)
    pred = clf.predict(X_te)
    return {
        "probe_accuracy": round(float((pred == y_te).mean()), 4),
        "probe_macro_f1": round(float(f1_score(y_te, pred, average="macro")), 4),
        "probe_n_test": int(len(y_te)),
    }


def _score_annotation(predicted: np.ndarray, truth: np.ndarray) -> dict[str, Any]:
    """Score the pipeline's actual cell-type calls against the author's.

    This is the terminal readout: the pipeline's output is a cell type per cell,
    and this asks how often that call is right. It is a harder bar than ARI,
    which only asks whether the partition agrees -- a partition can be perfect
    while every label on it is wrong.

    Label strings come from different vocabularies (Claude writes "CD14+
    Monocyte", CELLxGENE says "CD14-positive monocyte"), so exact-match accuracy
    is reported as a floor alongside a normalised match.
    """
    predicted = np.asarray(predicted).astype(str)
    truth = np.asarray(truth).astype(str)
    exact = float((predicted == truth).mean())
    loose = float((np.array([_norm(p) for p in predicted])
                   == np.array([_norm(t) for t in truth])).mean())
    return {
        "annotation_exact_accuracy": round(exact, 4),
        "annotation_normalised_accuracy": round(loose, 4),
        "annotation_macro_f1": round(float(f1_score(truth, predicted,
                                                    average="macro",
                                                    zero_division=0)), 4),
        "annotation_n_predicted_types": int(len(set(predicted))),
    }


def _norm(label: str) -> str:
    """Fold the trivial vocabulary differences between label sets."""
    text = label.lower().strip()
    for old, new in (("-positive", "+"), ("-negative", "-"), ("_", " ")):
        text = text.replace(old, new)
    return " ".join(text.replace(",", " ").split())
