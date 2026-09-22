"""The step registry: canonical order, dependency check, tool schemas."""

from __future__ import annotations

from typing import Any

from .steps import (
    AnnotateStep,
    ClusterStep,
    EvaluateStep,
    FeatureStep,
    IntegrateStep,
    LoadStep,
    NormalizeStep,
    QCStep,
    Step,
)

DEFAULT_ORDER = (
    "load",
    "qc",
    "normalize",
    "features",
    "integrate",
    "cluster",
    "annotate",
    "evaluate",
)


def build_steps(annotator: Any = None, context: str = "human PBMC") -> dict[str, Step]:
    steps: list[Step] = [
        LoadStep(),
        QCStep(),
        NormalizeStep(),
        FeatureStep(),
        IntegrateStep(),
        ClusterStep(),
        AnnotateStep(annotator=annotator, context=context),
        EvaluateStep(),
    ]
    return {step.name: step for step in steps}


def unmet_dependencies(step: Step, completed: list[str]) -> list[str]:
    return [need for need in step.needs if need not in completed]


def tool_schemas(steps: dict[str, Step]) -> list[dict[str, Any]]:
    """One Claude tool per step, plus the two control tools.

    Claude picks which step runs next and when the run is finished; it never sets
    the numeric parameters - those come back from Jev inside the step.
    """
    tools = [
        {
            "name": f"run_{name}",
            "description": (
                f"{step.description} Requires: "
                f"{', '.join(step.needs) if step.needs else 'nothing'}. "
                "Parameters inside this step are decided by the Jev decision model, "
                "not by you."
            ),
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
        for name, step in steps.items()
    ]
    tools.append(
        {
            "name": "inspect_state",
            "description": "Read the current run state: observations, decisions, step records, metrics.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
    )
    tools.append(
        {
            "name": "finish",
            "description": "End the run and report. Call once evaluation has produced metrics.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Two or three sentences on what was run and what the numbers say.",
                    }
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        }
    )
    return tools
