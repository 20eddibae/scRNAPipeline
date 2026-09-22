#!/usr/bin/env python3
"""Refuse to push anything that looks like a credential.

Key-anchored, not value-anchored: a tripwire built from a list of known key
prefixes only catches keys someone already thought to list. The last pattern
fires on any *_API_KEY / _TOKEN / _SECRET assigned a literal value.

Run with no arguments to scan the working tree, or `--outbound <remote_sha>` to
scan only the commits actually being pushed.

Deliberately written without type annotations or __future__ imports: this runs
from a git hook under whatever `python3` happens to be on PATH, which on a
cluster login node can be 3.6. A scanner that crashes is a scanner that gets
bypassed with --no-verify.
"""

import os
import re
import subprocess
import sys

PATTERNS = [
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("typesafe key", re.compile(r"\bts-(?:live|test)-[A-Za-z0-9_\-]{16,}")),
    ("aws key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("assigned credential", re.compile(
        r"(?i)\b[A-Z0-9_]*(?:API_KEY|APIKEY|SECRET|TOKEN|PASSWORD)\b\s*[:=]\s*"
        r"['\"]?(?!\s*$)(?!\$|\{|<|your|YOUR|xxx|XXX|\.\.\.|None|null|\"\"|''|os\.|process\.)"
        r"[A-Za-z0-9_\-]{12,}")),
]

SKIP_SUFFIXES = set([".png", ".pdf", ".h5ad", ".gz", ".npy", ".parquet", ".ipynb"])
SKIP_PARTS = set([".git", "__pycache__", "data", "runs", ".venv", "node_modules"])

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def scan_text(label, text):
    """Return one message per line that looks like it carries a credential."""
    hits = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if "scan_secrets" in label and "re.compile" in line:
            continue  # this file's own patterns
        for name, pattern in PATTERNS:
            if pattern.search(line):
                hits.append("%s:%d: possible %s" % (label, line_no, name))
    return hits


def _git_ignored(root, paths):
    """Ask git which of these it already ignores.

    Only unignored files can ever reach a commit, and .env holds a real key by
    design -- failing on it every run would train people to use --no-verify.
    """
    if not paths:
        return set()
    try:
        proc = subprocess.Popen(
            ["git", "-C", root, "check-ignore", "--stdin"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        out, _ = proc.communicate("\n".join(paths))
    except OSError:
        return set()
    return set(line for line in out.splitlines() if line)


def _candidates(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
        for filename in filenames:
            if os.path.splitext(filename)[1] in SKIP_SUFFIXES:
                continue
            found.append(os.path.join(dirpath, filename))
    return found


def scan_worktree(root=None):
    root = str(root or REPO_ROOT)
    candidates = _candidates(root)
    skip = _git_ignored(root, candidates)

    hits = []
    for path in candidates:
        if path in skip:
            continue
        try:
            with open(path, "r", errors="ignore") as handle:
                text = handle.read()
        except (OSError, IOError, UnicodeError):
            continue
        hits.extend(scan_text(os.path.relpath(path, root), text))
    return hits


def scan_outbound(remote_sha):
    """Scan the commits this push would actually add."""
    if not remote_sha or remote_sha.strip("0") == "":
        argv = ["git", "log", "-p", "--no-color", "HEAD"]
        label = "outbound[HEAD]"
    else:
        argv = ["git", "diff", "-U0", "--no-color", "%s..HEAD" % remote_sha]
        label = "outbound[%s..HEAD]" % remote_sha[:8]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True)
    out, _ = proc.communicate()
    return scan_text(label, out)


def main(argv):
    if len(argv) >= 3 and argv[1] == "--outbound":
        hits = scan_outbound(argv[2])
    else:
        hits = scan_worktree()

    if hits:
        sys.stderr.write("SECRET SCAN FAILED\n")
        for hit in hits:
            sys.stderr.write("  %s\n" % hit)
        sys.stderr.write("\nIf this is a genuine false positive: git push --no-verify\n")
        return 1
    sys.stdout.write("secret scan: clean\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
