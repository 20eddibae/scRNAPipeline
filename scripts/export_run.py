"""Turn a finished run into the record the web demo reads.

    python scripts/export_run.py runs/<run_id> web/data/run-demo.json

`run.json` already holds the decisions, the step records and the metrics. What
it does not hold is anything to plot: the embedding, the per-cell QC values and
the cluster/label assignment all live in `processed.h5ad`. This script joins the
two, subsamples the per-cell arrays to something a browser can draw, and writes
one self-contained JSON.

It also copies each decision's *option set* across. The run log records the
value that was chosen; the options it was chosen from live in the step's
question definition, so they are re-derived here from the same step objects the
pipeline used. That way the page can show what Jev was actually choosing between
rather than just what came back.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

MAX_CELLS = 4000  # points drawn in the UMAP panels
MAX_PCS = 50


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="export_run")
    parser.add_argument("run_dir", help="runs/<run_id>")
    parser.add_argument("out", help="destination .json (e.g. web/data/run-demo.json)")
    parser.add_argument("--max-cells", type=int, default=MAX_CELLS)
    parser.add_argument("--demo", action="store_true",
                        help="mark the record as a demo run in the UI banner")
    parser.add_argument("--provenance", default="",
                        help="banner text describing where this record came from")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    record = json.loads((run_dir / "run.json").read_text())

    out: dict[str, Any] = {
        "run_id": record.get("run_id"),
        "dataset": record.get("dataset"),
        "mode": record.get("mode", "scripted"),
        "demo": bool(args.demo),
        "provenance": args.provenance,
        "confidence_floor": _confidence_floor(),
        "obs": record.get("obs", {}),
        "steps": record.get("steps", []),
        "decisions": _with_options(record),
        "metrics": record.get("metrics", {}),
        "reasoning": record.get("reasoning", []),
        "viz": {},
    }

    h5ad = run_dir / "processed.h5ad"
    if h5ad.exists():
        out["viz"] = _viz_from_h5ad(h5ad, record, args.max_cells)
    else:
        print(f"note: {h5ad} not found; writing the record without plot data",
              file=sys.stderr)

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, separators=(",", ":"), default=str))
    print(f"wrote {dest} ({dest.stat().st_size / 1024:.0f} kB)")
    return 0


# -- decisions ------------------------------------------------------------

def _with_options(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach each decision's declared option set and instructions.

    The options come from the step's own `questions()`, so they cannot drift
    from what the pipeline actually offered. Claude's reframed instructions, if
    the planner ran, are already in the step summary and override the generic
    text here.
    """
    try:
        questions = _baseline_questions()
    except Exception as exc:  # the exporter must never need the science stack
        print(f"note: option sets unavailable ({exc.__class__.__name__})", file=sys.stderr)
        questions = {}

    framing = {
        step["step"]: (step.get("summary") or {}).get("framing") or {}
        for step in record.get("steps", [])
    }

    out = []
    for decision in record.get("decisions", []):
        q = questions.get((decision["step"], decision["question"]))
        enriched = dict(decision)
        if q:
            enriched.update(q)
        framed = framing.get(decision["step"], {}).get(decision["question"])
        if framed:
            enriched["instructions"] = framed
        out.append(enriched)
    return out


def _baseline_questions() -> dict[tuple[str, str], dict[str, Any]]:
    """(step, question) -> {kind, criteria, values, instructions, default}."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from scrnapipeline.jev import ChoiceQ, NoulQ, ScoreQ
    from scrnapipeline.registry import build_steps
    from scrnapipeline.state import RunState

    blank = RunState(run_id="schema", dataset="schema")
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for name, step in build_steps().items():
        try:
            questions = step.questions(None, blank)
        except Exception:
            continue  # a step whose questions depend on the matrix; skip it
        for qname, q in questions.items():
            entry: dict[str, Any] = {"instructions": q.instructions, "default": q.default}
            if isinstance(q, ChoiceQ):
                entry.update(kind="choice", criteria=q.criteria)
            elif isinstance(q, NoulQ):
                entry.update(kind="noul", criteria={"true": "yes", "false": "no"},
                             threshold=q.threshold)
            elif isinstance(q, ScoreQ):
                entry.update(kind="score", criteria=q.criteria, values=q.values)
            out[(name, qname)] = entry
    return out


def _confidence_floor() -> float:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from scrnapipeline.config import load_settings

        return float(load_settings().confidence_floor)
    except Exception:
        return 0.6


# -- plot data ------------------------------------------------------------

def _viz_from_h5ad(path: Path, record: dict[str, Any], max_cells: int) -> dict[str, Any]:
    import anndata as ad
    import numpy as np

    adata = ad.read_h5ad(path)
    rng = np.random.default_rng(0)
    n = adata.n_obs
    idx = (rng.choice(n, size=max_cells, replace=False) if n > max_cells
           else np.arange(n))
    idx.sort()

    viz: dict[str, Any] = {}

    embedding: dict[str, Any] = {"n_total": int(n), "subsampled": bool(n > max_cells)}
    if "X_umap" in adata.obsm:
        coords = np.asarray(adata.obsm["X_umap"])[idx]
        embedding["coords"] = [[round(float(a), 3), round(float(b), 3)] for a, b in coords]
    for key, out_key in (("leiden", "cluster"), ("cell_type", "label")):
        if key in adata.obs:
            embedding[out_key] = [str(v) for v in adata.obs[key].values[idx]]
    label_key = record.get("obs", {}).get("label_key")
    if label_key and label_key in adata.obs:
        embedding["truth"] = [str(v) for v in adata.obs[label_key].values[idx]]
    if "coords" in embedding:
        viz["embedding"] = embedding

    qc: dict[str, Any] = {}
    for key, out_key in (("n_genes_by_counts", "n_genes"),
                         ("pct_counts_mt", "pct_mt"),
                         ("total_counts", "total_counts")):
        if key in adata.obs:
            qc[out_key] = [round(float(v), 2) for v in adata.obs[key].values[idx]]
    if qc:
        # every cell in the processed object survived QC; the dropped ones are
        # gone, so `kept` is all-true and the UI says so honestly.
        qc["kept"] = [True] * len(idx)
        viz["qc"] = qc

    if "pca" in adata.uns and "variance_ratio" in adata.uns["pca"]:
        ratios = np.asarray(adata.uns["pca"]["variance_ratio"])[:MAX_PCS]
        viz["pca_variance"] = [round(float(v), 5) for v in ratios]

    if "top_markers" in adata.uns:
        viz["markers"] = {str(k): [str(g) for g in v[:8]]
                          for k, v in dict(adata.uns["top_markers"]).items()}

    if "leiden" in adata.obs and label_key and label_key in adata.obs:
        viz["confusion"] = _confusion(adata.obs[label_key], adata.obs["leiden"])

    return viz


def _confusion(truth: Any, clusters: Any) -> dict[str, Any]:
    import numpy as np
    import pandas as pd

    mask = pd.notna(truth)
    table = pd.crosstab(np.asarray(truth)[mask.values].astype(str),
                        np.asarray(clusters)[mask.values].astype(str))
    table = table.reindex(columns=sorted(table.columns, key=_natural))
    return {
        "rows": [str(r) for r in table.index],
        "cols": [str(c) for c in table.columns],
        "matrix": table.to_numpy().astype(int).tolist(),
    }


def _natural(value: str) -> tuple[int, Any]:
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))


if __name__ == "__main__":
    sys.exit(main())
