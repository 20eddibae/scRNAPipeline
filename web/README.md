# scRNAPipeline — demo UI

A one-page walkthrough of a single-cell run: all eight steps, and at each one
the decision that was actually made — the options Jev was choosing between, the
probability it put on each, the confidence against the floor, and the plots of
what the data looked like at that point.

It is **static**. No build step, no `npm install`, no framework. Every file here
is served as-is, which is what makes it a two-command Vercel deploy and what
lets you open it locally with nothing but Python.

## Run it locally

```bash
cd web
npm run dev          # http://localhost:5173
```

There is nothing to install — `package.json` declares no dependencies and no
build step, and `npm run dev` just serves the directory. Other ways in:

| command | what it gives you |
|---------|-------------------|
| `npm run dev` | static server on :5173 |
| `npm run dev:vercel` | same, plus the `api/run` function running locally |
| `npm run dev:python` | no Node at all: `python3 -m http.server 5173` |

(ES modules need a server of some kind; opening `index.html` off the filesystem
will not work, because of the module CORS rule.)

## Deploy to Vercel

```bash
cd web
npm run deploy       # npx vercel deploy --prod
```

`vercel.json` pins framework **none** and output directory **`.`**, so the files
are served as they are. If you deploy the repository rather than this folder,
set the project's *Root Directory* to `web`.

## Where the numbers come from

Nothing on the page is typed into the page. It reads one JSON record:

```bash
# run the pipeline, then export what the UI needs
python -m scrnapipeline.cli run --dataset pbmc3k
python scripts/export_run.py runs/<run_id> web/data/run-demo.json --demo
```

`export_run.py` joins `run.json` (decisions, step records, metrics) with
`processed.h5ad` (embedding, per-cell QC, clusters, markers), subsamples the
per-cell arrays to 4 000 points, and writes one self-contained file.

Three sources, in order:

| URL | what loads |
|-----|------------|
| `/` | the bundled `data/run-demo.json` |
| `/?run=<url>` | any exported record served from anywhere |
| `/?api=/api/run` | a live record proxied from Modal (needs `MODAL_RUN_URL`) |

## Layout

```
package.json                scripts only — no dependencies, no build
vercel.json                 framework none, output directory .
index.html                  three mount points and nothing else
styles/
  tokens.css                palette, type, geometry — restyle here
  base.css                  page frame and the rail/detail grid
  components.css            one block per component module
src/
  main.js                   load a record, draw the rail, draw a view
  config.js                 copy about the demo itself
  steps/spec.js             the eight steps as the UI describes them
  data/source.js            bundled | ?run= | ?api=
  data/schema.js            the view model over one run record
  components/
    dom.js                  h(), chip(), replace(), fmt()
    header.js               top bar + the "this is a demo record" banner
    stepper.js              the eight-step rail
    overview.js             landing view: the flow, the decision ledger, the scores
    step-card.js            the detail pane for one step
    decision.js             options, probabilities, confidence vs. floor
    reasoning.js            Claude's framing and the marker → label read
  viz/
    index.js                the panel registry — the seam for adding plots
    panels.js               each panel: (run) => {title, subtitle, node}
    primitives.js           ~150 lines of SVG helpers; no chart library
api/run.js                  optional Modal proxy; the page works without it
data/run-demo.json          the bundled record
```

## Adding a plot

Three lines, in three files:

1. `src/viz/panels.js` — write `(run) => ({title, subtitle, node})`, returning
   `null` when the run has nothing to draw.
2. `src/viz/index.js` — add it to `PANELS` under a name.
3. `src/steps/spec.js` — name it in that step's `viz` array.

Nothing else in the app needs to know it exists. If the record has no data for
it, the panel is skipped silently rather than drawing an empty frame.
