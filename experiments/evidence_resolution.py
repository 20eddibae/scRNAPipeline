"""Experiment 7: does a tuning decision become a real decision once Jev is shown its outcomes?

The boundary probe (Experiment 5) found `cluster.resolution` answering 0.8 for
every cell count from 300 to a million: it never moves. `n_hvg` sits below the
floor everywhere, so the default always decides it. One reading is that those
questions ask Jev to judge something the state never contains -- "how many
populations does this tissue plausibly have" -- so its answer can only be the
prior.

This gives the resolution question the evidence it names. Every rung is run
before the question is asked, and Jev chooses among the rungs by what each one
actually produced: cluster count, the smallest cluster, stability across Leiden
seeds, and embedding silhouette. The question then becomes semantic -- "which of
these partitions is cell-type granularity?" -- and not a guess about the tissue.

Five choosers, each scored on hindsight ARI and on downstream cell accuracy, with
the production naming route (`_annotate_jev` with cluster_nature) run on the
clustering each one picks:

  default     0.8, the declared default; what the unresponsive question yields
  jev_blind   the production question, no rung evidence
  jev_seen    the same rungs as a Choice, each described by its observed outcome
  stability   no model: the most seed-stable rung (ties -> coarser). The free
              baseline. Jev has to beat an internal criterion to earn this slot.
  hindsight   the rung with the best ARI against truth. A ceiling, not a chooser.
"""

from __future__ import annotations

import json
import os
import sys
from itertools import combinations
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nature_split import PINNED, score
from scrnapipeline.config import load_settings
from scrnapipeline.jev import ChoiceQ, JevDecider
from scrnapipeline.pipeline import Pipeline
from scrnapipeline.state import RunState
from scrnapipeline.steps.annotate import _annotate_jev, _top_markers

RUNGS = [0.4, 0.6, 0.8, 1.0, 1.2]
SEEDS = [0, 1, 2]
REPEATS = int(os.environ.get("ER_REPEATS", "3"))
DATASET = sys.argv[1] if len(sys.argv) > 1 else "pbmc3k"
OUT = Path(f"experiments/results/evidence_resolution_{DATASET}.json")


def rung_evidence(adata, rep):
    import scanpy as sc
    from sklearn.metrics import adjusted_rand_score, silhouette_score

    rng = np.random.default_rng(0)
    sample = rng.choice(adata.n_obs, size=min(3000, adata.n_obs), replace=False)
    emb = np.asarray(adata.obsm[rep])
    out, parts = {}, {}
    for r in RUNGS:
        runs = []
        for seed in SEEDS:
            sc.tl.leiden(adata, resolution=r, key_added="_tmp", flavor="igraph",
                         n_iterations=2, directed=False, random_state=seed)
            runs.append(adata.obs["_tmp"].astype(str).to_numpy())
        lab = runs[0]
        sizes = np.unique(lab, return_counts=True)[1]
        out[r] = {"n_clusters": int(len(sizes)), "smallest_cluster": int(sizes.min()),
                  "seed_stability_ari": round(float(np.mean(
                      [adjusted_rand_score(a, b) for a, b in combinations(runs, 2)])), 3),
                  "silhouette": round(float(silhouette_score(emb[sample], lab[sample])), 3)
                  if len(sizes) > 1 else None}
        parts[r] = lab
    return out, parts


def seen_question(ev) -> ChoiceQ:
    criteria = {
        str(r): (f"{e['n_clusters']} clusters, smallest {e['smallest_cluster']} cells, "
                 f"stability across Leiden seeds ARI {e['seed_stability_ari']}, "
                 f"silhouette {e['silhouette']}")
        for r, e in ev.items()}
    return ChoiceQ(
        instructions=(
            "Each option is a Leiden clustering of this matrix that has already been "
            "run, described by what it produced. Pick the one at cell-type "
            "granularity: each cluster should be one cell type, and no cell type "
            "should be split across clusters. Too coarse merges types that differ "
            "(CD8 T with NK); too fine splits one type into unstable fragments."),
        criteria=criteria, default="0.8")


def main():
    import scanpy as sc
    from sklearn.metrics import adjusted_rand_score

    settings = load_settings()
    pinned = {k: v for k, v in PINNED.items() if k != "cluster.resolution"}
    if DATASET != "pbmc3k":
        pinned.update({"integrate.integrate": True, "integrate.method": "harmony"})
    pipe = Pipeline(DATASET, overrides=pinned, plan=False)
    for step in ("load", "qc", "normalize", "features", "integrate"):
        pipe.execute(step)
    adata = pipe.adata
    label_key = pipe.state.obs["label_key"]
    rep = "X_emb" if "X_emb" in adata.obsm else "X_pca"
    sc.pp.neighbors(adata, use_rep=rep, n_neighbors=15)

    ev, parts = rung_evidence(adata, rep)
    mask = adata.obs[label_key].notna().to_numpy()
    truth = adata.obs[label_key].astype(str).to_numpy()
    for r in RUNGS:
        ev[r]["hindsight_ari"] = round(float(adjusted_rand_score(truth[mask], parts[r][mask])), 4)
        print(r, ev[r])

    # Downstream: the production naming route on each rung's partition.
    downstream = {}
    for r in RUNGS:
        a = adata.copy()
        a.obs["leiden"] = __import__("pandas").Categorical(parts[r])
        sc.tl.rank_genes_groups(a, "leiden", method="wilcoxon")
        labels, _, src = _annotate_jev(a, _top_markers(a, n=10), settings, "human PBMC")
        downstream[r] = score(a, labels, label_key)["cell_accuracy_vocab"]
        print(f"  rung {r}: downstream cell acc {downstream[r]}  ({src})")

    decider = JevDecider(settings)
    st = RunState("resolution", DATASET)
    st.observe(**{k: v for k, v in pipe.state.obs.items() if k not in ("label_key", "n_labelled_cells")})
    from scrnapipeline.steps.cluster import ClusterStep
    blind_q = ClusterStep().questions(adata, st)["resolution"]
    blind, seen = [], []
    for _ in range(REPEATS):
        d = decider.decide("cluster", {"resolution": blind_q}, st)["resolution"]
        blind.append({"value": d.value, "confidence": d.confidence, "source": d.source})
        d = decider.decide("cluster", {"resolution": seen_question(ev)}, st)["resolution"]
        seen.append({"value": float(d.value), "confidence": d.confidence, "source": d.source,
                     "probabilities": d.raw.get("probabilities")})

    stab = max(RUNGS, key=lambda r: (ev[r]["seed_stability_ari"], -r))
    hind = max(RUNGS, key=lambda r: ev[r]["hindsight_ari"])
    choosers = {"default": [0.8], "jev_blind": [b["value"] for b in blind],
                "jev_seen": [s["value"] for s in seen], "stability": [stab],
                "hindsight_ari": [hind],
                "hindsight_downstream": [max(RUNGS, key=lambda r: downstream[r])]}
    table = {}
    for name, picks in choosers.items():
        table[name] = {"picks": picks,
                       "ari": round(float(np.mean([ev[p]["hindsight_ari"] for p in picks])), 4),
                       "cell_acc": round(float(np.mean([downstream[p] for p in picks])), 4)}
        print(f"{name:22s} picks {picks}  ARI {table[name]['ari']}  cell acc {table[name]['cell_acc']}")
    OUT.write_text(json.dumps({"dataset": DATASET, "rungs": {str(k): v for k, v in ev.items()},
                               "downstream": {str(k): v for k, v in downstream.items()},
                               "jev_blind": blind, "jev_seen": seen, "table": table},
                              indent=2, default=str))


if __name__ == "__main__":
    main()
