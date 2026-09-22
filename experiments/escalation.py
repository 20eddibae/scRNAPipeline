"""Experiment 8: what drives the resolution swing on blood, and does escalating Jev's abstentions fix it?

Experiment 7 found downstream cell accuracy on scTab blood swinging 0.765-0.870
across resolutions, non-monotone. "Unclear" is scored as wrong, and Jev
abstained on 1-2 clusters per rung. If the swing is abstentions rather than
partition quality, the lever is not the resolution decision but what happens
after Jev says "I can't name this".

Arms, on each rung's identical partition (seed-0 Leiden, as in Experiment 7):

  jev          the production route (cluster_nature + evidence loop + floor)
  escalate     jev, but a cluster left Unclear is sent to Claude with the same
               markers and the same closed list. Jev's abstention is the router;
               Claude is paid only on the clusters Jev declined.
  claude       Claude on every cluster, the same prompt as Experiment 3. The
               reference for what escalation leaves on the table.

Per-cluster rows (size, majority true type, each arm's label) are saved so
the swing can be attributed to named clusters.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from head_to_head import arm_claude
from nature_split import PINNED, score, to_vocab
from scrnapipeline.config import load_settings
from scrnapipeline.pipeline import Pipeline
from scrnapipeline.steps.annotate import _annotate_jev, _top_markers

DATASET = sys.argv[1] if len(sys.argv) > 1 else "sctab_val_blood"
RUNGS = [float(r) for r in os.environ.get("ESC_RUNGS", "0.6,0.8,1.0").split(",")]
MODEL = os.environ.get("ESC_MODEL", "claude-haiku-4-5")
OUT = Path(f"experiments/results/escalation_{DATASET}.json")


def main():
    import scanpy as sc

    settings = load_settings()
    pinned = {k: v for k, v in PINNED.items() if k != "cluster.resolution"}
    if DATASET != "pbmc3k":
        pinned.update({"integrate.integrate": True, "integrate.method": "harmony"})
    pipe = Pipeline(DATASET, overrides=pinned, plan=False)
    for step in ("load", "qc", "normalize", "features", "integrate"):
        pipe.execute(step)
    base = pipe.adata
    label_key = pipe.state.obs["label_key"]
    rep = "X_emb" if "X_emb" in base.obsm else "X_pca"
    sc.pp.neighbors(base, use_rep=rep, n_neighbors=15)

    report = {"dataset": DATASET, "model": MODEL, "rungs": {}}
    for r in RUNGS:
        a = base.copy()
        sc.tl.leiden(a, resolution=r, key_added="leiden", flavor="igraph",
                     n_iterations=2, directed=False, random_state=0)
        sc.tl.rank_genes_groups(a, "leiden", method="wilcoxon")
        jev_labels, _, src = _annotate_jev(a, _top_markers(a, n=10), settings, "human PBMC")
        # _annotate_jev may have split clusters; name the partition it left.
        markers = a.uns["top_markers"] if "leiden_presplit" in a.obs else _top_markers(a, n=10)
        sizes = {c: int((a.obs["leiden"] == c).sum()) for c in markers}

        claude_calls, cost = arm_claude(markers, sizes, "human PBMC", settings, model=MODEL)
        claude_labels = {c: v["label"] for c, v in claude_calls.items()}
        unclear = [c for c, l in jev_labels.items() if l in (None, "Unclear")]
        esc_labels = {c: (claude_labels.get(c, "Unclear") if c in unclear else l)
                      for c, l in jev_labels.items()}

        truth = np.array([to_vocab(str(t)) if t == t else None
                          for t in a.obs[label_key].values], dtype=object)
        leiden = a.obs["leiden"].astype(str).to_numpy()
        rows = []
        for c in sorted(markers, key=lambda x: (len(x), x)):
            t = [x for x in truth[leiden == c] if x is not None]
            maj = Counter(t).most_common(1)[0] if t else (None, 0)
            rows.append({"cluster": c, "size": sizes[c], "majority": maj[0],
                         "majority_share": round(maj[1] / max(len(t), 1), 2),
                         "jev": jev_labels.get(c), "claude": claude_labels.get(c),
                         "top5": markers[c][:5]})
        arms = {"jev": jev_labels, "escalate": esc_labels, "claude": claude_labels}
        res = {name: score(a, labels, label_key)["cell_accuracy_vocab"] for name, labels in arms.items()}
        report["rungs"][str(r)] = {"accuracy": res, "n_unclear": len(unclear),
                                   "cells_in_unclear": int(sum(sizes[c] for c in unclear)),
                                   "claude_cost": cost, "jev_source": src, "clusters": rows}
        print(f"rung {r}: {res}  unclear {unclear} ({report['rungs'][str(r)]['cells_in_unclear']} cells)")
        for row in rows:
            flag = "" if row["jev"] == row["majority"] else "   <-- jev wrong"
            print(f"    {row['cluster']:>4} n={row['size']:<5} truth {str(row['majority']):<22}"
                  f"({row['majority_share']})  jev {str(row['jev']):<22} claude {row['claude']}{flag}")
        OUT.write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
