"""Wiring: build the steps, run them either scripted or agent-driven."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .claude import ClaudeClient, Orchestrator
from .config import Settings, load_settings
from .jev import OverrideDecider, build_decider
from .planner import QuestionPlanner
from .registry import DEFAULT_ORDER, build_steps, tool_schemas, unmet_dependencies
from .state import RunState, new_run_id

TASK = """Analyse the dataset '{dataset}' end to end and report how well the
result matches the ground-truth labels. Start by inspecting the state."""


class Pipeline:
    def __init__(self, dataset: str, settings: Settings | None = None,
                 context: str = "human PBMC", run_id: str | None = None,
                 overrides: dict[str, Any] | None = None, plan: bool = True):
        self.settings = settings or load_settings()
        self.dataset = dataset
        self.state = RunState(run_id=run_id or new_run_id(), dataset=dataset)
        self.decider = build_decider(self.settings)
        if overrides:
            self.decider = OverrideDecider(self.decider, overrides)
        self.annotator = None if self.settings.claude_offline else ClaudeClient(self.settings)
        # Claude frames each step's decision points for this dataset before Jev
        # answers them. Offline, the steps' baseline questions are used as-is.
        self.planner = None if (self.annotator is None or not plan) \
            else QuestionPlanner(self.annotator)
        self.steps = build_steps(annotator=self.annotator, context=context,
                                 settings=self.settings)
        self.adata: Any = None

    # -- the two drivers ---------------------------------------------------
    def run_scripted(self, order: tuple[str, ...] = DEFAULT_ORDER) -> RunState:
        """Fixed order, Jev still answering every decision point."""
        for name in order:
            self.execute(name)
        return self.state

    def run_agent(self) -> RunState:
        """Claude chooses the order; Jev still answers inside each step."""
        if self.settings.claude_offline:
            raise RuntimeError("agent mode needs Claude; unset CLAUDE_OFFLINE")
        orchestrator = Orchestrator(
            client=self.annotator,
            tools=tool_schemas(self.steps),
            dispatch=self.dispatch,
        )
        summary = orchestrator.run(TASK.format(dataset=self.dataset), self.state)
        self.state.artifacts["agent_summary"] = summary
        return self.state

    # -- tool surface ------------------------------------------------------
    def dispatch(self, tool_name: str, payload: dict[str, Any]) -> str:
        if tool_name == "inspect_state":
            return json.dumps(self.state.to_dict(), indent=2, default=str)[:12000]
        if tool_name.startswith("run_"):
            return json.dumps(self.execute(tool_name[4:]), default=str)
        raise ValueError(f"unknown tool {tool_name!r}")

    def execute(self, name: str) -> dict[str, Any]:
        step = self.steps.get(name)
        if step is None:
            raise ValueError(f"unknown step {name!r}")
        missing = unmet_dependencies(step, self.state.completed())
        if missing:
            raise ValueError(f"step {name!r} needs {missing} to run first")

        self.adata = step.run(self.adata, self.state, self.decider, self.planner)
        return self.state.steps[-1].summary

    # -- outputs -----------------------------------------------------------
    def save(self, out_dir: str | Path | None = None) -> Path:
        out = Path(out_dir or self.settings.run_dir) / self.state.run_id
        out.mkdir(parents=True, exist_ok=True)
        self.state.save(out / "run.json")
        if self.adata is not None:
            path = out / "processed.h5ad"
            self.adata.write_h5ad(path)
            self.state.artifacts["h5ad"] = str(path)
            self.state.save(out / "run.json")
        return out
