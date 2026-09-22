"""The decision layer.

Every branch point in the pipeline is a typed question. Jev (TypeSafe's System
One model) answers it with a calibrated probability; if the answer lands below
the confidence floor we fall back to the step's declared default and say so in
the run log. `OfflineDecider` answers everything from defaults, which is what
makes the pipeline runnable with no keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .config import Settings
from .state import Decision, RunState


@dataclass
class ChoiceQ:
    """Pick one of `criteria` (option -> what it means)."""

    instructions: str
    criteria: dict[str, str]
    default: str


@dataclass
class NoulQ:
    """A yes/no question; Jev returns a probability."""

    instructions: str
    default: bool
    threshold: float = 0.5


@dataclass
class ScoreQ:
    """Place the data on an ordered scale; `values` are the pipeline-side knobs."""

    instructions: str
    criteria: list[str]
    values: list[Any]
    default: Any


Question = ChoiceQ | NoulQ | ScoreQ


class Decider(Protocol):
    def decide(
        self, step: str, questions: dict[str, Question], state: RunState
    ) -> dict[str, Decision]: ...


class OfflineDecider:
    """Takes every step's declared default. No network, fully deterministic."""

    def decide(
        self, step: str, questions: dict[str, Question], state: RunState
    ) -> dict[str, Decision]:
        out: dict[str, Decision] = {}
        for name, q in questions.items():
            out[name] = Decision(
                step=step,
                question=name,
                value=q.default,
                source="default",
                note="offline decider",
            )
        return out


class JevDecider:
    """Calls TypeSafe's System One model once per step, batching its questions."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None

    def _client_or_build(self):
        if self._client is None:
            from typesafe_sdk import TypeSafeClient  # imported late: optional dep

            kwargs: dict[str, Any] = {"api_key": self.settings.require_typesafe()}
            if self.settings.typesafe_base_url:
                kwargs["base_url"] = self.settings.typesafe_base_url
            self._client = TypeSafeClient(**kwargs)
        return self._client

    def decide(
        self, step: str, questions: dict[str, Question], state: RunState
    ) -> dict[str, Decision]:
        from typesafe_sdk import Choice, Noul, Score

        payload: dict[str, Any] = {}
        for name, q in questions.items():
            if isinstance(q, ChoiceQ):
                payload[name] = Choice(instructions=q.instructions, criteria=q.criteria)
            elif isinstance(q, NoulQ):
                payload[name] = Noul(instructions=q.instructions)
            elif isinstance(q, ScoreQ):
                payload[name] = Score(instructions=q.instructions, criteria=q.criteria)
            else:  # pragma: no cover - guarded by the union type
                raise TypeError(f"unsupported question type: {type(q)!r}")

        client = self._client_or_build()
        response = client.system_one(
            state=_jev_state(step, state), questions=payload, model=self.settings.jev_model
        )
        return {
            name: self._to_decision(step, name, q, response)
            for name, q in questions.items()
        }

    def _to_decision(
        self, step: str, name: str, q: Question, response: Any
    ) -> Decision:
        answer = _answer_for(response, name)
        if answer is None:
            return Decision(
                step=step,
                question=name,
                value=q.default,
                source="default",
                note="jev returned no answer for this question",
            )

        confidence = _confidence(answer)
        floor = self.settings.confidence_floor
        raw = _raw(answer)

        if isinstance(q, ChoiceQ):
            value = getattr(answer, "choice", None)
            if value not in q.criteria:
                return Decision(step, name, q.default, "default", confidence, raw,
                                f"jev returned unknown option {value!r}")
        elif isinstance(q, NoulQ):
            p = getattr(answer, "noul", None)
            if p is None:
                return Decision(step, name, q.default, "default", confidence, raw,
                                "jev returned no probability")
            p = float(p)
            confidence = confidence if confidence is not None else abs(p - 0.5) * 2
            value = p >= q.threshold
        else:  # ScoreQ
            score = getattr(answer, "score", None)
            if score is None:
                return Decision(step, name, q.default, "default", confidence, raw,
                                "jev returned no score")
            value = _score_to_value(float(score), q.values)

        if confidence is not None and confidence < floor:
            return Decision(step, name, q.default, "default", confidence, raw,
                            f"confidence {confidence:.2f} below floor {floor:.2f}")
        return Decision(step, name, value, "jev", confidence, raw)


def build_decider(settings: Settings) -> Decider:
    return OfflineDecider() if settings.jev_offline else JevDecider(settings)


# -- response shims ------------------------------------------------------
# The SDK exposes both `response.answers[name]` and the typed shortcuts
# `.choices` / `.nouls` / `.scores`. Read whichever is populated so a shape
# change in either does not take the pipeline down mid-demo.

def _answer_for(response: Any, name: str) -> Any | None:
    for attr in ("answers", "choices", "nouls", "scores"):
        bucket = getattr(response, attr, None)
        if bucket is None:
            continue
        try:
            if name in bucket:
                return bucket[name]
        except TypeError:
            continue
    return None


def _confidence(answer: Any) -> float | None:
    value = getattr(answer, "confidence", None)
    return float(value) if value is not None else None


def _raw(answer: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for attr in ("choice", "noul", "score", "confidence", "probabilities"):
        value = getattr(answer, attr, None)
        if value is not None:
            out[attr] = value
    return out


def _score_to_value(score: float, values: list[Any]) -> Any:
    """A Score answer is a fractional index into the criteria list."""
    if not values:
        raise ValueError("ScoreQ needs at least one value")
    idx = int(round(score))
    return values[max(0, min(idx, len(values) - 1))]


def _jev_state(step: str, state: RunState) -> dict[str, Any]:
    payload = state.to_jev_state()
    payload["step"] = step
    return payload
