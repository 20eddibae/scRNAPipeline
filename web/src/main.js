/* Bootstrap and the one piece of mutable state in the app.
 *
 * Two modes, same rendering path:
 *
 *   replay  a saved record is fetched and drawn all at once
 *   live    a backend streams a record after every step, and the page follows
 *           along, moving to whichever step just finished
 *
 * The run record is always the single source of truth; live mode just replaces
 * it more often. There is no client-side merging, so there is no way for the
 * page to show a state the pipeline was never in. */

import { apiFromLocation, fetchDatasets, health, runLive } from "./data/live.js";
import { loadRun, sourceFromLocation } from "./data/source.js";
import { renderBanner, renderHeader } from "./components/header.js";
import { renderOverview } from "./components/overview.js";
import { renderRunner } from "./components/runner.js";
import { renderStepCard } from "./components/step-card.js";
import { renderStepper } from "./components/stepper.js";
import { OVERVIEW, STEPS, STEP_INDEX } from "./steps/spec.js";
import { h } from "./components/dom.js";
import { APP } from "./config.js";

const API_SLOT = "krino.api";  // localStorage slot, not a credential
const OLD_API_SLOT = "scrnapipeline.api";  // pre-rename slot, read once so a saved URL survives

const els = {
  topbar: document.getElementById("topbar"),
  banner: document.getElementById("banner"),
  runner: document.getElementById("runner"),
  rail: document.getElementById("rail"),
  detail: document.getElementById("detail"),
};

const ui = {
  run: null,          // the current record, wrapped
  active: OVERVIEW,   // which view is on screen
  api: apiFromLocation() || remembered(),
  live: false,        // a backend answered /health
  datasets: [],
  dataset: "",
  status: null,       // {text, running, error}
  running: null,      // step currently executing, or null
  abort: null,        // stops reading the live stream
  replayTimer: null,
};

async function boot() {
  ui.active = viewFromHash();

  // With a backend, start blank: the page fills in from the run you start, not
  // from the bundled record. `?run=` still loads a saved record on purpose.
  await connect();
  if (ui.live && sourceFromLocation().kind === "bundled") {
    ui.active = OVERVIEW;
    drawChrome();
    showEmpty();
    return;
  }

  try {
    ui.run = await loadRun(sourceFromLocation());
  } catch (err) {
    // No saved record is not fatal any more: with a backend you can just run one.
    ui.run = null;
    showMessage("No saved run record", String(err.message ?? err));
  }

  drawChrome();
  if (ui.run) draw();
}

/* -- backend ------------------------------------------------------------- */

async function connect() {
  if (!ui.api) {
    // Modal (and uvicorn locally) serve the page from its own backend, so try
    // the page's origin before settling for replay. A static host 404s here.
    const own = await health(".").catch(() => null);
    if (!own) { drawChrome(); return; }
    ui.api = ".";
  }
  try {
    const info = await health(ui.api);
    ui.status = info ? { text: info.guarded ? "ready · token required" : "ready" }
                     : { error: "backend not reachable" };
    ui.live = Boolean(info);
    ui.datasets = await fetchDatasets(ui.api);
    if (!ui.dataset && ui.datasets.length) ui.dataset = ui.datasets[0].name;
  } catch (err) {
    ui.status = { error: `backend not reachable` };
    ui.live = false;
    ui.datasets = [];
  }
  drawChrome();
}

function promptForApi() {
  const next = window.prompt(
    "Backend URL.\n\nLocal:  http://localhost:8000\nModal:  https://<workspace>--krino-web.modal.run\n\n" +
    "Leave empty to go back to replaying the saved record.",
    ui.api || "http://localhost:8000",
  );
  if (next === null) return;
  ui.api = next.trim().replace(/\/+$/, "");
  remember(ui.api);
  ui.status = null;
  ui.datasets = [];
  ui.dataset = "";
  drawChrome();
  connect();
}

function startRun() {
  if (ui.abort) ui.abort();
  stopReplay();

  ui.running = null;
  ui.run = null;  // each run starts from a blank page, not the previous record
  ui.active = OVERVIEW;
  ui.status = { running: true, text: "starting…" };
  drawChrome();
  showEmpty("starting…");

  const pass = tokenFromLocation();
  ui.abort = runLive({
    api: ui.api,
    dataset: ui.dataset,
    token: pass,
    onEvent: handleEvent,
  });
}

function stopRun() {
  if (ui.abort) ui.abort();
  ui.abort = null;
  ui.running = null;
  // The run itself carries on server-side; say that rather than imply it stopped.
  ui.status = { text: "stopped watching · the run continues on the backend" };
  drawChrome();
  draw();
}

function handleEvent(event) {
  switch (event.type) {
    case "start":
      ui.status = { running: true, text: `run ${event.run_id}` };
      ui.running = null;
      select(STEPS[0].name, { quiet: true });
      break;

    case "step_start":
      ui.running = event.step;
      ui.status = { running: true, text: `running ${event.step}` };
      select(event.step, { quiet: true });
      break;

    case "step_done":
      ui.running = null;
      if (event.run) ui.run = event.run;
      select(event.step, { quiet: true });
      break;

    case "done":
      ui.running = null;
      ui.abort = null;
      if (event.run) ui.run = event.run;
      ui.status = { text: `finished in ${event.seconds}s` };
      select(OVERVIEW, { quiet: true });
      break;

    case "error":
      ui.running = null;
      ui.abort = null;
      if (event.run) ui.run = event.run;
      ui.status = { error: truncate(event.message) };
      if (ui.run) draw();
      else showMessage("The run failed", event.message);
      return;

    default:
      return;
  }
  drawChrome();
  if (ui.run) draw();
  else showEmpty(ui.running ? `running ${ui.running}…` : "starting…");
}

/* -- rendering ----------------------------------------------------------- */

function drawChrome() {
  if (ui.run) {
    renderHeader(els.topbar, ui.run, { onReplay: ui.api ? null : toggleReplay });
    renderBanner(els.banner, ui.run);
  } else {
    els.topbar.replaceChildren(h("div", { class: "topbar-inner" },
      h("div", { class: "brand" },
        h("h1", { text: APP.title }),
        h("span", { class: "sub", text: APP.tagline }))));
    els.banner.replaceChildren();
  }
  renderRunner(els.runner, {
    api: ui.api,
    datasets: ui.datasets,
    dataset: ui.dataset,
    status: ui.status,
    running: Boolean(ui.abort),
    onDataset: (name) => { ui.dataset = name; drawChrome(); },
    onRun: startRun,
    onStop: stopRun,
    onApi: promptForApi,
  });
}

function draw() {
  if (!ui.run) return;
  renderStepper(els.rail, ui.run, {
    active: ui.active, running: ui.running, onSelect: select,
  });
  if (ui.active === OVERVIEW) renderOverview(els.detail, ui.run, { onSelect: select });
  else renderStepCard(els.detail, ui.run, ui.active, STEP_INDEX[ui.active] ?? 0,
                      { running: ui.running === ui.active });
}

function select(name, { quiet = false } = {}) {
  ui.active = name;
  window.history.replaceState(null, "", `#${name}`);
  if (!quiet) { draw(); els.detail.scrollIntoView({ behavior: "smooth", block: "start" }); }
}

/** The blank state: no record yet, only the step list and what to do. */
function showEmpty(progress = null) {
  const item = (step, i) => {
    const live = step.name === ui.running;
    return h("li", {},
      h("div", { class: `rail-item pending${live ? " active" : ""}` },
        h("span", { class: "rail-num", text: String(i + 1) }),
        h("span", {},
          h("span", { class: "rail-name", text: step.name }),
          h("span", { class: "rail-who", style: "display:block", text: live ? "running…" : "" }),
        ),
      ),
    );
  };
  els.rail.replaceChildren(
    h("div", { class: "rail-title", text: "pipeline" }),
    h("ol", { class: "rail-list" }, STEPS.map(item)),
  );
  els.detail.replaceChildren(h("section", { class: "card" },
    h("div", { class: "card-body" },
      h("h2", { text: progress ? "Running" : "Nothing has run yet" }),
      h("p", { class: "lede", text: progress
        ? `${progress} Each step appears here as soon as the backend finishes it.`
        : "Pick a dataset and press Run pipeline. Each step fills in as the " +
          "backend finishes it." }),
    ),
  ));
}

function showMessage(title, body) {
  els.detail.replaceChildren(h("section", { class: "card" },
    h("div", { class: "card-body" },
      h("h2", { text: title }),
      h("p", { class: "lede", text: body }),
      h("p", { class: "lede", text:
        "Connect a backend and press Run, or export a record with " +
        "`python scripts/export_run.py runs/<run_id> web/data/run-demo.json`." }),
    ),
  ));
}

/* -- replay (no backend): walk the saved run on a timer ------------------ */

function toggleReplay() {
  if (ui.replayTimer) { stopReplay(); return; }
  let i = 0;
  select(STEPS[0].name);
  ui.replayTimer = setInterval(() => {
    i += 1;
    if (i >= STEPS.length) { stopReplay(); return; }
    select(STEPS[i].name);
  }, 2600);
}

function stopReplay() {
  if (ui.replayTimer) clearInterval(ui.replayTimer);
  ui.replayTimer = null;
}

/* -- odds and ends ------------------------------------------------------- */

function viewFromHash() {
  const name = window.location.hash.replace("#", "");
  return name in STEP_INDEX || name === OVERVIEW ? name : OVERVIEW;
}

function tokenFromLocation() {
  return new URLSearchParams(window.location.search).get("token") ?? "";
}

/** Remembering the backend URL is a per-viewer convenience, so it may fail. */
function remembered() {
  try {
    return window.localStorage.getItem(API_SLOT) ??
      window.localStorage.getItem(OLD_API_SLOT) ?? "";
  } catch { return ""; }
}

function remember(value) {
  try {
    if (value) window.localStorage.setItem(API_SLOT, value);
    else window.localStorage.removeItem(API_SLOT);
  } catch { /* private window, blocked storage: the URL still works */ }
}

function truncate(text) {
  const s = String(text ?? "");
  return s.length > 160 ? `${s.slice(0, 159)}…` : s;
}

window.addEventListener("hashchange", () => {
  const name = viewFromHash();
  if (name !== ui.active) { ui.active = name; draw(); }
});

boot();
