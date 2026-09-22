/* One decision: the question, the options with Jev's probability on each, and a
 * sentence saying what was used and why.
 *
 * Everything is read off the run record. The card never infers a probability
 * that was not returned. */

import { QUESTIONS } from "../steps/spec.js";
import { fmt, h } from "./dom.js";

export function renderDecision(decision) {
  const options = decision.options();
  const id = `${decision.step}.${decision.question}`;

  return h("div", { class: "decision" },
    h("div", { class: "decision-head" },
      h("span", { class: "decision-q", text: QUESTIONS[id] ?? decision.question }),
      h("span", { class: "decision-id", text: id }),
    ),
    h("div", { class: "decision-verdict" }, ...verdict(decision)),
    h("table", { class: "opts" },
      h("tbody", {}, options.map((o) => renderOption(o, decision)))),
    renderConfidence(decision),
    decision.instructions
      ? h("details", { class: "decision-context" },
          h("summary", {}, decision.framed ? "How Claude put the question to Jev" : "The question as asked"),
          h("p", { text: decision.instructions }))
      : null,
  );
}

/** "Used X. Jev's confidence 0.93, above the 0.55 floor." as inline nodes. */
function verdict(d) {
  const used = display(d, d.value);
  const floor = d.floor.toFixed(2);
  if (d.source === "jev") {
    return ["Used ", h("b", { text: used }), `. Jev's confidence ${d.confidence?.toFixed(2) ?? "?"}` +
      `, above the ${floor} floor.`];
  }
  if (d.confidence === null) {
    return ["Used the default, ", h("b", { text: used }), ". No answer came back from Jev."];
  }
  const leaned = jevPick(d);
  return [
    "Used the default, ", h("b", { text: used }), ". ",
    leaned !== null && String(leaned) !== String(d.value)
      ? `Jev leaned towards ${display(d, leaned)}, but its confidence was `
      : "Jev's confidence was ",
    `${d.confidence.toFixed(2)}, below the ${floor} floor.`,
  ];
}

/** The value Jev itself would have picked, or null when that is not recorded. */
function jevPick(d) {
  const raw = d.raw.raw ?? {};
  if (d.kind === "choice" && raw.choice !== undefined) return raw.choice;
  if (d.kind === "noul" && Number.isFinite(raw.noul)) {
    return raw.noul >= (d.raw.threshold ?? 0.5);
  }
  const probs = raw.probabilities;
  if (d.kind === "score" && probs && Array.isArray(d.raw.values)) {
    const best = Object.entries(probs).sort((a, b) => b[1] - a[1])[0];
    return best ? d.raw.values[Number(best[0])] : null;
  }
  return null;
}

function renderOption(option, decision) {
  const p = Number.isFinite(option.probability) ? option.probability : null;
  const classes = [];
  if (option.picked) classes.push("picked");
  if (option.picked && decision.fellBack()) classes.push("default");
  if (!option.offered) classes.push("dropped");

  const value = decision.kind === "score" && Array.isArray(decision.raw.values)
    ? decision.raw.values[Number(option.key)] ?? option.key
    : option.key;

  return h("tr", { class: classes.join(" ") },
    h("td", { class: "mark", text: option.picked ? "✓" : "" }),
    h("td", { class: "key", text: display(decision, value) }),
    h("td", { class: "bar" },
      h("div", { class: "pbar" }, h("i", { style: `width:${p !== null ? p * 100 : 0}%` }))),
    h("td", { class: "p", text: !option.offered ? "removed" : p !== null ? `${Math.round(p * 100)}%` : "" }),
    h("td", { class: "desc", text: tidy(option.description) }),
  );
}

/** Confidence against the floor: one short track with the floor marked. */
function renderConfidence(d) {
  if (d.confidence === null) return null;
  const pct = Math.max(0, Math.min(1, d.confidence)) * 100;
  return h("div", { class: "conf" },
    h("span", { text: "confidence" }),
    h("span", { class: "conf-track", title: `floor ${d.floor.toFixed(2)}` },
      h("span", { class: `conf-bar ${d.belowFloor() ? "below" : ""}`.trim(), style: `width:${pct}%` }),
      h("span", { class: "conf-floor", style: `left:${d.floor * 100}%` }),
    ),
    h("span", { class: "mono", text: `${d.confidence.toFixed(2)} ${d.belowFloor() ? "<" : "≥"} floor ${d.floor.toFixed(2)}` }),
  );
}

function display(d, value) {
  if (d.kind === "noul" || typeof value === "boolean") {
    return String(value) === "true" ? "yes" : "no";
  }
  return fmt(value);
}

/** "Standard - a typical tissue" reads better as "Standard: a typical tissue". */
function tidy(text) {
  return (text ?? "").replace(/^(\w+) - /, "$1: ");
}
