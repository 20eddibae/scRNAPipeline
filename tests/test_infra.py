"""Infra-level tests: no network, no scanpy, no keys."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scrnapipeline.jev import ChoiceQ, NoulQ, OfflineDecider, ScoreQ, _score_to_value
from scrnapipeline.registry import DEFAULT_ORDER
from scrnapipeline.state import Decision, RunState, StepRecord


def test_default_order_is_dependency_consistent():
    # Every step's dependencies appear before it in the canonical order.
    from scrnapipeline.registry import build_steps

    steps = build_steps()
    assert set(steps) == set(DEFAULT_ORDER)
    seen: list[str] = []
    for name in DEFAULT_ORDER:
        for need in steps[name].needs:
            assert need in seen, f"{name} needs {need}, which runs later"
        seen.append(name)


def test_offline_decider_returns_declared_defaults():
    questions = {
        "stringency": ChoiceQ("pick one", {"a": "A", "b": "B"}, default="b"),
        "doublets": NoulQ("yes or no", default=True),
        "n_hvg": ScoreQ("how many", ["few", "many"], [1000, 4000], default=2000),
    }
    decisions = OfflineDecider().decide("qc", questions, RunState("r", "pbmc3k"))
    assert decisions["stringency"].value == "b"
    assert decisions["doublets"].value is True
    assert decisions["n_hvg"].value == 2000
    assert all(d.source == "default" for d in decisions.values())


def test_score_maps_to_the_nearest_knob():
    assert _score_to_value(0.0, [0.4, 1.0, 1.6]) == 0.4
    assert _score_to_value(1.4, [0.4, 1.0, 1.6]) == 1.0
    assert _score_to_value(9.0, [0.4, 1.0, 1.6]) == 1.6  # clamped


def test_run_record_round_trips_and_carries_no_credentials(tmp_path):
    state = RunState("run-1", "pbmc3k")
    state.observe(n_cells=2700, n_genes=32738)
    state.record_decision(Decision("qc", "stringency", "standard", "jev", 0.81))
    state.record_step(StepRecord("qc", "ok", 1.2, {"cells_after": 2638}))

    path = state.save(tmp_path / "run.json")
    payload = json.loads(path.read_text())

    assert payload["decisions"][0]["source"] == "jev"
    assert payload["steps"][0]["summary"]["cells_after"] == 2638
    # The run record is an artifact we publish; it must never carry a key.
    blob = path.read_text().lower()
    for token in ("api_key", "sk-ant-", "authorization", "bearer "):
        assert token not in blob


def test_jev_state_is_summary_only():
    state = RunState("run-1", "pbmc3k")
    state.observe(n_cells=2700)
    state.record_decision(Decision("qc", "stringency", "strict", "jev", 0.9))
    payload = state.to_jev_state()
    assert payload["observations"]["n_cells"] == 2700
    assert payload["prior_decisions"]["qc.stringency"] == "strict"
    assert "adata" not in payload and "counts" not in payload
