"""Claude frames the decision; Jev makes it.

A step ships a *baseline* question - the vocabulary of options it can actually
dispatch on, plus a safe default. That baseline is generic by construction: it
was written before anyone saw this matrix. The planner hands it to Claude
together with the observed statistics and gets back the same question rephrased
for *this* run: options that do not apply here dropped, and each surviving option
described in terms of what it would do to this data.

Jev then picks among what Claude framed, with a calibrated probability.

The hard constraint is that Claude may only ever *narrow and reword*. Option keys
are the branch labels the step dispatches on, so an invented key would be a
decision the pipeline has no code for. Anything Claude returns that is not a
declared key is dropped, and any failure at all falls back to the baseline
question unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from .jev import ChoiceQ, NoulQ, Question, ScoreQ
from .state import RunState

PLANNER_PROMPT = """You are framing the decision points for one step of a
single-cell RNA-seq pipeline. A separate decision model (Jev) will answer the
questions you produce; you are not answering them.

Step: {step}

Observed state of this run:
{state}

Baseline questions, as written generically before anyone saw this data:
{questions}

Rewrite each question for THIS dataset:

- `instructions`: refer to the actual observed numbers and say what the decision
  turns on here. Do not state or hint at which option you would pick.
- For a choice question, rewrite each option's description to say what that
  option would do to THIS matrix. You may DROP an option that is clearly
  inapplicable here, as long as at least two remain.
- For a score question, rewrite the rungs of the scale. Keep the same number of
  rungs in the same order - they index pipeline settings you cannot see.
- You may NOT invent new option keys. The keys are branch labels in code.
- Be brief: at most two sentences of instructions, one sentence per option.

Return JSON only, no prose:
{{"questions": {{"<name>": {{"instructions": "...",
  "criteria": {{"<key>": "<what it does here>"}} }} }} }}

For a score question `criteria` is a LIST of rung descriptions, not an object."""


class QuestionPlanner:
    """Refines a step's baseline questions with Claude. Fails back to baseline."""

    def __init__(self, client: Any):
        self.client = client  # ClaudeClient
        self.log: list[dict[str, Any]] = []

    def refine(
        self, step: str, questions: dict[str, Question], state: RunState
    ) -> tuple[dict[str, Question], dict[str, str]]:
        """Returns (questions, per-question status).

        The status is not decoration. When the framing call fails -- a 429, a
        malformed reply -- these are the baseline questions, and a run log that
        still claims Claude framed them is lying about its own provenance.
        """
        if not questions:
            return questions, {}

        prompt = PLANNER_PROMPT.format(
            step=step,
            state=json.dumps(state.to_jev_state(), indent=2, default=str),
            questions=json.dumps(_describe(questions), indent=2),
        )
        try:
            payload = json.loads(_strip_fence(self.client.ask(prompt, max_tokens=4000)))
            refined = payload["questions"]
        except Exception as exc:
            reason = f"baseline (framing failed: {exc.__class__.__name__})"
            self.log.append({"step": step, "status": reason})
            return questions, {name: reason for name in questions}

        out: dict[str, Question] = {}
        notes: dict[str, str] = {}
        for name, question in questions.items():
            spec = refined.get(name)
            if not isinstance(spec, dict):
                out[name] = question
                notes[name] = "baseline (not returned by claude)"
                continue
            out[name], notes[name] = _apply_spec(question, spec)

        self.log.append({"step": step, "status": "refined", "questions": notes})
        return out, notes


def _describe(questions: dict[str, Question]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, q in questions.items():
        if isinstance(q, ChoiceQ):
            out[name] = {"type": "choice", "instructions": q.instructions,
                         "criteria": q.criteria}
        elif isinstance(q, NoulQ):
            out[name] = {"type": "boolean", "instructions": q.instructions}
        elif isinstance(q, ScoreQ):
            out[name] = {"type": "score", "instructions": q.instructions,
                         "criteria": q.criteria}
    return out


def _apply_spec(question: Question, spec: dict[str, Any]) -> tuple[Question, str]:
    """Take what is safe from Claude's spec; keep the baseline for the rest."""
    instructions = spec.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        instructions = question.instructions

    if isinstance(question, NoulQ):
        return NoulQ(instructions, question.default, question.threshold), "reworded"

    if isinstance(question, ChoiceQ):
        criteria = spec.get("criteria")
        if not isinstance(criteria, dict):
            return ChoiceQ(instructions, question.criteria, question.default), "reworded"
        # Narrowing only: unknown keys are branch labels the step cannot dispatch.
        kept = {k: str(v) for k, v in criteria.items() if k in question.criteria}
        dropped = [k for k in criteria if k not in question.criteria]
        if len(kept) < 2:
            return ChoiceQ(instructions, question.criteria, question.default), \
                "reworded (narrowing rejected: fewer than two options left)"
        default = question.default if question.default in kept else next(iter(kept))
        note = f"{len(kept)}/{len(question.criteria)} options"
        if dropped:
            note += f"; ignored invented key(s) {dropped}"
        if default != question.default:
            note += f"; default moved to {default!r}"
        return ChoiceQ(instructions, kept, default), note

    # ScoreQ: the rungs index pipeline settings, so the count and order are fixed.
    criteria = spec.get("criteria")
    if not isinstance(criteria, list) or len(criteria) != len(question.criteria):
        return ScoreQ(instructions, question.criteria, question.values,
                      question.default), "reworded (rung count fixed)"
    return ScoreQ(instructions, [str(c) for c in criteria], question.values,
                  question.default), "reworded rungs"


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return text.strip()
