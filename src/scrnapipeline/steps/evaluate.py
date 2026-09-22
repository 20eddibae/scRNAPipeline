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
    # `multi_class` was removed in scikit-learn 1.7; multinomial is the
    # default for a multiclass problem, which is what "auto" resolved to.
    clf = LogisticRegression(max_iter=2000)
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

    # Both diagnostics below read the folded strings. Macro-F1 over the raw
    # ones was reporting 0.0 for the same reason exact-match did -- with no
    # vocabulary in common, every class has zero support in the other set, so
    # it scored spelling rather than assignment.
    pred_norm = np.array([_norm(p) for p in predicted])
    truth_norm = np.array([_norm(t) for t in truth])
    loose = float((pred_norm == truth_norm).mean())
    return {
        # The headline: right cells grouped under a consistent name, whatever
        # that name is.
        "annotation_matched_accuracy": _matched_accuracy(predicted, truth),
        # Diagnostics. These two measure VOCABULARY AGREEMENT, not biology: a
        # perfect annotator using a different label set scores near zero here.
        "annotation_exact_accuracy": round(exact, 4),
        "annotation_normalised_accuracy": round(loose, 4),
        "annotation_macro_f1": round(float(f1_score(truth_norm, pred_norm,
                                                    average="macro",
                                                    zero_division=0)), 4),
        "annotation_n_predicted_types": int(len(set(predicted))),
    }


def _matched_accuracy(predicted: np.ndarray, truth: np.ndarray) -> float:
    """Accuracy under the best one-to-one map from predicted to true labels.

    Annotators disagree about words far more than about cells. CellTypist calls
    a cluster "Tcm/Naive helper T cells" where pbmc3k's author wrote "CD4 T
    cells"; those are the same cells and exact-match scores them zero. Solving
    the assignment problem on the contingency table asks the question that
    actually matters -- were the right cells grouped together and given *a*
    consistent name -- and leaves the naming convention out of it.

    Predicted types beyond the number of true types stay unmapped and count as
    errors, so over-splitting is still penalised.
    """
    from scipy.optimize import linear_sum_assignment

    pred_labels = sorted(set(predicted))
    true_labels = sorted(set(truth))
    table = np.zeros((len(pred_labels), len(true_labels)), dtype=np.int64)
    pred_index = {label: i for i, label in enumerate(pred_labels)}
    true_index = {label: j for j, label in enumerate(true_labels)}
    for p, t in zip(predicted, truth):
        table[pred_index[p], true_index[t]] += 1

    rows, cols = linear_sum_assignment(-table)
    return round(float(table[rows, cols].sum() / len(truth)), 4)


def _norm(label: str) -> str:
    """Fold *orthographic* differences between label sets. Only those.

    The previous version folded punctuation but not number, which meant not one
    of pbmc3k's 2,638 cells matched: "B cell" against "B cells" is a plural,
    and a plural was enough to score a correct call as wrong. The three folds
    that matter in practice:

        "B cells"          -> "b cell"        plural
        "CD4+ T cell"      -> "cd4 t cell"    marker suffix, so "CD4+" and
                                              "CD4" are one claim, not two
        "CD14-positive"    -> "cd14"          spelled-out marker polarity

    What this deliberately does NOT do is fold synonyms. "CD16+ Monocyte" and
    "FCGR3A+ Monocytes" are the same cells under two naming conventions, and
    "Platelet" and "Megakaryocyte" are one lineage; folding either here would
    turn a vocabulary metric into a biology metric and hide the difference
    between an annotator that was right and one that was lucky. Naming
    convention is what `annotation_matched_accuracy` exists to factor out, by
    solving the assignment problem rather than by guessing at a thesaurus.

    A negative marker keeps its sign: "CD14-" does not fold to "cd14", because
    that is the opposite claim.
    """
    text = label.lower().strip()
    text = text.replace("-positive", "+").replace("-negative", "-")
    text = text.replace("_", " ").replace(",", " ").replace("/", " / ")
    text = text.replace("+", "")
    return " ".join(_singular(word) for word in text.split())


def _singular(word: str) -> str:
    """Crude plural fold. Applied to both label sets, so a stem it mangles
    ("langerhans" -> "langerhan") still matches its own mangling; the only cost
    of getting one wrong is an ugly string, not a wrong count."""
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word
