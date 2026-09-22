"""The learning layer: corrections are stored, blended, and re-decided honestly."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scrnapipeline.feedback import FeedbackStore, LearnedDecider
from scrnapipeline.jev import ChoiceQ, NoulQ, ScoreQ
from scrnapipeline.state import Decision, RunState

FLOOR = 0.55
METHOD = ChoiceQ("which?", {"a": "", "b": "", "c": ""}, default="a")


class Fixed:
    """A decider that always returns the same Jev-shaped answers."""

    def __init__(self, decisions):
        self.decisions = decisions

    def decide(self, step, questions, state):
        return {n: Decision(**vars(self.decisions[n])) for n in questions}


def jev_choice(probs, confidence, choice):
    return Decision("normalize", "method", choice, "jev", confidence,
                    {"choice": choice, "confidence": confidence, "probabilities": probs})


def test_store_normalises_and_rejects_nonsense(tmp_path):
    store = FeedbackStore(tmp_path)
    saved = store.add({"step": "s", "question": "q", "probabilities": {"a": 0.3, "b": 0.3}})
    assert saved["probabilities"] == {"a": 0.5, "b": 0.5}
    for bad in ({"step": "s", "question": "q", "probabilities": {"a": 1}},
                {"step": "s", "question": "q", "probabilities": {"a": 1.5, "b": 0}},
                {"step": "", "question": "q", "probabilities": {"a": 1, "b": 0}},
                {"step": "s", "question": "q", "probabilities": {"a": 0.5, "b": 0.5}, "confidence": 2}):
        with pytest.raises(ValueError):
            store.add(bad)
    assert len(store.entries()) == 1


def test_no_feedback_means_jev_untouched(tmp_path):
    base = Fixed({"method": jev_choice({"a": 0.2, "b": 0.7, "c": 0.1}, 0.8, "b")})
    out = LearnedDecider(base, FeedbackStore(tmp_path), FLOOR).decide(
        "normalize", {"method": METHOD}, RunState("r", "d"))["method"]
    assert (out.value, out.source, out.confidence) == ("b", "jev", 0.8)
    assert "learned" not in out.raw


def test_corrections_pull_the_pick_and_record_provenance(tmp_path):
    store = FeedbackStore(tmp_path)
    base = Fixed({"method": jev_choice({"a": 0.2, "b": 0.7, "c": 0.1}, 0.8, "b")})
    decider = LearnedDecider(base, store, FLOOR)
    ask = lambda: decider.decide("normalize", {"method": METHOD}, RunState("r", "d"))["method"]

    store.add({"step": "normalize", "question": "method",
               "probabilities": {"a": 0.9, "b": 0.05, "c": 0.05}, "confidence": 0.9})
    one = ask()  # weight 1/3: Jev's b still leads (0.48 vs 0.43)
    assert one.value == "b" and one.raw["learned"]["n"] == 1
    assert one.raw["learned"]["weight"] == pytest.approx(1 / 3, abs=1e-3)
    assert one.raw["jev_probabilities"] == {"a": 0.2, "b": 0.7, "c": 0.1}
    assert sum(one.raw["probabilities"].values()) == pytest.approx(1.0, abs=1e-3)  # stored at 4 dp

    store.add({"step": "normalize", "question": "method",
               "probabilities": {"a": 0.9, "b": 0.05, "c": 0.05}, "confidence": 0.9})
    two = ask()  # weight 1/2: the scientists' a now leads
    assert two.value == "a" and two.source == "jev"
    assert two.raw["learned"]["jev_value"] == "b"
    assert "2 human corrections" in two.note


def test_low_learned_confidence_falls_back_to_the_default(tmp_path):
    store = FeedbackStore(tmp_path)
    for _ in range(4):
        store.add({"step": "normalize", "question": "method",
                   "probabilities": {"a": 0.1, "b": 0.1, "c": 0.8}, "confidence": 0.1})
    base = Fixed({"method": jev_choice({"a": 0.2, "b": 0.7, "c": 0.1}, 0.8, "b")})
    out = LearnedDecider(base, store, FLOOR).decide(
        "normalize", {"method": METHOD}, RunState("r", "d"))["method"]
    assert out.source == "default" and out.value == "a"
    assert "below floor" in out.note


def test_noul_and_score_use_the_backend_rules(tmp_path):
    store = FeedbackStore(tmp_path)
    for _ in range(2):
        store.add({"step": "qc", "question": "flag", "probabilities": {"true": 0.95, "false": 0.05}})
        store.add({"step": "features", "question": "n_hvg",
                   "probabilities": {"0": 0.9, "1": 0.1, "2": 0.0}})
    flag = NoulQ("doublets?", default=False)
    noul = Fixed({"flag": Decision("qc", "flag", False, "default", 0.04, {"noul": 0.48})})
    out = LearnedDecider(noul, store, FLOOR).decide("qc", {"flag": flag}, RunState("r", "d"))["flag"]
    # (1 - 1/2) * 0.48 + 1/2 * 0.95 = 0.715 -> yes, but |0.715 - 0.5| * 2 = 0.43 is
    # under the floor, so the declared default (no) is what runs
    assert out.raw["noul"] == pytest.approx(0.715)
    assert out.confidence == pytest.approx(0.43)
    assert (out.value, out.source) == (False, "default")

    hvg = ScoreQ("how many?", ["n", "s", "b"], [1000, 2000, 4000], default=2000)
    score = Fixed({"n_hvg": Decision("features", "n_hvg", 2000, "jev", 0.9,
                                     {"score": 1.0, "confidence": 0.9,
                                      "probabilities": {"0": 0.05, "1": 0.9, "2": 0.05}})})
    ask = lambda: LearnedDecider(score, store, FLOOR).decide(
        "features", {"n_hvg": hvg}, RunState("r", "d"))["n_hvg"]
    # two corrections (w = 1/2): expected rung 0.55, still rounds to 2000
    assert ask().value == 2000
    for _ in range(2):
        store.add({"step": "features", "question": "n_hvg",
                   "probabilities": {"0": 0.9, "1": 0.1, "2": 0.0}})
    # four (w = 2/3): expected rung 0.40 -> the narrow 1000-gene setting
    assert ask().value == 1000


def test_int_keyed_rungs_from_the_sdk_still_blend(tmp_path):
    """The live SDK returns score probabilities keyed 0/1/2, not "0"/"1"/"2"."""
    store = FeedbackStore(tmp_path)
    for _ in range(2):
        store.add({"step": "features", "question": "n_hvg",
                   "probabilities": {"0": 0.85, "1": 0.15, "2": 0.0}, "confidence": 0.8})
    hvg = ScoreQ("how many?", ["n", "s", "b"], [1000, 2000, 4000], default=2000)
    score = Fixed({"n_hvg": Decision("features", "n_hvg", 2000, "jev", 0.95,
                                     {"score": 0.99, "confidence": 0.95,
                                      "probabilities": {0: 0.02, 1: 0.97, 2: 0.01}})})
    out = LearnedDecider(score, store, FLOOR).decide(
        "features", {"n_hvg": hvg}, RunState("r", "d"))["n_hvg"]
    assert out.raw["probabilities"]["0"] == pytest.approx(0.435, abs=1e-3)
    assert out.raw["probabilities"] != out.raw["jev_probabilities"]
