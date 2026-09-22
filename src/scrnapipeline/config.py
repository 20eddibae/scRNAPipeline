"""Environment-driven configuration.

The hackathon hands out one gateway key. Either provider can also be pointed at
its own key/base_url; those win over the gateway fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# TypeSafe clients reach Jev through the Vercel AI Gateway at this base; the SDK
# appends /v1/systemone. The Vercel key doubles as the TypeSafe key.
VERCEL_TYPESAFE_BASE = "https://ai-gateway.vercel.sh/typesafe"


@dataclass
class Settings:
    # A gateway key is NOT an Anthropic key. Fall back to it only when a base URL
    # says a gateway is deliberately in the path -- otherwise a `vck_...` key
    # would be sent to api.anthropic.com and come back as a bare 401.
    anthropic_api_key: str | None = field(
        default_factory=lambda: _env("ANTHROPIC_API_KEY")
        or (_env("AI_GATEWAY_API_KEY") if _env("ANTHROPIC_BASE_URL") else None)
    )
    anthropic_base_url: str | None = field(
        default_factory=lambda: _env("ANTHROPIC_BASE_URL")
    )
    claude_model: str = field(
        default_factory=lambda: _env("CLAUDE_MODEL", default="claude-opus-5")
    )
    # The one-shot calls (question framing, marker annotation) sit on the
    # critical path of every step, one per step. Measured on the gateway for one
    # framing prompt: opus-5 18-28 s, sonnet-5 7 s, haiku-4-5 3.5-9 s. Sonnet and
    # Haiku tie on annotation accuracy (experiments/head_to_head.py), so the
    # orchestrator keeps `claude_model` and these calls default to Sonnet.
    fast_model: str = field(
        default_factory=lambda: _env("CLAUDE_FAST_MODEL", default="claude-sonnet-5")
    )
    # An API key that is not scoped to a workspace must name one per request, or
    # every call returns a 400 that reads like a malformed request rather than a
    # missing header.
    # Tried in order when the primary model is rate-limited on the gateway.
    fallback_models: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            m.strip() for m in
            _env("CLAUDE_FALLBACK_MODELS", default="claude-sonnet-5,claude-haiku-4-5").split(",")
            if m.strip()
        )
    )
    anthropic_workspace_id: str | None = field(
        default_factory=lambda: _env("ANTHROPIC_WORKSPACE_ID")
    )
    # The server-side refusal fallback is a beta parameter. Off by default because
    # a third-party gateway may reject unknown betas; flip it on against the
    # first-party API.
    claude_server_fallbacks: bool = field(
        default_factory=lambda: _flag("CLAUDE_SERVER_FALLBACKS", False)
    )

    # The Vercel gateway key is the TypeSafe key, so this fallback is correct.
    typesafe_api_key: str | None = field(
        default_factory=lambda: _env("TYPESAFE_API_KEY", "AI_GATEWAY_API_KEY")
    )
    typesafe_base_url: str | None = field(
        default_factory=lambda: _env("TYPESAFE_BASE_URL", default=VERCEL_TYPESAFE_BASE)
    )
    jev_model: str = field(default_factory=lambda: _env("JEV_MODEL", default="jev-latest"))

    # Offline modes fall back to the hard-coded defaults declared by each step,
    # so the whole pipeline runs with no network access.
    jev_offline: bool = field(default_factory=lambda: _flag("JEV_OFFLINE", False))
    claude_offline: bool = field(default_factory=lambda: _flag("CLAUDE_OFFLINE", False))

    # Below this confidence a Jev answer is not trusted and the step's declared
    # default is used instead. Every such event is recorded in the run log.
    confidence_floor: float = field(
        default_factory=lambda: float(os.environ.get("JEV_CONFIDENCE_FLOOR", "0.55"))
    )

    # Blend Jev's answers with the corrections scientists saved from the page
    # (feedback.py). Off means Jev's answers alone.
    learn_from_feedback: bool = field(default_factory=lambda: _flag("KRINO_LEARN", True))

    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("SCRNA_DATA_DIR", "data"))
    )
    run_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("SCRNA_RUN_DIR", "runs"))
    )

    def require_anthropic(self) -> str:
        if not self.anthropic_api_key:
            raise RuntimeError(
                "No Anthropic credential. Set ANTHROPIC_API_KEY (or AI_GATEWAY_API_KEY "
                "together with ANTHROPIC_BASE_URL to route through a gateway), or run "
                "with CLAUDE_OFFLINE=1."
            )
        return self.anthropic_api_key

    def require_typesafe(self) -> str:
        if not self.typesafe_api_key:
            raise RuntimeError(
                "No TypeSafe credential. Set TYPESAFE_API_KEY or AI_GATEWAY_API_KEY, "
                "or run with JEV_OFFLINE=1."
            )
        return self.typesafe_api_key


def load_settings() -> Settings:
    _load_dotenv()
    return Settings()


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env reader so local runs need no extra dependency."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
