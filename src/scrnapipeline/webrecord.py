"""One run, in the shape the web demo reads.

There are two callers and they must not drift: `scripts/export_run.py` builds a
record from a finished run on disk, and `server.py` builds one after every step
while the run is still going. Both come through here, so the page sees the same
fields whether it is replaying a file or watching a live run.

Two things this module is careful about:

  * the *options* a decision was choosing between are re-derived from the same
    step objects the pipeline used, not typed out again, so the page cannot show
    a choice the pipeline never offered;
  * everything per-cell is subsampled before it leaves, because the browser has
    to draw it.
"""

from __future__ import annotations

from typing import Any

MAX_CELLS = 4000  # points drawn in the embedding panels
MAX_PCS = 50


def build(
    state: Any,
    adata: Any = None,
    *,
    mode: str = "scripted",
    demo: bool = False,
    provenance: str = "",
    max_cells: int = MAX_CELLS,
    confidence_floor: float | None = None,
) -> dict[str, Any]:
    """The full record. `state` is a RunState or its `to_dict()` output."""
    record = state.to_dict() if hasattr(state, "to_dict") else dict(state)

    return {
        "run_id": record.get("run_id"),
        "dataset": record.get("dataset"),
        "mode": mode,
        "demo": bool(demo),
        "provenance": provenance,
        "confidence_floor": (confidence_floor if confidence_floor is not None
                             else _confidence_floor()),
        "obs": record.get("obs", {}),
        "steps": record.get("steps", []),
        "decisions": decisions_with_options(record),
        "metrics": record.get("metrics", {}),
        "reasoning": record.get("reasoning", []),
        "viz": viz_from_adata(adata, record, max_cells) if adata is not None else {},
    }


# -- decisions ------------------------------------------------------------

def decisions_with_options(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach each decision's declared option set and its framed instructions.

    Claude's reframing, when the planner ran, is recorded in the step summary;
    it replaces the generic instructions the step ships with.
    """
    try:
        questions = baseline_questions()
    except Exception:  # never let a schema lookup take a live run down
        questions = {}

    framing = {
        step.get("step"): (step.get("summary") or {}).get("framing") or {}
        for step in record.get("steps", [])
    }

    out = []
    for decision in record.get("decisions", []):
        enriched = dict(decision)
        declared = questions.get((decision.get("step"), decision.get("question")))
        if declared:
            enriched.update(declared)
        framed = framing.get(decision.get("step"), {}).get(decision.get("question"))
        if framed:
            enriched["instructions"] = framed
        out.append(enriched)
    return out


def baseline_questions() -> dict[tuple[str, str], dict[str, Any]]:
    """(step, question) -> {kind, criteria, values, instructions, default}."""
    from .jev import ChoiceQ, NoulQ, ScoreQ
    from .registry import build_steps
    from .state import RunState

    blank = RunState(run_id="schema", dataset="schema")
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for name, step in build_steps().items():
        try:
            questions = step.questions(None, blank)
        except Exception:
            continue  # a step whose questions need the matrix; nothing to declare
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
        from .config import load_settings

        return float(load_settings().confidence_floor)
    except Exception:
        return 0.55


# -- plot data ------------------------------------------------------------

def viz_from_adata(adata: Any, record: dict[str, Any],
                   max_cells: int = MAX_CELLS) -> dict[str, Any]:
    """Whatever is drawable right now.

    Called mid-run as well as at the end, so every block is conditional: before
    `cluster` there is no embedding, before `annotate` there are no markers. A
    panel with no data is skipped by the page rather than drawn empty.
    """
    import numpy as np

    if adata is None:
        return {}

    rng = np.random.default_rng(0)
    n = int(adata.n_obs)
    idx = rng.choice(n, size=max_cells, replace=False) if n > max_cells else np.arange(n)
    idx = np.sort(idx)

    viz: dict[str, Any] = {}
    label_key = (record.get("obs") or {}).get("label_key")

    embedding: dict[str, Any] = {"n_total": n, "subsampled": bool(n > max_cells)}
    if "X_umap" in getattr(adata, "obsm", {}):
        coords = np.asarray(adata.obsm["X_umap"])[idx]
        embedding["coords"] = [[round(float(a), 3), round(float(b), 3)] for a, b in coords]
    for key, out_key in (("leiden", "cluster"), ("cell_type", "label")):
        if key in adata.obs:
            embedding[out_key] = [str(v) for v in adata.obs[key].values[idx]]
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
        # Everything still in the object survived filtering, so `kept` is all
        # true; the page words its legend from that rather than implying it
        # is showing cells that were dropped.
        qc["kept"] = [True] * len(idx)
        viz["qc"] = qc

    uns = getattr(adata, "uns", {})
    if "pca" in uns and "variance_ratio" in uns["pca"]:
        ratios = np.asarray(uns["pca"]["variance_ratio"])[:MAX_PCS]
        viz["pca_variance"] = [round(float(v), 5) for v in ratios]

    if "top_markers" in uns:
        viz["markers"] = {str(k): [str(g) for g in list(v)[:8]]
                          for k, v in dict(uns["top_markers"]).items()}

    if "leiden" in adata.obs and label_key and label_key in adata.obs:
        try:
            viz["confusion"] = _confusion(adata.obs[label_key], adata.obs["leiden"])
        except Exception:
            pass

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
