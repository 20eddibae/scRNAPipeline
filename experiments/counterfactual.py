"""Experiment 1: does Jev's confidence predict which branch actually wins?

Most decisions in this pipeline are unfalsifiable. Nobody can say log1p_cpm was
"correct" for a given matrix, so a confidence attached to that choice has never
been checked against anything. This script manufactures the missing ground
truth the only way available: run every arm, measure what came out, and see
which one won.

Two questions fall out, and the second matters more than the first.

  1. Did Jev pick the winner?
  2. How much did the choice matter at all? If every arm lands within noise of
     the others, the decision does not deserve a model call, a human, or an
     argument -- and knowing which decisions are inert is prerequisite to
     deciding who should make the live ones.

Everything not under test is pinned to its declared default so each sweep is a
controlled comparison.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scrnapipeline.config import load_settings
from scrnapipeline.jev import ChoiceQ, JevDecider, ScoreQ
from scrnapipeline.pipeline import Pipeline
from scrnapipeline.registry import DEFAULT_ORDER

# decision -> the arms to run
SWEEPS: dict[str, list] = {
    "normalize.method": ["log1p_cpm", "pearson_residuals", "scran_pooling"],
    "features.n_hvg": [1000, 2000, 4000],
    "cluster.resolution": [0.4, 1.0, 1.6],
}

# Held constant across every arm so the sweep measures one thing.
PINNED = {"annotate.model": "celltypist", "qc.stringency": "standard",
          "qc.flag_doublets": False}

METRICS = ("ari", "nmi", "probe_accuracy", "annotation_matched_accuracy")


def run_arm(decision: str, value, dataset: str = "pbmc3k") -> dict:
    overrides = dict(PINNED)
    overrides[decision] = value
    started = time.time()
    # plan=False: Claude's framing is irrelevant here, the decision is forced.
    pipeline = Pipeline(dataset, overrides=overrides, plan=False)
    for step in DEFAULT_ORDER:
        pipeline.execute(step)
    out = {k: pipeline.state.metrics.get(k) for k in METRICS}
    out["seconds"] = round(time.time() - started, 1)

    # Verify the counterfactual actually happened. A step that catches its own
    # failure and falls back produces an arm identical to the default, which
    # reads as "this decision does not matter" when it means "this arm never
    # ran". The scran arm did exactly that: it raised on every sparse matrix,
    # fell back to log1p_cpm, and reported identical metrics to four decimals.
    step = decision.split(".")[0]
    record = next((s for s in pipeline.state.steps if s.step == step), None)
    applied = (record.summary.get("applied") or record.summary.get("model")
               or record.summary.get("status")) if record else None
    out["applied"] = applied
    out["arm_ran"] = (applied is None
                      or str(value) in str(applied)
                      or "unavailable" not in str(applied))
    return out


def ask_jev(decision: str, observations: dict, settings) -> dict:
    """What Jev says, given the same observed state the pipeline had."""
    from scrnapipeline.state import RunState

    step, question = decision.split(".")
    state = RunState("counterfactual", "pbmc3k")
    state.observe(**observations)

    steps = __import__("scrnapipeline.registry", fromlist=["build_steps"]).build_steps(
        settings=settings)
    baseline = steps[step].questions(None, state)
    if question not in baseline:
        return {}
    decided = JevDecider(settings).decide(step, {question: baseline[question]}, state)
    d = decided[question]
    return {"choice": d.value, "confidence": d.confidence,
            "probabilities": d.raw.get("probabilities"), "source": d.source}


def main() -> int:
    settings = load_settings()
    report: dict = {"sweeps": {}}

    # One baseline run supplies the observations Jev would have seen.
    print("baseline run for observed state ...", flush=True)
    base = Pipeline("pbmc3k", overrides=PINNED, plan=False)
    for step in DEFAULT_ORDER:
        base.execute(step)
    observations = dict(base.state.obs)
    report["baseline_metrics"] = {k: base.state.metrics.get(k) for k in METRICS}
    print("  ", json.dumps(report["baseline_metrics"]), flush=True)

    for decision, arms in SWEEPS.items():
        print(f"\n=== {decision} ===", flush=True)
        results = {}
        for value in arms:
            results[str(value)] = run_arm(decision, value)
            print(f"  arm {value!r:20s} {json.dumps(results[str(value)])}", flush=True)

        # Only arms that actually ran can be compared.
        scored = {k: v for k, v in results.items()
                  if v.get("ari") is not None and v.get("arm_ran")}
        skipped = [k for k, v in results.items() if not v.get("arm_ran")]
        if skipped:
            print(f"  !! arms that fell back and were excluded: {skipped}", flush=True)
        winner = max(scored, key=lambda k: scored[k]["ari"]) if scored else None
        spread = (max(v["ari"] for v in scored.values())
                  - min(v["ari"] for v in scored.values())) if scored else None

        jev = ask_jev(decision, observations, settings)
        report["sweeps"][decision] = {
            "arms": results,
            "empirical_winner_by_ari": winner,
            "ari_spread": round(spread, 4) if spread is not None else None,
            "arms_excluded_as_fallbacks": skipped,
            "jev": jev,
            "jev_picked_winner": (str(jev.get("choice")) == winner) if jev else None,
        }
        print(f"  -> winner={winner}  ari spread={spread}", flush=True)
        print(f"  -> jev said {jev.get('choice')!r} @ {jev.get('confidence')}"
              f"  matched winner: {report['sweeps'][decision]['jev_picked_winner']}",
              flush=True)

    out = Path("experiments/results/counterfactual.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
