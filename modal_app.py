"""Modal deployment.

    modal secret create ai-gateway AI_GATEWAY_API_KEY=...     # once, from a shell
    modal run modal_app.py --dataset pbmc3k --mode agent

The key only ever exists in Modal's secret store and in `~/.config/`; it is never
written into this repo, into the image, or into a run record.
"""

from __future__ import annotations

import json

import modal

APP_NAME = "krino"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "scanpy>=1.10",
        "anndata>=0.10",
        "leidenalg>=0.10",
        "igraph>=0.11",
        "scikit-learn>=1.4",
        "harmonypy>=0.0.10",
        "anthropic>=0.70",
        "typesafe-sdk>=0.1",
        "fastapi>=0.110",
    )
    .add_local_dir("src/scrnapipeline", remote_path="/root/scrnapipeline")
)

app = modal.App(APP_NAME)

# Datasets and run records survive between invocations.
volume = modal.Volume.from_name("krino-data", create_if_missing=True)

# `ai-gateway` holds AI_GATEWAY_API_KEY (and optionally AI_GATEWAY_BASE_URL).
secret = modal.Secret.from_name("ai-gateway")


@app.function(
    image=image,
    volumes={"/vol": volume},
    secrets=[secret],
    timeout=60 * 60,
    cpu=8.0,
    memory=32768,
)
def run_pipeline(dataset: str = "pbmc3k", mode: str = "scripted",
                 context: str = "human PBMC") -> dict:
    import os
    import sys

    sys.path.insert(0, "/root")
    os.environ.setdefault("SCRNA_DATA_DIR", "/vol/data")
    os.environ.setdefault("SCRNA_RUN_DIR", "/vol/runs")

    from scrnapipeline.pipeline import Pipeline

    pipeline = Pipeline(dataset, context=context)
    state = pipeline.run_agent() if mode == "agent" else pipeline.run_scripted()
    out = pipeline.save()
    volume.commit()

    return {
        "run_id": state.run_id,
        "metrics": state.metrics,
        "decisions": [
            {"step": d.step, "question": d.question, "value": d.value,
             "source": d.source, "confidence": d.confidence}
            for d in state.decisions
        ],
        "output": str(out),
    }


@app.function(
    image=image,
    volumes={"/vol": volume},
    secrets=[secret],
    timeout=60 * 30,
    cpu=4.0,
    memory=16384,
    # gpu="A10G",  # uncomment once a stage actually selects a GPU-bound model
)
def run_model(model: str, *args, **kwargs):
    """Execute one named model for one pipeline stage.

    This is the target of `executors.Executor` with MODAL_REMOTE=1. It exists so
    that a stage whose decision selected a heavy model (scTab, scVI) can send
    just that model to a GPU, while the rest of the stage stays where it is.
    """
    import sys

    sys.path.insert(0, "/root")
    from scrnapipeline import executors  # noqa: F401  (import guard)

    raise NotImplementedError(
        f"no remote implementation registered for {model!r}. Register it here "
        "once the stage that selects it is ready to run remotely."
    )


@app.function(
    image=image,
    volumes={"/vol": volume},
    secrets=[secret],
    timeout=60 * 60,
    cpu=8.0,
    memory=32768,
)
# One run per container. A run holds a whole matrix in memory and streams for
# minutes, so packing two into one container helps nobody; this is the default
# in Modal 1.x and is stated here because it is a requirement, not an accident.
@modal.concurrent(max_inputs=1)
@modal.asgi_app()
def web():
    """The demo's backend: pick a dataset, press run, watch the steps land.

    Same ASGI app as `uvicorn scrnapipeline.server:app` locally - this only
    puts it on a URL with the volume mounted and the gateway key in the
    environment.

    The endpoint is open unless DEMO_TOKEN is set on the `ai-gateway` secret (or
    any secret attached here). A run costs real compute and real model credits,
    so set it before the URL goes anywhere public.
    """
    import os
    import sys

    sys.path.insert(0, "/root")
    os.environ.setdefault("SCRNA_DATA_DIR", "/vol/data")
    os.environ.setdefault("SCRNA_RUN_DIR", "/vol/runs")

    from scrnapipeline.server import app as api

    return api


@app.local_entrypoint()
def main(dataset: str = "pbmc3k", mode: str = "scripted", context: str = "human PBMC"):
    result = run_pipeline.remote(dataset=dataset, mode=mode, context=context)
    print(json.dumps(result, indent=2, default=str))
