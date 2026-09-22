"""Learning from a scientist's corrections to Jev.

Jev is a hosted model with no feedback or fine-tuning endpoint, so it cannot be
retrained from here. What can learn is the layer between Jev and the pipeline:

  1. On the page, a scientist tunes Jev's probabilities for one decision and
     presses "Teach Jev". That correction - the distribution they believe, and
     the confidence they would put on it - is appended to `feedback.jsonl` next
     to the run records.
  2. On every later run, `LearnedDecider` blends Jev's answer for the same
     (step, question) towards the mean of those corrections, with weight
     n / (n + PRIOR_STRENGTH): one correction moves it a third of the way, two
     halfway, and it approaches the human consensus as corrections accumulate
     without ever discarding Jev's own read of the new dataset.
  3. The pick, the confidence and the floor check are then re-derived with the
     same rule `JevDecider` uses, and the decision records Jev's raw answer, the
     blend weight and how many corrections went in, so a reader can always see
     what the human changed.

Corrections are keyed by step and question, not by dataset: a scientist who
says "on PBMCs n_hvg should lean narrow" is teaching a preference for this
question in general. Set KRINO_LEARN=0 to run on Jev's answers alone.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .jev import ChoiceQ, Decider, NoulQ, Question, ScoreQ, _score_to_value
from .state import Decision, RunState

PRIOR_STRENGTH = 2.0  # corrections needed to move halfway to the human consensus
FILENAME = "feedback.jsonl"

_lock = threading.Lock()


class FeedbackStore:
    """An append-only JSONL log of corrections. Small by construction."""

    def __init__(self, root: str | Path):
        self.path = Path(root) / FILENAME

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        record = _validate(entry)
        record["ts"] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, self.path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
        return record

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn write loses one correction, not the log
        return out

    def prior(self, step: str, question: str) -> dict[str, Any] | None:
        """Mean corrected distribution (and confidence) for one question."""
        rows = [e for e in self.entries()
                if e.get("step") == step and e.get("question") == question]
        if not rows:
            return None
        keys = sorted({k for e in rows for k in e["probabilities"]})
        mean = {k: sum(e["probabilities"].get(k, 0.0) for e in rows) / len(rows) for k in keys}
        confs = [e["confidence"] for e in rows if e.get("confidence") is not None]
        return {"n": len(rows), "probabilities": mean,
                "confidence": sum(confs) / len(confs) if confs else None}

    def summary(self) -> dict[str, dict[str, Any]]:
        seen = {(e["step"], e["question"]) for e in self.entries()}
        return {f"{s}.{q}": self.prior(s, q) for s, q in sorted(seen)}


def _validate(entry: dict[str, Any]) -> dict[str, Any]:
    step, question = entry.get("step"), entry.get("question")
    probs = entry.get("probabilities")
    if not (isinstance(step, str) and isinstance(question, str) and step and question):
        raise ValueError("step and question are required")
    if not isinstance(probs, dict) or len(probs) < 2:
        raise ValueError("probabilities must map at least two options to numbers")
    clean = {str(k): float(v) for k, v in probs.items()}
    if any(not 0.0 <= v <= 1.0 for v in clean.values()):
        raise ValueError("probabilities must lie in [0, 1]")
    total = sum(clean.values())
    if total <= 0:
        raise ValueError("probabilities must not all be zero")
    clean = {k: v / total for k, v in clean.items()}
    conf = entry.get("confidence")
    if conf is not None:
        conf = float(conf)
        if not 0.0 <= conf <= 1.0:
            raise ValueError("confidence must lie in [0, 1]")
    return {
        "step": step, "question": question, "probabilities": clean, "confidence": conf,
        "run_id": str(entry.get("run_id") or ""), "dataset": str(entry.get("dataset") or ""),
        "jev_probabilities": entry.get("jev_probabilities"),
    }


class LearnedDecider:
    """Wraps a decider; blends its probabilities with the human corrections."""

    def __init__(self, base: Decider, store: FeedbackStore, floor: float):
        self.base = base
        self.store = store
        self.floor = floor

    def decide(self, step: str, questions: dict[str, Question],
               state: RunState) -> dict[str, Decision]:
        out = self.base.decide(step, questions, state)
        for name, decision in out.items():
            prior = self.store.prior(step, name)
            if prior is None or decision.source not in ("jev", "default"):
                continue  # nothing learned yet, or a forced override
            blended = _blend(decision, questions[name], prior, self.floor)
            if blended is not None:
                out[name] = blended
        return out


def _blend(d: Decision, q: Question, prior: dict[str, Any], floor: float) -> Decision | None:
    raw = dict(d.raw)
    w = prior["n"] / (prior["n"] + PRIOR_STRENGTH)

    if isinstance(q, NoulQ):
        p_jev = raw.get("noul")
        human = prior["probabilities"].get("true")
        if p_jev is None or human is None:
            return None
        p = (1 - w) * float(p_jev) + w * human
        value, confidence = p >= q.threshold, abs(p - 0.5) * 2
        raw.update(noul=round(p, 4), jev_noul=p_jev)
        jev_value = float(p_jev) >= q.threshold
    else:
        jev_probs = raw.get("probabilities")
        if not isinstance(jev_probs, dict) or len(jev_probs) < 2:
            return None
        # The SDK keys a score's rungs by int; the page (and so every saved
        # correction) keys them by string. Unmatched, the human side of the
        # blend reads as zero and renormalising hands back Jev's answer.
        jev_probs = {str(k): float(v) for k, v in jev_probs.items()}
        keys = list(jev_probs)  # only what Jev was offered: framing may have narrowed it
        human = prior["probabilities"]
        mixed = {k: (1 - w) * float(jev_probs[k]) + w * human.get(k, 0.0) for k in keys}
        total = sum(mixed.values()) or 1.0
        probs = {k: v / total for k, v in mixed.items()}

        if isinstance(q, ScoreQ):
            expected = sum(int(k) * p for k, p in probs.items())
            value = _score_to_value(expected, q.values)
            jev_value = _score_to_value(sum(int(k) * float(p) for k, p in jev_probs.items()), q.values)
        else:  # ChoiceQ
            value = max(probs, key=probs.get)
            jev_value = raw.get("choice")

        # Jev's confidence, moved the way the what-if on the page moves it, then
        # pulled towards the confidence the scientists put on their corrections.
        jev_conf = d.confidence if d.confidence is not None else max(jev_probs.values())
        top_before = max(float(p) for p in jev_probs.values()) or 1.0
        scaled = min(1.0, jev_conf * max(probs.values()) / top_before)
        confidence = scaled if prior["confidence"] is None \
            else (1 - w) * scaled + w * prior["confidence"]
        raw.update(probabilities={k: round(v, 4) for k, v in probs.items()},
                   jev_probabilities=jev_probs, confidence=round(confidence, 4))
        if isinstance(q, ChoiceQ):
            raw["choice"] = value

    raw["learned"] = {"n": prior["n"], "weight": round(w, 3), "jev_value": jev_value,
                      "jev_confidence": d.confidence}
    note = f"blended with {prior['n']} human correction{'s' if prior['n'] != 1 else ''} (weight {w:.2f})"
    if confidence < floor:
        return Decision(d.step, d.question, q.default, "default", confidence, raw,
                        f"confidence {confidence:.2f} below floor {floor:.2f} | {note}")
    return Decision(d.step, d.question, value, "jev", confidence, raw, note)
