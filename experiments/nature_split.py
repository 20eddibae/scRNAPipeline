"""Experiment 6: does asking "what kind of cluster is this?" lift the ceiling?

Experiment 4 found the per-cluster ceiling is set by the clustering: pbmc3k's
cluster 5 holds CD8 T cells and NK cells together, so any namer drops one of
them. The cluster_nature decision is meant to catch that before naming. This
runs the production Jev route three ways on the identical clustering from
Experiments 3/4, so the only thing that differs is the route:

  before      the Experiment-3 route: confidence-floor trigger only, no split
  margin      + the evidence loop also fires on a small top-2 margin
  nature      + cluster_nature asked per cluster; split when P(two_types) >= 0.30

Scored as cell accuracy in the shared vocabulary (Experiment 3's metric) and
against the oracle for the partition each route ends up naming -- a split that
raises the ceiling but is then named wrongly shows up as a gap to its own oracle.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from head_to_head import PINNED, TRUTH_TO_VOCAB
from scrnapipeline.config import load_settings
from scrnapipeline.pipeline import Pipeline
from scrnapipeline.steps.annotate import _annotate_jev, _top_markers

REPEATS = 3
OUT = Path("experiments/results/nature_split.json")
ROUTES = {"before": dict(split_mixed=False, margin_below=0.0),
          "margin": dict(split_mixed=False, margin_below=0.30),
          "nature": dict(split_mixed=True, margin_below=0.30)}


def score(adata, labels, label_key):
    mask = adata.obs[label_key].notna().values
    truth = np.array([TRUTH_TO_VOCAB.get(t) for t in adata.obs[label_key].values[mask].astype(str)])
    clusters = adata.obs["leiden"].values[mask].astype(str)
    pred = np.array([labels.get(c) for c in clusters])
    oracle = {c: Counter(truth[clusters == c]).most_common(1)[0][0] for c in set(clusters)}
    return {"cell_accuracy_vocab": round(float((pred == truth).mean()), 4),
            "oracle_for_this_partition": round(float(
                np.mean([oracle[c] == t for c, t in zip(clusters, truth)])), 4),
            "n_clusters": len(set(clusters)),
            "types_present": sorted(set(labels.values()))}


def main():
    import scanpy as sc

    settings = load_settings()
    pipe = Pipeline("pbmc3k", overrides=PINNED, plan=False)
    for step in ("load", "qc", "normalize", "features", "integrate", "cluster"):
        pipe.execute(step)
    base = pipe.adata
    label_key = pipe.state.obs["label_key"]
    sc.tl.rank_genes_groups(base, "leiden", method="wilcoxon")
    markers = _top_markers(base, n=10)

    report = {"pinned": PINNED, "repeats": REPEATS, "routes": {}}
    for route, kw in ROUTES.items():
        runs = []
        for i in range(REPEATS):
            adata = base.copy()
            labels, _, source = _annotate_jev(adata, dict(markers), settings,
                                              "human PBMC", **kw)
            s = score(adata, labels, label_key)
            s["source"] = source
            s["labels"] = labels
            s["nature"] = adata.uns.get("cluster_nature", {})
            runs.append(s)
            print(f"{route:7s} rep{i}  acc {s['cell_accuracy_vocab']}  oracle "
                  f"{s['oracle_for_this_partition']}  k={s['n_clusters']}  {source}")
        report["routes"][route] = runs
        OUT.write_text(json.dumps(report, indent=2, default=str))
    for route, runs in report["routes"].items():
        accs = [r["cell_accuracy_vocab"] for r in runs]
        print(f"{route:7s} mean {np.mean(accs):.4f}  range {min(accs)}-{max(accs)}")


if __name__ == "__main__":
    main()
