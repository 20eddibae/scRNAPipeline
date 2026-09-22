"""The push guard has to actually catch things."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scan_secrets import scan_text, scan_worktree

REPO = Path(__file__).resolve().parent.parent


def test_catches_an_anthropic_style_key():
    hits = scan_text("x.py", 'KEY = "sk-ant-' + "A" * 40 + '"')
    assert hits


def test_catches_any_assigned_credential_not_just_known_prefixes():
    # Key-anchored: the value is arbitrary, the *name* is what trips it.
    hits = scan_text("x.env", "AI_GATEWAY_API_KEY=" + "q7x" * 8)
    assert hits


def test_allows_placeholders_and_indirection():
    assert not scan_text(".env.example", "AI_GATEWAY_API_KEY=")
    assert not scan_text("c.py", 'api_key = os.environ["AI_GATEWAY_API_KEY"]')
    assert not scan_text("r.md", "AI_GATEWAY_API_KEY=your-key-here")


def test_a_gitignored_env_file_is_not_reported():
    # .env holds a real key by design and can never be committed. Failing on it
    # every run would train people to push with --no-verify.
    env = REPO / ".env"
    if not env.exists():
        return
    assert not [h for h in scan_worktree(REPO) if h.startswith(".env")]


def test_the_repo_itself_is_clean():
    assert scan_worktree(REPO) == []
