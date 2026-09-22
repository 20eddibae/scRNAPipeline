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
  claude      a general language model given THE SAME closed option set and the
              same markers, so the comparison is decision-model-vs-LLM rather
              than two different tasks.

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
                            "confidence": getattr(answer, "confidence", None)}
            in_tok += response.usage.input_tokens
            out_tok += response.usage.output_tokens
    return out, _cost("jev", in_tok, out_tok, latencies)


def arm_claude(markers, sizes, context, settings):
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key,
                                 base_url=settings.anthropic_base_url)
    options = "\n".join(f"- {k}: {v}" for k, v in PBMC_VOCABULARY.items())
    out, in_tok, out_tok, latencies = {}, 0, 0, []

    # The gateway throttles one model at a time ("No access to this model at this
    # time") while others stay free, so walk the same fallback list ClaudeClient
    # uses. Pricing follows whichever model actually answered -- billing the
    # winner's tokens at the primary model's rate would overstate the cost.
    candidates = [settings.claude_model] + [m for m in settings.fallback_models
                                            if m != settings.claude_model]
    used: dict[str, int] = {}

    for cluster, genes in markers.items():
        prompt = PROMPT.format(context=context, cluster=cluster,
                               size=sizes[cluster], markers=", ".join(genes),
                               options=options)
        message = None
        for model in candidates:
            t0 = time.time()
            try:
                message = client.messages.create(
                    model=model, max_tokens=2000,
                    thinking={"type": "adaptive"},
                    messages=[{"role": "user", "content": prompt}])
            except anthropic.RateLimitError:
                continue
            latencies.append(time.time() - t0)
            used[model] = used.get(model, 0) + 1
            break
        if message is None:
            raise RuntimeError("every candidate model was rate-limited")
        text = "".join(b.text for b in message.content if b.type == "text")
        label, confidence = _parse(text)
        out[cluster] = {"label": label, "confidence": confidence}
        in_tok += message.usage.input_tokens
        out_tok += message.usage.output_tokens
    meters = _cost("claude", in_tok, out_tok, latencies,
                   model=max(used, key=used.get) if used else None)
    meters["models_used"] = used
    return out, meters


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
        out[cluster] = {"label": str(values[counts.argmax()]), "confidence": None}
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
    return {
        "matched_accuracy": _matched_accuracy(predicted, truth),
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

    arms = {"overlap": (arm_overlap, 1), "celltypist": (arm_celltypist, 1),
            "jev": (arm_jev, REPEATS), "claude": (arm_claude, REPEATS)}
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    if only:
        arms = {k: v for k, v in arms.items() if k in only}
    report = {}
    for name, (fn, repeats) in arms.items():
        runs = []
        for i in range(repeats):
            kwargs = {"adata": adata} if name == "celltypist" else {}
            try:
                calls, meters = fn(markers, sizes, "human PBMC", settings, **kwargs)
            except Exception as exc:
                print(f"  {name} repeat {i} FAILED: {type(exc).__name__}: {exc}",
                      flush=True)
                continue
            runs.append({**score(calls, clusters, truth), **meters})
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
                "cost_usd_mean": round(float(np.mean([r["cost_usd"] for r in runs])), 6),
                "seconds_mean": round(float(np.mean([r["seconds"] for r in runs])), 2),
            }

    print("\n=== HEAD TO HEAD (9 clusters, identical markers and vocabulary) ===")
    print(f"  {'arm':12s} {'accuracy':>20s} {'cost $':>12s} {'wall s':>9s}")
    for name, r in report.items():
        span = (f"{r['accuracy_mean']:.4f}" if r["accuracy_min"] == r["accuracy_max"]
                else f"{r['accuracy_mean']:.4f} [{r['accuracy_min']:.3f}-{r['accuracy_max']:.3f}]")
        print(f"  {name:12s} {span:>20s} {r['cost_usd_mean']:12.6f} {r['seconds_mean']:9.1f}")

    out = Path("experiments/results/head_to_head.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
