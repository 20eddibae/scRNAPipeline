"""Experiment 2: per-cluster Jev annotation, and what abstention buys.

One Jev call per cluster instead of one per run, each returning a calibrated
probability over a fixed cell-type vocabulary. Because output tokens are free
and the probabilities are kept on `adata.uns`, the abstention threshold can be
swept afterwards without paying for a single extra call.

The output is a risk-coverage curve: how accurate the labels are against how
many cells got one. That curve is the human-in-the-loop interface. It does not
say "the model is good" -- it says "at this threshold you label 78% of cells at
this accuracy and hand the rest to a person", which is a staffing decision
someone can actually make.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scrnapipeline.pipeline import Pipeline
from scrnapipeline.registry import DEFAULT_ORDER
from scrnapipeline.steps.evaluate import _matched_accuracy

PINNED = {"annotate.model": "jev_markers", "qc.stringency": "standard",
          "qc.flag_doublets": False, "normalize.method": "log1p_cpm",
          "features.n_hvg": 2000, "features.n_pcs": 50, "cluster.resolution": 1.0}

THRESHOLDS = [0.0, 0.3, 0.4, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 0.95]


def main() -> int:
    pipeline = Pipeline("pbmc3k", overrides=PINNED, plan=False)
    for step in DEFAULT_ORDER:
        pipeline.execute(step)

    adata = pipeline.adata
    per_cluster = adata.uns["jev_annotation"]
    label_key = pipeline.state.obs["label_key"]

    mask = adata.obs[label_key].notna().values
    truth = adata.obs[label_key].values[mask].astype(str)
    clusters = adata.obs["leiden"].values[mask].astype(str)

    print("=== per-cluster Jev calls ===")
    for cluster, info in sorted(per_cluster.items(), key=lambda kv: int(kv[0])):
        size = int((clusters == cluster).sum())
        top_true = ""
        if size:
            values, counts = np.unique(truth[clusters == cluster], return_counts=True)
            top_true = f"{values[counts.argmax()]} ({counts.max()}/{size})"
        print(f"  cluster {cluster:>2s} n={size:>5d}  jev={info['label']:<24s}"
              f" conf={info['confidence']}  truth={top_true}")
        print(f"            markers: {', '.join(info['markers'])}")

    print("\n=== risk-coverage ===")
    print(f"  {'floor':>6s} {'coverage':>9s} {'n_labelled':>11s} {'matched_acc':>12s}")
    curve = []
    for floor in THRESHOLDS:
        keep = np.array([
            (per_cluster[c]["confidence"] is None)
            or (per_cluster[c]["confidence"] >= floor)
            for c in clusters
        ])
        coverage = float(keep.mean())
        if keep.sum() < 20:
            row = {"floor": floor, "coverage": round(coverage, 4),
                   "n_labelled": int(keep.sum()), "matched_accuracy": None}
        else:
            predicted = np.array([per_cluster[c]["label"] for c in clusters])[keep]
            row = {"floor": floor, "coverage": round(coverage, 4),
                   "n_labelled": int(keep.sum()),
                   "matched_accuracy": _matched_accuracy(predicted, truth[keep])}
        curve.append(row)
        print(f"  {floor:6.2f} {row['coverage']:9.3f} {row['n_labelled']:11d}"
              f" {str(row['matched_accuracy']):>12s}")

    report = {
        "per_cluster": per_cluster,
        "risk_coverage": curve,
        "reference": {
            "probe_accuracy": pipeline.state.metrics.get("probe_accuracy"),
            "ari": pipeline.state.metrics.get("ari"),
            "celltypist_matched_accuracy": 0.7987,  # measured earlier, same config
        },
    }
    out = Path("experiments/results/risk_coverage.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nprobe floor to beat: {report['reference']['probe_accuracy']}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
