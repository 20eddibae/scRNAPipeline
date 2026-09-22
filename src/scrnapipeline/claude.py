"""Claude, in two roles: one-shot annotator and pipeline orchestrator.

The orchestrator is a manual tool-use loop rather than the SDK's tool runner.
Two reasons: every tool call is appended to the run log as provenance, and the
same loop body runs in `CLAUDE_OFFLINE=1` mode against a fixed step order, so
the pipeline is demoable with no API access.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .config import Settings
from .state import RunState

SYSTEM_PROMPT = """You orchestrate a single-cell RNA-seq analysis pipeline.

You decide which step runs next and when the run is done. You do NOT choose
filtering thresholds, normalization methods, gene counts or clustering
resolution - a separate decision model (Jev) answers those inside each step from
the observed statistics. Your job is sequencing and judgement about the run as a
whole.

Rules:
- Respect declared dependencies; call inspect_state if unsure what has run.
- The usual order is load, qc, normalize, features, integrate, cluster,
  annotate, evaluate. Deviate only for a stated reason.
- Integration is a no-op on single-batch data; running it is harmless.
- Call finish once evaluate has produced metrics."""


# How long a model that returned 429 is tried last rather than first.
THROTTLE_SECONDS = 120.0


class ClaudeClient:
    """Thin wrapper over the Messages API, pointed at the hackathon gateway."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        self.last_model: str | None = None
        self._throttled: dict[str, float] = {}  # model -> monotonic time it may lead again

    def _client_or_build(self):
        if self._client is None:
            import anthropic

            kwargs: dict[str, Any] = {"api_key": self.settings.require_anthropic()}
            if self.settings.anthropic_base_url:
                kwargs["base_url"] = self.settings.anthropic_base_url
            if self.settings.anthropic_workspace_id:
                kwargs["default_headers"] = {
                    "anthropic-workspace-id": self.settings.anthropic_workspace_id
                }
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def _extra(self) -> dict[str, Any]:
        if not self.settings.claude_server_fallbacks:
            return {}
        # Server-side refusal fallback: routes by refusal category instead of a
        # model list. Beta, so it is opt-in - a gateway may reject the flag.
        return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

    def ask(self, prompt: str, max_tokens: int = 4000) -> str:
        """One-shot text answer. Used for framing and marker annotation.

        The gateway intermittently returns 429 "No access to this model at this
        time" for a given model while others are free, so a rate limit here is
        not a reason to wait -- it is a reason to try the next model. Without
        this, every framing call during a throttled window silently falls back
        to the baseline question.
        """
        import anthropic

        models = [self.settings.fast_model]
        for candidate in self.settings.fallback_models:
            if candidate not in models:
                models.append(candidate)
        # A model that 429'd recently goes to the back rather than being tried
        # first again: each attempt on it costs a round trip before failing over.
        now = time.monotonic()
        models.sort(key=lambda m: self._throttled.get(m, 0.0) > now)

        last: Exception | None = None
        for i, model in enumerate(models):
            # The SDK's own retry backs off and re-asks the SAME model on a 429,
            # which is ~20 s spent on a model the gateway has said no to. Fail
            # over at once instead; only the last candidate keeps SDK retries.
            client = self._client_or_build()
            if i < len(models) - 1:
                client = client.with_options(max_retries=0)
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    thinking={"type": "adaptive"},
                    messages=[{"role": "user", "content": prompt}],
                    **self._extra(),
                )
            except anthropic.RateLimitError as exc:
                self._throttled[model] = time.monotonic() + THROTTLE_SECONDS
                last = exc
                continue
            if response.stop_reason == "refusal":
                raise RuntimeError(f"request refused: {response.stop_details}")
            self.last_model = model
            return "".join(b.text for b in response.content if b.type == "text")
        raise last if last else RuntimeError("no model available")

    def messages(self, **kwargs: Any) -> Any:
        return self._client_or_build().messages.create(**kwargs, **self._extra())


class Orchestrator:
    """Agentic loop: Claude calls step tools until it calls `finish`."""

    def __init__(
        self,
        client: ClaudeClient,
        tools: list[dict[str, Any]],
        dispatch: Callable[[str, dict[str, Any]], str],
        max_turns: int = 24,
    ):
        self.client = client
        self.tools = tools
        self.dispatch = dispatch
        self.max_turns = max_turns

    def run(self, task: str, state: RunState) -> str:
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        final = ""

        for _ in range(self.max_turns):
            response = self.client.messages(
                model=self.client.settings.claude_model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                tools=self.tools,
                messages=messages,
            )

            if response.stop_reason == "refusal":
                raise RuntimeError(f"request refused: {response.stop_details}")

            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.type == "tool_use"]

            if response.stop_reason == "end_turn" or not tool_uses:
                final = "".join(b.text for b in response.content if b.type == "text")
                break

            results = []
            for block in tool_uses:
                if block.name == "finish":
                    final = block.input.get("summary", "")
                    return final
                try:
                    content = self.dispatch(block.name, dict(block.input))
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": content}
                    )
                except Exception as exc:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"{exc.__class__.__name__}: {exc}",
                            "is_error": True,
                        }
                    )
            messages.append({"role": "user", "content": results})
        else:
            final = f"stopped after {self.max_turns} turns without finishing"

        return final
