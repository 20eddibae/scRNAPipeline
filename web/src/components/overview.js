/* The summary view: what happened to the data, the picture at the end, every
 * decision in one table, and the scores. Rows link to their step. */

import { QUESTIONS } from "../steps/spec.js";
import { buildPanel } from "../viz/index.js";
import { umapView } from "../viz/umap.js";
import { fmt, h } from "./dom.js";

export function renderOverview(root, run, { onSelect }) {
  const e = run.viz.embedding;
  const umap = e?.coords?.length ? umapView(e, { initial: e.label ? "label" : "cluster", height: 520 }) : null;

  const card = h("section", { class: "card" },
    h("div", { class: "card-head" },
      h("h2", { text: "Summary" }),
      h("span", { class: "spacer" }),
      h("span", { class: "meta mono", text: run.runId }),
    ),
    h("div", { class: "card-body" },
      funnel(run, onSelect),
      umap ?? h("div", { class: "viz-empty", text: "The UMAP appears once the cluster step has run." }),
      h("div", {},
        h("h3", { class: "section", text: "Decisions" }),
        h("p", { class: "small", text:
          `Jev answers each question with a probability. When its confidence is below ` +
          `${run.confidenceFloor.toFixed(2)}, the step uses its declared default instead.` }),
        decisionTable(run, onSelect),
      ),
      buildPanel("metrics", run),
    ),
  );

  root.replaceChildren(card);
}

/** Cells, genes and groups as they move through the run, one box per stage. */
function funnel(run, onSelect) {
  const s = (step) => run.summary(step) ?? {};
  const stages = [
    ["load", s("load").n_cells, "cells loaded"],
    ["qc", s("qc").cells_after, "cells after QC"],
    ["features", s("features").n_hvg, "variable genes"],
    ["features", s("features").n_pcs, "PCs"],
    ["cluster", s("cluster").n_clusters, "clusters"],
    ["annotate", s("annotate").n_types_assigned, "cell types named"],
  ];
  return h("div", { class: "funnel" }, stages.map(([step, v, label]) => {
    const known = Number.isFinite(Number(v));
    return h("button", {
      class: `stage${known ? "" : " pending"}`, onClick: () => onSelect(step),
    },
      h("div", { class: "v", text: known ? Number(v).toLocaleString() : "–" }),
      h("div", { class: "k", text: label }),
    );
  }));
}

function decisionTable(run, onSelect) {
  const rows = run.timeline().flatMap((e) => e.decisions);
  if (!rows.length) return h("p", { class: "small", text: "No decisions recorded yet." });

  return h("table", { class: "plain" },
    h("thead", {}, h("tr", {},
      h("th", { text: "step" }), h("th", { text: "question" }), h("th", { text: "used" }),
      h("th", { text: "decided by" }), h("th", { class: "num", text: "confidence" }))),
    h("tbody", {}, rows.map((d) => h("tr", { class: "link", onClick: () => onSelect(d.step) },
      h("td", { class: "dim", text: d.step }),
      h("td", { text: QUESTIONS[`${d.step}.${d.question}`] ?? d.question }),
      h("td", { class: "mono", text: shown(d) }),
      h("td", {}, d.fellBack()
        ? h("span", { class: "tag fallback", text: "default" })
        : h("span", { class: "tag jev", text: "Jev" })),
      h("td", { class: "num mono", text: d.confidence === null ? "–" : d.confidence.toFixed(2) }),
    ))),
  );
}

function shown(d) {
  if (d.kind === "noul" || typeof d.value === "boolean") return String(d.value) === "true" ? "yes" : "no";
  return fmt(d.value);
}
