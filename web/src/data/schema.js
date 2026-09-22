/* The view model.
 *
 * Wraps one run record - the JSON that `scripts/export_run.py` writes out of
 * `runs/<id>/run.json` + `processed.h5ad` - in the handful of accessors the
 * components need. Everything the UI shows comes through here, so a change to
 * the record's shape is absorbed in this file alone. */

import { STEPS } from "../steps/spec.js";

export class Run {
  constructor(raw) {
    this.raw = raw ?? {};
    this.runId = this.raw.run_id ?? "unknown";
    this.dataset = this.raw.dataset ?? "unknown";
    this.mode = this.raw.mode ?? "scripted";
    this.demo = Boolean(this.raw.demo);
    this.provenance = this.raw.provenance ?? "";
    this.confidenceFloor = numberOr(this.raw.confidence_floor, 0.6);
    this.obs = this.raw.obs ?? {};
    this.metrics = this.raw.metrics ?? {};
    this.viz = this.raw.viz ?? {};

    this._steps = new Map((this.raw.steps ?? []).map((s) => [s.step, s]));
    this._decisions = groupBy(this.raw.decisions ?? [], (d) => d.step);
    this._reasoning = groupBy(this.raw.reasoning ?? [], (r) => r.step);
  }

  /** Steps in canonical order, each married to its record (or `pending`). */
  timeline() {
    return STEPS.map((spec) => {
      const record = this._steps.get(spec.name) ?? null;
      return {
        spec,
        record,
        status: record ? record.status : "pending",
        seconds: record?.seconds ?? null,
        decisions: this.decisions(spec.name),
      };
    });
  }

  summary(step) {
    return this._steps.get(step)?.summary ?? null;
  }

  /** One numeric/scalar field out of a step's summary. */
  summaryValue(step, key) {
    const v = this.summary(step)?.[key];
    const n = Number(v);
    return Number.isFinite(n) ? n : v;
  }

  decisions(step) {
    return (this._decisions.get(step) ?? []).map((d) => new DecisionView(d, this));
  }

  /** Claude's prose for a step: the planner's framing and any run narration. */
  reasoning(step) {
    const fromRecord = this._reasoning.get(step) ?? [];
    const framing = this.summary(step)?.framing;
    const framed = framing
      ? Object.entries(framing).map(([question, text]) => ({
          step, kind: "framing", question, text,
        }))
      : [];
    return [...framed, ...fromRecord];
  }

  /** cluster id -> cell type.
   *
   * The annotate step used to publish this map in its summary and no longer
   * always does, so fall back to pairing the two per-cell arrays in the
   * embedding, which carry the same assignment. */
  labels() {
    const declared = this.summary("annotate")?.labels;
    if (declared && Object.keys(declared).length) return declared;

    const e = this.viz.embedding;
    if (!e?.cluster?.length || !e.label?.length) return {};
    const out = {};
    e.cluster.forEach((cluster, i) => {
      const label = e.label[i];
      if (label !== undefined && !(cluster in out)) out[cluster] = label;
    });
    return out;
  }

  /** Who decided what, counted - drives the header chips. */
  tally() {
    const all = this.raw.decisions ?? [];
    return {
      total: all.length,
      jev: all.filter((d) => d.source === "jev").length,
      fallback: all.filter((d) => d.source !== "jev").length,
      claudeSteps: STEPS.filter((s) => s.decidedBy === "claude").length,
    };
  }
}

/** One typed decision, with its option set and probabilities resolved. */
export class DecisionView {
  constructor(raw, run) {
    this.raw = raw ?? {};
    this.runId = run.runId;
    this.dataset = run.dataset;
    this.step = this.raw.step;
    this.question = this.raw.question;
    this.value = this.raw.value;
    this.source = this.raw.source ?? "default";
    this.confidence = numberOrNull(this.raw.confidence);
    const note = this.raw.note ?? "";
    this.floor = run.confidenceFloor;
    this.instructions = this.raw.instructions ?? "";
    // the pipeline marks a framed question inside the note; that fact is shown
    // as a chip, so keep it out of the note line where it would be pure noise
    this.framed = /framed by claude/i.test(note);
    this.note = note.replace(/\s*\|?\s*framed by claude/i, "").trim();
    this.kind = this.raw.kind ?? inferKind(this.raw);
  }

  /** The options Jev was given, each with its probability when one came back. */
  options() {
    const probs = this.raw.raw?.probabilities ?? this.raw.probabilities ?? null;
    const declared = this.raw.criteria ?? null;

    let keys;
    if (declared && !Array.isArray(declared)) keys = Object.keys(declared);
    else if (Array.isArray(declared)) keys = declared.map((_, i) => String(i));
    else if (probs && !Array.isArray(probs)) keys = Object.keys(probs);
    else if (this.kind === "noul") keys = ["true", "false"];
    else keys = [String(this.value)];

    // Claude's framing may narrow a choice question before Jev sees it. The
    // narrowed set is not in the run log, but Jev's probability distribution is
    // keyed by the options it was actually offered - so a declared key missing
    // from that distribution is one the framing dropped.
    const offeredKnown = Boolean(probs) && !Array.isArray(probs);

    return keys.map((key, i) => ({
      key,
      description: Array.isArray(declared) ? declared[i] : declared?.[key] ?? "",
      probability: probabilityFor(key, i, probs, this),
      picked: String(key) === String(this.valueKey()),
      offered: offeredKnown ? key in probs : true,
    }));
  }

  /** The option key that corresponds to the value the pipeline actually used. */
  valueKey() {
    if (this.kind === "noul") return String(Boolean(this.value));
    if (this.kind === "score" && Array.isArray(this.raw.values)) {
      const i = this.raw.values.findIndex((v) => String(v) === String(this.value));
      return i >= 0 ? String(i) : String(this.value);
    }
    return String(this.value);
  }

  /** True when the confidence floor forced the declared default. */
  fellBack() {
    return this.source !== "jev";
  }

  belowFloor() {
    return this.confidence !== null && this.confidence < this.floor;
  }
}

function probabilityFor(key, i, probs, decision) {
  if (probs && !Array.isArray(probs) && key in probs) return Number(probs[key]);
  if (Array.isArray(probs) && Number.isFinite(probs[i])) return Number(probs[i]);
  if (decision.kind === "noul" && Number.isFinite(decision.raw.raw?.noul)) {
    const p = Number(decision.raw.raw.noul);
    return key === "true" ? p : 1 - p;
  }
  return null;
}

function inferKind(raw) {
  const r = raw.raw ?? {};
  if ("noul" in r || typeof raw.value === "boolean") return "noul";
  if ("score" in r || typeof raw.value === "number") return "score";
  return "choice";
}

function groupBy(items, key) {
  const out = new Map();
  for (const item of items) {
    const k = key(item);
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(item);
  }
  return out;
}

function numberOr(v, fallback) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function numberOrNull(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
