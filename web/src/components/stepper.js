/* The left rail: all eight steps at once, which ran, and who decided inside. */

import { OVERVIEW } from "../steps/spec.js";
import { h } from "./dom.js";

const WHO = { jev: "Jev decides", claude: "Claude reads", neither: "no decision point" };

export function renderStepper(root, run, { active, running, onSelect }) {
  const overview = h("button", {
    class: "rail-item",
    "aria-current": active === OVERVIEW,
    onClick: () => onSelect(OVERVIEW),
    style: "margin-bottom:6px",
  },
    h("span", { class: "rail-num", text: "◎" }),
    h("span", {},
      h("span", { class: "rail-name", text: "overview" }),
      h("span", { class: "rail-who", style: "display:block",
                  text: "the whole run on one screen" }),
    ),
  );

  const list = h("ol", { class: "rail-list" });

  run.timeline().forEach((entry, i) => {
    const { spec, status, decisions } = entry;
    const live = spec.name === running;
    const item = h("button", {
      class: `rail-item ${status === "ok" ? "done" : status}${live ? " active" : ""}`,
      "aria-current": spec.name === active,
      onClick: () => onSelect(spec.name),
    },
      h("span", { class: "rail-num", text: String(i + 1) }),
      h("span", {},
        h("span", { class: "rail-name", text: spec.name }),
        h("span", { class: "rail-who", style: "display:block",
          text: live
            ? "running…"
            : decisions.length
              ? `${decisions.length} decision${decisions.length > 1 ? "s" : ""} · ${WHO[spec.decidedBy]}`
              : WHO[spec.decidedBy] }),
      ),
    );
    list.appendChild(h("li", {}, item));
  });

  root.replaceChildren(
    h("div", { class: "rail-title", text: "pipeline" }), overview, list,
  );
}
