/* A decision card: the question, the options Jev had, the probability on each,
 * and how the confidence sat against the floor.
 *
 * This is the part of the demo that is the point. Everything here is read off
 * the run record - the card never infers a probability that was not returned. */

import { h, chip, sectionLabel } from "./dom.js";

const KIND_LABEL = { choice: "Choice", noul: "Noul", score: "Score" };

export function renderDecision(decision) {
  const options = decision.options();
  const hasProbabilities = options.some((o) => Number.isFinite(o.probability));

  return h("div", { class: "decision" },
    h("div", { class: "decision-head" },
      h("span", { class: "decision-q", text: decision.question }),
      h("span", { class: "decision-type", text: KIND_LABEL[decision.kind] ?? decision.kind }),
      h("span", { class: "spacer", style: "margin-left:auto" }),
      decision.fellBack()
        ? chip("default taken", "fallback")
        : chip("answered by Jev", "jev"),
      decision.framed ? chip("framed by Claude", "claude") : null,
    ),

    decision.instructions
      ? h("div", { class: `decision-instr ${decision.framed ? "framed" : ""}`,
                   text: decision.instructions })
      : null,

    sectionLabel(hasProbabilities
      ? "options, with the probability Jev put on each"
      : "options Jev was given"),
    h("div", { class: "options" }, options.map((o) => renderOption(o, decision))),

    renderConfidence(decision),
    decision.note ? h("div", { class: "note", text: decision.note }) : null,
  );
}

function renderOption(option, decision) {
  const picked = option.picked;
  const p = Number.isFinite(option.probability) ? option.probability : null;

  const classes = ["option"];
  if (picked) classes.push(decision.fellBack() ? "picked-default" : "picked");
  if (!option.offered) classes.push("dropped");

  return h("div", { class: classes.join(" ") },
    h("span", { class: "fill", style: `width:${p !== null ? p * 100 : 0}%` }),
    h("span", { class: "option-key", text: labelFor(option, decision) }),
    h("span", { class: "option-p",
                text: option.offered ? (p !== null ? `${(p * 100).toFixed(1)}%` : "—")
                                     : "dropped in framing" }),
    option.description ? h("span", { class: "option-desc", text: option.description }) : null,
  );
}

function labelFor(option, decision) {
  if (decision.kind === "score" && Array.isArray(decision.raw.values)) {
    const v = decision.raw.values[Number(option.key)];
    return v === undefined ? option.key : `${option.key} → ${v}`;
  }
  return option.key;
}

/** Confidence against the floor, drawn as one track with a floor marker. */
function renderConfidence(decision) {
  if (decision.confidence === null) {
    return h("div", { class: "conf-legend" },
      h("span", { text: "no confidence returned" }),
      h("span", { text: `floor ${decision.floor.toFixed(2)}` }),
    );
  }
  const pct = Math.max(0, Math.min(1, decision.confidence)) * 100;
  return h("div", { class: "conf" },
    h("div", { class: "conf-track" },
      h("span", { class: `conf-bar ${decision.belowFloor() ? "below" : ""}`.trim(),
                  style: `width:${pct}%` }),
      h("span", { class: "conf-floor", style: `left:${decision.floor * 100}%` }),
    ),
    h("div", { class: "conf-legend" },
      h("span", { text: `confidence ${decision.confidence.toFixed(2)}` }),
      h("span", { text: decision.belowFloor()
        ? `below floor ${decision.floor.toFixed(2)} → declared default used`
        : `above floor ${decision.floor.toFixed(2)} → answer used` }),
    ),
  );
}
