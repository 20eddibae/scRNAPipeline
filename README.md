# scRNAPipeline

An scRNA-seq analysis pipeline where the judgement calls are explicit.

**Claude** decides *which step runs next* and reads the marker genes.
**Jev** (TypeSafe's System One model) answers the *typed decision points* inside
each step — filtering stringency, normalization method, HVG count, whether
integration is needed, clustering resolution.
**Modal** runs the whole thing.

Every decision is recorded with its source and confidence, so a run is auditable
after the fact: which knob was set by the decision model, which fell back to a
default, and what the resulting clustering scored against ground truth.

## Why split it this way

A standard scRNA-seq workflow is mostly mechanical. What is *not* mechanical is a
handful of contested choices — the mitochondrial cutoff, log1p-CPM vs. Pearson
residuals, Leiden resolution — that get made once by whoever wrote the notebook
and then never revisited. Those are exactly the points a calibrated decision
model is for: typed output, a probability attached, and a confidence floor below
which the pipeline falls back to the declared default and says so.

Prose generation is a bad fit for that job and a good fit for two others:
sequencing the run and reading a ranked marker list. Claude does those.

## Pipeline

| # | Step | Decision point | Decided by |
|---|------|----------------|-----------|
| 1 | `load` | — (start from a public processed matrix) | — |
| 2 | `qc` | filtering stringency; whether to score doublets | Jev |
| 3 | `normalize` | log1p-CPM vs. Pearson residuals vs. pooled size factors | Jev |
| 4 | `features` | how many HVGs; how many PCs | Jev |
| 5 | `integrate` | whether integration is needed at all; which method | Jev |
| 6 | `cluster` | Leiden resolution | Jev |
| 7 | `annotate` | cell-type label from top markers | Claude |
| 8 | `evaluate` | — (ARI/NMI + linear probe vs. ground truth) | — |

Alignment and UMI dedup (Cell Ranger / STARsolo / alevin-fry) are out of scope;
the pipeline starts from a count matrix.

## Evaluation

Two readouts of a different kind, both against held-out author labels:

- **unsupervised** — ARI and NMI of the Leiden partition vs. the ground-truth
  cell types. Asks whether the pipeline's decisions recovered known structure.
- **supervised** — a logistic-regression probe on the PCA embedding, 70/30
  stratified. Asks how much cell-type information the representation carries at
  all, independent of where the cluster boundaries landed.

`pbmc3k` is loaded raw and its labels are grafted by barcode from
`pbmc3k_processed`, so no label touches the pipeline itself.

[scTab](https://github.com/theislab/scTab) (Fischer et al., *Nat Commun* 2024) is
the intended third column — a de novo classifier trained across CELLxGENE, which
answers the annotation question without any of this pipeline's choices.
**It is not wired up yet**: `src/scrnapipeline/baselines.py` holds the slot and
raises `NotImplementedError` rather than reporting a placeholder number. The
adapter needs the 8.1 GB checkpoint and a `var_names` → Merlin feature-space
mapping.

## Running it

```bash
pip install -e .

# fully offline: no API calls, every decision takes its declared default
JEV_OFFLINE=1 CLAUDE_OFFLINE=1 python -m scrnapipeline.cli run --dataset pbmc3k

# Jev decides, fixed step order
python -m scrnapipeline.cli run --dataset pbmc3k

# Claude sequences the run, Jev decides inside each step
python -m scrnapipeline.cli run --dataset pbmc3k --mode agent
```

On Modal:

```bash
# Put the keys in a mode-600 file OUTSIDE any repo, then hand that file to Modal.
# --from-dotenv keeps them off the command line, where `ps` would expose them to
# anyone else on a shared machine.
modal secret create ai-gateway --from-dotenv ~/.config/modal-hackathon/ai-gateway.env
modal run modal_app.py --dataset pbmc3k --mode agent
```

Clustering needs `igraph` + `leidenalg`; they are in the Modal image and in
`pyproject.toml`, but a bare local env may not have them.

Output lands in `runs/<run_id>/`: `run.json` (decisions, step records, metrics)
and `processed.h5ad`.

## Credentials

**No key is ever written into this repo, into the Modal image, or into a run
record.** The layout:

- keys live in `~/.config/modal-hackathon/`, mode 600; `.env` here is a symlink
  to that file and is gitignored
- on Modal they live in the `ai-gateway` Modal secret and reach the container as
  an environment variable
- Jev is reached through the **Vercel AI Gateway**: the Vercel `vck_...` key *is*
  the TypeSafe key, and the base URL is `https://ai-gateway.vercel.sh/typesafe`
  (the SDK appends `/v1/systemone`). So `AI_GATEWAY_API_KEY` is a correct
  fallback for `TYPESAFE_API_KEY`.
- It is **not** a correct fallback for Claude. A gateway key sent to
  `api.anthropic.com` comes back as a bare 401 that looks like a broken key, so
  `AI_GATEWAY_API_KEY` is only used for Claude when `ANTHROPIC_BASE_URL` is also
  set — i.e. when a gateway is deliberately in the path.
- `scripts/scan_secrets.py` is key-anchored, not value-anchored — it trips on any
  `*_API_KEY`/`_TOKEN`/`_SECRET` assigned a literal, not just on key prefixes
  someone thought to list
- a `pre-push` hook runs that scanner over the outbound commits

**`.git/hooks` does not survive a clone.** After cloning:

```bash
./scripts/install_hooks.sh
```

## Layout

```
modal_app.py                 Modal image, volume, secret, entrypoint
src/scrnapipeline/
  config.py                  env-driven settings, gateway key fallback
  state.py                   RunState: observations, decisions, step records
  jev.py                     decision layer: ChoiceQ/NoulQ/ScoreQ, confidence floor
  claude.py                  annotator + manual tool-use orchestration loop
  registry.py                canonical step order, dependencies, tool schemas
  pipeline.py                scripted and agent drivers
  baselines.py               scTab slot (not implemented)
  steps/                     one module per pipeline step
scripts/scan_secrets.py      push guard
tests/                       infra tests; no network, no keys
```

## Status

Hackathon scaffold. Steps 1–8 run; the agent loop and the Jev layer are wired but
have only been exercised offline. scTab is a stub.
