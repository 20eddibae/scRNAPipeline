"""The act phase: run the model Jev chose, locally or on Modal.

Each stage of the pipeline is the same three beats:

    1. Claude builds the typed context and the candidate answers   (planner.py)
    2. Jev picks among them, with a calibrated probability         (jev.py)
    3. the chosen model runs                                       (here)

Beat 3 is a dispatch, not a branch of hardcoded logic: a decision can select a
*model*, and some models want a GPU. An executor hides where that happens. With
`MODAL_REMOTE=1` the work is sent to a Modal function; otherwise it runs
in-process. The step does not know or care which.
"""

from __future__ import annotations

import os
from typing import Any, Callable

MODAL_APP = os.environ.get("MODAL_APP_NAME", "krino")


def remote_enabled() -> bool:
    return os.environ.get("MODAL_REMOTE", "").strip().lower() in {"1", "true", "yes", "on"}


class Executor:
    """Runs a named model. Falls back to local execution with the reason kept."""

    def __init__(self, local: dict[str, Callable[..., Any]],
                 remote_fn: str = "run_model"):
        self.local = local
        self.remote_fn = remote_fn
        self.trace: list[dict[str, Any]] = []

    def run(self, model: str, *args: Any, **kwargs: Any) -> tuple[Any, str]:
        """Returns (result, where) -- `where` is recorded in the run log."""
        if remote_enabled():
            try:
                import modal

                fn = modal.Function.from_name(MODAL_APP, self.remote_fn)
                result = fn.remote(model, *args, **kwargs)
                self.trace.append({"model": model, "where": "modal"})
                return result, "modal"
            except Exception as exc:
                # A GPU that is not there is a reason to say so, not to crash a
                # demo. The fallback is recorded, never silent.
                where = f"local (modal unavailable: {exc.__class__.__name__})"
        else:
            where = "local"

        if model not in self.local:
            raise ValueError(
                f"no local implementation for {model!r}; have {sorted(self.local)}"
            )
        result = self.local[model](*args, **kwargs)
        self.trace.append({"model": model, "where": where})
        return result, where
