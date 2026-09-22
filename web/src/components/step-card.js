/* The detail pane for one step: what it does, what came out, the decisions
 * taken inside it, and the figures. */

import { buildPanels } from "../viz/index.js";
import { renderDecision } from "./decision.js";
import { renderReasoning } from "./reasoning.js";
import { h, fmt, stepResult } from "./dom.js";

export function renderStepCard(root, run, stepName, index, { running = false } = {}) {
  const entry = run.timeline().find((e) => e.spec.name === stepName);
  if (!entry) { root.replaceChildren(); return; }
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

  const card = h("section", { class: "card" },
    head,
    h("div", { class: "card-body" },
      h("p", { class: "lede", text: `${spec.blurb} ${spec.detail}` }),

      result ? h("div", { class: "result", text: result }) : null,
      record?.error ? h("div", { class: "result", style: "color:var(--err)", text: record.error }) : null,

      decisions.length
        ? h("div", {},
            h("h3", { class: "section", text: decisions.length > 1 ? "Decisions" : "Decision" }),
            decisions.map(renderDecision))
        : running
          ? h("p", { class: "small", text: "Claude is writing this step's questions and Jev is answering them." })
          : spec.decidedBy === "jev"
            ? h("p", { class: "small", text: "No question was asked in this step on this dataset." })
            : null,

      renderReasoning(run.reasoning(spec.name).filter((r) => r.kind !== "framing")),

      panels.length ? panels : null,
    ),
  );

  root.replaceChildren(card);
}
