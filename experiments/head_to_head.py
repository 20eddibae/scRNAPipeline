"""Experiment 3: what does the decision model actually buy?

Four annotators on one identical task -- name each Leiden cluster of pbmc3k,
choosing from one fixed vocabulary, given one fixed list of marker genes.
Accuracy, cost and latency measured the same way for each.

The arms are chosen so the comparison isolates something:

  overlap     no model at all. Set intersection between the cluster's markers
              and a canonical panel. This is the free baseline, and the only
              honest way to ask whether any model is earning its place.
  celltypist  a purpose-built supervised classifier. No language model.
  jev         a calibrated decision model over a closed option set.
  jev_loop    the production route: Jev behind the confidence floor, with the
              abstention-triggered evidence loop that fetches a lineage panel.
  claude-*    a general language model given THE SAME closed option set and the
              same markers, so the comparison is decision-model-vs-LLM rather
              than two different tasks. One arm per model, each pinned: a
              rate-limited call waits and retries the same model instead of
              quietly answering with a different one.
  oracle      not an annotator. Each cluster's majority true type, written in
              the vocabulary. The ceiling any per-cluster namer can reach with
              this clustering and this closed list, e.g. one "Monocyte" option
              for two monocyte populations.

Claude is deliberately not allowed to free-form. Letting it answer open-ended
would make it look better or worse for reasons that have nothing to do with the
question being asked here.

Jev is non-deterministic -- the same markers returned 0.70 and 0.64 on two runs
earlier today -- so the model arms are repeated and the spread reported.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scrnapipeline.config import load_settings
from scrnapipeline.pipeline import Pipeline
from scrnapipeline.steps.annotate import PBMC_VOCABULARY, _top_markers
from scrnapipeline.steps.evaluate import _matched_accuracy

REPEATS = 3
RETRIES = int(__import__("os").environ.get("H2H_RETRIES", "5"))
PINNED = {"qc.stringency": "standard", "qc.flag_doublets": False,
          "normalize.method": "log1p_cpm", "features.n_hvg": 2000,
          "features.n_pcs": 50, "cluster.resolution": 1.0}

# Published list prices, $ per million tokens.
PRICES = {
    "jev":               {"in": 0.042, "out": 0.0},   # output is not billed
    "claude":            {"in": 5.00,  "out": 25.00},
    "claude-opus-5":     {"in": 5.00,  "out": 25.00},
    "claude-sonnet-5":   {"in": 2.00,  "out": 10.00},
    "claude-haiku-4-5":  {"in": 1.00,  "out": 5.00},
}

CLAUDE_ARMS = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5")
RESULTS = Path("experiments/results/head_to_head.json")

# Majority true type -> vocabulary, for the oracle arm only.
TRUTH_TO_VOCAB = {
    "CD4 T cells": "CD4 T cell", "CD8 T cells": "CD8 T cell", "NK cells": "NK cell",
    "B cells": "B cell", "CD14+ Monocytes": "Monocyte",
    "FCGR3A+ Monocytes": "Monocyte", "Dendritic cells": "Dendritic cell",
    "Megakaryocytes": "Megakaryocyte/Platelet",
}

# CellTypist answers in its own ontology. Translate by keyword, written from its
# label names alone -- never from the ground truth -- so the translation cannot
# flatter it. First match wins; order puts the more specific rules first.
CELLTYPIST_RULES = (
    (("helper", "cd4", "treg", "regulatory t"), "CD4 T cell"),
    (("cd8", "cytotoxic t", "mait"), "CD8 T cell"),
    (("nk",), "NK cell"),
    (("b cell", "plasma"), "B cell"),
    (("monocyte", "macrophage"), "Monocyte"),
    (("dc", "dendritic"), "Dendritic cell"),
    (("megakaryocyte", "platelet"), "Megakaryocyte/Platelet"),
)


def celltypist_to_vocab(label: str) -> str:
    low = label.lower()
    for keys, target in CELLTYPIST_RULES:
        if any(k in low for k in keys):
            return target
    return "Unclear"


# The free baseline's entire knowledge base.
CANONICAL = {
    "CD4 T cell": {"IL7R", "CCR7", "LTB", "CD3D", "CD3E", "LDHB", "IL32", "CD4"},
    "CD8 T cell": {"CD8A", "CD8B", "CCL5", "GZMK", "CD3D", "CD3E"},
    "NK cell": {"GNLY", "NKG7", "KLRD1", "GZMB", "PRF1", "FGFBP2"},
    "B cell": {"CD79A", "CD79B", "MS4A1", "CD74", "HLA-DRA", "CD19"},
    "Monocyte": {"LYZ", "S100A8", "S100A9", "CD14", "FCGR3A", "TYROBP", "LST1",
                 "FCER1G", "AIF1"},
    "Dendritic cell": {"FCER1A", "CST3", "HLA-DPA1", "HLA-DPB1", "HLA-DRB1"},
    "Megakaryocyte/Platelet": {"PPBP", "PF4", "NRGN"},
}

PROMPT = """You are naming a cell cluster from a single-cell RNA-seq run of {context}.

Cluster {cluster} contains {size} cells. Its most enriched genes, ranked by
differential expression against all other clusters, are:

{markers}

Choose exactly one label from this closed list:
{options}

Reply with JSON only: {{"label": "<one of the options>", "confidence": <0..1>}}"""


# ---------------------------------------------------------------- arms

def arm_overlap(markers, sizes, context, settings):
    """No model. Largest set intersection with the canonical panel wins."""
    out, started = {}, time.time()
    for cluster, genes in markers.items():
        top = set(g.upper() for g in genes)
        scores = {label: len(top & {g.upper() for g in panel})
                  for label, panel in CANONICAL.items()}
        best = max(scores, key=scores.get)
        out[cluster] = {"label": best if scores[best] else "Unclear",
                        "confidence": None}
    return out, {"seconds": round(time.time() - started, 3),
                 "in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0}


def arm_jev(markers, sizes, context, settings):
    from typesafe_sdk import Choice, TypeSafeClient

    out, in_tok, out_tok, latencies = {}, 0, 0, []
    with TypeSafeClient(api_key=settings.typesafe_api_key,
                        base_url=settings.typesafe_base_url) as client:
        for cluster, genes in markers.items():
            t0 = time.time()
            response = client.system_one(
                state={"tissue": context, "cluster": cluster,
                       "n_cells_in_cluster": sizes[cluster], "top_markers": genes},
                questions={"cell_type": Choice(
                    instructions=(
                        f"These are the genes most enriched in cluster {cluster} "
                        f"({sizes[cluster]} cells) of a {context} sample. Which "
                        "cell type do they indicate?"),
                    criteria=PBMC_VOCABULARY)},
            )
            latencies.append(time.time() - t0)
            answer = response.answers["cell_type"]
            out[cluster] = {"label": getattr(answer, "choice", None),
                            "confidence": getattr(answer, "confidence", None),
                            "probabilities": dict(getattr(answer, "probabilities",
                                                          None) or {})}
            in_tok += response.usage.input_tokens
            out_tok += response.usage.output_tokens
    return out, _cost("jev", in_tok, out_tok, latencies)


def arm_claude(markers, sizes, context, settings, model="claude-sonnet-5"):
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key,
                                 base_url=settings.anthropic_base_url)
    options = "\n".join(f"- {k}: {v}" for k, v in PBMC_VOCABULARY.items())
    out, in_tok, out_tok, latencies = {}, 0, 0, []
    # Haiku 4.5 predates adaptive thinking; the Claude 5 models get it.
    extra = {} if "haiku" in model else {"thinking": {"type": "adaptive"}}

    for cluster, genes in markers.items():
        prompt = PROMPT.format(context=context, cluster=cluster,
                               size=sizes[cluster], markers=", ".join(genes),
                               options=options)
        message = None
        for attempt in range(RETRIES):
            t0 = time.time()
            try:
                message = client.messages.create(
                    model=model, max_tokens=2000,
                    messages=[{"role": "user", "content": prompt}], **extra)
            except anthropic.RateLimitError:
                time.sleep(2 ** attempt)
                continue
            latencies.append(time.time() - t0)
            break
        if message is None:
            raise RuntimeError(f"{model} stayed rate-limited after {RETRIES} attempts")
        text = "".join(b.text for b in message.content if b.type == "text")
        label, confidence = _parse(text)
        out[cluster] = {"label": label, "confidence": confidence}
        in_tok += message.usage.input_tokens
        out_tok += message.usage.output_tokens
    return out, _cost("claude", in_tok, out_tok, latencies, model=model)


def arm_jev_loop(markers, sizes, context, settings, adata=None):
    """The route the pipeline actually ships, metered at the SDK boundary."""
    import typesafe_sdk

    from scrnapipeline.steps.annotate import _annotate_jev

    meter = {"in": 0, "out": 0, "lat": []}
    original = typesafe_sdk.TypeSafeClient.system_one

    def metered(self, *args, **kwargs):
        t0 = time.time()
        response = original(self, *args, **kwargs)
        meter["lat"].append(time.time() - t0)
        usage = getattr(response, "usage", None)
        meter["in"] += getattr(usage, "input_tokens", 0) or 0
        meter["out"] += getattr(usage, "output_tokens", 0) or 0
        return response

    typesafe_sdk.TypeSafeClient.system_one = metered
    try:
        labels, _, _ = _annotate_jev(adata, markers, settings, context)
    finally:
        typesafe_sdk.TypeSafeClient.system_one = original
    info = adata.uns["jev_annotation"]
    out = {c: {"label": labels[c], "confidence": info[c]["confidence"],
               "attempts": info[c]["attempts"]} for c in markers}
    meters = _cost("jev", meter["in"], meter["out"], meter["lat"])
    meters["n_calls"] = len(meter["lat"])
    return out, meters


def arm_oracle(markers, sizes, context, settings, adata=None, label_key=None):
    out = {}
    for cluster in markers:
        in_cluster = adata.obs["leiden"].to_numpy().astype(str) == cluster
        values = adata.obs[label_key].to_numpy()[in_cluster]
        values = values[values == values].astype(str)   # drop NaN
        uniq, counts = np.unique(values, return_counts=True)
        out[cluster] = {"label": TRUTH_TO_VOCAB.get(str(uniq[counts.argmax()]), "Unclear"),
                        "confidence": None}
    return out, {"seconds": 0.0, "in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0}


def arm_celltypist(markers, sizes, context, settings, adata=None):
    import celltypist
    from celltypist import models

    t0 = time.time()
    models.download_models(model="Immune_All_Low.pkl", force_update=False)
    result = celltypist.annotate(adata.raw.to_adata(), model="Immune_All_Low.pkl",
                                 majority_voting=True)
    seconds = time.time() - t0
    labels = result.predicted_labels
    column = "majority_voting" if "majority_voting" in labels else "predicted_labels"
    per_cell = labels[column].astype(str).to_numpy()
    # Collapse to a per-cluster call so every arm is scored identically.
    out = {}
    for cluster in markers:
        in_cluster = (adata.obs["leiden"].to_numpy().astype(str) == cluster)
        values, counts = np.unique(per_cell[in_cluster], return_counts=True)
        native = str(values[counts.argmax()])
        out[cluster] = {"label": celltypist_to_vocab(native), "native": native,
                        "confidence": None}
    return out, {"seconds": round(seconds, 3), "in_tokens": 0, "out_tokens": 0,
                 "cost_usd": 0.0}


def _cost(arm, in_tok, out_tok, latencies, model=None):
    price = PRICES.get(model) or PRICES[arm]
    return {
        "seconds": round(sum(latencies), 3),
        "median_latency_s": round(float(np.median(latencies)), 3),
        "in_tokens": in_tok,
        "out_tokens": out_tok,
        "cost_usd": round(in_tok / 1e6 * price["in"] + out_tok / 1e6 * price["out"], 6),
    }


def _parse(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        payload = json.loads(text[text.index("{"):text.rindex("}") + 1])
        return str(payload["label"]), payload.get("confidence")
    except Exception:
        return "Unclear", None


# ---------------------------------------------------------------- scoring

def score(calls, clusters, truth):
    predicted = np.array([calls[c]["label"] for c in clusters])
    per_cluster_correct = {}
    for cluster in set(clusters):
        in_cluster = clusters == cluster
        values, counts = np.unique(truth[in_cluster], return_counts=True)
        per_cluster_correct[cluster] = str(values[counts.argmax()])
    mapped_truth = {c: TRUTH_TO_VOCAB.get(t) for c, t in per_cluster_correct.items()}
    return {
        "matched_accuracy": _matched_accuracy(predicted, truth),
        # Vocabulary-aware, per cluster: did it pick the option the majority
        # true type belongs to. Coarser than matched accuracy, but it does not
        # need a one-to-one map, so two "Monocyte" clusters can both be right.
        "clusters_correct": sum(1 for c in calls
                                if calls[c]["label"] == mapped_truth.get(c)),
        "n_clusters": len(calls),
        "cell_accuracy_vocab": round(float(np.mean(
            [predicted[i] == TRUTH_TO_VOCAB.get(t) for i, t in enumerate(truth)])), 4),
        "n_unclear_clusters": sum(1 for c in calls.values()
                                  if c["label"] in (None, "Unclear")),
        "labels": {c: calls[c]["label"] for c in sorted(calls, key=int)},
        "truth": {c: per_cluster_correct[c] for c in sorted(per_cluster_correct,
                                                            key=int)},
    }


def main() -> int:
    settings = load_settings()
    pipeline = Pipeline("pbmc3k", overrides=PINNED, plan=False)
    for step in ("load", "qc", "normalize", "features", "integrate", "cluster"):
        pipeline.execute(step)

    adata = pipeline.adata
    import scanpy as sc
    sc.tl.rank_genes_groups(adata, "leiden", method="wilcoxon")
    markers = _top_markers(adata, n=10)
    sizes = {c: int((adata.obs["leiden"] == c).sum()) for c in markers}

    label_key = pipeline.state.obs["label_key"]
    mask = adata.obs[label_key].notna().values
    truth = adata.obs[label_key].values[mask].astype(str)
    clusters = adata.obs["leiden"].values[mask].astype(str)

    arms = {"oracle": (arm_oracle, 1), "overlap": (arm_overlap, 1),
            "celltypist": (arm_celltypist, 1), "jev": (arm_jev, REPEATS),
            "jev_loop": (arm_jev_loop, REPEATS)}
    for model in CLAUDE_ARMS:
        arms[model] = (arm_claude, REPEATS)
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    if only:
        arms = {k: v for k, v in arms.items() if k in only}
    # Merge into what earlier invocations measured: running one arm must not
    # erase the others.
    report = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    report.pop("claude", None)   # pre-split arm that silently mixed models
    report["_setup"] = {"cluster_sizes": sizes, "markers": markers,
                        "pinned": PINNED, "repeats": REPEATS}
    for name, (fn, repeats) in arms.items():
        runs = []
        for i in range(repeats):
            kwargs = {}
            if name in ("celltypist", "jev_loop", "oracle"):
                kwargs["adata"] = adata
            if name == "oracle":
                kwargs["label_key"] = label_key
            if name in CLAUDE_ARMS:
                kwargs["model"] = name
            try:
                calls, meters = fn(markers, sizes, "human PBMC", settings, **kwargs)
            except Exception as exc:
                print(f"  {name} repeat {i} FAILED: {type(exc).__name__}: {exc}",
                      flush=True)
                continue
            runs.append({**score(calls, clusters, truth), **meters})
            report[name] = {"runs": runs}   # partial, overwritten below
            print(f"  {name:11s} run {i}  acc={runs[-1]['matched_accuracy']:.4f}"
                  f"  ${runs[-1]['cost_usd']:.6f}  {runs[-1]['seconds']:.1f}s",
                  flush=True)
        if runs:
            accs = [r["matched_accuracy"] for r in runs]
            report[name] = {
                "runs": runs,
                "accuracy_mean": round(float(np.mean(accs)), 4),
                "accuracy_min": round(float(np.min(accs)), 4),
                "accuracy_max": round(float(np.max(accs)), 4),
                "clusters_correct_mean": round(float(np.mean(
                    [r["clusters_correct"] for r in runs])), 2),
                "cell_accuracy_vocab_mean": round(float(np.mean(
                    [r["cell_accuracy_vocab"] for r in runs])), 4),
                "cost_usd_mean": round(float(np.mean([r["cost_usd"] for r in runs])), 6),
                "seconds_mean": round(float(np.mean([r["seconds"] for r in runs])), 2),
            }

    # Matched accuracy is reported but is NOT the headline: its one-to-one map
    # gives full credit to a cluster consistently given the wrong name (CD8 T
    # cells called "NK cell" map onto CD8 T cells), so on this task every arm
    # ties at the oracle. Cell accuracy in the shared vocabulary does not.
    print("\n=== HEAD TO HEAD (9 clusters, identical markers and vocabulary) ===")
    print(f"  {'arm':18s} {'cell acc (vocab)':>17s} {'clusters':>9s} {'matched':>8s}"
          f" {'cost $':>10s} {'wall s':>7s}")
    for name, r in report.items():
        if name.startswith("_") or "runs" not in r or "accuracy_mean" not in r:
            continue
        accs = [x["cell_accuracy_vocab"] for x in r["runs"]]
        span = (f"{np.mean(accs):.4f}" if min(accs) == max(accs)
                else f"{np.mean(accs):.3f} [{min(accs):.3f}-{max(accs):.3f}]")
        print(f"  {name:18s} {span:>17s} {r['clusters_correct_mean']:>6.1f}/9"
              f" {r['accuracy_mean']:8.4f} {r['cost_usd_mean']:10.6f} {r['seconds_mean']:7.1f}")

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
