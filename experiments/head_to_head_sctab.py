"""Experiment 3b: the same head-to-head, on labels nobody derived from markers.

pbmc3k's ground truth was itself made by reading each cluster's marker genes, so
three of the four arms there were marker->type mappings scored against a
marker->type mapping, and all three tied at 0.848. That tie could be a property
of the task or of pbmc3k. This asks which.

The substrate is the blood rows of the scTab validation split: raw counts from
25 independent CELLxGENE studies, labelled by those studies' authors with Cell
Ontology terms. No label here was made by looking at this run's clusters. The
canonical panel the overlap arm uses was written before these cells were ever
loaded, so here -- unlike on pbmc3k -- it is a clean free baseline.

Every arm is imported unchanged from `head_to_head.py`. What differs is only the
dataset, and the scoring: the truth is ~68 fine Cell Ontology types against a
seven-way vocabulary, so it is collapsed to that vocabulary first, by keyword
rules written from the ontology NAMES alone. Types the vocabulary cannot name
without guessing (gamma-delta T, MAIT, NK T, a bare "T cell", erythrocytes,
neutrophils...) are excluded from scoring, and how many cells that drops is
reported rather than hidden.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

# The arms retry a rate-limited model with exponential backoff; on a model the
# gateway is refusing outright that is minutes of sleeping per cluster.
os.environ.setdefault("H2H_RETRIES", "2")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import head_to_head as h2h  # noqa: E402

from scrnapipeline.config import load_settings  # noqa: E402
from scrnapipeline.pipeline import Pipeline  # noqa: E402
from scrnapipeline.steps.annotate import _top_markers  # noqa: E402

DATASET = "sctab_val_blood"
CONTEXT = "human blood (PBMC and whole blood)"
RESULTS = Path("experiments/results/head_to_head_sctab.json")
# pbmc3k's pins, plus integration: this data has 25 studies in it, pbmc3k had one.
PINNED = {**h2h.PINNED, "integrate.integrate": True, "integrate.method": "harmony"}

# Cell Ontology name -> vocabulary. First match wins. EXCLUDE comes first so an
# ambiguous name never falls through to a lineage it only partly matches.
EXCLUDE = ("double negative", "double-positive", "gamma-delta", "mucosal invariant",
           "nk t cell", "progenitor", "stem cell")
CL_RULES = (
    (("natural killer",), "NK cell"),
    (("cd8",), "CD8 T cell"),
    (("cd4", "regulatory t", "t-helper", "helper t", "t follicular helper"),
     "CD4 T cell"),
    # Before B cell: "plasmacytoid dendritic cell" contains "plasma".
    (("dendritic",), "Dendritic cell"),
    (("b cell", "plasma", "plasmablast", "pro-b", "precursor b"), "B cell"),
    (("monocyte", "macrophage"), "Monocyte"),
    (("platelet", "megakaryocyte"), "Megakaryocyte/Platelet"),
)


def cl_to_vocab(name: str) -> str | None:
    low = name.lower()
    if any(k in low for k in EXCLUDE):
        return None
    for keys, target in CL_RULES:
        if any(k in low for k in keys):
            return target
    return None


def model_available(settings, model: str) -> bool:
    """One tiny call. The gateway 429s whole models for stretches at a time."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key,
                                 base_url=settings.anthropic_base_url)
    try:
        client.messages.create(model=model, max_tokens=4,
                               messages=[{"role": "user", "content": "ok"}])
        return True
    except anthropic.RateLimitError:
        return False


def main() -> int:
    settings = load_settings()
    pipeline = Pipeline(DATASET, overrides=PINNED, plan=False, context=CONTEXT)
    for step in ("load", "qc", "normalize", "features", "integrate", "cluster"):
        pipeline.execute(step)
    integrate = pipeline.state.steps[-2].summary
    assert integrate.get("applied") == "harmony", f"integration did not run: {integrate}"

    adata = pipeline.adata
    import scanpy as sc
    sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")
    markers = _top_markers(adata, n=10)
    sizes = {c: int((adata.obs["leiden"] == c).sum()) for c in markers}

    fine = adata.obs[pipeline.state.obs["label_key"]].astype(str).to_numpy()
    coarse = np.array([cl_to_vocab(t) for t in fine], dtype=object)
    adata.obs["truth_coarse"] = coarse
    mask = np.array([c is not None for c in coarse])
    truth = coarse[mask].astype(str)
    clusters = adata.obs["leiden"].to_numpy().astype(str)[mask]
    excluded = {t: int(n) for t, n in zip(*np.unique(fine[~mask], return_counts=True))}

    # The truth is already in the vocabulary, so the scorer's map is identity.
    h2h.TRUTH_TO_VOCAB = {v: v for v in h2h.PBMC_VOCABULARY}

    print(f"cells {adata.n_obs}, scored {int(mask.sum())}, excluded "
          f"{int((~mask).sum())} ({sorted(excluded, key=excluded.get, reverse=True)[:5]}...)",
          flush=True)
    print(f"clusters {len(markers)}; coarse truth "
          f"{dict(zip(*np.unique(truth, return_counts=True)))}", flush=True)

    arms = {"oracle": (h2h.arm_oracle, 1), "overlap": (h2h.arm_overlap, 1),
            "celltypist": (h2h.arm_celltypist, 1), "jev": (h2h.arm_jev, h2h.REPEATS),
            "jev_loop": (h2h.arm_jev_loop, h2h.REPEATS)}
    for model in h2h.CLAUDE_ARMS:
        if model_available(settings, model):
            arms[model] = (h2h.arm_claude, h2h.REPEATS)
        else:
            print(f"  {model}: gateway refusing (429) -- arm skipped, not scored",
                  flush=True)
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    if only:
        arms = {k: v for k, v in arms.items() if k in only}

    report = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    report["_setup"] = {
        "dataset": DATASET, "context": CONTEXT, "pinned": PINNED,
        "n_cells": int(adata.n_obs), "n_scored": int(mask.sum()),
        "n_excluded": int((~mask).sum()), "excluded_types": excluded,
        "integration": integrate, "cluster_sizes": sizes, "markers": markers,
        "repeats": h2h.REPEATS,
    }
    for name, (fn, repeats) in arms.items():
        runs = []
        for i in range(repeats):
            kwargs = {}
            if name in ("celltypist", "jev_loop", "oracle"):
                kwargs["adata"] = adata
            if name == "oracle":
                kwargs["label_key"] = "truth_coarse"
            if name in h2h.CLAUDE_ARMS:
                kwargs["model"] = name
            try:
                calls, meters = fn(markers, sizes, CONTEXT, settings, **kwargs)
            except Exception as exc:
                print(f"  {name} repeat {i} FAILED: {type(exc).__name__}: {exc}",
                      flush=True)
                continue
            runs.append({**h2h.score(calls, clusters, truth), **meters})
            print(f"  {name:17s} run {i}  cell_acc={runs[-1]['cell_accuracy_vocab']:.4f}"
                  f"  matched={runs[-1]['matched_accuracy']:.4f}"
                  f"  clusters={runs[-1]['clusters_correct']}/{runs[-1]['n_clusters']}"
                  f"  ${runs[-1]['cost_usd']:.6f}  {runs[-1]['seconds']:.1f}s", flush=True)
        if runs:
            cell = [r["cell_accuracy_vocab"] for r in runs]
            report[name] = {
                "runs": runs,
                "cell_accuracy_mean": round(float(np.mean(cell)), 4),
                "cell_accuracy_min": round(float(np.min(cell)), 4),
                "cell_accuracy_max": round(float(np.max(cell)), 4),
                "matched_accuracy_mean": round(float(np.mean(
                    [r["matched_accuracy"] for r in runs])), 4),
                "clusters_correct_mean": round(float(np.mean(
                    [r["clusters_correct"] for r in runs])), 2),
                "cost_usd_mean": round(float(np.mean([r["cost_usd"] for r in runs])), 6),
                "seconds_mean": round(float(np.mean([r["seconds"] for r in runs])), 2),
            }
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text(json.dumps(report, indent=2, default=str))

    print(f"\n=== HEAD TO HEAD on {DATASET} ({len(markers)} clusters, "
          f"{int(mask.sum())} scored cells) ===")
    print(f"  {'arm':17s} {'cell acc':>18s} {'matched':>8s} {'clusters':>9s} "
          f"{'cost $':>10s} {'wall s':>7s}")
    for name, r in report.items():
        if name.startswith("_"):
            continue
        span = (f"{r['cell_accuracy_mean']:.4f}"
                if r["cell_accuracy_min"] == r["cell_accuracy_max"] else
                f"{r['cell_accuracy_mean']:.3f} [{r['cell_accuracy_min']:.3f}-"
                f"{r['cell_accuracy_max']:.3f}]")
        print(f"  {name:17s} {span:>18s} {r['matched_accuracy_mean']:8.4f} "
              f"{r['clusters_correct_mean']:9.1f} {r['cost_usd_mean']:10.6f} "
              f"{r['seconds_mean']:7.1f}")
    print(f"\nwrote {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
