/* The landing view: the whole run on one screen.
 *
 * Three things, in the order a reader needs them - the eight steps as a flow,
 * every decision the run made in one ledger, and what it scored. Each row in
 * the flow and the ledger is a link into that step's detail. */

import { APP } from "../config.js";
import { buildPanel } from "../viz/index.js";
import { chip, fmt, h, sectionLabel } from "./dom.js";

const WHO_CLASS = { jev: "jev", claude: "claude", neither: "fallback" };
const WHO_TEXT = { jev: "Jev", claude: "Claude", neither: "—" };

export function renderOverview(root, run, { onSelect }) {
  const timeline = run.timeline();
  const t = run.tally();
  // counted, not asserted: a step with no batches asks Jev nothing at all
  const withDecisions = timeline.filter((e) => e.decisions.length).length;

  const card = h("section", { class: "card" },
    h("div", { class: "card-head" },
      h("h2", { text: "The run, end to end" }),
      h("span", { class: "spacer", style: "margin-left:auto" }),
      chip(run.runId, "mono"),
    ),
    h("div", { class: "card-body" },
      h("p", { class: "lede", text:
        `Eight steps. ${withDecisions} of them contained a choice that a person ` +
        "would normally make once in a notebook and never revisit. Here each one " +
        "is a typed question with a probability on it, and a floor below which " +
        "the pipeline refuses the answer and takes its declared default instead." }),

      h("div", { class: "kv" },
        actorCell("jev", APP.actors.jev, `${t.jev} of ${t.total} decisions answered`),
        actorCell("claude", APP.actors.claude, `${t.claudeSteps} step reads the markers`),
        actorCell("fallback", { label: "Declared default",
          blurb: "What the step does when no answer clears the floor." },
          `${t.fallback} of ${t.total} decisions`),
      ),

      sectionLabel("the eight steps"),
      h("div", { class: "options" }, timeline.map((entry, i) => stepRow(entry, i, onSelect))),

      sectionLabel("every decision this run made"),
      ledger(run, onSelect),

      buildPanel("metrics", run),
    ),
  );

  root.replaceChildren(card);
}

function actorCell(variant, actor, note) {
  return h("div", { class: "kv-cell" },
    chip(actor.label, variant),
    h("div", { class: "kv-k", style: "margin-top:6px", text: note }),
    h("div", { style: "font-size:12px;color:var(--text-dim);margin-top:4px", text: actor.blurb }),
  );
}

function stepRow(entry, i, onSelect) {
  const { spec, status, decisions } = entry;
  const pending = status === "pending";
  return h("button", {
    class: pending ? "option pending" : "option",
    style: "text-align:left;cursor:pointer;font:inherit;color:inherit;width:100%",
    onClick: () => onSelect(spec.name),
  },
    h("span", { class: "option-key", text: `${i + 1}. ${spec.name}` }),
    h("span", { class: "option-p" },
      pending ? "" : chip(WHO_TEXT[spec.decidedBy], WHO_CLASS[spec.decidedBy]),
      decisions.length ? ` ${decisions.length}` : "",
      status === "ok" || pending ? "" : ` · ${status}`,
    ),
    h("span", { class: "option-desc", text: pending ? "" : spec.blurb }),
  );
}

/** One row per decision: what was asked, what came back, and on what evidence. */
function ledger(run, onSelect) {
  const rows = run.timeline().flatMap((e) => e.decisions);
  if (!rows.length) {
    return h("div", { class: "viz-empty", text: "this run recorded no decisions" });
  }

  return h("div", { class: "options" }, rows.map((d) => {
    const top = d.options().find((o) => o.picked);
    const p = Number.isFinite(top?.probability) ? `${(top.probability * 100).toFixed(0)}%` : "—";
    return h("button", {
      class: "option",
      style: "text-align:left;cursor:pointer;font:inherit;color:inherit;width:100%",
      onClick: () => onSelect(d.step),
    },
      Number.isFinite(top?.probability)
        ? h("span", { class: "fill", style:
            `width:${top.probability * 100}%;background:${d.fellBack() ? "var(--fallback-soft)" : "var(--jev-soft)"}` })
        : null,
      h("span", { class: "option-key", text: `${d.step}.${d.question} → ${fmt(d.value)}` }),
      h("span", { class: "option-p" },
        p, " ",
        d.fellBack() ? chip("default", "fallback") : chip("Jev", "jev"),
      ),
      h("span", { class: "option-desc", text:
        d.confidence === null
          ? "no confidence returned with this answer"
          : `confidence ${d.confidence.toFixed(2)} against a floor of ${d.floor.toFixed(2)}` +
            (d.belowFloor() ? " — the declared default was used instead" : "") }),
    );
  }));
}
