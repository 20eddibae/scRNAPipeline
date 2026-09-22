"""The live backend: pick a dataset, press run, watch the steps land.

A browser cannot run scanpy, so the page needs something to talk to. This is it:
one small ASGI app that drives the pipeline a step at a time and streams the run
record after each one, so the page fills in as the analysis happens instead of
appearing all at once at the end.

The same app runs in two places, unchanged:

    uvicorn scrnapipeline.server:app --port 8000        # locally
    modal deploy modal_app.py                           # as a Modal web endpoint

Server-sent events rather than a websocket, because the traffic is one-way and
SSE survives proxies that would drop a socket. The stream is deliberately
chatty: the whole record goes out after every step, which costs a few hundred
kilobytes over a run and buys a page that just re-renders whatever it last
received, with no client-side merging to get wrong.

**Access:** set `DEMO_TOKEN` and every run needs `?token=`. Leave it unset and
the endpoint is open - which is fine behind a private URL, and is not fine on a
public one, because a run spends real Modal compute and real model credits.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .registry import DEFAULT_ORDER
from .steps.load import DATASETS

DATASET_BLURB = {
    "pbmc3k": "2 700 human PBMCs, raw counts. Author labels grafted by barcode "
              "from the processed twin, so no label touches the pipeline.",
    "pbmc3k_processed": "The same 2 700 PBMCs, already filtered and normalised, "
                        "carrying the author's louvain labels.",
    "pbmc68k_reduced": "700 PBMCs in a pre-reduced space, with bulk-sorted "
                       "labels. Small and fast.",
}

def _allowed_origins() -> list[str]:
    """Which sites may call this. `DEMO_ORIGINS` is a comma-separated list;
    unset means any, which is what a hackathon demo wants and a real one does
    not."""
    raw = os.environ.get("DEMO_ORIGINS", "*")
    return [o.strip() for o in raw.split(",") if o.strip()] or ["*"]


app = FastAPI(title="scRNAPipeline", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "steps": list(DEFAULT_ORDER), "guarded": bool(os.environ.get("DEMO_TOKEN"))}


@app.get("/datasets")
def datasets() -> dict[str, Any]:
    return {
        "datasets": [
            {"name": name, "label_key": label_key,
             "blurb": DATASET_BLURB.get(name, "")}
            for name, (_loader, label_key) in DATASETS.items()
        ]
    }


@app.get("/run")
def run(
    dataset: str = Query("pbmc3k"),
    context: str = Query("human PBMC"),
    token: str | None = Query(None),
) -> StreamingResponse:
    """Stream one run as it happens."""
    _authorise(token)
    if dataset not in DATASETS:
        raise HTTPException(400, f"unknown dataset {dataset!r}")

    return StreamingResponse(
        _events(dataset, context),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _authorise(token: str | None) -> None:
    expected = os.environ.get("DEMO_TOKEN")
    if expected and token != expected:
        raise HTTPException(401, "this endpoint requires a token")


# -- the stream -----------------------------------------------------------

def _events(dataset: str, context: str) -> Iterator[str]:
    """Drive the pipeline one step at a time, emitting after each."""
    from .pipeline import Pipeline
    from .webrecord import build

    started = time.time()
    try:
        pipeline = Pipeline(dataset, context=context)
    except Exception as exc:
        yield _sse("error", step="load", message=f"{type(exc).__name__}: {exc}")
        return

    yield _sse("start",
               run_id=pipeline.state.run_id,
               dataset=dataset,
               steps=list(DEFAULT_ORDER),
               confidence_floor=pipeline.settings.confidence_floor,
               offline={"jev": pipeline.settings.jev_offline,
                        "claude": pipeline.settings.claude_offline})

    for name in DEFAULT_ORDER:
        yield _sse("step_start", step=name)
        try:
            pipeline.execute(name)
        except Exception as exc:
            # The run log already holds the error record; send the partial
            # record too, so the page keeps whatever did complete.
            yield _sse("error", step=name, message=f"{type(exc).__name__}: {exc}",
                       record=_record(pipeline, build))
            return
        yield _sse("step_done", step=name, record=_record(pipeline, build))

    try:
        out = pipeline.save()
    except Exception as exc:  # a failed write must not lose the run
        out = f"not saved ({type(exc).__name__})"

    yield _sse("done",
               seconds=round(time.time() - started, 1),
               output=str(out),
               record=_record(pipeline, build))


def _record(pipeline: Any, build: Any) -> dict[str, Any]:
    return build(
        pipeline.state,
        pipeline.adata,
        mode="scripted",
        provenance="Live run, streamed from the pipeline as each step finished.",
        confidence_floor=pipeline.settings.confidence_floor,
    )


def _sse(kind: str, **payload: Any) -> str:
    return f"data: {json.dumps({'type': kind, **payload}, default=str)}\n\n"
