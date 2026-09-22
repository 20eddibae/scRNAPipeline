#!/usr/bin/env python3
"""Refuse to push anything that looks like a credential.

Key-anchored, not value-anchored: a tripwire built from a list of known keys only
catches keys someone already typed. Run with no arguments to scan the working
tree, or `--outbound <remote_sha>` to scan the commits actually being pushed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("typesafe key", re.compile(r"\bts-(?:live|test)-[A-Za-z0-9_\-]{16,}")),
    ("aws key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # Key-anchored: any *_API_KEY / _TOKEN / _SECRET assigned a literal value.
    ("assigned credential", re.compile(
        r"(?i)\b[A-Z0-9_]*(?:API_KEY|APIKEY|SECRET|TOKEN|PASSWORD)\b\s*[:=]\s*"
        r"['\"]?(?!\s*$)(?!\$|\{|<|your|YOUR|xxx|XXX|\.\.\.|None|null|\"\"|''|os\.|process\.)"
        r"[A-Za-z0-9_\-]{12,}")),
]

SKIP_SUFFIXES = {".png", ".pdf", ".h5ad", ".gz", ".npy", ".parquet", ".ipynb"}
SKIP_PARTS = {".git", "__pycache__", "data", "runs", ".venv", "node_modules"}


def scan_text(label: str, text: str) -> list[str]:
    hits = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if "scan_secrets" in label and "re.compile" in line:
            continue  # this file's own patterns
        for name, pattern in PATTERNS:
            if pattern.search(line):
                hits.append(f"{label}:{line_no}: possible {name}")
    return hits


def ignored(root: Path, paths: list[Path]) -> set[Path]:
    """Ask git which of these it is already ignoring.

    A guard that fires on `.env` every single run is a guard people learn to
    skip with --no-verify. Only unignored files can ever reach a commit, so only
    those are worth failing over.
    """
    if not paths:
        return set()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--stdin"],
            input="\n".join(str(p) for p in paths),
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return set()
    return {Path(line) for line in proc.stdout.splitlines() if line}


def scan_worktree(root: Path) -> list[str]:
    candidates = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix not in SKIP_SUFFIXES
        and not (SKIP_PARTS & set(path.relative_to(root).parts))
    ]
    skip = ignored(root, candidates)

    hits = []
    for path in candidates:
        if path in skip:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        hits.extend(scan_text(str(path.relative_to(root)), text))
    return hits


def scan_outbound(remote_sha: str) -> list[str]:
    rng = "HEAD" if remote_sha.strip("0") == "" else f"{remote_sha}..HEAD"
    diff = subprocess.run(
        ["git", "diff", "-U0", rng] if ".." in rng else ["git", "log", "-p", "--no-color", rng],
        capture_output=True, text=True, check=False,
    )
    return scan_text(f"outbound[{rng}]", diff.stdout)


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--outbound":
        hits = scan_outbound(argv[2])
    else:
        hits = scan_worktree(Path(__file__).resolve().parent.parent)

    if hits:
        print("SECRET SCAN FAILED", file=sys.stderr)
        for hit in hits:
            print(f"  {hit}", file=sys.stderr)
        print("\nIf this is a false positive: git push --no-verify", file=sys.stderr)
        return 1
    print("secret scan: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
