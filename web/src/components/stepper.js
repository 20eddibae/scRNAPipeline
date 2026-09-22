/* The pipeline: all eight steps across the top of the page, one node each.
 * A node says who makes the call in that step, whether it ran, and what it
 * produced, read from that step's own summary. With no record yet it still
 * draws, so the shape of the run is visible before anything has happened. */

import { OVERVIEW, STEPS } from "../steps/spec.js";
import { h, stepResult } from "./dom.js";

// A step that has not run yet shows the same check, greyed; it fills in green
// when the step finishes.
const MARK = { ok: "✓", pending: "✓", error: "✗", skipped: "–" };
const WHO = {
  jev: { cls: "jev", text: "Jev decides" },
  claude: { cls: "claude", text: "Claude decides" },
  neither: { cls: "", text: "no decision" },
};

/**
 * Built once, then patched in place. The live stream redraws the rail after
 * every event; replacing the buttons each time meant a click whose mousedown
 * and mouseup straddled a redraw landed on a detached node and did nothing.
 * `onSelect` is read at click time, so the latest handler always wins.
 */
export function renderStepper(root, run, { active, running, onSelect }) {
  const entries = run ? run.timeline()
    : STEPS.map((spec) => ({ spec, status: "pending", record: null, decisions: [] }));
  const rail = root._rail ?? build(root);
  rail.onSelect = onSelect;

  entries.forEach((entry, i) => {
    const { spec, status, record, decisions } = entry;
    const el = rail.steps.get(spec.name);
    if (!el) return;
    const live = spec.name === running;
    const state = live ? "active" : status === "ok" ? "done" : status;
    const who = WHO[spec.decidedBy] ?? WHO.neither;
    const note = live ? "running…"
      : status === "pending" ? ""
      : stepResult(spec, record?.summary, "brief");
    const tag = decisions.length
      ? `${decisions.length} decision${decisions.length > 1 ? "s" : ""}` : who.text;

    el.li.className = `pipe-step ${state}`;
    setCurrent(el.button, spec.name === active);
    el.button.disabled = !run || (status === "pending" && !live);
    el.dot.textContent = live ? "" : MARK[status] ?? String(i + 1);
    el.who.className = `pipe-who ${who.cls}`.trim();
    el.who.textContent = tag;
    el.note.textContent = note;
  });

  setCurrent(rail.summary, active === OVERVIEW);
  rail.summary.disabled = !run;
}

function build(root) {
  const rail = { steps: new Map(), onSelect: null, summary: null };
  const pick = (name) => rail.onSelect?.(name);

  const items = STEPS.map((spec) => {
    const dot = h("span", { class: "pipe-dot" });
    const who = h("span", { class: "pipe-who" });
    const note = h("span", { class: "pipe-note" });
    const button = h("button", { class: "pipe-node", onClick: () => pick(spec.name) },
      dot, h("span", { class: "pipe-title", text: spec.title }), who, note);
    const li = h("li", { class: "pipe-step" }, button);
    rail.steps.set(spec.name, { li, button, dot, who, note });
    return li;
  });
  rail.summary = h("button", { class: "pipe-summary", onClick: () => pick(OVERVIEW) }, "Summary");

  root.replaceChildren(h("div", { class: "pipe-wrap" }, rail.summary, h("ol", { class: "pipe" }, items)));
  root._rail = rail;
  return rail;
}

function setCurrent(el, on) {
  if (on) el.setAttribute("aria-current", "true");
  else el.removeAttribute("aria-current");
}
