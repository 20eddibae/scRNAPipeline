"""Read aggregate.json and test the pre-registered hypotheses, dataset as unit.

Nothing here is tuned: TAU, the escalation target and the hypotheses were fixed
in aggregate.py before its first run. The tau sweep at the end is EXPLORATORY
and is printed under that heading so it cannot be mistaken for the test.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
from scipy.stats import wilcoxon

IN = Path(sys.argv[1] if len(sys.argv) > 1 else "experiments/results/aggregate.json")
ARMS = ("oracle", "claude-sonnet-5", "claude-haiku-4-5", "cascade", "split",
        "split_all", "jev", "overlap", "celltypist")


def boot_ci(x, n=10000, seed=0):
    rng = np.random.default_rng(seed)
    x = np.asarray(x, dtype=float)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def paired(data, a, b, metric):
    rows = [(d["scores"][a][metric], d["scores"][b][metric]) for d in data
            if a in d["scores"] and b in d["scores"]
            and d["scores"][a][metric] is not None and d["scores"][b][metric] is not None]
    x = np.array([r[0] - r[1] for r in rows])
    if len(x) == 0:
        return None
    nz = x[x != 0]
    p = float(wilcoxon(nz).pvalue) if len(nz) >= 1 and np.any(nz) else 1.0
    lo, hi = boot_ci(x)
    return {"n": len(x), "mean": float(x.mean()), "ci": (lo, hi),
            "wins": int((x > 0).sum()), "ties": int((x == 0).sum()),
            "losses": int((x < 0).sum()), "p": p}


def fmt(r):
    if r is None:
        return "n/a"
    return (f"Δ {r['mean']:+.4f} [{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}]  "
            f"W/T/L {r['wins']}/{r['ties']}/{r['losses']}  p={r['p']:.3g}  (n={r['n']})")


def main() -> int:
    report = json.loads(IN.read_text())
    data = {k: v for k, v in report.items() if not k.startswith("_") and "scores" in v}
    skipped = {k: v.get("skipped") or v.get("failed") for k, v in report.items()
               if not k.startswith("_") and "scores" not in v}
    rows = list(data.values())
    print(f"{len(rows)} datasets scored; skipped: {skipped}\n")

    print(f"{'dataset':18s} {'cells':>6s} {'clust':>5s} {'esc':>4s}  " +
          " ".join(f"{a[:9]:>9s}" for a in ARMS))
    for k, d in data.items():
        print(f"{k:18s} {d['n_scorable']:6d} {d['n_clusters']:5d} {len(d['escalated']):4d}  " +
              " ".join(f"{d['scores'][a]['cell_accuracy']:9.3f}" if a in d["scores"]
                       else f"{'-':>9s}" for a in ARMS))

    print("\nmean over datasets (cell acc | DE Jaccard | composition TV | $ total | s total)")
    summary = {}
    for a in ARMS:
        have = [d for d in rows if a in d["scores"]]
        if not have:
            continue
        s = {m: float(np.mean([d["scores"][a][m] for d in have
                               if d["scores"][a][m] is not None]))
             for m in ("cell_accuracy", "de_jaccard_mean", "composition_tv")}
        s["cost_usd"] = float(np.sum([d["scores"][a]["cost_usd"] for d in have]))
        s["seconds"] = float(np.sum([d["scores"][a]["seconds"] for d in have]))
        summary[a] = s
        print(f"  {a:18s} {s['cell_accuracy']:.4f} | {s['de_jaccard_mean']:.4f} | "
              f"{s['composition_tv']:.4f} | {s['cost_usd']:.4f} | {s['seconds']:.0f}")

    print("\n=== PRE-REGISTERED ===")
    h1 = paired(rows, "cascade", "claude-sonnet-5", "cell_accuracy")
    ratio = summary["cascade"]["cost_usd"] / summary["claude-sonnet-5"]["cost_usd"]
    h1_pass = h1 is not None and h1["mean"] > -0.01 and ratio <= 0.25
    print(f"H1 cascade vs sonnet (acc)   {fmt(h1)}")
    print(f"   cost ratio cascade/sonnet = {ratio:.3f}   ->  "
          f"{'SUPPORTED' if h1_pass else 'NOT SUPPORTED'} (needs Δ > -0.01 and ratio <= 0.25)")
    h2 = paired(rows, "cascade", "jev", "cell_accuracy")
    print(f"H2 cascade vs jev (acc)      {fmt(h2)}  ->  "
          f"{'SUPPORTED' if h2 and h2['mean'] > 0 and h2['p'] < 0.05 else 'NOT SUPPORTED'}")
    h3 = paired(rows, "cascade", "overlap", "cell_accuracy")
    print(f"H3 cascade vs overlap (acc)  {fmt(h3)}  ->  "
          f"{'SUPPORTED' if h3 and h3['mean'] > 0 and h3['p'] < 0.05 else 'NOT SUPPORTED'}")
    h4a = paired(rows, "split", "cascade", "cell_accuracy")
    h4b = paired(rows, "split", "cascade", "de_jaccard_mean")
    h4c = paired(rows, "split", "split_all", "cell_accuracy")
    print(f"H4 split vs cascade (acc)    {fmt(h4a)}")
    print(f"   split vs cascade (DE J)   {fmt(h4b)}")
    print(f"   split vs split_all (acc)  {fmt(h4c)}  "
          f"cost {summary['split']['cost_usd']:.4f} vs {summary['split_all']['cost_usd']:.4f}")
    h4_pass = (h4a and h4a["mean"] > 0 and h4a["p"] < 0.05 and h4b and h4b["mean"] > 0
               and h4c and h4c["mean"] >= 0)
    print(f"   -> {'SUPPORTED' if h4_pass else 'NOT SUPPORTED'}")

    print("\n=== OTHER PAIRED COMPARISONS (descriptive) ===")
    for a, b, m in (("claude-sonnet-5", "jev", "cell_accuracy"),
                    ("claude-haiku-4-5", "claude-sonnet-5", "cell_accuracy"),
                    ("cascade", "celltypist", "cell_accuracy"),
                    ("cascade", "celltypist", "de_jaccard_mean"),
                    ("claude-sonnet-5", "celltypist", "cell_accuracy"),
                    ("jev", "overlap", "cell_accuracy"),
                    ("split", "cascade", "composition_tv")):
        print(f"  {a} vs {b} ({m}): {fmt(paired(rows, a, b, m))}")

    # Escalation targeting: are the clusters Jev flags the ones it gets wrong?
    flagged_wrong = flagged = kept_wrong = kept = 0
    for d in rows:
        truth = d["truth_by_cluster"]
        for c, calls in d["calls"].items():
            if truth.get(c) in (None, "Unclear"):
                continue
            wrong = calls["jev"]["label"] != truth[c]
            if c in d["escalated"]:
                flagged += 1
                flagged_wrong += wrong
            else:
                kept += 1
                kept_wrong += wrong
    print(f"\nJev error rate on clusters it escalated: {flagged_wrong}/{flagged}"
          f" = {flagged_wrong / max(flagged, 1):.2f};  on clusters it kept: "
          f"{kept_wrong}/{kept} = {kept_wrong / max(kept, 1):.2f}")

    print("\n=== EXPLORATORY (not a test): cascade accuracy by tau, cluster-level ===")
    for tau in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        right = n = esc = 0
        for d in rows:
            truth = d["truth_by_cluster"]
            for c, calls in d["calls"].items():
                if truth.get(c) in (None, "Unclear"):
                    continue
                j = calls["jev"]
                torn = j["label"] in (None, "Unclear") or j["margin"] < tau
                lab = calls["claude-haiku-4-5"]["label"] if torn else j["label"]
                esc += torn
                right += lab == truth[c]
                n += 1
        print(f"  tau {tau:.1f}: clusters right {right}/{n} = {right / n:.3f}, "
              f"escalated {esc / n:.2f}")

    out = IN.with_name("aggregate_summary.json")
    out.write_text(json.dumps({"summary": summary, "H1": h1, "H1_cost_ratio": ratio,
                               "H2": h2, "H3": h3, "H4_acc": h4a, "H4_de": h4b,
                               "H4_vs_split_all": h4c}, indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
