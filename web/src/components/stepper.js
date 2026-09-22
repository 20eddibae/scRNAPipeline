/* The step list: every step, whether it ran, and what it produced.
 * The line under each name is read from that step's own summary, so the list
 * reads top to bottom as what happened to the data. */

import { OVERVIEW } from "../steps/spec.js";
import { h, stepResult } from "./dom.js";

const MARK = { ok: "✓", error: "✗", pending: "○", skipped: "–" };

export function renderStepper(root, run, { active, running, onSelect }) {
  const overview = h("button", {
    class: "rail-item done",
    "aria-current": active === OVERVIEW,
    onClick: () => onSelect(OVERVIEW),
  },
    h("span", { class: "rail-mark", text: "≡" }),
    h("span", {}, h("span", { class: "rail-name", text: "Summary" })),
  );

  const list = h("ol", { class: "rail-list" });
  run.timeline().forEach((entry, i) => {
    const { spec, status, record } = entry;
    const live = spec.name === running;
    const state = live ? "active" : status === "ok" ? "done" : status;
    list.appendChild(h("li", {}, h("button", {
      class: `rail-item ${state}`,
      "aria-current": spec.name === active,
      onClick: () => onSelect(spec.name),
    },
      h("span", { class: "rail-mark", text: live ? "●" : MARK[status] ?? "○" }),
      h("span", {},
        h("span", { class: "rail-name", text: `${i + 1}. ${spec.title}` }),
        h("span", { class: "rail-who", text: live ? "running…"
          : status === "pending" ? "" : stepResult(spec, record?.summary, "brief") }),
      ),
    )));
  });

  root.replaceChildren(overview, h("div", { class: "rail-sep" }), list);
}
