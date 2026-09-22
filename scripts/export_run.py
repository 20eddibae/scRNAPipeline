"""Turn a finished run into the record the web demo reads.

    python scripts/export_run.py runs/<run_id> web/data/run-demo.json

`run.json` holds the decisions, the step records and the metrics. What it does
not hold is anything to plot: the embedding, the per-cell QC values and the
cluster assignment live in `processed.h5ad`. This joins the two.

The record itself is assembled by `scrnapipeline.webrecord`, which is also what
the live server uses, so a replayed run and a streamed one are the same shape.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="export_run")
    parser.add_argument("run_dir", help="runs/<run_id>")
    parser.add_argument("out", help="destination .json (e.g. web/data/run-demo.json)")
    parser.add_argument("--max-cells", type=int, default=4000)
    parser.add_argument("--demo", action="store_true",
                        help="mark the record as a demo run in the UI banner")
    parser.add_argument("--provenance", default="",
                        help="banner text describing where this record came from")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from scrnapipeline import webrecord

    run_dir = Path(args.run_dir)
    record = json.loads((run_dir / "run.json").read_text())

    adata = None
    h5ad = run_dir / "processed.h5ad"
    if h5ad.exists():
        import anndata as ad

        adata = ad.read_h5ad(h5ad)
    else:
        print(f"note: {h5ad} not found; writing the record without plot data",
              file=sys.stderr)

    out = webrecord.build(
        record,
        adata,
        demo=args.demo,
        provenance=args.provenance,
        max_cells=args.max_cells,
    )

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, separators=(",", ":"), default=str))
    print(f"wrote {dest} ({dest.stat().st_size / 1024:.0f} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
