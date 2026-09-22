/* The detail pane for one step: what it does, what came out, the decisions
 * taken inside it, and the figures. */

import { buildPanels } from "../viz/index.js";
import { renderDecision } from "./decision.js";
import { renderReasoning } from "./reasoning.js";
import { h, fmt, stepResult } from "./dom.js";
import { resumeAnimations, skipAnimations } from "./typewriter.js";

let current = null;  // the card now playing, so a new one can stop it

/** Mounts the card and, when `animate` is set (the step has just finished in a
 * live run), plays its decisions in order. Resolves when they have finished
 * playing, so the live view can wait before moving on. Without `animate` the
 * card renders finished: clicking back to a step shows it, it does not replay. */
export async function renderStepCard(root, run, stepName, index,
                                     { running = false, animate = false } = {}) {
  current?.abort();  // the card this replaces stops playing
  if (!run) return;
  const entry = run.timeline().find((e) => e.spec.name === stepName);
  if (!entry) { root.replaceChildren(); return; }
  resumeAnimations();  // a skip applies to the card it was pressed on
  const playing = new AbortController();
  current = playing;
  const { spec, record, status, decisions } = entry;

  const head = h("div", { class: "card-head" },
    h("h2", { text: `${index + 1}. ${spec.title}` }),
    h("span", { class: "spacer" }),
    h("span", { class: "meta", text: running ? "running…"
      : status === "ok" ? `${fmt(entry.seconds)} s`
      : status === "pending" ? "not run yet" : status }),
  );

  if (status === "pending" && !running) {
    root.replaceChildren(h("section", { class: "card pending" }, head));
    return;
  }

  const result = stepResult(spec, record?.summary);
  const panels = buildPanels(spec.viz, run);
  const nodes = decisions.map((d) => renderDecision(d, { animate }));
  const skip = h("button", { class: "linklike skip", onClick: () => { skipAnimations(); skip.remove(); } },
    "skip animation");

  const card = h("section", { class: "card" },
    head,
    h("div", { class: "card-body" },
      h("p", { class: "lede", text: `${spec.blurb} ${spec.detail}` }),

      result ? h("div", { class: "result", text: result }) : null,
      record?.error ? h("div", { class: "result", style: "color:var(--err)", text: record.error }) : null,

      decisions.length
        ? h("div", {},
            h("h3", { class: "section", text: decisions.length > 1 ? "Decisions" : "Decision" }),
            nodes)
        : running
          ? thinking(spec)
          : spec.decidedBy === "jev"
            ? h("p", { class: "small", text: "No question was asked in this step on this dataset." })
            : null,

      renderReasoning(run.reasoning(spec.name).filter((r) => r.kind !== "framing")),

      panels.length ? panels : null,
    ),
  );

  root.replaceChildren(card);

  // results and figures wait until the exchange that produced them has played
  const after = [...card.querySelectorAll(".card-body > .result, .card-body > .viz, .card-body > .viz-grid")];
  // later decisions wait their turn rather than sitting there empty
  nodes.slice(1).forEach((n) => { if (n.fresh) n.classList.add("unrevealed"); });
  if (nodes.some((n) => n.fresh)) {
    head.querySelector(".spacer").after(skip);
    after.forEach((el) => el.classList.add("unrevealed"));
  }
  for (const n of nodes) { n.classList.remove("unrevealed"); await n.play(playing.signal); }
  after.forEach((el) => el.classList.remove("unrevealed"));
  skip.remove();
}

/** While a live step runs: which of the two is working on it right now. */
function thinking(spec) {
  const who = spec.decidedBy === "claude" ? "Claude is reading the marker genes"
    : spec.decidedBy === "jev" ? "Claude is looking at the matrix and framing this step's questions for Jev"
    : "Modal is running this step";
  return h("div", { class: `turn ${spec.decidedBy === "neither" ? "baseline" : "claude"} speaking` },
    h("div", { class: "turn-who" }, h("span", { class: "avatar", text: spec.decidedBy === "neither" ? "M" : "C" }), who),
    h("p", { class: "turn-text" }, h("span", { class: "dots" }, h("i"), h("i"), h("i"))),
  );
}
