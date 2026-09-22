"""Step contract.

A step declares (a) the typed questions it wants answered before it runs and
(b) what it does with the answers. The decision layer sits between the two, so
swapping Jev for defaults changes nothing about the science code.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from ..jev import Decider, Question
from ..state import RunState, StepRecord


class Step(ABC):
    name: str = ""
    description: str = ""
    needs: tuple[str, ...] = ()

    def questions(self, adata: Any, state: RunState) -> dict[str, Question]:
        """Decision points this step wants resolved before `apply`."""
        return {}

    @abstractmethod
    def apply(
        self, adata: Any, state: RunState, choices: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        """Do the work. Returns (adata, summary)."""

    def run(self, adata: Any, state: RunState, decider: Decider) -> Any:
        started = time.time()
        questions = self.questions(adata, state)
        choices: dict[str, Any] = {}
        if questions:
            for name, decision in decider.decide(self.name, questions, state).items():
                state.record_decision(decision)
                choices[name] = decision.value
        try:
            adata, summary = self.apply(adata, state, choices)
        except Exception as exc:  # keep the run log honest about failures
            state.record_step(
                StepRecord(self.name, "error", time.time() - started, {}, repr(exc))
            )
            raise
        summary = {**{k: _jsonable(v) for k, v in choices.items()}, **summary}
        state.record_step(
            StepRecord(self.name, "ok", round(time.time() - started, 2), summary)
        )
        return adata


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
