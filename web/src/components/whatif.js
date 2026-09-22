/* "What if Jev had said...": a scientist drags one option's probability and
 * sees what the pipeline's rule would have done with that answer.
 *
 * The rule is the backend's, replayed here (jev.py `_to_decision`):
 *   choice  the most probable option
 *   score   the rung at the rounded expected index (Jev's score is a
 *           fractional index into the rungs)
 *   noul    yes when P(yes) >= threshold; confidence = |P(yes) - 0.5| * 2
 * and below the confidence floor the step's declared default is used instead.
 *
 * Jev reports confidence separately from the probabilities, so for choice and
 * score questions the confidence has its own slider, and dragging a
 * probability scales it by how much the top probability moved. Untouched, it
 * reads exactly what Jev said. A yes/no question's confidence is derived from
 * P(yes), as in the backend, so it has no slider of its own.
 *
 * Nothing here changes the run: the record, the summary and the steps
 * downstream all still show what actually happened. "Teach Jev" is what makes
 * it stick: it saves the tuned answer to the backend (POST /feedback), and
 * every later run blends Jev's answer to this question towards the saved
 * corrections (feedback.py). */

import { fmt, h } from "./dom.js";

let backend = null;  // {api, token}: set once a live backend answers

/** Where "Teach Jev" sends corrections; null hides the button. */
export function setFeedbackBackend(api, token = "") {
  backend = api ? { api: String(api).replace(/\/+$/, ""), token } : null;
}

/**
 * `rows` are the option rows from decision.js, each with
 *   { key, offered, p, picked, show(p, picked, fellBack), slider }.
 * Returns {button, panel}: the toggle, and the line that says what the
 * adjusted answer would have done (hidden until the toggle is on).
 */
export function whatIf(decision, rows, conf, { onChange } = {}) {
  const live = rows.filter((r) => r.offered && r.p !== null);
  if (live.length < 2) return null;  // nothing to trade probability between

  const original = live.map((r) => r.p);
  let probs = [...original];
  let on = false;
  // confidence = base.conf * top / base.top; the confidence slider re-bases it
  let base = { conf: decision.confidence, top: Math.max(...original) };

  const note = h("p", { class: "whatif-note" });
  const reset = h("button", { class: "linklike", onClick: () => {
    probs = [...original];
    base = { conf: decision.confidence, top: Math.max(...original) };
    update();
  } },
    "reset to Jev's answer");
  const teach = h("button", { class: "control primary teach", disabled: true, onClick: save },
    "Teach Jev");
  const saved = h("p", { class: "small teach-note" });
  const panel = h("div", { class: "whatif", hidden: true },
    note,
    h("p", { class: "small", text:
      "Raising one option lowers the others in proportion. This run is not changed; " +
      "Teach Jev saves your answer, and later runs blend Jev's answer to this question towards it." }),
    h("div", { class: "whatif-actions" }, backend ? teach : null, reset),
    saved,
  );

  // a sliding switch rather than a link: it is a mode, and it reads as one
  const check = h("input", { type: "checkbox", role: "switch", onChange: toggle,
    "aria-label": "tune Jev's probabilities" });
  const button = h("label", { class: "switch", title: "Tune Jev's probabilities by hand" },
    check, h("span", { class: "switch-track" }, h("span", { class: "switch-thumb" })), "Tune");

  live.forEach((row, i) => {
    row.slider.addEventListener("input", () => {
      rebalance(i, Number(row.slider.value) / 100);
      update();
    });
  });
  conf?.slider?.addEventListener("input", () => {
    base = { conf: Number(conf.slider.value) / 100, top: Math.max(...probs) };
    update();
  });

  async function save() {
    teach.disabled = true;
    saved.textContent = "saving…";
    const body = {
      run_id: decision.runId, dataset: decision.dataset,
      step: decision.step, question: decision.question,
      probabilities: Object.fromEntries(live.map((r, i) => [r.key, round(probs[i])])),
      jev_probabilities: Object.fromEntries(live.map((r, i) => [r.key, original[i]])),
    };
    if (decision.kind !== "noul") {
      body.confidence = round(decide(decision, live, probs, base).confidence);
    }
    try {
      const q = backend.token ? `?token=${encodeURIComponent(backend.token)}` : "";
      const res = await fetch(`${backend.api}/feedback${q}`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
      });
      const out = await res.json();
      if (!res.ok) throw new Error(out.detail ?? res.status);
      const n = out.learned?.n ?? 1;
      const w = n / (n + 2);
      saved.textContent = `Saved. From the next run, Jev's answer to this question is blended with ` +
        `${n} correction${n === 1 ? "" : "s"} (weight ${w.toFixed(2)}).`;
    } catch (err) {
      saved.textContent = `Not saved: ${err.message ?? err}`;
      teach.disabled = false;
    }
  }

  function toggle() {
    on = check.checked;
    panel.hidden = !on;
    onChange?.(on);
    if (on) { update(); return; }
    // back to exactly what the record says, not a re-derivation of it
    probs = [...original];
    base = { conf: decision.confidence, top: Math.max(...original) };
    live.forEach((row) => row.show(row.p, row.picked, decision.fellBack()));
    if (decision.confidence !== null) conf?.show(decision.confidence);
  }

  /** Set option i to p and scale the others so the total stays 1. */
  function rebalance(i, p) {
    const rest = probs.reduce((s, q, j) => (j === i ? s : s + q), 0);
    const left = 1 - p;
    probs = probs.map((q, j) => {
      if (j === i) return p;
      return rest > 0 ? (q / rest) * left : left / (probs.length - 1);
    });
  }

  function update() {
    const out = decide(decision, live, probs, base);
    live.forEach((row, i) => row.show(probs[i], row.key === out.key, out.fellBack));
    conf?.show(out.confidence);
    const changed = probs.some((q, i) => Math.abs(q - original[i]) > 1e-9)
      || base.conf !== decision.confidence;
    note.replaceChildren(...sentence(decision, out, changed));
    teach.disabled = !changed;
    if (changed) saved.textContent = "";
  }

  return { button, panel };
}

/** What the backend would have done with these probabilities. Exported for
 * the check that it reproduces every recorded decision at Jev's own answer. */
export function decide(d, live, probs, base = { conf: d.confidence, top: Math.max(...probs) }) {
  const floor = d.floor;
  const top = Math.max(...probs);
  let key, confidence;

  if (d.kind === "noul") {
    const pYes = probs[live.findIndex((r) => r.key === "true")] ?? 0;
    key = pYes >= (d.raw.threshold ?? 0.5) ? "true" : "false";
    confidence = Math.abs(pYes - 0.5) * 2;
  } else {
    if (d.kind === "score") {
      const expected = live.reduce((s, r, i) => s + Number(r.key) * probs[i], 0);
      const idx = Math.max(0, Math.min(live.length - 1, Math.round(expected)));
      key = live.find((r) => Number(r.key) === idx)?.key ?? live[0].key;
    } else {
      key = live[probs.indexOf(top)].key;
    }
    confidence = base.conf === null ? top
      : Math.min(1, base.conf * (base.top > 0 ? top / base.top : 1));
  }

  const fellBack = confidence < floor;
  const defaultKey = d.raw.default === undefined ? null : keyOf(d, d.raw.default);
  return { key: fellBack && defaultKey !== null ? defaultKey : key, jevKey: key, confidence, fellBack };
}

function sentence(d, out, changed) {
  const name = (key) => label(d, key);
  const lead = changed ? "With these probabilities the pipeline would use " : "As Jev answered, the pipeline uses ";
  if (!out.fellBack) {
    return [lead, h("b", { text: name(out.key) }), ` (confidence ${out.confidence.toFixed(2)}, above the ${d.floor.toFixed(2)} floor).`];
  }
  return [lead, "the default, ", h("b", { text: name(out.key) }),
    `: Jev would lean towards ${name(out.jevKey)}, but at confidence ${out.confidence.toFixed(2)} that is below the ${d.floor.toFixed(2)} floor.`];
}

function keyOf(d, value) {
  if (d.kind === "noul") return String(Boolean(value));
  if (d.kind === "score" && Array.isArray(d.raw.values)) {
    const i = d.raw.values.findIndex((v) => String(v) === String(value));
    return i >= 0 ? String(i) : null;
  }
  return String(value);
}

function label(d, key) {
  if (d.kind === "noul") return key === "true" ? "yes" : "no";
  if (d.kind === "score" && Array.isArray(d.raw.values)) return fmt(d.raw.values[Number(key)] ?? key);
  return fmt(key);
}

const round = (x) => Math.round(x * 1e4) / 1e4;
