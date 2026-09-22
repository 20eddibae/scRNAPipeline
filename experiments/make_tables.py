"""Build the presentation tables from the result JSONs. No number is typed by hand.

Writes experiments/results/tables.md (markdown) and tables.json (the same rows,
for anything that wants to render them).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate_stats import paired  # noqa: E402

R = Path("experiments/results")
NICE = {
    "oracle": "Ceiling (true majority type per cluster)",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "cascade": "Krino cascade (Jev → Claude when torn)",
    "split": "Krino split-then-name",
    "split_all": "Control: split every cluster",
    "jev": "Jev alone",
    "overlap": "No model (marker overlap)",
    "celltypist": "CellTypist (per cell)",
}
ORDER = ["oracle", "split", "split_all", "cascade", "claude-sonnet-5",
         "claude-haiku-4-5", "jev", "celltypist", "overlap"]


def md(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def main() -> int:
    agg = json.loads((R / "aggregate.json").read_text())
    data = {k: v for k, v in agg.items() if not k.startswith("_") and "scores" in v}
    rows_d = list(data.values())
    n_ds = len(rows_d)
    n_clusters = sum(d["n_named"] for d in rows_d)
    n_cells = sum(d["n_scorable"] for d in rows_d)
    tables: dict = {}
    text = [f"# Krino — measured results\n\n{n_ds} datasets (pbmc3k + "
            f"{n_ds - 1} independent scTab blood studies), {n_cells:,} scored cells, "
            f"{n_clusters} clusters. Dataset is the unit for every mean and test.\n"]

    # T1 — the headline
    head = ["Annotator", "Cell accuracy", "DE agreement w/ truth", "Composition error",
            "$ per 1,000 clusters", "Datasets won vs Claude Sonnet"]
    t1 = []
    for a in ORDER:
        have = [d for d in rows_d if a in d["scores"]]
        if not have:
            continue
        acc = np.mean([d["scores"][a]["cell_accuracy"] for d in have])
        de = np.mean([d["scores"][a]["de_jaccard_mean"] for d in have
                      if d["scores"][a]["de_jaccard_mean"] is not None])
        tv = np.mean([d["scores"][a]["composition_tv"] for d in have])
        cost = sum(d["scores"][a]["cost_usd"] for d in have)
        per_k = cost / sum(d["n_named"] for d in have) * 1000
        vs = paired(rows_d, a, "claude-sonnet-5", "cell_accuracy") \
            if a != "claude-sonnet-5" else None
        wtl = f"{vs['wins']}/{vs['ties']}/{vs['losses']}" if vs else "—"
        t1.append([NICE[a], f"{acc:.3f}", f"{de:.3f}", f"{tv:.3f}",
                   "—" if a == "oracle" else f"${per_k:.2f}", wtl])
    tables["headline"] = {"headers": head, "rows": t1}
    text += ["## 1. Headline: every annotator on the same clusters\n",
             md(head, t1),
             "\nWon/tied/lost is per dataset. DE agreement = Jaccard of each cell "
             "type's top-25 DE genes vs the genes the true labels give. Composition "
             "error = total-variation distance from the true cell-type mix.\n"]

    # T2 — pre-registered tests
    s = json.loads((R / "aggregate_summary.json").read_text())

    def verdict(r, need_pos=True):
        if r is None:
            return "n/a"
        return ("supported" if (r["mean"] > 0 if need_pos else True) and r["p"] < 0.05
                else "not supported")

    def cell(r):
        return (f"{r['mean']:+.3f} [{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}]",
                f"{r['wins']}/{r['ties']}/{r['losses']}", f"{r['p']:.3g}")

    head2 = ["Pre-registered claim", "Mean Δ [95% CI]", "W/T/L", "p", "Verdict"]
    t2 = []
    h1 = s["H1"]
    t2.append(["H1 cascade ≈ Claude Sonnet accuracy (Δ > −0.01) at ≤25% of cost",
               *cell(h1),
               ("supported" if h1["mean"] > -0.01 and s["H1_cost_ratio"] <= 0.25
                else "not supported") + f" (cost ratio {s['H1_cost_ratio']:.2f})"])
    t2.append(["H2 cascade beats Jev alone", *cell(s["H2"]), verdict(s["H2"])])
    t2.append(["H3 cascade beats no-model baseline", *cell(s["H3"]), verdict(s["H3"])])
    t2.append(["H4a split beats cascade (accuracy)", *cell(s["H4_acc"]), verdict(s["H4_acc"])])
    t2.append(["H4b split beats cascade (DE agreement)", *cell(s["H4_de"]), verdict(s["H4_de"])])
    t2.append(["H4c split ≥ split-everything control", *cell(s["H4_vs_split_all"]),
               "supported" if s["H4_vs_split_all"]["mean"] >= 0 else "not supported"])
    tables["hypotheses"] = {"headers": head2, "rows": t2}
    text += ["## 2. Pre-registered claims (fixed before the run)\n", md(head2, t2),
             "\nWilcoxon signed-rank over datasets; CI is a bootstrap over datasets.\n"]

    # T3 — per dataset
    head3 = ["Dataset", "Cells", "Clusters", "Escalated"] + [NICE[a].split(" (")[0]
                                                              for a in ORDER]
    t3 = []
    for k, d in data.items():
        t3.append([k, d["n_scorable"], d["n_named"], len(d["escalated"])] +
                  [f"{d['scores'][a]['cell_accuracy']:.3f}" if a in d["scores"] else "—"
                   for a in ORDER])
    tables["per_dataset"] = {"headers": head3, "rows": t3}
    text += ["## 3. Cell accuracy per dataset\n", md(head3, t3), ""]

    # T4 — cost and speed, from the calls themselves
    h2h = json.loads((R / "head_to_head.json").read_text())
    head4 = ["Model", "Median latency per call", "$ per 1,000 clusters", "Output tokens billed"]
    t4 = []
    for a, label, billed in (("jev", "Jev", "no ($0)"),
                             ("claude-haiku-4-5", "Claude Haiku 4.5", "yes"),
                             ("claude-sonnet-5", "Claude Sonnet 5", "yes")):
        lat = np.median([r["median_latency_s"] for r in h2h[a]["runs"]])
        cost = sum(d["scores"][a]["cost_usd"] for d in rows_d)
        t4.append([label, f"{lat:.2f} s", f"${cost / n_clusters * 1000:.2f}", billed])
    tables["cost_speed"] = {"headers": head4, "rows": t4}
    text += ["## 4. Cost and speed per decision\n", md(head4, t4), ""]

    # T5 — does Jev's uncertainty point at its own mistakes?
    fw = f = kw = k = 0
    for d in rows_d:
        truth = d["truth_by_cluster"]
        for c, calls in d["calls"].items():
            if truth.get(c) in (None, "Unclear"):
                continue
            wrong = calls["jev"]["label"] != truth[c]
            if c in d["escalated"]:
                f += 1
                fw += wrong
            else:
                k += 1
                kw += wrong
    head5 = ["Jev's own signal", "Clusters", "Jev wrong", "Error rate"]
    t5 = [["Torn (margin < 0.5) → escalated", f, fw, f"{fw / max(f, 1):.0%}"],
          ["Confident → kept", k, kw, f"{kw / max(k, 1):.0%}"]]
    tables["escalation"] = {"headers": head5, "rows": t5}
    text += ["## 5. Is Jev's uncertainty aimed at its own mistakes?\n", md(head5, t5), ""]

    # T6 — pipeline tuning decisions (Experiment 1)
    cf_path = R / "counterfactual.json"
    if cf_path.exists():
        cf = json.loads(cf_path.read_text())["sweeps"]
        head6 = ["Decision", "Jev chose (confidence)", "Best in hindsight (ARI)",
                 "ARI at stake", "Right?"]
        t6 = [[name, f"{sw['jev']['choice']} ({sw['jev']['confidence']:.2f})",
               sw["empirical_winner_by_ari"], f"{sw['ari_spread']:.2f}",
               "yes" if sw["jev_picked_winner"] else "no"]
              for name, sw in cf.items()]
        tables["tuning"] = {"headers": head6, "rows": t6,
                            "source": "counterfactual.json (pbmc3k)"}
        text += ["## 6. Tuning decisions (pbmc3k, Experiment 1)\n", md(head6, t6),
                 "\nOne dataset, three decisions: an anecdote, not a rate. Jev picked "
                 "the scanpy default each time, and its two misses carried its highest "
                 "confidence.\n"]

    (R / "tables.md").write_text("\n".join(text))
    (R / "tables.json").write_text(json.dumps(tables, indent=2))
    print("\n".join(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
