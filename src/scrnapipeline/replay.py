"""Canned runs: replay a recorded event stream instead of computing it.

For a dataset listed in `REPLAYED`, `/run` streams the events a real run of
that dataset emitted - the same `start` / `step_start` / `step_done` / `done`
frames, carrying the same records - with a short pause per step so the page
still lights up one step at a time. Nothing is computed and no model is
called, so the only latency is the page's own animation. Every other dataset
runs live.

Record a stream with `python scripts/record_replay.py pbmc3k`. Set
`SCRNA_REPLAY=0` to run the replayed datasets live again.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterator

REPLAY_DIR = Path(__file__).parent / "resources" / "replay"
REPLAYED = ("pbmc3k",)


def replay_path(dataset: str) -> Path:
    return REPLAY_DIR / f"{dataset}.jsonl"


def has_replay(dataset: str) -> bool:
    if os.environ.get("SCRNA_REPLAY", "1") == "0":
        return False
    return dataset in REPLAYED and replay_path(dataset).is_file()


def replay(dataset: str, sse) -> Iterator[str]:
    """Yield the recorded frames of `dataset`, paced like a quick live run."""
    step_seconds = float(os.environ.get("SCRNA_REPLAY_STEP_SECONDS", "1.2"))
    started = time.time()
    with replay_path(dataset).open() as fh:
        for line in fh:
            event = json.loads(line)
            kind = event.pop("type")
            if kind == "step_done":
                time.sleep(step_seconds)
            if kind == "done":
                # the recorded compute time would claim work this stream did not do
                event["seconds"] = round(time.time() - started, 1)
            yield sse(kind, **event)
