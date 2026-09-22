"""The run record: what happened, and who decided it."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Decision:
    """One typed decision at one branch point."""

    step: str
    question: str
    value: Any
    source: str  # "jev" | "default" | "override"
    confidence: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass
class StepRecord:
    step: str
    status: str  # "ok" | "skipped" | "error"
    seconds: float
    summary: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class RunState:
    """Everything the agent and Jev are allowed to see about the run so far.

    Deliberately small and JSON-serialisable: it is both the Jev `state` payload
    and the provenance record written next to the outputs.
    """

    run_id: str
    dataset: str
    obs: dict[str, Any] = field(default_factory=dict)  # observed facts about the data
    decisions: list[Decision] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    # -- observations -----------------------------------------------------
    def observe(self, **facts: Any) -> None:
        self.obs.update(facts)

    def record_decision(self, decision: Decision) -> None:
        self.decisions.append(decision)

    def record_step(self, record: StepRecord) -> None:
        self.steps.append(record)

    def completed(self) -> list[str]:
        return [s.step for s in self.steps if s.status == "ok"]

    def decision_value(self, step: str, question: str, default: Any = None) -> Any:
        for d in reversed(self.decisions):
            if d.step == step and d.question == question:
                return d.value
        return default

    # -- serialisation ----------------------------------------------------
    def to_jev_state(self) -> dict[str, Any]:
        """Compact view handed to Jev as the decision `state`.

        Jev is text-in / typed-decision-out, so keep this to summary statistics
        and prior choices, never raw matrices.
        """
        return {
            "dataset": self.dataset,
            "observations": self.obs,
            "completed_steps": self.completed(),
            "prior_decisions": {
                f"{d.step}.{d.question}": d.value for d in self.decisions
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dataset": self.dataset,
            "obs": self.obs,
            "decisions": [asdict(d) for d in self.decisions],
            "steps": [asdict(s) for s in self.steps],
            "artifacts": self.artifacts,
            "metrics": self.metrics,
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))
        return path


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"
