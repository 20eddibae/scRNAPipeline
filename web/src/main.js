/* Bootstrap and the one piece of mutable state in the app.
 *
 * Two modes, same rendering path:
 *
 *   replay  a saved record (`?run=`) is fetched and drawn all at once
 *   live    a backend streams a record after every step, and the page follows
 *           along, moving to whichever step just finished
 *
 * Only a step that has just finished in a live run plays its exchange (Claude
 * typing the framing, Jev answering). Everything else - clicking back to a
 * step, a saved record - renders finished.
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
import { resumeAnimations } from "./components/typewriter.js";
import { setFeedbackBackend } from "./components/whatif.js";

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
  follow: Promise.resolve(),  // the live view's queue of steps to show, in order
  gen: 0,             // bumped per run, so a stopped run's queued views are dropped
  pinned: false,      // the viewer clicked somewhere mid-run: stop moving the view
  shown: null,        // the last step the live view put on screen
};

async function boot() {
  ui.active = viewFromHash();

  // Start blank: the page fills in from the run you start, never from the
  // bundled record - with or without a backend. `?run=` still loads a saved
  // record on purpose.
  await connect();
  if (sourceFromLocation().kind === "bundled") {
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
    setFeedbackBackend(ui.live ? ui.api : null, tokenFromLocation());
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
    "Backend URL.\n\nLocal:  http://localhost:8000\nModal:  https://<workspace>--sckrino-web.modal.run\n\n" +
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
  ui.gen += 1;
  ui.follow = Promise.resolve();
  ui.pinned = false;
  ui.shown = null;
  renderStepCard(els.detail, null);  // stops whatever card was playing

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
  ui.gen += 1;
  ui.running = null;
  // The run itself carries on server-side; say that rather than imply it stopped.
  ui.status = { text: "stopped watching · the run continues on the backend" };
  drawChrome();
  draw();
}

function handleEvent(event) {
  // The record, the status line and the rail follow the stream at once; the
  // detail pane is queued, so a step's exchange finishes playing before the
  // view moves on to the next one.
  const gen = ui.gen;
  switch (event.type) {
    case "start":
      ui.status = { running: true, text: `run ${event.run_id}` };
      ui.running = null;
      break;

    case "step_start":
      ui.running = event.step;
      ui.status = { running: true, text: `running ${event.step}` };
      // if it has already finished by the time the queue gets here, its
      // step_done entry shows it instead
      if (!ui.pinned) follow(gen, () => (ui.running === event.step ? show(event.step) : null));
      break;

    case "step_done":
      ui.running = null;
      if (event.run) ui.run = event.run;
      ui.shown = event.step;
      if (!ui.pinned) follow(gen, () => show(event.step, { animate: true }));
      break;

    case "done":
      ui.running = null;
      ui.abort = null;
      if (event.run) ui.run = event.run;
      ui.status = { text: `finished in ${event.seconds}s` };
      if (!ui.pinned) follow(gen, () => show(OVERVIEW));
      break;

    case "error":
      ui.running = null;
      ui.abort = null;
      ui.gen += 1;  // the error is shown now, not after the queue drains
      if (event.run) ui.run = event.run;
      ui.status = { error: truncate(event.message) };
      drawChrome();
      if (ui.run) draw();
      else showMessage("The run failed", event.message);
      return;

    default:
      return;
  }
  drawChrome();
  drawRail();
}

/** Queue a live view change behind whatever is playing now. */
function follow(gen, fn) {
  ui.follow = ui.follow
    .then(() => (gen === ui.gen ? fn() : null))
    .catch((err) => console.error(err));
}

/** Put one view on screen for the live run. Resolves when it has played. */
function show(name, { animate = false } = {}) {
  ui.active = name;
  window.history.replaceState(null, "", `#${name}`);
  if (!ui.run) {
    showEmpty(ui.running ? `running ${ui.running}…` : "starting…");
    return null;
  }
  return draw({ animate });
}

/* -- rendering ----------------------------------------------------------- */

function drawChrome() {
  if (ui.run) {
    renderHeader(els.topbar, ui.run, { onReplay: ui.api ? null : toggleReplay });
    renderBanner(els.banner, ui.run, { replay: !ui.abort && !ui.run.provenance?.startsWith("Live run, streamed") });
  } else {
    renderHeader(els.topbar, null);
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
    onFollow: ui.abort && ui.pinned ? resumeFollow : null,
  });
}

function drawRail() {
  renderStepper(els.rail, ui.run, {
    active: ui.run ? ui.active : null, running: ui.running, onSelect: pick,
  });
}

/** Redraw the rail and the active view. Only the live stream passes
 * `animate`; a click, a hash change or a saved record renders finished. */
function draw({ animate = false } = {}) {
  if (!ui.run) return Promise.resolve();
  drawRail();
  if (ui.active === OVERVIEW) {
    renderStepCard(els.detail, null);  // stops whatever card was playing
    renderOverview(els.detail, ui.run, { onSelect: pick });
    return Promise.resolve();
  }
  return renderStepCard(els.detail, ui.run, ui.active, STEP_INDEX[ui.active] ?? 0,
                        { running: ui.running === ui.active, animate });
}

/** A click by the viewer. It wins over everything automatic: the step-through
 * stops, and during a live run the view stays where they put it (queued views
 * are dropped) until they ask to follow the run again. */
function pick(name) {
  stopReplay();
  // Drop whatever the live view still had queued, even after the stream has
  // ended: a replayed run finishes faster than its exchanges can play, and the
  // queue kept moving the view after the viewer had clicked somewhere else.
  ui.gen += 1;
  ui.follow = Promise.resolve();
  if (ui.abort && !ui.pinned) {
    ui.pinned = true;
    drawChrome();
  }
  select(name);
}

/** Back to following the live run from wherever it has got to. */
function resumeFollow() {
  ui.pinned = false;
  drawChrome();
  select(ui.running ?? ui.shown ?? OVERVIEW);
}

function select(name, { quiet = false } = {}) {
  ui.active = name;
  window.history.replaceState(null, "", `#${name}`);
  if (!quiet) {
    const played = draw();
    els.rail.scrollIntoView({ behavior: "smooth", block: "start" });
    return played;
  }
  return Promise.resolve();
}

/** The blank state: no record yet, only the step list and what to do. */
function showEmpty(progress = null) {
  renderStepper(els.rail, null, { active: null, running: ui.running, onSelect: pick });
  els.detail.replaceChildren(h("section", { class: "card" },
    h("div", { class: "card-head" }, h("h2", { text: progress ? "Running" : "Nothing has run yet" })),
    h("div", { class: "card-body" },
      h("p", { class: "lede", text: progress
        ? `${progress[0].toUpperCase()}${progress.slice(1)} Each step lights up above as it finishes.`
        : ui.live
          ? "Choose a dataset and press Run. Each step lights up above as it finishes."
          : "No backend is connected. Connect one, then choose a dataset and press Run." }),
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
  resumeAnimations();
  const walk = { stopped: false };
  ui.replayTimer = walk;
  (async () => {
    for (const step of STEPS) {
      if (walk.stopped) return;
      await select(step.name);
      await pause(1800);  // a beat to look at the figures before moving on
    }
    if (!walk.stopped) { ui.replayTimer = null; select(OVERVIEW); }
  })();
}

function stopReplay() {
  if (ui.replayTimer) ui.replayTimer.stopped = true;
  ui.replayTimer = null;
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
  if (name !== ui.active) pick(name);
});

boot();
