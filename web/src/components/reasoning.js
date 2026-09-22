/* Claude's side of the run, simplified to what a reader needs:
 *  - how it reframed a generic question for this particular matrix
 *  - what it concluded from the markers, and any closing narration. */

import { h, chip } from "./dom.js";

export function renderReasoning(entries) {
  if (!entries.length) return null;
  return h("div", { class: "reasoning" },
    h("h4", { text: "Claude" }),
    entries.map((e) => h("p", {},
      e.question ? chip(e.question, "claude") : null,
      e.question ? " " : null,
      e.text,
    )),
  );
}

/** The annotate step's labels, as cluster → cell type with its evidence. */
export function renderLabels(run) {
  const summary = run.summary("annotate");
  const labels = run.labels();
  if (!Object.keys(labels).length) return null;
  const markers = summary?.markers ?? {};
  const rows = Object.entries(labels).sort(
    (a, b) => Number(a[0]) - Number(b[0]) || a[0].localeCompare(b[0]),
  );

  return h("div", { class: "options" },
    rows.map(([cluster, label]) => h("div", { class: "option" },
      h("span", { class: "option-key", text: `${cluster} → ${label}` }),
      h("span", { class: "option-p", text: summary?.source ?? "" }),
      markers[cluster]
        ? h("span", { class: "option-desc mono", text: markers[cluster].join(", ") })
        : null,
    )),
  );
}
