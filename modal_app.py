"""Modal deployment.

    modal secret create ai-gateway AI_GATEWAY_API_KEY=...     # once, from a shell
    modal run modal_app.py --dataset pbmc3k --mode agent

The key only ever exists in Modal's secret store and in `~/.config/`; it is never
written into this repo, into the image, or into a run record.
"""

from __future__ import annotations

import json

import modal

APP_NAME = "scrna-pipeline"

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
    )
    .add_local_dir("src/scrnapipeline", remote_path="/root/scrnapipeline")
)

app = modal.App(APP_NAME)

# Datasets and run records survive between invocations.
volume = modal.Volume.from_name("scrna-pipeline-data", create_if_missing=True)

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


@app.local_entrypoint()
def main(dataset: str = "pbmc3k", mode: str = "scripted", context: str = "human PBMC"):
    result = run_pipeline.remote(dataset=dataset, mode=mode, context=context)
    print(json.dumps(result, indent=2, default=str))
