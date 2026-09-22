/* One decision, as the exchange it was: Claude's framing of the question, the
 * hand-off, Jev's probability on each option, and a sentence saying what was
 * used and why.
 *
 * Everything is read off the run record. The card never infers a probability
 * that was not returned. */

import { QUESTIONS } from "../steps/spec.js";
import { fmt, h } from "./dom.js";
import { hasPlayed, markPlayed, typeInto, wait } from "./typewriter.js";
import { whatIf } from "./whatif.js";

/**
 * Returns the decision's node. `node.play()` runs it as a sequence - Claude
 * frames, hands off, Jev answers, the floor is checked - and resolves when it
 * has finished; a decision already played renders finished and plays nothing.
 *
 * `animate` is true only when the decision has just arrived from a live run.
 * Opened any other way - a click on a step, a saved record - it is a record
 * being read, so it renders finished.
 */
export function renderDecision(decision, { animate = false } = {}) {
  const options = decision.options();
  const id = `${decision.step}.${decision.question}`;
  const key = `${decision.runId}/${id}`;  // a second run in the same page plays again
  const fresh = animate && !hasPlayed(key);
  const framing = decision.instructions;

  const said = h("p", { class: "turn-text", text: fresh ? "" : framing });
  const claude = framing ? h("div", { class: `turn ${decision.framed ? "claude" : "baseline"}` },
    h("div", { class: "turn-who" },
      h("span", { class: "avatar", text: decision.framed ? "C" : "?" }),
      decision.framed ? "Claude frames the question for this dataset" : "The question as written in the step"),
    said,
  ) : null;

  const handoff = h("div", { class: "handoff" },
    h("span", { class: "handoff-line" }),
    `sent to Jev · ${options.filter((o) => o.offered).length} options`);

  const rows = options.map((o) => renderOption(o, decision));
  const conf = renderConfidence(decision);
  const adjust = whatIf(decision, rows, conf, {
    onChange: (on) => jev.classList.toggle("adjusting", on),
  });
  const jev = h("div", { class: "turn jev" },
    h("div", { class: "turn-who" }, h("span", { class: "avatar", text: "J" }), "Jev answers with a probability on each option",
      adjust ? h("span", { class: "spacer" }) : null, adjust?.button ?? null),
    h("table", { class: "opts" }, h("tbody", {}, rows.map((r) => r.row))),
    conf?.node ?? null,
    adjust?.panel ?? null,
  );
  const verdictEl = h("div", { class: "decision-verdict" }, ...verdict(decision));

  const node = h("div", { class: "decision" },
    h("div", { class: "decision-head" },
      h("span", { class: "decision-q", text: QUESTIONS[id] ?? decision.question }),
      h("span", { class: "decision-id", text: id }),
    ),
    h("div", { class: "exchange" }, claude, handoff, jev),
    verdictEl,
  );

  const later = [handoff, jev, verdictEl];
  const learnedEl = learnedNote(decision);
  if (learnedEl) { verdictEl.after(learnedEl); later.push(learnedEl); }
  if (fresh) {
    later.forEach((el) => el.classList.add("unrevealed"));
    rows.forEach((r) => r.set(0));
    conf?.set(0);
  }

  node.fresh = fresh;
  node.play = async (signal) => {
    if (!fresh || hasPlayed(key) || signal?.aborted) { reveal(); return; }
    markPlayed(key);  // started counts: a rebuild mid-play shows it finished
    if (claude) {
      claude.classList.add("speaking");
      await typeInto(said, framing, { signal });
      claude.classList.remove("speaking");
      await wait(300, signal);
    }
    handoff.classList.remove("unrevealed");
    await wait(450, signal);
    jev.classList.remove("unrevealed");
    jev.classList.add("speaking");
    await wait(250, signal);
    for (const r of rows) { r.set(); await wait(160, signal); }
    await wait(250, signal);
    conf?.set();
    await wait(600, signal);
    jev.classList.remove("speaking");
    verdictEl.classList.remove("unrevealed");
  };

  function reveal() {
    if (claude) said.textContent = framing;
    later.forEach((el) => el.classList.remove("unrevealed"));
    rows.forEach((r) => r.set());
    conf?.set();
  }

  return node;
}

/** When saved corrections were blended in: say so, and what Jev alone said. */
function learnedNote(d) {
  const l = d.raw.raw?.learned;
  if (!l) return null;
  const jevSaid = l.jev_value !== undefined && l.jev_value !== null ? display(d, l.jev_value) : "?";
  const conf = Number.isFinite(l.jev_confidence) ? ` at confidence ${l.jev_confidence.toFixed(2)}` : "";
  return h("div", { class: "decision-learned" },
    h("span", { class: "tag learned", text: "learned" }),
    ` Blended with ${l.n} scientist correction${l.n === 1 ? "" : "s"} (weight ${Number(l.weight).toFixed(2)}). ` +
    `Jev alone said ${jevSaid}${conf}.`);
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

  const fill = h("i", { style: `width:${p !== null ? p * 100 : 0}%` });
  const pct = h("td", { class: "p", text: !option.offered ? "removed" : p !== null ? `${Math.round(p * 100)}%` : "" });
  const mark = h("td", { class: "mark", text: option.picked ? "✓" : "" });
  // shown in place of the bar while the scientist is adjusting (whatif.js)
  const slider = h("input", { type: "range", min: 0, max: 100, step: 1, class: "pslider",
    value: Math.round((p ?? 0) * 100), "aria-label": `probability of ${display(decision, value)}` });
  const row = h("tr", { class: classes.join(" ") },
    mark,
    h("td", { class: "key", text: display(decision, value) }),
    h("td", { class: "bar" }, h("div", { class: "pbar" }, fill), slider),
    pct,
    h("td", { class: "desc", text: tidy(option.description) === String(display(decision, value)) ? "" : tidy(option.description) }),
  );
  const final = pct.textContent;
  // set(0) empties the bar for the animation; set() puts the recorded value back
  const set = (to) => {
    const shown = to === 0 ? 0 : p ?? 0;
    fill.style.width = `${shown * 100}%`;
    pct.textContent = to === 0 && option.offered && p !== null ? "" : final;
    row.classList.toggle("settled", to !== 0);
  };
  // a what-if value: moves the bar, the percentage and the check mark together
  const show = (q, picked, fellBack) => {
    fill.style.width = `${q * 100}%`;
    pct.textContent = `${Math.round(q * 100)}%`;
    slider.value = Math.round(q * 100);
    mark.textContent = picked ? "✓" : "";
    row.classList.toggle("picked", picked);
    row.classList.toggle("default", picked && fellBack);
  };
  return { row, set, show, slider, key: option.key, offered: option.offered, picked: option.picked, p };
}

/** Confidence against the floor: one short track with the floor marked.
 * Returns {node, set} so the track can fill in as the last beat. */
function renderConfidence(d) {
  if (d.confidence === null) return null;
  const pct = Math.max(0, Math.min(1, d.confidence)) * 100;
  const bar = h("span", { class: `conf-bar ${d.belowFloor() ? "below" : ""}`.trim(), style: `width:${pct}%` });
  // what-if only: Jev reports confidence apart from the probabilities, so the
  // scientist can move it on its own (whatif.js). Hidden otherwise.
  const slider = d.kind === "noul" ? null : h("input", { type: "range", min: 0, max: 100, step: 1,
    class: "cslider", value: Math.round(pct), "aria-label": "Jev's confidence" });
  const readout = h("span", { class: "mono", text: `${d.confidence.toFixed(2)} ${d.belowFloor() ? "<" : "≥"} floor ${d.floor.toFixed(2)}` });
  const node = h("div", { class: "conf", title:
    "Jev reports its confidence separately from the probabilities, so it is not the tallest bar. " +
    "On a yes/no question with none reported, it is the distance from 50/50." },
    h("span", { text: "Jev's confidence" }),
    h("span", { class: "conf-track", title: `floor ${d.floor.toFixed(2)}` },
      bar,
      h("span", { class: "conf-floor", style: `left:${d.floor * 100}%` }),
    ),
    slider,
    readout,
  );
  const set = (to) => {
    bar.style.width = `${to === 0 ? 0 : pct}%`;
    readout.style.visibility = to === 0 ? "hidden" : "";
  };
  const show = (c) => {
    const below = c < d.floor;
    bar.style.width = `${Math.max(0, Math.min(1, c)) * 100}%`;
    bar.classList.toggle("below", below);
    readout.textContent = `${c.toFixed(2)} ${below ? "<" : "≥"} floor ${d.floor.toFixed(2)}`;
    if (slider) slider.value = Math.round(c * 100);
  };
  return { node, set, show, slider };
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
