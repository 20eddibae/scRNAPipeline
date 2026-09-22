/* Bootstrap: load a run record, draw the rail, draw the selected step.
 *
 * The whole app is this file plus the components it calls. There is no state
 * container: the run record is immutable, the only mutable state is which step
 * is on screen, and it lives in the URL hash so a view is linkable. */

import { loadRun, sourceFromLocation } from "./data/source.js";
import { renderBanner, renderHeader } from "./components/header.js";
import { renderOverview } from "./components/overview.js";
import { renderStepCard } from "./components/step-card.js";
import { renderStepper } from "./components/stepper.js";
import { OVERVIEW, STEPS, STEP_INDEX } from "./steps/spec.js";
import { h } from "./components/dom.js";

const els = {
  topbar: document.getElementById("topbar"),
  banner: document.getElementById("banner"),
  rail: document.getElementById("rail"),
  detail: document.getElementById("detail"),
};

let run = null;
let active = stepFromHash();
let replayTimer = null;

async function boot() {
  try {
    run = await loadRun(sourceFromLocation());
  } catch (err) {
    els.detail.replaceChildren(h("section", { class: "card" },
      h("div", { class: "card-body" },
        h("h2", { text: "No run record" }),
        h("p", { class: "lede", text: String(err.message ?? err) }),
        h("p", { class: "lede", text:
          "Export one with `python scripts/export_run.py runs/<run_id> " +
          "web/data/run-demo.json`, or point the page at a URL with ?run=<url>." }),
      ),
    ));
    return;
  }

  renderHeader(els.topbar, run, { onReplay: toggleReplay });
  renderBanner(els.banner, run);
  draw();
}

function draw() {
  renderStepper(els.rail, run, { active, onSelect: select });
  if (active === OVERVIEW) renderOverview(els.detail, run, { onSelect: select });
  else renderStepCard(els.detail, run, active, STEP_INDEX[active] ?? 0);
}

function select(name) {
  active = name;
  window.history.replaceState(null, "", `#${name}`);
  draw();
  els.detail.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** Walk the eight steps on a timer - the demo's "press play". */
function toggleReplay() {
  if (replayTimer) { clearInterval(replayTimer); replayTimer = null; return; }
  let i = 0;
  select(STEPS[0].name);  // the replay walks the steps, not the overview
  replayTimer = setInterval(() => {
    i += 1;
    if (i >= STEPS.length) { clearInterval(replayTimer); replayTimer = null; return; }
    select(STEPS[i].name);
  }, 2600);
}

function stepFromHash() {
  const name = window.location.hash.replace("#", "");
  return name in STEP_INDEX || name === OVERVIEW ? name : OVERVIEW;
}

window.addEventListener("hashchange", () => {
  const name = stepFromHash();
  if (name !== active) { active = name; draw(); }
});

boot();
