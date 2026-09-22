"""Add one Claude model to an existing aggregate.json without re-running the rest.

Rebuilds each dataset's clustering with the same deterministic preprocessing,
refuses to continue if it differs from the stored run (cluster set and the
oracle's per-cluster truth must match), asks the new model once per named
cluster, and scores it -- plus a cascade that escalates Jev's torn clusters to
the new model instead of Haiku. That cascade was NOT pre-registered; it is
reported as exploratory.

    ADD_MODEL=claude-opus-5 python experiments/add_arm.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from aggregate import (OUT, Ledger, de_list, load_datasets, markers_for,  # noqa: E402
                       preprocess, score_arm, torn, MIN_CLUSTER, MIN_DE_CELLS)
from scrnapipeline.config import load_settings  # noqa: E402

MODEL = os.environ.get("ADD_MODEL", "claude-opus-5")


def main() -> int:
    settings = load_settings()
    report = json.loads(OUT.read_text())
    cascade_key = f"cascade_{MODEL.split('-')[1]}"
    for name, context, adata in load_datasets():
        d = report.get(name)
        if not d or "scores" not in d:
            continue
        if MODEL in d["scores"]:
            print(f"{name}: {MODEL} cached", flush=True)
            continue
        t0 = time.time()
        adata = preprocess(adata)
        clusters = adata.obs["leiden"].astype(str).to_numpy()
        sizes = {c: int((clusters == c).sum()) for c in np.unique(clusters)}
        named = [c for c, n in sizes.items() if n >= MIN_CLUSTER]
        if sorted(named) != sorted(d["calls"]) or len(sizes) != d["n_clusters"]:
            raise SystemExit(f"{name}: clustering differs from the stored run")
        truth_all = adata.obs["truth"].to_numpy()
        scorable = np.array([t is not None and t == t for t in truth_all])
        for c in sizes:
            t = truth_all[(clusters == c) & scorable]
            if len(t):
                v, n = np.unique(t.astype(str), return_counts=True)
                if str(v[n.argmax()]) != d["truth_by_cluster"][c]:
                    raise SystemExit(f"{name}: cluster {c} truth differs from stored run")

        markers = markers_for(adata, "leiden", named)
        ledger = Ledger(settings, context)
        new_labels, cost, secs = {}, 0.0, 0.0
        casc_labels, casc_cost, casc_secs = {}, 0.0, 0.0
        for c in sizes:
            if c not in d["calls"]:
                new_labels[c] = casc_labels[c] = "Unclear"
                continue
            call = ledger.one(MODEL, c, markers[c], sizes[c])
            d["calls"][c][MODEL] = {k: v for k, v in call.items() if k != "probabilities"}
            new_labels[c] = call["label"] or "Unclear"
            cost += call["cost_usd"]
            secs += call["seconds"]
            jev = d["calls"][c]["jev"]
            jev_torn = jev["label"] in (None, "Unclear") or jev["margin"] < 0.5
            casc_cost += jev["cost_usd"]
            casc_secs += jev["seconds"]
            if jev_torn:
                casc_labels[c] = new_labels[c]
                casc_cost += call["cost_usd"]
                casc_secs += call["seconds"]
            else:
                casc_labels[c] = jev["label"]

        sub = adata[scorable].copy()
        truth = truth_all[scorable].astype(str)
        types = [t for t in sorted(set(truth)) if (truth == t).sum() >= MIN_DE_CELLS]
        reference = {t: de_list(sub, truth, t, "truth") for t in types}
        for key, labels, cst, sec in ((MODEL, new_labels, cost, secs),
                                      (cascade_key, casc_labels, casc_cost, casc_secs)):
            per_cell = np.array([labels[c] for c in clusters])[scorable].astype(str)
            d["scores"][key] = {**score_arm(sub, per_cell, truth, types, reference),
                                "cost_usd": round(cst, 6), "seconds": round(sec, 2)}
        print(f"{name}: {MODEL}={d['scores'][MODEL]['cell_accuracy']:.3f} "
              f"{cascade_key}={d['scores'][cascade_key]['cell_accuracy']:.3f} "
              f"sonnet={d['scores']['claude-sonnet-5']['cell_accuracy']:.3f} "
              f"(${cost:.4f}, {time.time() - t0:.0f}s)", flush=True)
        OUT.write_text(json.dumps(report, indent=2, default=str))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
