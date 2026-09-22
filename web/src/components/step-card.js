/* The detail pane for one step: what it does, the state it was handed, the
 * decisions taken inside it, Claude's prose, the plots, and what came out. */

import { buildPanels } from "../viz/index.js";
import { renderDecision } from "./decision.js";
import { renderLabels, renderReasoning } from "./reasoning.js";
import { chip, fmt, h, sectionLabel } from "./dom.js";

const WHO_CHIP = {
  jev: () => chip("decision point", "jev"),
  claude: () => chip("Claude reads the markers", "claude"),
  neither: () => chip("no decision point", "fallback"),
};

const HIDE_FROM_SUMMARY = new Set(["framing", "labels", "markers"]);

export function renderStepCard(root, run, stepName, index, { running = false } = {}) {
  const entry = run.timeline().find((e) => e.spec.name === stepName);
  if (!entry) { root.replaceChildren(); return; }

  const { spec, record, status, decisions } = entry;
  const panels = buildPanels(spec.viz, run);
  const reasoning = renderReasoning(run.reasoning(spec.name));

  const card = h("section", { class: "card" },
    h("div", { class: "card-head" },
      h("h2", { text: `${index + 1}. ${spec.title}` }),
      h("code", { class: "decision-type", text: spec.name }),
      h("span", { class: "spacer" }),
      WHO_CHIP[spec.decidedBy](),
      running
        ? chip("running…", "modal")
        : chip(status === "ok" ? `ran in ${fmt(entry.seconds)}s` : status,
               status === "ok" ? "mono" : "fallback"),
    ),
    h("div", { class: "card-body" },
      h("p", { class: "lede", text: spec.blurb }),
      h("p", { class: "lede", text: spec.detail }),

      decisions.length
        ? h("div", {}, sectionLabel("decisions taken inside this step"),
            decisions.map(renderDecision))
        : running
          ? h("p", { class: "lede", text:
              "Claude is framing this step's questions and Jev is answering them…" })
          : null,

      reasoning,
      spec.name === "annotate" ? renderLabels(run) : null,

      panels.length
        ? h("div", {}, sectionLabel("what the data looks like here"),
            h("div", { class: "viz-grid" }, panels))
        : null,

      record ? renderSummary(record.summary) : null,
    ),
  );

  root.replaceChildren(card);
}

function renderSummary(summary) {
  const pairs = Object.entries(summary ?? {})
    .filter(([k, v]) => !HIDE_FROM_SUMMARY.has(k) && typeof v !== "object");
  if (!pairs.length) return null;

  return h("div", {}, sectionLabel("what this step recorded"),
    h("div", { class: "kv" },
      pairs.map(([k, v]) => h("div", { class: "kv-cell" },
        h("div", { class: "kv-k", text: k }),
        h("div", { class: "kv-v", text: fmt(v) }),
      )),
    ),
  );
}
