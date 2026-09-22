"""Experiment 4: does the annotation difference survive into the results?

"Annotation is solved" is only true if annotation differences do not change what
you would report. This runs the same downstream analysis twice over the same
cells -- once on raw CellTypist labels, once on the triaged labels the decision
layer produces (marker filtering, plus the evidence loop on anything it could
not resolve) -- and measures what moved:

  * how many cells got re-called,
  * how cell-type composition shifted,
  * which marker genes enter or leave the top differential-expression list.

pbmc3k has ground truth, so each of those deltas is also scored: a difference
that makes the labels *worse* is not an argument for the decision layer. A delta
on its own says only that two methods disagree.

Deliberately no new pipeline step. The point is to price the annotation
difference in the currency a biologist actually reads -- composition and DE --
using only what already exists.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import scanpy as sc

from scrnapipeline.pipeline import Pipeline
from scrnapipeline.steps.annotate import (_annotate_celltypist, _annotate_jev,
                                          _top_markers)
from scrnapipeline.steps.evaluate import _matched_accuracy

PINNED = {"qc.stringency": "standard", "qc.flag_doublets": False,
          "normalize.method": "log1p_cpm", "features.n_hvg": 2000,
          "features.n_pcs": 50, "cluster.resolution": 1.0}
TOP_N = 25


def align(predicted: np.ndarray, truth: np.ndarray) -> dict[str, str]:
    """Best one-to-one map from a label set onto the ground-truth vocabulary."""
    from scipy.optimize import linear_sum_assignment

    pred_labels = sorted(set(predicted))
    true_labels = sorted(set(truth))
    table = np.zeros((len(pred_labels), len(true_labels)), dtype=np.int64)
    pi = {l: i for i, l in enumerate(pred_labels)}
    ti = {l: j for j, l in enumerate(true_labels)}
    for p, t in zip(predicted, truth):
        table[pi[p], ti[t]] += 1
    rows, cols = linear_sum_assignment(-table)
    return {pred_labels[r]: true_labels[c] for r, c in zip(rows, cols)}


def de_genes(adata, labels: np.ndarray, group: str, key: str) -> list[str]:
    """Top differentially expressed genes for one cell type, one-vs-rest."""
    adata.obs[key] = labels
    counts = adata.obs[key].value_counts()
    if counts.get(group, 0) < 10 or len(counts[counts >= 3]) < 2:
        return []
    sc.tl.rank_genes_groups(adata, key, groups=[group], method="wilcoxon",
                            key_added=f"de_{key}")
    names = adata.uns[f"de_{key}"]["names"]
    return [str(g) for g in names[group][:TOP_N]]


def main() -> int:
    pipeline = Pipeline("pbmc3k", overrides=PINNED, plan=False)
    for step in ("load", "qc", "normalize", "features", "integrate", "cluster"):
        pipeline.execute(step)
    adata = pipeline.adata
    settings = pipeline.settings

    sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")
    markers = _top_markers(adata, n=10)
    clusters = adata.obs["leiden"].to_numpy().astype(str)

    print("annotating: A = raw celltypist, B = triaged (filter + evidence loop)",
          flush=True)
    _, per_cell_a, source_a = _annotate_celltypist(adata)
    labels_b_map, _, source_b = _annotate_jev(adata, markers, settings, "human PBMC")
    per_cell_b = np.array([labels_b_map[c] for c in clusters])
    print(f"  A: {source_a}\n  B: {source_b}", flush=True)

    label_key = pipeline.state.obs["label_key"]
    mask = adata.obs[label_key].notna().to_numpy()
    truth = adata.obs[label_key].to_numpy().astype(str)[mask]
    a = np.asarray(per_cell_a).astype(str)[mask]
    b = np.asarray(per_cell_b).astype(str)[mask]

    # Project both onto the ground-truth vocabulary so they are comparable.
    a_mapped = np.array([align(a, truth).get(x, "Unclear") for x in a])
    b_mapped = np.array([align(b, truth).get(x, "Unclear") for x in b])

    changed = a_mapped != b_mapped
    a_right, b_right = a_mapped == truth, b_mapped == truth
    report: dict = {
        "n_cells": int(mask.sum()),
        "recalled_fraction": round(float(changed.mean()), 4),
        "accuracy_A_celltypist": round(float(a_right.mean()), 4),
        "accuracy_B_triaged": round(float(b_right.mean()), 4),
        "matched_accuracy_A": _matched_accuracy(a, truth),
        "matched_accuracy_B": _matched_accuracy(b, truth),
        # Of the cells the two disagree about, who is right?
        "among_recalled": {
            "n": int(changed.sum()),
            "A_correct": int(a_right[changed].sum()),
            "B_correct": int(b_right[changed].sum()),
            "neither": int((~a_right[changed] & ~b_right[changed]).sum()),
        },
    }

    print(f"\n=== cells re-called: {report['recalled_fraction']:.1%} "
          f"({report['among_recalled']['n']} of {report['n_cells']}) ===")
    print(f"  of those, A right: {report['among_recalled']['A_correct']}"
          f"  B right: {report['among_recalled']['B_correct']}"
          f"  neither: {report['among_recalled']['neither']}")

    print("\n=== composition (fraction of cells) ===")
    types = sorted(set(truth) | set(a_mapped) | set(b_mapped))
    print(f"  {'cell type':<24s} {'truth':>8s} {'A':>8s} {'B':>8s} {'B-A':>8s}")
    composition = {}
    for t in types:
        ft, fa, fb = (truth == t).mean(), (a_mapped == t).mean(), (b_mapped == t).mean()
        composition[t] = {"truth": round(float(ft), 4), "A": round(float(fa), 4),
                          "B": round(float(fb), 4), "delta": round(float(fb - fa), 4)}
        print(f"  {t:<24s} {ft:8.3f} {fa:8.3f} {fb:8.3f} {fb - fa:+8.3f}")
    report["composition"] = composition

    print(f"\n=== top-{TOP_N} DE genes, per cell type, A vs B ===")
    sub = adata[mask].copy()
    de_delta = {}
    for t in types:
        if (a_mapped == t).sum() < 10 or (b_mapped == t).sum() < 10:
            continue
        ga = de_genes(sub, a_mapped, t, "label_a")
        gb = de_genes(sub, b_mapped, t, "label_b")
        if not ga or not gb:
            continue
        entered, left = sorted(set(gb) - set(ga)), sorted(set(ga) - set(gb))
        de_delta[t] = {"entered": entered, "left": left,
                       "jaccard": round(len(set(ga) & set(gb)) / len(set(ga) | set(gb)), 3)}
        print(f"  {t:<24s} jaccard={de_delta[t]['jaccard']:.3f}"
              f"  entered={len(entered):2d} left={len(left):2d}")
        if entered:
            print(f"      entered: {', '.join(entered[:8])}")
    report["de_delta"] = de_delta

    out = Path("experiments/results/downstream_delta.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
