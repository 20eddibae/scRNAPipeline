"""Experiment 5: across many independent datasets, does the pipeline help?

Experiments 3 and 4 were one dataset, and the ranking rested on one cluster.
This repeats the annotation comparison on every usable study in the blood rows
of the scTab validation split -- each labelled by its own authors, none by
reading this pipeline's markers -- plus pbmc3k, and aggregates with the
DATASET as the unit.

PRE-REGISTERED before the first run (the thresholds are constants below and are
not to be moved after seeing results; anything tuned afterwards is labelled
exploratory in the write-up):

  Policies built from the pipeline's own signal, Jev's top-2 probability margin:
    cascade      Jev names every cluster; a cluster Jev is torn on (margin <
                 TAU, or it answered Unclear) is escalated to Claude Haiku 4.5.
    split        clusters Jev is torn on are sub-clustered (Leiden within the
                 cluster) before naming; subclusters are named by the cascade.
  Controls:
    jev, claude-haiku-4-5, claude-sonnet-5, overlap (no model),
    celltypist per cell, oracle (majority true type per cluster),
    split_all    every cluster sub-clustered the same way. If `split` only
                 matches this, Jev's flag is not what helps -- resolution is.

  H1  cascade accuracy is non-inferior to Claude Sonnet 5 (mean paired
      difference > -0.01) at <= 25% of its cost.
  H2  cascade beats jev alone (paired Wilcoxon over datasets, p < 0.05).
  H3  cascade beats the free baseline, overlap (same test).
  H4  split beats cascade on cell accuracy AND on DE Jaccard, and is not
      beaten by split_all at lower cost.

Accuracy is cell accuracy in the shared 8-option vocabulary, scored only on
cells whose true label maps into it; the rest (gamma-delta T, MAIT, "mature
alpha-beta T cell", erythrocytes ...) stay in the clustering but are not scored,
because no option could be right for them. The truth mapping is keyword rules
written from label names, the same kind as CellTypist's translation.

Preprocessing is the scanpy convention, identical for every dataset, because
tuning is not what is being tested here.
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

import scanpy as sc

from head_to_head import (TRUTH_TO_VOCAB, arm_claude, arm_jev, arm_overlap,
                          celltypist_to_vocab)
from scrnapipeline.config import load_settings
from scrnapipeline.steps.annotate import _annotate_celltypist, _top_markers

TAU = 0.5                 # pre-registered escalation margin
ESCALATE_TO = "claude-haiku-4-5"
MIN_STUDY_CELLS = 150
MIN_SCORABLE = 100
MIN_CLUSTER = 5           # smaller clusters get no markers; every arm says Unclear
MIN_SPLIT = 20
SPLIT_RESOLUTION = 0.5
TOP_N_DE = 25
MIN_DE_CELLS = 10

ROOT = Path(os.environ.get("KRINO_ROOT", HERE.parent))
OUT = Path(os.environ.get("AGG_OUT", ROOT / "experiments/results/aggregate.json"))

# CELLxGENE label -> vocabulary. Exclusions first: these are real types the
# 8-option list cannot express, so they are unscored rather than forced.
EXCLUDE = ("gamma-delta", "mucosal invariant", "nk t", "thymocyte", "double negative",
           "plasma", "erythro", "progenitor", "stem cell")
VOCAB_RULES = (
    (("cd4", "helper", "regulatory t", "t-helper", "t follicular"), "CD4 T cell"),
    (("cd8",), "CD8 T cell"),
    (("natural killer",), "NK cell"),
    (("b cell",), "B cell"),
    (("monocyte", "macrophage"), "Monocyte"),
    (("dendritic",), "Dendritic cell"),
    (("platelet", "megakaryocyte"), "Megakaryocyte/Platelet"),
)


def cxg_to_vocab(label: str) -> str | None:
    low = label.lower()
    if any(k in low for k in EXCLUDE):
        return None
    for keys, target in VOCAB_RULES:
        if any(k in low for k in keys):
            return target
    return None       # e.g. 'T cell', 'mature alpha-beta T cell', 'neutrophil'


# ---------------------------------------------------------------- datasets

def load_datasets():
    import anndata as ad
    import pandas as pd

    out = []
    raw = ad.read_h5ad(ROOT / "data/pbmc3k_raw.h5ad")
    proc = ad.read_h5ad(ROOT / "data/pbmc3k_processed.h5ad")
    raw.var_names_make_unique()
    louvain = proc.obs["louvain"].astype(str).reindex(raw.obs_names)
    raw.obs["truth"] = [TRUTH_TO_VOCAB.get(t) if isinstance(t, str) else None
                        for t in louvain]
    out.append(("pbmc3k", "human PBMC", raw))

    backed = ad.read_h5ad(ROOT / "sctab_raw_1pct_VAL.h5ad", backed="r")
    obs = backed.obs
    features = pd.read_csv(ROOT / "src/scrnapipeline/resources/sctab_features.csv")
    blood = obs["tissue_general"].astype(str) == "blood"
    counts = obs.loc[blood, "dataset_id"].astype(str).value_counts()
    for study in counts[counts >= MIN_STUDY_CELLS].index:
        rows = (blood & (obs["dataset_id"].astype(str) == study)).to_numpy()
        adata = backed[rows].to_memory()
        order = adata.var_names.astype(int).to_numpy()
        adata.var_names = features["feature_name"].astype(str).to_numpy()[order]
        adata.var_names_make_unique()
        # Some studies repeat cell ids; CellTypist keys its output by them and
        # returns extra rows for the duplicates.
        adata.obs_names_make_unique()
        adata.obs["truth"] = [cxg_to_vocab(str(t)) for t in adata.obs["cell_type"]]
        out.append((f"sctab:{study[:8]}", "human blood (PBMC)", adata))
    backed.file.close()
    return out


def preprocess(adata):
    sc.pp.filter_cells(adata, min_genes=200)
    sc.pp.filter_genes(adata, min_cells=3)
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    adata.raw = adata
    sc.pp.highly_variable_genes(adata, n_top_genes=min(2000, adata.n_vars - 1))
    adata = adata[:, adata.var["highly_variable"]].copy()
    sc.pp.scale(adata, max_value=10)
    sc.tl.pca(adata, n_comps=min(50, adata.n_obs - 1, adata.n_vars - 1))
    sc.pp.neighbors(adata, n_neighbors=15)
    sc.tl.leiden(adata, resolution=1.0, random_state=0, key_added="leiden")
    return adata


def markers_for(adata, key: str, groups: list[str]) -> dict[str, list[str]]:
    sc.tl.rank_genes_groups(adata, key, groups=groups, reference="rest",
                            method="wilcoxon")
    return _top_markers(adata, n=10)


# ---------------------------------------------------------------- calls

class Ledger:
    """Per-cluster calls and their price, so policies can be costed exactly."""

    def __init__(self, settings, context):
        self.settings, self.context = settings, context

    def one(self, arm: str, cluster: str, genes: list[str], size: int):
        markers, sizes = {cluster: genes}, {cluster: size}
        if arm == "jev":
            out, m = arm_jev(markers, sizes, self.context, self.settings)
        elif arm == "overlap":
            out, m = arm_overlap(markers, sizes, self.context, self.settings)
        else:
            out, m = arm_claude(markers, sizes, self.context, self.settings, model=arm)
        call = out[cluster]
        call["cost_usd"], call["seconds"] = m["cost_usd"], m["seconds"]
        return call


def margin(call) -> float:
    probs = sorted((call.get("probabilities") or {}).values(), reverse=True)
    if len(probs) < 2:
        return 0.0
    return float(probs[0] - probs[1])


def torn(call) -> bool:
    return call["label"] in (None, "Unclear") or margin(call) < TAU


def cascade(ledger, cluster, genes, size, jev_call, claude_call=None):
    """Returns (label, cost, seconds, escalated). Pass `claude_call` when the
    escalation target has already been asked, so it is not paid for twice."""
    if not torn(jev_call):
        return jev_call["label"], jev_call["cost_usd"], jev_call["seconds"], False
    c = claude_call or ledger.one(ESCALATE_TO, cluster, genes, size)
    return (c["label"] or "Unclear", jev_call["cost_usd"] + c["cost_usd"],
            jev_call["seconds"] + c["seconds"], True)


# ---------------------------------------------------------------- scoring

def de_list(adata, labels, group, key):
    adata.obs[key] = labels
    counts = adata.obs[key].value_counts()
    if counts.get(group, 0) < MIN_DE_CELLS or len(counts[counts >= 3]) < 2:
        return []
    sc.tl.rank_genes_groups(adata, key, groups=[group], reference="rest",
                            method="wilcoxon", key_added=f"de_{key}")
    return [str(g) for g in adata.uns[f"de_{key}"]["names"][group][:TOP_N_DE]]


def score_arm(sub, labels, truth, types, reference):
    all_types = sorted(set(truth) | set(labels))
    tv = 0.5 * sum(abs((labels == t).mean() - (truth == t).mean()) for t in all_types)
    jac = {}
    for t in types:
        genes = de_list(sub, labels, t, "arm")
        jac[t] = (len(set(genes) & set(reference[t])) /
                  len(set(genes) | set(reference[t]))) if genes else 0.0
    return {"cell_accuracy": round(float((labels == truth).mean()), 4),
            "composition_tv": round(float(tv), 4),
            "de_jaccard_mean": round(float(np.mean(list(jac.values()))), 4)
            if jac else None,
            "de_jaccard": {t: round(v, 3) for t, v in jac.items()}}


# ---------------------------------------------------------------- one dataset

def run_dataset(name, context, adata, settings):
    t0 = time.time()
    adata = preprocess(adata)
    truth_all = adata.obs["truth"].to_numpy()
    scorable = np.array([t is not None and t == t for t in truth_all])
    if scorable.sum() < MIN_SCORABLE or len(set(truth_all[scorable])) < 2:
        return {"skipped": f"{int(scorable.sum())} scorable cells"}

    ledger = Ledger(settings, context)
    clusters = adata.obs["leiden"].astype(str).to_numpy()
    sizes = {c: int((clusters == c).sum()) for c in np.unique(clusters)}
    named = [c for c, n in sizes.items() if n >= MIN_CLUSTER]
    markers = markers_for(adata, "leiden", named)

    per_cluster, cost, secs = {}, {}, {}
    arms = ("jev", "overlap", "claude-haiku-4-5", "claude-sonnet-5")
    for a in arms:
        cost[a], secs[a] = 0.0, 0.0
    for c in named:
        per_cluster[c] = {}
        for a in arms:
            call = ledger.one(a, c, markers[c], sizes[c])
            per_cluster[c][a] = call
            cost[a] += call["cost_usd"]
            secs[a] += call["seconds"]

    labels: dict[str, dict[str, str]] = {a: {} for a in arms}
    for c in sizes:
        for a in arms:
            labels[a][c] = (per_cluster[c][a]["label"] or "Unclear") if c in per_cluster \
                else "Unclear"

    # cascade
    labels["cascade"], cost["cascade"], secs["cascade"] = {}, 0.0, 0.0
    escalated = []
    for c in sizes:
        if c not in per_cluster:
            labels["cascade"][c] = "Unclear"
            continue
        # The escalation target was already asked as a control arm: reuse that
        # call, so the policy is scored on the same draw the control saw.
        lab, cst, s, esc = cascade(ledger, c, markers[c], sizes[c], per_cluster[c]["jev"],
                                   per_cluster[c][ESCALATE_TO])
        if esc:
            escalated.append(c)
        labels["cascade"][c] = lab
        cost["cascade"] += cst
        secs["cascade"] += s

    # oracle
    labels["oracle"] = {}
    for c in sizes:
        t = truth_all[(clusters == c) & scorable]
        if len(t):
            v, n = np.unique(t.astype(str), return_counts=True)
            labels["oracle"][c] = str(v[n.argmax()])
        else:
            labels["oracle"][c] = "Unclear"

    per_cell = {a: np.array([labels[a][c] for c in clusters]) for a in labels}

    # split / split_all: sub-cluster, re-rank, name each subcluster by cascade
    def split_policy(targets, tag):
        key = f"leiden_{tag}"
        targets = [c for c in targets if sizes[c] >= MIN_SPLIT]
        # One cluster at a time. scanpy's restrict_to with several categories
        # runs ONE Leiden over their union -- it can merge across the parents
        # -- and prefixes every label with all of their ids.
        adata.obs[key] = adata.obs["leiden"].astype(str).astype("category")
        for c in targets:
            sc.tl.leiden(adata, resolution=SPLIT_RESOLUTION, random_state=0,
                         restrict_to=(key, [c]), key_added=key)
        sub_clusters = adata.obs[key].astype(str).to_numpy()
        new = sorted({s for s in np.unique(sub_clusters)
                      if "," in s and s.split(",")[0] in targets})
        sub_sizes = {s: int((sub_clusters == s).sum()) for s in new}
        big = [s for s in new if sub_sizes[s] >= MIN_CLUSTER]
        sub_markers = markers_for(adata, key, big) if big else {}
        lab = dict(labels["cascade"])
        c_cost, c_secs = cost["cascade"], secs["cascade"]
        # remove the cost of naming the parents that were split
        for c in targets:
            jc = per_cluster[c]["jev"]
            c_cost -= jc["cost_usd"] + (per_cluster[c]["claude-haiku-4-5"]["cost_usd"]
                                        if c in escalated else 0.0)
        for s in new:
            if s not in sub_markers:
                lab[s] = "Unclear"
                continue
            j = ledger.one("jev", s, sub_markers[s], sub_sizes[s])
            l, cst, sec, _ = cascade(ledger, s, sub_markers[s], sub_sizes[s], j)
            lab[s] = l or "Unclear"
            c_cost += cst
            c_secs += sec
        cells = np.array([lab.get(s, lab.get(s.split(",")[0], "Unclear"))
                          for s in sub_clusters])
        return cells, c_cost, c_secs, len(targets), len(new)

    flagged = list(escalated)
    per_cell["split"], cost["split"], secs["split"], n_split, n_sub = \
        split_policy(flagged, "split")
    per_cell["split_all"], cost["split_all"], secs["split_all"], n_split_all, n_sub_all = \
        split_policy(list(per_cluster), "split_all")

    # celltypist per cell
    tc = time.time()
    try:
        _, ct, _ = _annotate_celltypist(adata)
        per_cell["celltypist"] = np.array([celltypist_to_vocab(x) for x in ct])
        cost["celltypist"], secs["celltypist"] = 0.0, round(time.time() - tc, 2)
    except Exception as exc:
        print(f"  celltypist failed on {name}: {exc}", flush=True)

    for a, v in per_cell.items():
        if len(v) != adata.n_obs:
            raise RuntimeError(f"{a} returned {len(v)} labels for {adata.n_obs} cells")

    # score on scorable cells
    sub = adata[scorable].copy()
    truth = truth_all[scorable].astype(str)
    types = [t for t in sorted(set(truth)) if (truth == t).sum() >= MIN_DE_CELLS]
    reference = {t: de_list(sub, truth, t, "truth") for t in types}
    scores = {a: {**score_arm(sub, v[scorable].astype(str), truth, types, reference),
                  "cost_usd": round(cost.get(a, 0.0), 6),
                  "seconds": round(secs.get(a, 0.0), 2)}
              for a, v in per_cell.items()}

    return {
        "context": context, "n_cells": int(adata.n_obs),
        "n_scorable": int(scorable.sum()), "n_clusters": len(sizes),
        "n_named": len(named), "truth_types": types,
        "escalated": escalated, "n_split": n_split, "n_subclusters": n_sub,
        "n_split_all": n_split_all, "n_subclusters_all": n_sub_all,
        "wall_s": round(time.time() - t0, 1),
        "calls": {c: {a: {k: v for k, v in call.items() if k != "probabilities"}
                      | {"margin": round(margin(call), 3)} if a == "jev" else
                      {k: v for k, v in call.items() if k != "probabilities"}
                      for a, call in d.items()} for c, d in per_cluster.items()},
        "truth_by_cluster": labels["oracle"],
        "scores": scores,
    }


# ---------------------------------------------------------------- main

def main() -> int:
    settings = load_settings()
    report = json.loads(OUT.read_text()) if OUT.exists() else {}
    report["_preregistered"] = {"tau": TAU, "escalate_to": ESCALATE_TO,
                                "split_resolution": SPLIT_RESOLUTION,
                                "hypotheses": ["H1", "H2", "H3", "H4"]}
    only = os.environ.get("AGG_ONLY")
    for name, context, adata in load_datasets():
        if only and name not in only.split(","):
            continue
        if name in report and "scores" in report[name]:
            print(f"{name}: cached", flush=True)
            continue
        print(f"{name}: {adata.n_obs} cells", flush=True)
        try:
            report[name] = run_dataset(name, context, adata, settings)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            report[name] = {"failed": f"{type(exc).__name__}: {exc}"}
        r = report[name]
        if "scores" in r:
            line = "  ".join(f"{a}={s['cell_accuracy']:.3f}"
                             for a, s in r["scores"].items())
            print(f"  {line}", flush=True)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2, default=str))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
