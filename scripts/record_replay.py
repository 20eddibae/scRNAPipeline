"""Record one real run's event stream for `scrnapipeline.replay`.

    python scripts/record_replay.py pbmc3k [context]

Runs the pipeline exactly as `/run` does (same models, same records) and
writes every event, one JSON object per line, to
`src/scrnapipeline/resources/replay/<dataset>.jsonl`.
"""

import json
import os
import sys

os.environ["SCRNA_REPLAY"] = "0"  # compute it, do not replay an old recording

from scrnapipeline.replay import replay_path  # noqa: E402
from scrnapipeline.server import _events  # noqa: E402


def main():
    dataset = sys.argv[1] if len(sys.argv) > 1 else "pbmc3k"
    context = sys.argv[2] if len(sys.argv) > 2 else "human PBMC"
    out = replay_path(dataset)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".partial")
    with tmp.open("w") as fh:
        for frame in _events(dataset, context):
            event = json.loads(frame[len("data: "):])
            if event["type"] == "error":
                sys.exit("run failed at {}: {}".format(event.get("step"), event.get("message")))
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")
            print(event["type"], event.get("step", ""), flush=True)
    tmp.replace(out)
    print("wrote", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
