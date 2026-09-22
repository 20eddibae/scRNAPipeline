"""Experiment 4: does the annotation difference survive into the results?

"Annotation is solved" is only true if annotation differences do not change what
you would report. This runs the same downstream analysis once per annotator over
the same cells, and once more on the ground-truth labels, then asks of each
annotator how far its results sit from the ones the truth would have produced:

  * how many cells it called correctly,
  * how far its cell-type composition is from the true one (total variation),
  * whether each cell type's top differential-expression list matches the list
    the true labels give (Jaccard of the top-N genes, one-vs-rest Wilcoxon).

Every delta is scored against ground truth, not against another annotator: a
difference that makes the results worse is not an argument for anything, and
two annotators disagreeing says only that they disagree.

The per-cluster annotators (Jev, Jev with the evidence loop, each Claude model,
the marker-overlap baseline) are read from `head_to_head.json` rather than
called again, so this costs nothing and scores exactly the calls that were
priced there. CellTypist is re-run here per cell, which is how it is used in
practice. The run refuses to start if the clustering it rebuilds differs from
the one head_to_head scored, because then the labels would land on other cells.

Everything is compared in the shared vocabulary. Ground truth is projected onto
it (both monocyte populations become "Monocyte"), and CellTypist's own labels
are translated by the same fixed keyword rules head_to_head uses, which never
read the answer key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import scanpy as sc

from head_to_head import PINNED, RESULTS, TRUTH_TO_VOCAB, celltypist_to_vocab
from scrnapipeline.pipeline import Pipeline
from scrnapipeline.steps.annotate import _annotate_celltypist

TOP_N = 25
MIN_CELLS = 10
OUT = Path("experiments/results/downstream_delta.json")


def de_genes(adata, labels: np.ndarray, group: str, key: str) -> list[str]:
    """Top differentially expressed genes for one cell type, one-vs-rest."""
    adata.obs[key] = labels
    counts = adata.obs[key].value_counts()
    if counts.get(group, 0) < MIN_CELLS or len(counts[counts >= 3]) < 2:
        return []
    sc.tl.rank_genes_groups(adata, key, groups=[group], method="wilcoxon",
                            key_added=f"de_{key}")
    return [str(g) for g in adata.uns[f"de_{key}"]["names"][group][:TOP_N]]


def main() -> int:
    h2h = json.loads(RESULTS.read_text())
    setup = h2h["_setup"]

    pipeline = Pipeline("pbmc3k", overrides=PINNED, plan=False)
    for step in ("load", "qc", "normalize", "features", "integrate", "cluster"):
        pipeline.execute(step)
    adata = pipeline.adata

    sizes = {c: int((adata.obs["leiden"] == c).sum())
             for c in adata.obs["leiden"].cat.categories}
    if sizes != setup["cluster_sizes"]:
        raise SystemExit(f"clustering differs from head_to_head's:\n  here  {sizes}\n"
                         f"  there {setup['cluster_sizes']}")

    label_key = pipeline.state.obs["label_key"]
    mask = adata.obs[label_key].notna().to_numpy()
    truth = np.array([TRUTH_TO_VOCAB[t] for t in
                      adata.obs[label_key].to_numpy()[mask].astype(str)])
    clusters = adata.obs["leiden"].to_numpy().astype(str)[mask]

    # ------------------------------------------------ per-cell labels, per arm
    arms: dict[str, np.ndarray] = {}
    notes: dict[str, str] = {}
    for name, result in h2h.items():
        if name.startswith("_") or not result.get("runs"):
            continue
        runs = result["runs"]
        # Score the first repeat; say so when the repeats disagreed.
        labels = runs[0]["labels"]
        n_distinct = len({json.dumps(r["labels"], sort_keys=True) for r in runs})
        if n_distinct > 1:
            notes[name] = f"{n_distinct} distinct labelings across {len(runs)} repeats; first scored"
        per_cell = np.array([labels.get(c) or "Unclear" for c in clusters])
        arms["celltypist_cluster" if name == "celltypist" else name] = per_cell

    print("running celltypist per cell", flush=True)
    _, ct_cells, source = _annotate_celltypist(adata)
    ct_cells = np.asarray(ct_cells).astype(str)[mask]
    arms["celltypist_per_cell"] = np.array([celltypist_to_vocab(x) for x in ct_cells])
    notes["celltypist_per_cell"] = (f"{source}; native -> vocab: " + ", ".join(
        f"{n}->{celltypist_to_vocab(n)}" for n in sorted(set(ct_cells))))

    # ------------------------------------------------ DE under the truth
    sub = adata[mask].copy()
    types = [t for t in sorted(set(truth)) if (truth == t).sum() >= MIN_CELLS]
    reference = {t: de_genes(sub, truth, t, "truth") for t in types}

    report: dict = {"n_cells": int(mask.sum()), "top_n": TOP_N, "types": types,
                    "truth_composition": {t: round(float((truth == t).mean()), 4)
                                          for t in sorted(set(truth))},
                    "notes": notes, "arms": {}}

    for name, labels in arms.items():
        print(f"scoring {name}", flush=True)
        all_types = sorted(set(truth) | set(labels))
        tv = 0.5 * sum(abs((labels == t).mean() - (truth == t).mean())
                       for t in all_types)
        per_type = {}
        for t in types:
            genes = de_genes(sub, labels, t, "arm")
            # A type the annotator never produced has no DE list at all: every
            # true marker for it is lost, which is Jaccard 0, not a skip.
            j = (len(set(genes) & set(reference[t])) /
                 len(set(genes) | set(reference[t]))) if genes else 0.0
            per_type[t] = {
                "jaccard": round(j, 3),
                "n_cells_called": int((labels == t).sum()),
                "missing_true_markers": sorted(set(reference[t]) - set(genes))[:10],
            }
        report["arms"][name] = {
            "cell_accuracy": round(float((labels == truth).mean()), 4),
            "composition_tv": round(float(tv), 4),
            "de_jaccard_mean": round(float(np.mean([v["jaccard"]
                                                    for v in per_type.values()])), 3),
            "de_jaccard_min": round(float(min(v["jaccard"]
                                              for v in per_type.values())), 3),
            "per_type": per_type,
            "composition": {t: round(float((labels == t).mean()), 4)
                            for t in all_types},
        }

    # The original A-vs-B question: of the cells CellTypist and the Jev route
    # disagree about, who is right?
    if "jev_loop" in arms:
        a, b = arms["celltypist_per_cell"], arms["jev_loop"]
        changed = a != b
        report["celltypist_vs_jev_loop"] = {
            "n_disagree": int(changed.sum()),
            "celltypist_right": int((a == truth)[changed].sum()),
            "jev_loop_right": int((b == truth)[changed].sum()),
            "neither": int(((a != truth) & (b != truth))[changed].sum()),
        }

    print(f"\n=== DOWNSTREAM vs TRUTH ({report['n_cells']} cells, top-{TOP_N} DE) ===")
    print(f"  {'arm':22s} {'cell acc':>9s} {'comp TV':>8s} {'DE J mean':>10s} {'DE J min':>9s}")
    for name, r in sorted(report["arms"].items(),
                          key=lambda kv: -kv[1]["de_jaccard_mean"]):
        print(f"  {name:22s} {r['cell_accuracy']:9.4f} {r['composition_tv']:8.4f}"
              f" {r['de_jaccard_mean']:10.3f} {r['de_jaccard_min']:9.3f}")
    print("\n  per type (DE Jaccard vs truth):")
    print("  " + " " * 22 + "".join(f"{t[:10]:>11s}" for t in types))
    for name, r in report["arms"].items():
        print(f"  {name:22s}" + "".join(f"{r['per_type'][t]['jaccard']:11.2f}"
                                        for t in types))
    if "celltypist_vs_jev_loop" in report:
        print(f"\n  celltypist vs jev_loop: {report['celltypist_vs_jev_loop']}")
    for k, v in notes.items():
        print(f"  note {k}: {v}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
