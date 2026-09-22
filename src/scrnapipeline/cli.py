"""Local entry point: `python -m scrnapipeline.cli run --dataset pbmc3k`."""

from __future__ import annotations

import argparse
import json
import sys

from .config import load_settings
from .pipeline import Pipeline
from .registry import DEFAULT_ORDER


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scrnapipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the pipeline")
    run.add_argument("--dataset", default="pbmc3k",
                     help="pbmc3k | pbmc3k_processed | pbmc68k_reduced | sctab_val_raw | sctab_train_preprocessed | h5ad:<path> | merlin:<dir>")
    run.add_argument("--mode", choices=("scripted", "agent"), default="scripted")
    run.add_argument("--context", default="human PBMC",
                     help="tissue context given to the annotator")
    run.add_argument("--out", default=None, help="output directory")
    run.add_argument("--label-key", default=None,
                     help="ground-truth obs column; autodetected when omitted")

    sub.add_parser("steps", help="list the steps in canonical order")
    sub.add_parser("datasets", help="list the datasets this pipeline knows about")

    args = parser.parse_args(argv)

    if args.command == "steps":
        for name in DEFAULT_ORDER:
            print(name)
        return 0

    if args.command == "datasets":
        from .steps.load import DATASETS, FILE_DATASETS

        for name in sorted(DATASETS):
            print(f"  {name:28s} scanpy builtin")
        for name, (filename, note) in sorted(FILE_DATASETS.items()):
            print(f"  {name:28s} {filename} -- {note}")
        print(f"  {'h5ad:<path>':28s} any AnnData file; label column autodetected")
        print(f"  {'merlin:<dir>':28s} an unpacked scTab Merlin parquet store")
        return 0

    settings = load_settings()
    pipeline = Pipeline(args.dataset, settings=settings, context=args.context)
    if args.label_key:
        pipeline.state.observe(label_key=args.label_key)
    state = pipeline.run_agent() if args.mode == "agent" else pipeline.run_scripted()
    out = pipeline.save(args.out)

    print(json.dumps({"run_id": state.run_id, "metrics": state.metrics,
                      "output": str(out)}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
