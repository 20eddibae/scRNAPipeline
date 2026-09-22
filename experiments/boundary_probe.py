"""Experiment 5: which of Jev's decisions are actually hard?

Experiment 1 showed Jev returning the scanpy defaults at high confidence. That
leaves two possible readings, and they need different fixes:

  (a) the questions are easy -- every realistic input sits deep inside one
      option's region, so the default is simply right and nothing is decided;
  (b) Jev is not reading the evidence -- the answer would stay the same even
      when the statistics move to where the textbook says it should change.

This script separates them. For each decision point it holds everything fixed
except the one statistic the question tells Jev to weigh, sweeps that statistic
from one textbook extreme to the other, and asks at every point. A question the
model is actually deciding has to satisfy three things:

  responsive   the answer changes somewhere along the sweep (a rubber stamp
               never flips, and that is (b));
  ordered      it changes in the direction the evidence points, once;
  honest       confidence dips at the flip. A calibrated model cannot be 0.95
               sure on both sides of its own boundary.

The distance between the flip and the realistic operating range is what makes
a question *tough*: a boundary nobody's data comes near is a question with one
answer.

Two controls, because a flip can come from something other than evidence:

  repeat     every point is asked REPEATS times. Jev is not deterministic, and a
             "flip" that also happens between identical calls is noise.
  reorder    Choice questions are asked again with the options in reverse
             order. An answer that moves with option order is reading position.

No pipeline step runs here -- only the typed questions, with synthetic but
realistic observation dicts -- so every call is the question and nothing else.
The questions are the production ones, imported from the steps, so this probes
what the pipeline actually asks.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("JEV_CONFIDENCE_FLOOR", "0")  # see the raw answer, never the fallback
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scrnapipeline.config import load_settings
from scrnapipeline.jev import ChoiceQ, JevDecider, NoulQ, ScoreQ
from scrnapipeline.state import RunState
from scrnapipeline.steps import annotate

REPEATS = int(os.environ.get("PROBE_REPEATS", "3"))
OUT = Path(__file__).resolve().parent / "results" / os.environ.get(
    "PROBE_OUT", "boundary_probe.json")

# A healthy 10x PBMC run. Every sweep perturbs one field of this and nothing else.
BASE = {
    "n_cells": 8000, "n_genes": 33000, "n_batches": 1,
    "median_genes_per_cell": 1200.0, "median_counts_per_cell": 4000.0,
    "median_pct_mt": 3.0, "tissue": "peripheral blood (PBMC)",
}


def _panel_q(cluster_size: int) -> ChoiceQ:
    return ChoiceQ(
        instructions=(
            f"These are the genes most enriched in cluster 5 ({cluster_size} cells) "
            "of a PBMC sample, ranked by differential expression against all other "
            "clusters. You are also given mean expression of a canonical lineage "
            "panel within this cluster; use it to break ties that the ranked "
            "markers alone cannot settle. Which cell type do they indicate?"),
        criteria=annotate.PBMC_VOCABULARY, default="Unclear")


# name -> (question, field swept, values, extra obs, textbook expectation)
def sweeps():
    from scrnapipeline.steps.qc import QCStep
    from scrnapipeline.steps.normalize import NormalizeStep
    from scrnapipeline.steps.features import FeatureStep
    from scrnapipeline.steps.integrate import IntegrateStep
    from scrnapipeline.steps.cluster import ClusterStep

    import types
    import pandas as pd
    # integrate only asks when the matrix has >1 batch, so hand it a stub that does.
    stub = types.SimpleNamespace(obs=pd.DataFrame({"batch": ["a", "b"]}))

    def q(cls, name):
        return cls().questions(stub, RunState("probe", "probe"))[name]

    cytotoxic = ["NKG7", "CST7", "GZMA", "CTSW", "B2M", "CCL5", "GNLY", "PRF1", "GZMB", "FGFBP2"]
    return [
        dict(name="qc.stringency / mito", q=q(QCStep, "stringency"), field="median_pct_mt",
             values=[1, 3, 5, 8, 12, 18, 25, 35],
             expect="standard at low mito, strict once mito passes ~10-15%"),
        dict(name="qc.stringency / depth", q=q(QCStep, "stringency"), field="median_genes_per_cell",
             values=[150, 300, 500, 800, 1200, 2000, 3500, 6000],
             expect="lenient when shallow, standard when typical"),
        dict(name="qc.flag_doublets / cells", q=q(QCStep, "flag_doublets"), field="n_cells",
             values=[300, 1000, 3000, 6000, 10000, 20000, 50000, 150000],
             expect="False below a few thousand cells, True above ~10k (10x rate ~0.8%/1k cells)"),
        dict(name="normalize.method / depth", q=q(NormalizeStep, "method"), field="median_counts_per_cell",
             values=[300, 800, 1500, 3000, 6000, 12000, 30000, 80000],
             expect="pearson_residuals when very shallow, log1p_cpm when deep"),
        dict(name="features.n_hvg / cells", q=q(FeatureStep, "n_hvg"), field="n_cells",
             values=[300, 1000, 3000, 10000, 30000, 100000, 300000, 1000000],
             expect="narrow for small homogeneous data, broad for atlas-scale"),
        dict(name="features.n_pcs / cells", q=q(FeatureStep, "n_pcs"), field="n_cells",
             values=[300, 1000, 3000, 10000, 30000, 100000, 300000, 1000000],
             expect="compact for small data, generous for atlas-scale"),
        dict(name="cluster.resolution / cells", q=q(ClusterStep, "resolution"), field="n_cells",
             values=[300, 1000, 3000, 10000, 30000, 100000, 300000, 1000000],
             expect="coarse for small data, fine for large"),
        dict(name="integrate.integrate / batches", q=q(IntegrateStep, "integrate"), field="n_batches",
             values=[2, 3, 5, 10, 30, 100, 300, 700],
             expect="True at many donors; the pipeline never asks at 1 batch"),
        dict(name="integrate.method / batches", q=q(IntegrateStep, "method"), field="n_batches",
             values=[2, 3, 5, 10, 30, 100, 300, 700],
             expect="harmony for few batches; bbknn for many small ones"),
        # The hard semantic one: the pbmc3k cluster-5 markers, with the CD3 panel
        # swept from absent (NK) to T-cell level (CD8). Real CD8 T cells sit ~1.5-2.5
        # log1p mean CD3E; NK ~0-0.2.
        dict(name="annotate.cell_type / CD3E panel", q=_panel_q(437), field="cd3_level",
             values=[0.0, 0.1, 0.25, 0.5, 0.8, 1.2, 1.8, 2.5],
             extra={"top_markers": cytotoxic},
             expect="NK cell with no CD3, CD8 T cell at T-cell CD3 levels",
             panel=True),
    ] + nature_sweeps()


def _nature_obs(t=0.0, cyto=0.95, myeloid=0.02, co_tm=None, stress=0.08, markers=None):
    """Evidence dict for the cluster-nature question, in `_lineage_evidence` shape."""
    frac = {"T (CD3D/CD3E)": t, "cytotoxic (NKG7/GNLY)": cyto, "B (MS4A1/CD79A)": 0.02,
            "myeloid (LYZ/CD14)": myeloid, "platelet (PPBP/PF4)": 0.01}
    pairs = {}
    if t >= 0.1 and cyto >= 0.1:
        # CD8 T cells are cytotoxic: every T-positive cell is also cytotoxic-positive.
        pairs["T (CD3D/CD3E) & cytotoxic (NKG7/GNLY)"] = {
            "co_positive": round(min(t, cyto), 3), "expected_if_independent": round(t * cyto, 3)}
    if t >= 0.1 and myeloid >= 0.1:
        co = min(t, myeloid) if co_tm is None else co_tm
        pairs["T (CD3D/CD3E) & myeloid (LYZ/CD14)"] = {
            "co_positive": round(co, 3), "expected_if_independent": round(t * myeloid, 3)}
    return {"top_markers": markers or ["NKG7", "CST7", "GZMA", "CTSW", "CCL5", "GNLY", "PRF1"],
            "fraction_of_cells_positive": frac, "co_positivity": pairs,
            "median_stress_gene_share": stress}


def nature_sweeps():
    from scrnapipeline.steps.annotate import cluster_nature_question
    q = cluster_nature_question("5", 437)
    tmark = ["CD3D", "IL7R", "LDHB", "CD3E", "LTB", "NOSIP", "CD2"]
    return [
        # CD3 carried by 0% (NK) -> half (CD8+NK mixed) -> all (CD8) of a cytotoxic cluster.
        dict(name="cluster_nature / CD3 fraction in cytotoxic cluster", q=q, field="t_frac",
             values=[0.0, 0.05, 0.15, 0.3, 0.5, 0.7, 0.85, 0.97],
             build=lambda v: _nature_obs(t=v),
             expect="one_type at 0 and ~1; two_types across the middle"),
        # T cluster where myeloid genes appear IN THE SAME cells (doublets).
        dict(name="cluster_nature / myeloid co-expressed in T cells", q=q, field="myeloid_frac",
             values=[0.0, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8],
             build=lambda v: _nature_obs(t=0.95, cyto=0.1, myeloid=v, markers=tmark),
             expect="one_type when rare, doublets once a sizeable fraction co-expresses"),
        # Same myeloid fractions but in DIFFERENT cells from the T cells.
        dict(name="cluster_nature / myeloid in separate cells", q=q, field="myeloid_frac",
             values=[0.0, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8],
             build=lambda v: _nature_obs(t=round(1 - v, 3), cyto=0.1, myeloid=v, co_tm=0.0,
                                         markers=tmark),
             expect="one_type when rare, two_types (not doublets) once sizeable"),
        dict(name="cluster_nature / stress share", q=q, field="stress_share",
             values=[0.02, 0.05, 0.1, 0.15, 0.25, 0.35, 0.5, 0.7],
             build=lambda v: _nature_obs(t=0.95, cyto=0.1, stress=v, markers=tmark),
             expect="one_type at normal stress share, low_quality when it dominates"),
    ]


def _state(field: str, value, extra: dict | None, panel: bool, build=None) -> RunState:
    st = RunState("probe", "probe")
    if build is not None:
        st.observe(tissue=BASE["tissue"], cluster="5", n_cells_in_cluster=437, **build(value))
        return st
    obs = dict(BASE)
    obs.update(extra or {})
    if panel:
        obs["canonical_marker_expression"] = {
            "CD3D": value, "CD3E": value, "CD8A": round(value * 0.7, 3),
            "CD4": 0.05, "NCAM1": round(max(0.0, 0.9 - value * 0.35), 3),
            "KLRD1": round(max(0.2, 1.6 - value * 0.5), 3), "FCGR3A": round(max(0.1, 1.4 - value * 0.5), 3),
        }
    else:
        obs[field] = value
    st.observe(**obs)
    return st


def _reversed(q):
    if isinstance(q, ChoiceQ):
        return ChoiceQ(q.instructions, dict(reversed(list(q.criteria.items()))), q.default)
    return None


def _ask(decider, q, st, retries=4):
    for attempt in range(retries):
        try:
            d = decider.decide("probe", {"x": q}, st)["x"]
            if "no answer" in d.note:
                raise RuntimeError(d.note)
            return {"value": d.value, "confidence": d.confidence, "raw": d.raw, "note": d.note}
        except Exception as exc:  # recorded, never substituted
            err = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * (attempt + 1))
    return {"value": None, "error": err}


def summarise(points, values):
    modal = []
    for p in points:
        vals = [a["value"] for a in p["answers"] if a.get("value") is not None]
        modal.append(max(set(map(str, vals)), key=[str(v) for v in vals].count) if vals else None)
    flips = [i for i in range(1, len(modal)) if modal[i] != modal[i - 1]]
    confs, probs = [], []
    for p in points:
        ps = [a["raw"].get("noul") for a in p["answers"] if a.get("raw", {}).get("noul") is not None]
        probs.append(round(sum(ps) / len(ps), 3) if ps else None)
        c = [a["confidence"] for a in p["answers"] if a.get("confidence") is not None]
        confs.append(round(sum(c) / len(c), 3) if c else None)
    unstable = sum(len({str(a.get("value")) for a in p["answers"]}) > 1 for p in points)
    order_moved = sum(1 for p in points
                      if p.get("reordered") and p["reordered"].get("value") is not None
                      and str(p["reordered"]["value"]) != modal[points.index(p)])
    near = [confs[i] for f in flips for i in (f - 1, f) if confs[i] is not None]
    far = [c for i, c in enumerate(confs) if c is not None
           and all(abs(i - f) > 1 and abs(i - (f - 1)) > 1 for f in flips)]
    return {
        "modal": modal, "mean_confidence": confs, "mean_noul_p": probs,
        "flips_at": [(values[f - 1], values[f]) for f in flips],
        "n_distinct_answers": len(set(m for m in modal if m is not None)),
        "points_where_repeats_disagree": unstable,
        "points_where_order_moved_answer": order_moved,
        "conf_near_flip": round(sum(near) / len(near), 3) if near else None,
        "conf_far_from_flip": round(sum(far) / len(far), 3) if far else None,
    }


def main():
    settings = load_settings()
    decider = JevDecider(settings)
    results = []
    only = os.environ.get("PROBE_ONLY")
    for sw in sweeps():
        if only and only not in sw["name"]:
            continue
        points, t0 = [], time.time()
        for v in sw["values"]:
            st = _state(sw["field"], v, sw.get("extra"), sw.get("panel", False), sw.get("build"))
            answers = [_ask(decider, sw["q"], st) for _ in range(REPEATS)]
            rq = _reversed(sw["q"])
            points.append({"value": v, "answers": answers,
                           "reordered": _ask(decider, rq, st) if rq else None})
        s = summarise(points, sw["values"])
        results.append({"name": sw["name"], "field": sw["field"], "values": sw["values"],
                        "expect": sw["expect"], "summary": s, "points": points,
                        "seconds": round(time.time() - t0, 1)})
        print(f"\n{sw['name']}  ({sw['field']})   expect: {sw['expect']}")
        for v, m, c, pp in zip(sw["values"], s["modal"], s["mean_confidence"], s["mean_noul_p"]):
            print(f"   {v!s:>9}  {m!s:<22} conf {c}" + (f"  p {pp}" if pp is not None else ""))
        print(f"   flips {s['flips_at']}  repeat-unstable {s['points_where_repeats_disagree']}"
              f"  order-moved {s['points_where_order_moved_answer']}"
              f"  conf near/far {s['conf_near_flip']}/{s['conf_far_from_flip']}")
        OUT.write_text(json.dumps({"repeats": REPEATS, "base": BASE, "sweeps": results},
                                  indent=2, default=str))


if __name__ == "__main__":
    main()
